#!/usr/bin/env python3
"""
Content Image Automation — GUI für die Google-Sheets→Nano-Banana-2-Pipeline.

Start:  python image_generator_ui.py   (oder per Doppelklick über die
        "Content Image Automation.app", siehe create_macos_app_with_icon.sh)

Funktionen:
- Tab "Generieren": Pipeline starten/stoppen, Log läuft live mit, Ordner öffnen
- Tabs "Jobs" / "Inhalte" / "Styles" / "Templates": Einträge des Google Sheets
  übersichtlich einsehen, per Schritt-für-Schritt-Assistent neu anlegen,
  bearbeiten und (mit Referenz-Schutz) löschen. IDs werden automatisch im
  bestehenden Muster vergeben (JOB-0001, ITEM-0001, STYLE-<KÜRZEL>-001, …).
  Das Google Sheet bleibt der Speicherort — Detailpflege dort ist weiter möglich.
- Update suchen (git pull, falls Git-Checkout)
- Status-Prüfung (fehlende .env-Werte, Service-Account, DRY_RUN)

Design: gleiche Sprache wie "Folder Converter" und "TTS Studio" — Scroll-Layout
mit gepinntem Footer, Karten-Frames, runde Buttons, resizable Log mit Zieh-Griff
und "Reset size"-Chip. Akzente in Weinrot auf sehr hellem Rot (statt Blau/Petrol).

Requirements (einmalig):
    pip install customtkinter gspread google-auth
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

try:
    from dotenv import dotenv_values
except Exception:
    dotenv_values = None

try:
    import sheet_admin as sa
    SHEET_ADMIN_ERROR = None
except Exception as _e:          # z. B. gspread nicht installiert
    sa = None
    SHEET_ADMIN_ERROR = _e


APP_TITLE = "Content Image Automation"

# ---------------------------------------------------------------------------
# Farbschema — weinrot auf sehr hellem Rot (ersetzt Blau/Petrol der anderen Apps)
# ---------------------------------------------------------------------------
WINE = "#8c2633"            # Haupt-Akzent + primäre "Los geht's"-Aktion (Start)
WINE_HOVER = "#6e1d28"
RED = "#c0392b"             # destruktiv / Abbruch (Stoppen, Löschen)
RED_HOVER = "#922b21"
GREY = ("gray75", "gray30")         # dezente Hilfs-Buttons (Update, Log leeren)
GREY_HOVER = ("gray65", "gray40")
# Fenster-Hintergrund: sehr blasses Rot (Light-Mode) bzw. ein minimal
# rotstichiges Dunkelgrau (Dark-Mode). Hebt die App vom hellgrauen Converter-
# und blassgrünen TTS-Fenster ab und macht sofort erkennbar, wo man ist.
APP_BG = ("#faeceb", "#241c1c")
# Karten liegen als leicht abgesetzte Flächen auf dem roten Grund.
CARD_BG = ("#d7a399", "#2c2222")

STATUS_COLORS = {"todo": WINE, "redo": "#b45309", "done": ("gray55", "gray45")}

# ---------------------------------------------------------------------------
# Auswahlwerte für die Assistenten (aus Sheet-Beispielen + Pipeline abgeleitet)
# ---------------------------------------------------------------------------
JOB_TYPES = ["batch_theme", "content_linked"]
STATUS_VALUES = ["todo", "redo", "done"]
ASPECT_RATIOS = ["1:1", "9:16", "16:9", "4:3", "3:4", "3:2", "2:3", "21:9"]
IMAGE_SIZES = ["1K", "2K", "4K"]
DEFAULT_MODEL = "gemini-3.1-flash-image"
CONTENT_TYPES = ["joke", "batch_seed", "fact", "tip", "quote"]
MATURITY_SUGGESTIONS = ["playful_but_mature", "premium", "adult_friendly", "neutral"]
# Platzhalter, die generate_images.render_prompt ersetzt (einfache {klammern})
PLACEHOLDERS = [
    "{source_text_or_topic}", "{title}", "{content_type}", "{asset_goal}",
    "{job_name}", "{job_type}", "{aspect_ratio}", "{preset_name}",
    "{visual_style}", "{color_palette}", "{composition_rules}",
    "{ui_safe_area}", "{positive_style_prompt}", "{negative_style_prompt}",
    "{notes}",
]

PLACEHOLDER_MENU_TEXT = "Platzhalter einfügen …"


def _apply_wine_theme() -> None:
    """Färbt die sonst blauen Standard-Widgets (Fokusrahmen, Checkboxen,
    Fortschritt, Menüs, Tab-Leiste) einheitlich weinrot ein — analog zum
    Petrol-Trick in TTS Studio, nur ohne eigenes Theme-File."""
    t = ctk.ThemeManager.theme
    t["CTkButton"]["fg_color"] = [WINE, WINE]
    t["CTkButton"]["hover_color"] = [WINE_HOVER, WINE_HOVER]
    t["CTkOptionMenu"]["fg_color"] = [WINE, WINE]
    t["CTkOptionMenu"]["button_color"] = [WINE_HOVER, WINE_HOVER]
    t["CTkOptionMenu"]["button_hover_color"] = [WINE_HOVER, WINE_HOVER]
    t["CTkComboBox"]["button_color"] = [WINE, WINE]
    t["CTkComboBox"]["button_hover_color"] = [WINE_HOVER, WINE_HOVER]
    t["CTkCheckBox"]["fg_color"] = [WINE, WINE]
    t["CTkCheckBox"]["hover_color"] = [WINE_HOVER, WINE_HOVER]
    t["CTkProgressBar"]["progress_color"] = [WINE, WINE]
    t["CTkSwitch"]["progress_color"] = [WINE, WINE]
    t["CTkSlider"]["progress_color"] = [WINE, WINE]
    t["CTkSegmentedButton"]["selected_color"] = [WINE, WINE]
    t["CTkSegmentedButton"]["selected_hover_color"] = [WINE_HOVER, WINE_HOVER]
    for key in ("border_color", "hover_color"):
        if key in t.get("DropdownMenu", {}):
            t["DropdownMenu"][key] = [WINE, WINE]


def find_project_root() -> Path:
    """Works when this file is in project/scripts or project root."""
    configured = os.getenv("CIA_PROJECT_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    here = Path(__file__).resolve()
    candidates = [here.parent, here.parent.parent, Path.cwd()]
    for candidate in candidates:
        if (candidate / ".env").exists() and (candidate / "scripts" / "generate_images.py").exists():
            return candidate
    return here.parent.parent


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _shorten(text: str, n: int = 70) -> str:
    text = _clean(text).replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Schritt-für-Schritt-Dialog (gemeinsame Basis aller Assistenten)
# ---------------------------------------------------------------------------

class StepDialog(ctk.CTkToplevel):
    """Mehrseitiger Assistent: Schritt x/y, Zurück/Weiter, Validierung,
    automatische Zusammenfassung mit ID-Vorschau, Speichern-Callback.

    steps: Liste von Dicts:
      {"title": str,
       "fields": [Feld-Dicts]           # deklarative Eingabefelder
       "builder": fn(dialog, parent)    # ODER eigener Inhalt (z. B. Inhalte-Liste)
       "skip": fn(dialog) -> bool       # Schritt dynamisch überspringen
       "summary": fn(dialog) -> [(label, wert)]}   # Beitrag zur Zusammenfassung

    Feld-Dict:
      {"key", "label", "type": entry|int|text|option|combo|kuerzel|files,
       "values": [..] | fn(dialog)->[..], "default": str | fn(dialog)->str,
       "required": bool, "help": str, "height": int, "transform": fn(str)->str,
       "placeholders": bool (Textfeld bekommt Platzhalter-Menü),
       "create_only": bool (im Bearbeiten-Modus ausgeblendet)}
    """

    def __init__(self, app, title: str, steps: list, on_save,
                 initial=None, fixed_id: str | None = None,
                 id_preview=None) -> None:
        super().__init__(app)
        self.app = app
        self.on_save = on_save
        self.initial = {k: v for k, v in (initial or {}).items()
                        if not str(k).startswith("_")}
        self.fixed_id = fixed_id
        self.id_preview = id_preview          # fn(dialog, kuerzel|None) -> str
        self.values: dict = {}
        self.extra: dict = {}                 # für eigene Schritte (z. B. Items)
        self._widgets: dict = {}              # key -> (field, widget)
        self._saving = False

        self.steps = list(steps) + [{"title": "Zusammenfassung",
                                     "builder": self._build_summary}]
        self.cur = 0

        self.title(title)
        w, h = 680, 620
        try:
            x = app.winfo_rootx() + max(0, (app.winfo_width() - w) // 2)
            y = app.winfo_rooty() + max(0, (app.winfo_height() - h) // 3)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            self.geometry(f"{w}x{h}")
        self.minsize(560, 480)
        self.configure(fg_color=APP_BG)
        self.transient(app)

        bold15 = ctk.CTkFont(size=15, weight="bold")
        self.step_label = ctk.CTkLabel(self, text="", font=bold15, anchor="w")
        self.step_label.pack(side="top", fill="x", padx=20, pady=(16, 4))

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(side="bottom", fill="x", padx=20, pady=(6, 16))
        ctk.CTkButton(nav, text="Abbrechen", width=100, fg_color=GREY,
                      hover_color=GREY_HOVER, command=self._cancel
                      ).pack(side="left")
        self.next_btn = ctk.CTkButton(nav, text="Weiter ›", width=150,
                                      font=ctk.CTkFont(size=13, weight="bold"),
                                      fg_color=WINE, hover_color=WINE_HOVER,
                                      command=self._next)
        self.next_btn.pack(side="right")
        self.back_btn = ctk.CTkButton(nav, text="‹ Zurück", width=100,
                                      fg_color=GREY, hover_color=GREY_HOVER,
                                      command=self._back)
        self.back_btn.pack(side="right", padx=(0, 10))

        self.content = ctk.CTkScrollableFrame(self, fg_color=CARD_BG)
        self.content.pack(side="top", fill="both", expand=True,
                          padx=20, pady=(4, 6))

        self.bind("<Escape>", lambda e: self._cancel())
        self._show_step(0)
        # grab_set erst, wenn das Fenster wirklich gemappt ist (macOS-Eigenart)
        self.after(200, self._grab)

    def _grab(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    def _cancel(self):
        if self._saving:
            return
        self.destroy()

    # -- Navigation ----------------------------------------------------------

    def _visible_steps(self) -> list[int]:
        out = []
        for i, step in enumerate(self.steps):
            skip = step.get("skip")
            if skip and skip(self):
                continue
            out.append(i)
        return out

    def _show_step(self, idx: int):
        self.cur = idx
        step = self.steps[idx]
        vis = self._visible_steps()
        pos = vis.index(idx) + 1 if idx in vis else 1
        self.step_label.configure(
            text=f"Schritt {pos} von {len(vis)} — {step['title']}")

        for child in self.content.winfo_children():
            child.destroy()
        self._widgets = {}

        if step.get("builder"):
            step["builder"](self, self.content)
        for f in step.get("fields", []):
            if f.get("create_only") and self.fixed_id:
                continue
            self._build_field(self.content, f)

        is_last = (idx == vis[-1])
        self.next_btn.configure(
            text="💾  Speichern" if is_last else "Weiter ›",
            state="normal" if not self._saving else "disabled")
        self.back_btn.configure(
            state="normal" if idx != vis[0] else "disabled")

    def _step_move(self, direction: int) -> int:
        i = self.cur + direction
        while 0 <= i < len(self.steps):
            skip = self.steps[i].get("skip")
            if skip and skip(self):
                i += direction
            else:
                break
        return i

    def _next(self):
        if self._saving:
            return
        if not self._collect_current(validate=True):
            return
        vis = self._visible_steps()
        if self.cur == vis[-1]:
            self._saving = True
            self.next_btn.configure(state="disabled", text="Speichere …")
            self.on_save(self)
            return
        self._show_step(self._step_move(+1))

    def _back(self):
        if self._saving:
            return
        self._collect_current(validate=False)
        self._show_step(self._step_move(-1))

    def fail_save(self):
        """Nach fehlgeschlagenem Speichern wieder freigeben."""
        self._saving = False
        self.next_btn.configure(state="normal", text="💾  Speichern")

    # -- Felder ----------------------------------------------------------------

    def _current_value(self, f) -> str:
        key = f["key"]
        if key in self.values:
            return _clean(self.values[key])
        if _clean(self.initial.get(key)):
            return _clean(self.initial.get(key))
        d = f.get("default", "")
        return _clean(d(self) if callable(d) else d)

    def _resolve_values(self, f) -> list[str]:
        vals = f.get("values", [])
        vals = list(vals(self) if callable(vals) else vals)
        return [v for v in vals if _clean(v)] or ["—"]

    def _build_field(self, parent, f):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(box, text=f["label"] + (" *" if f.get("required") else ""),
                     font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(fill="x")

        t = f.get("type", "entry")
        current = self._current_value(f)
        widget = None

        if t in ("entry", "int", "kuerzel", "files", "folder"):
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", pady=(2, 0))
            widget = ctk.CTkEntry(row)
            widget.insert(0, current)
            widget.pack(side="left", fill="x", expand=True)
            if t == "files":
                ctk.CTkButton(row, text="Durchsuchen…", width=110,
                              fg_color=WINE, hover_color=WINE_HOVER,
                              command=lambda w=widget: self._browse_files(w)
                              ).pack(side="left", padx=(8, 0))
            if t == "folder":
                ctk.CTkButton(row, text="Ordner wählen…", width=120,
                              fg_color=WINE, hover_color=WINE_HOVER,
                              command=lambda w=widget: self._browse_folder(w)
                              ).pack(side="left", padx=(8, 0))
            if t == "kuerzel" and self.id_preview:
                preview = ctk.CTkLabel(box, text="", anchor="w",
                                       text_color=("gray35", "gray65"),
                                       font=ctk.CTkFont(size=11))
                preview.pack(fill="x")

                def upd(_e=None, w=widget, lbl=preview):
                    lbl.configure(
                        text=f"ID-Vorschau: {self.id_preview(self, w.get())}")
                widget.bind("<KeyRelease>", upd)
                upd()
        elif t == "text":
            widget = ctk.CTkTextbox(box, height=f.get("height", 70))
            if current:
                widget.insert("1.0", current)
            widget.pack(fill="x", pady=(2, 0))
            if f.get("placeholders"):
                menu = ctk.CTkOptionMenu(
                    box, values=PLACEHOLDERS, width=220, height=24,
                    font=ctk.CTkFont(size=11), fg_color=GREY,
                    button_color=GREY, button_hover_color=GREY_HOVER,
                    text_color=("gray20", "gray85"))

                def insert_ph(v, tb=widget, m=menu):
                    if v in PLACEHOLDERS:
                        tb.insert("insert", v)
                    m.set(PLACEHOLDER_MENU_TEXT)
                menu.configure(command=insert_ph)
                menu.set(PLACEHOLDER_MENU_TEXT)
                menu.pack(anchor="w", pady=(4, 0))
        elif t == "option":
            vals = self._resolve_values(f)
            if current and current not in vals:
                vals.insert(0, current)
            widget = ctk.CTkOptionMenu(box, values=vals, width=280)
            widget.set(current if current else vals[0])
            widget.pack(anchor="w", pady=(2, 0))
        elif t == "combo":
            vals = self._resolve_values(f)
            widget = ctk.CTkComboBox(box, values=vals, width=280)
            widget.set(current)
            widget.pack(anchor="w", pady=(2, 0))

        if f.get("help"):
            ctk.CTkLabel(box, text=f["help"], anchor="w", justify="left",
                         wraplength=560, text_color=("gray35", "gray65"),
                         font=ctk.CTkFont(size=11)).pack(fill="x")

        self._widgets[f["key"]] = (f, widget)

    def _browse_files(self, entry: ctk.CTkEntry):
        initial = self.app.project_root / "references"
        paths = filedialog.askopenfilenames(
            parent=self, title="Referenzbilder wählen",
            initialdir=str(initial if initial.is_dir() else self.app.project_root))
        if not paths:
            return
        rels = []
        for p in paths:
            try:
                # Sheet values are relative to REFERENCE_DIR, not to the
                # project root.  Existing values with a leading "references/"
                # remain supported by the generator for backwards compatibility.
                rels.append(str(Path(p).relative_to(initial)))
            except ValueError:
                rels.append(p)
        cur = entry.get().strip()
        joined = "; ".join(([cur] if cur else []) + rels)
        entry.delete(0, "end")
        entry.insert(0, joined)

    def _browse_folder(self, entry: ctk.CTkEntry):
        """Finder-Dialog zum Wählen ODER Neu-Anlegen des Ausgabeordners.
        Der native macOS-Dialog hat unten links 'Neuer Ordner'."""
        cur = entry.get().strip()
        outputs = self.app.project_root / "outputs"
        start = self.app.project_root / cur if cur else outputs
        if not start.is_dir():
            start = outputs if outputs.is_dir() else self.app.project_root
        chosen = filedialog.askdirectory(
            parent=self, title="Ausgabeordner wählen (oder unten 'Neuer Ordner')",
            initialdir=str(start), mustexist=False)
        if not chosen:
            return
        try:
            rel = str(Path(chosen).relative_to(self.app.project_root))
        except ValueError:
            rel = chosen   # außerhalb des Projekts → absoluter Pfad
        entry.delete(0, "end")
        entry.insert(0, rel)

    def _read_widget(self, f, widget) -> str:
        if widget is None:
            return _clean(self.values.get(f["key"], ""))
        if f.get("type") == "text":
            v = widget.get("1.0", "end").strip()
        else:
            v = widget.get().strip()
        if v == "—":
            v = ""
        tr = f.get("transform")
        return tr(v) if tr else v

    def _collect_current(self, validate: bool) -> bool:
        step = self.steps[self.cur]
        for key, (f, widget) in self._widgets.items():
            v = self._read_widget(f, widget)
            if validate:
                if f.get("required") and not v:
                    messagebox.showwarning(
                        APP_TITLE, f"Bitte „{f['label']}“ ausfüllen.",
                        parent=self)
                    return False
                if f.get("type") == "int" and v and not v.isdigit():
                    messagebox.showwarning(
                        APP_TITLE, f"„{f['label']}“ muss eine Zahl sein.",
                        parent=self)
                    return False
            self.values[key] = v
        if validate and step.get("validate"):
            err = step["validate"](self)
            if err:
                messagebox.showwarning(APP_TITLE, err, parent=self)
                return False
        return True

    # -- Zusammenfassung -------------------------------------------------------

    def _build_summary(self, dlg, parent):
        rid = self.fixed_id or (self.id_preview(self, None)
                                if self.id_preview else "automatisch")
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(head, text="ID:", font=ctk.CTkFont(size=13, weight="bold"),
                     width=170, anchor="w").pack(side="left")
        ctk.CTkLabel(head, text=rid, anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=WINE).pack(side="left")

        for step in self.steps[:-1]:
            skip = step.get("skip")
            if skip and skip(self):
                continue
            rows = []
            for f in step.get("fields", []):
                if f.get("create_only") and self.fixed_id:
                    continue
                if f["key"].startswith("_"):
                    continue
                rows.append((f["label"], _shorten(self.values.get(f["key"], ""), 90)))
            if step.get("summary"):
                rows.extend(step["summary"](self))
            if not rows:
                continue
            ctk.CTkLabel(parent, text=step["title"],
                         font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w").pack(fill="x", padx=12, pady=(8, 0))
            for label, value in rows:
                row = ctk.CTkFrame(parent, fg_color="transparent")
                row.pack(fill="x", padx=12)
                ctk.CTkLabel(row, text=label + ":", width=170, anchor="nw",
                             text_color=("gray35", "gray65"),
                             font=ctk.CTkFont(size=12)).pack(side="left")
                ctk.CTkLabel(row, text=value or "—", anchor="w",
                             justify="left", wraplength=380,
                             font=ctk.CTkFont(size=12)).pack(
                    side="left", fill="x", expand=True)


# ---------------------------------------------------------------------------
# Haupt-App
# ---------------------------------------------------------------------------

class ImageGeneratorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        self.project_root = find_project_root()
        self.env_path = self.project_root / ".env"
        self.generator_script = self.project_root / "scripts" / "generate_images.py"
        self.frozen = bool(getattr(sys, "frozen", False))
        self.venv_python = (
            Path(sys.executable)
            if self.frozen
            else self.project_root / ".venv" / "bin" / "python"
        )
        self.process: subprocess.Popen[str] | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._ui_queue: queue.Queue = queue.Queue()
        self.running = False

        # Verwaltung (Google-Sheet-Daten)
        self._admin = None
        self.tables: dict = {}
        self._loading: set = set()
        self._list_frames: dict = {}
        self._count_labels: dict = {}

        self.title(APP_TITLE)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(920, sw - 40), min(760, sh - 80)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(760, 540)

        ctk.set_appearance_mode("system")
        # "blue" als Basis, dann alle Akzente weinrot überschreiben (siehe
        # _apply_wine_theme) — so bleibt kein Widget blau.
        ctk.set_default_color_theme("blue")
        _apply_wine_theme()
        self.configure(fg_color=APP_BG)

        # resizable-panel state (wie Converter/TTS — hier nur das Log-Panel)
        self._default_heights = {"log": 240}
        self._heights = dict(self._default_heights)
        self._containers = {}

        self._build_ui()
        self._refresh_status()
        self._append_log(
            "Content Image Automation bereit. Status prüfen, dann Start.\n")
        self.after(150, self._poll_log_queue)

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        bold14 = ctk.CTkFont(size=14, weight="bold")
        bold13 = ctk.CTkFont(size=13, weight="bold")

        # Gepinnter Footer: Status oben, Fortschritt + Stoppen/Start unten-rechts
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=20, pady=(6, 14))
        self.status = ctk.CTkLabel(footer, text="Bereit.", text_color="gray",
                                   anchor="w")
        self.status.pack(side="top", fill="x")
        self.hint_label = ctk.CTkLabel(
            footer, anchor="w", text_color="gray",
            text="Hinweis: Bei DRY_RUN=true werden keine echten Bilder generiert.")
        self.hint_label.pack(side="top", fill="x")
        row = ctk.CTkFrame(footer, fg_color="transparent")
        row.pack(side="top", fill="x", pady=(6, 0))
        self.progress = ctk.CTkProgressBar(row, progress_color=WINE)
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.stop_btn = ctk.CTkButton(
            row, text="■", width=52, height=46, font=ctk.CTkFont(size=16),
            fg_color=RED, hover_color=RED_HOVER, command=self.stop_generation,
            state="disabled")
        self.stop_btn.pack(side="right")
        self.start_btn = ctk.CTkButton(
            row, text="▶  Generierung starten", width=200, height=46,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=WINE, hover_color=WINE_HOVER, command=self.start_generation)
        self.start_btn.pack(side="right", padx=(0, 12))

        # Tab-Ansicht: Generieren + Verwaltung
        self.tabs = ctk.CTkTabview(
            self, fg_color="transparent",
            segmented_button_selected_color=WINE,
            segmented_button_selected_hover_color=WINE_HOVER,
            command=self._on_tab_change)
        self.tabs.pack(side="top", fill="both", expand=True, padx=8)

        self._sheet_by_tab = {}
        gen_tab = self.tabs.add("Generieren")
        if sa is not None:
            self._tab_titles = {
                sa.SHEET_JOBS: ("Jobs", "Jobs & Batches"),
                sa.SHEET_ITEMS: ("Inhalte", "Content Items"),
                sa.SHEET_STYLES: ("Styles", "Style Presets"),
                sa.SHEET_TEMPLATES: ("Templates", "Prompt Templates"),
            }
            for sheet, (tab_name, heading) in self._tab_titles.items():
                tab = self.tabs.add(tab_name)
                self._sheet_by_tab[tab_name] = sheet
                self._build_mgmt_tab(tab, sheet, heading)

        # ---- Tab "Generieren": scrollbarer Inhalt wie bisher ----
        self.scroll = ctk.CTkScrollableFrame(gen_tab, fg_color="transparent")
        self.scroll.pack(side="top", fill="both", expand=True)
        self.scroll.grid_columnconfigure(0, weight=1)
        c = self.scroll

        # "Update suchen": oben im Scrollbereich, scrollt beim Runterscrollen weg
        upd_row = ctk.CTkFrame(c, fg_color="transparent")
        upd_row.grid(row=0, column=0, padx=20, pady=(4, 0), sticky="ew")
        self.update_btn = ctk.CTkButton(
            upd_row, text="Update suchen", width=118, height=26,
            font=ctk.CTkFont(size=11), fg_color=GREY, hover_color=GREY_HOVER,
            command=self.check_update)
        self.update_btn.pack(side="right")
        # "Reset size": oben-links als Overlay, sichtbar wenn das Log vergrößert ist
        self.reset_btn = ctk.CTkButton(
            self, text="⤢ Reset size", width=110, height=26,
            font=ctk.CTkFont(size=11), fg_color=GREY, hover_color=GREY_HOVER,
            command=self.reset_layout)

        # Karte: Projekt / Status
        info_frame = ctk.CTkFrame(c, fg_color=CARD_BG)
        info_frame.grid(row=1, column=0, padx=20, pady=(10, 8), sticky="ew")
        info_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(info_frame, text="Projekt", font=bold13).grid(
            row=0, column=0, columnspan=2, padx=12, pady=(8, 2), sticky="w")
        ctk.CTkLabel(info_frame, text="Ordner:", anchor="w").grid(
            row=1, column=0, padx=(12, 8), pady=2, sticky="w")
        self.project_value = ctk.CTkLabel(
            info_frame, text=str(self.project_root), anchor="w",
            text_color=("gray30", "gray70"))
        self.project_value.grid(row=1, column=1, padx=(0, 12), pady=2, sticky="ew")
        ctk.CTkLabel(info_frame, text="Pipeline:", anchor="w").grid(
            row=2, column=0, padx=(12, 8), pady=(2, 12), sticky="w")
        ctk.CTkLabel(
            info_frame, text="Google Sheets → Nano Banana 2 → Output-Ordner",
            anchor="w", text_color=("gray30", "gray70")).grid(
            row=2, column=1, padx=(0, 12), pady=(2, 12), sticky="ew")

        # Karte: Aktionen
        act_frame = ctk.CTkFrame(c, fg_color=CARD_BG)
        act_frame.grid(row=2, column=0, padx=20, pady=8, sticky="ew")
        ctk.CTkLabel(act_frame, text="Aktionen", font=bold13).grid(
            row=0, column=0, padx=12, pady=(8, 2), sticky="w")
        arow = ctk.CTkFrame(act_frame, fg_color="transparent")
        arow.grid(row=1, column=0, padx=12, pady=(4, 12), sticky="w")
        ctk.CTkButton(arow, text="📄  Google Sheet öffnen", width=180,
                      fg_color=WINE, hover_color=WINE_HOVER,
                      command=self.open_google_sheet).pack(side="left")
        ctk.CTkButton(arow, text="🖼  Outputs öffnen", width=150,
                      fg_color=WINE, hover_color=WINE_HOVER,
                      command=lambda: self.open_path(self.project_root / "outputs")
                      ).pack(side="left", padx=(10, 0))
        ctk.CTkButton(arow, text="🗂  References öffnen", width=160,
                      fg_color=WINE, hover_color=WINE_HOVER,
                      command=lambda: self.open_path(self.project_root / "references")
                      ).pack(side="left", padx=(10, 0))
        arow2 = ctk.CTkFrame(act_frame, fg_color="transparent")
        arow2.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="w")
        ctk.CTkButton(arow2, text="📁  Projektordner", width=150,
                      fg_color=WINE, hover_color=WINE_HOVER,
                      command=lambda: self.open_path(self.project_root)
                      ).pack(side="left")
        ctk.CTkButton(arow2, text="🧹  Log leeren", width=130,
                      fg_color=GREY, hover_color=GREY_HOVER,
                      command=self.clear_log).pack(side="left", padx=(10, 0))

        # Karte: Protokoll (resizable, wie das Log in Converter/TTS)
        log_card = ctk.CTkFrame(c, fg_color=CARD_BG)
        log_card.grid(row=3, column=0, padx=20, pady=8, sticky="ew")
        log_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_card, text="Protokoll", font=bold14).grid(
            row=0, column=0, padx=12, pady=(10, 2), sticky="w")
        log_outer = ctk.CTkFrame(log_card, fg_color="transparent",
                                 height=self._heights["log"])
        log_outer.grid(row=1, column=0, padx=12, pady=(4, 0), sticky="ew")
        log_outer.grid_propagate(False)
        log_outer.grid_columnconfigure(0, weight=1)
        log_outer.grid_rowconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(log_outer, font=ctk.CTkFont(family="Menlo", size=11))
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self.log_box.configure(state="disabled")
        self._containers["log"] = log_outer
        self._make_grip(log_card, "log", log_outer, min_h=120).grid(
            row=2, column=0, padx=12, pady=(3, 10), sticky="ew")

        self._setup_qol()

    # -- Verwaltungs-Tabs -----------------------------------------------------

    def _build_mgmt_tab(self, tab, sheet: str, heading: str) -> None:
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(side="top", fill="x", padx=12, pady=(4, 6))
        ctk.CTkLabel(bar, text=heading,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        count = ctk.CTkLabel(bar, text="", text_color="gray")
        count.pack(side="left", padx=(10, 0))
        self._count_labels[sheet] = count

        ctk.CTkButton(bar, text="➕  Neu…", width=110,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      fg_color=WINE, hover_color=WINE_HOVER,
                      command=lambda s=sheet: self.new_record(s)
                      ).pack(side="right")
        ctk.CTkButton(bar, text="🔄  Aktualisieren", width=120, height=26,
                      font=ctk.CTkFont(size=11), fg_color=GREY,
                      hover_color=GREY_HOVER,
                      command=lambda s=sheet: self._load_table(s, force=True)
                      ).pack(side="right", padx=(0, 8))
        ctk.CTkButton(bar, text="Im Sheet öffnen", width=110, height=26,
                      font=ctk.CTkFont(size=11), fg_color=GREY,
                      hover_color=GREY_HOVER,
                      command=self.open_google_sheet
                      ).pack(side="right", padx=(0, 8))

        lst = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        lst.pack(side="top", fill="both", expand=True, padx=6, pady=(0, 6))
        self._list_frames[sheet] = lst

    def _on_tab_change(self, *_args) -> None:
        sheet = self._sheet_by_tab.get(self.tabs.get())
        if sheet:
            self._load_table(sheet)

    # -- Google-Sheet-Zugriff ---------------------------------------------------

    def _get_admin(self):
        if self._admin is not None:
            return self._admin
        if sa is None:
            raise RuntimeError(
                f"Verwaltung nicht verfügbar — Modul konnte nicht geladen "
                f"werden:\n{SHEET_ADMIN_ERROR}\n\n"
                "Bitte einmal ausführen:  pip install gspread google-auth")
        env = self._read_env()
        sheet_id = env.get("GOOGLE_SHEET_ID", "").strip()
        sa_file = env.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        if not sheet_id or not sa_file:
            raise RuntimeError(
                "GOOGLE_SHEET_ID oder GOOGLE_SERVICE_ACCOUNT_FILE fehlt in der .env.")
        sa_path = Path(sa_file).expanduser()
        if not sa_path.is_absolute():
            sa_path = self.project_root / sa_path
        if not sa_path.exists():
            raise RuntimeError(f"Service-Account-Datei nicht gefunden: {sa_path}")
        self._admin = sa.SheetAdmin(sheet_id, str(sa_path))
        return self._admin

    def _run_async(self, fn, on_done, on_error=None) -> None:
        """Führt fn in einem Thread aus; on_done/on_error laufen im
        Main-Thread (über die UI-Queue, wie beim Log — Tkinter-sicher)."""
        def work():
            try:
                result = fn()
            except Exception as e:
                def show(e=e):
                    if on_error:
                        on_error(e)
                    else:
                        messagebox.showerror(
                            APP_TITLE, f"Google-Sheet-Zugriff fehlgeschlagen:\n{e}")
                self._ui_queue.put(show)
                return
            self._ui_queue.put(lambda: on_done(result))
        threading.Thread(target=work, daemon=True).start()

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception as e:
                    self._append_log(f"UI-Fehler: {e}\n")
        except queue.Empty:
            pass

    def _load_table(self, sheet: str, force: bool = False) -> None:
        if not force and sheet in self.tables:
            return
        if sheet in self._loading:
            return
        self._loading.add(sheet)
        self._set_list_message(sheet, "⏳  Lade Daten aus dem Google Sheet …")

        def done(table):
            self._loading.discard(sheet)
            self.tables[sheet] = table
            self._render_table(sheet)

        def err(e):
            self._loading.discard(sheet)
            self._set_list_message(sheet, f"❌  Laden fehlgeschlagen:\n{e}",
                                   retry=True)

        self._run_async(lambda: self._get_admin().load(sheet), done, err)

    def _ensure_tables(self, sheets: list, callback) -> None:
        """Lädt fehlende Tabellen asynchron und ruft danach callback auf."""
        missing = [s for s in sheets if s not in self.tables]
        if not missing:
            callback()
            return
        state = {"left": len(missing), "failed": False}

        def one_done(sheet, table):
            self._loading.discard(sheet)
            self.tables[sheet] = table
            self._render_table(sheet)
            state["left"] -= 1
            if state["left"] == 0 and not state["failed"]:
                callback()

        def one_err(e):
            state["failed"] = True
            messagebox.showerror(APP_TITLE,
                                 f"Google-Sheet-Zugriff fehlgeschlagen:\n{e}")

        self.status.configure(text="Lade Daten aus dem Google Sheet …")
        for sheet in missing:
            self._loading.add(sheet)
            self._run_async(lambda s=sheet: self._get_admin().load(s),
                            lambda t, s=sheet: one_done(s, t), one_err)

    def _after_write(self, sheet: str, message: str) -> None:
        self._append_log(message + "\n")
        self.status.configure(text=message)
        self._load_table(sheet, force=True)

    # -- Übersichtslisten --------------------------------------------------------

    def _set_list_message(self, sheet: str, text: str, retry: bool = False) -> None:
        frame = self._list_frames.get(sheet)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(frame, text=text, justify="left",
                     text_color=("gray35", "gray65")).pack(pady=18)
        if retry:
            ctk.CTkButton(frame, text="Erneut versuchen", width=140,
                          fg_color=WINE, hover_color=WINE_HOVER,
                          command=lambda: self._load_table(sheet, force=True)
                          ).pack()

    def _record_lines(self, sheet: str, r: dict) -> tuple[str, str, str]:
        """(id, titel, detailzeile) für eine Listenkarte."""
        j = lambda *parts: "  ·  ".join([p for p in parts if _clean(p)])
        if sheet == sa.SHEET_JOBS:
            tc = _clean(r.get("target_count"))
            qc_off = _clean(r.get("qc_enabled")).lower() in {
                "nein", "no", "false", "0", "aus", "off"}
            return (
                _clean(r.get("job_id")), _clean(r.get("job_name")),
                j(_clean(r.get("job_type")),
                  f"Style: {_clean(r.get('style_preset_id'))}" if _clean(r.get("style_preset_id")) else "",
                  f"Template: {_clean(r.get('prompt_template_id'))}" if _clean(r.get("prompt_template_id")) else "",
                  _clean(r.get("aspect_ratio")),
                  f"{tc} Bilder" if tc else "",
                  "QC aus" if qc_off else "",
                  _shorten(r.get("output_folder"), 40)))
        if sheet == sa.SHEET_ITEMS:
            return (
                _clean(r.get("item_id")), _clean(r.get("title")),
                j(_clean(r.get("job_id")), _clean(r.get("content_type")),
                  _shorten(r.get("source_text_or_topic"), 70)))
        if sheet == sa.SHEET_STYLES:
            return (
                _clean(r.get("style_preset_id")), _clean(r.get("preset_name")),
                j(_shorten(r.get("use_case"), 46), _shorten(r.get("visual_style"), 60)))
        return (
            _clean(r.get("prompt_template_id")), _clean(r.get("template_name")),
            j(_clean(r.get("job_type")), _clean(r.get("model")),
              _shorten(r.get("template_purpose"), 60)))

    def _render_table(self, sheet: str) -> None:
        frame = self._list_frames.get(sheet)
        if frame is None:
            return
        table = self.tables.get(sheet)
        for child in frame.winfo_children():
            child.destroy()

        count = self._count_labels.get(sheet)
        n = len(table.records) if table else 0
        if count:
            count.configure(text=f"{n} Einträge" if n != 1 else "1 Eintrag")
        if table is None or not table.records:
            ctk.CTkLabel(frame, text="Keine Einträge vorhanden. Mit „➕ Neu…“ "
                                     "den ersten Eintrag anlegen.",
                         text_color=("gray35", "gray65")).pack(pady=18)
            return

        bold13 = ctk.CTkFont(size=13, weight="bold")
        small = ctk.CTkFont(size=11)
        for rec in table.records:
            rid, title, line2 = self._record_lines(sheet, rec)
            card = ctk.CTkFrame(frame, fg_color=CARD_BG)
            card.pack(fill="x", padx=4, pady=4)
            card.grid_columnconfigure(0, weight=1)

            left = ctk.CTkFrame(card, fg_color="transparent")
            left.grid(row=0, column=0, padx=(12, 6), pady=8, sticky="ew")
            head = ctk.CTkFrame(left, fg_color="transparent")
            head.pack(fill="x", anchor="w")
            ctk.CTkLabel(head, text=rid, font=bold13,
                         text_color=WINE).pack(side="left")
            if title:
                ctk.CTkLabel(head, text="  " + title, font=bold13
                             ).pack(side="left")
            if line2:
                ctk.CTkLabel(left, text=line2, font=small, anchor="w",
                             justify="left", wraplength=520,
                             text_color=("gray30", "gray65")).pack(
                    fill="x", anchor="w")

            right = ctk.CTkFrame(card, fg_color="transparent")
            right.grid(row=0, column=1, padx=(0, 12), pady=8, sticky="e")
            if sheet == sa.SHEET_JOBS:
                status = _clean(rec.get("status")).lower() or "todo"
                menu = ctk.CTkOptionMenu(
                    right, values=STATUS_VALUES, width=86, height=26,
                    font=small,
                    fg_color=STATUS_COLORS.get(status, WINE),
                    button_color=STATUS_COLORS.get(status, WINE),
                    button_hover_color=WINE_HOVER)
                menu.set(status)
                menu.configure(command=lambda v, r=rec, m=menu:
                               self._quick_status(r, v, m))
                menu.pack(side="left", padx=(0, 8))
            ctk.CTkButton(right, text="Bearbeiten", width=92, height=26,
                          font=small, fg_color=WINE, hover_color=WINE_HOVER,
                          command=lambda s=sheet, r=rec: self.edit_record(s, r)
                          ).pack(side="left")
            ctk.CTkButton(right, text="🗑", width=36, height=26, font=small,
                          fg_color=RED, hover_color=RED_HOVER,
                          command=lambda s=sheet, r=rec: self.delete_record(s, r)
                          ).pack(side="left", padx=(6, 0))

    # -- Aktionen der Verwaltung ---------------------------------------------------

    def _quick_status(self, rec: dict, new_status: str, menu) -> None:
        job_id = _clean(rec.get("job_id"))

        def done(_):
            rec["status"] = new_status
            color = STATUS_COLORS.get(new_status, WINE)
            menu.configure(fg_color=color, button_color=color)
            self._append_log(f"✏️ {job_id}: Status → {new_status}\n")
            self.status.configure(text=f"{job_id}: Status → {new_status}")

        def err(e):
            menu.set(_clean(rec.get("status")) or "todo")
            messagebox.showerror(APP_TITLE, f"Status konnte nicht gespeichert werden:\n{e}")

        self._run_async(
            lambda: self._get_admin().update_record(
                sa.SHEET_JOBS, job_id, {"status": new_status}),
            done, err)

    def new_record(self, sheet: str) -> None:
        needed = {
            sa.SHEET_JOBS: [sa.SHEET_JOBS, sa.SHEET_STYLES, sa.SHEET_TEMPLATES],
            sa.SHEET_ITEMS: [sa.SHEET_ITEMS, sa.SHEET_JOBS],
            sa.SHEET_STYLES: [sa.SHEET_STYLES],
            sa.SHEET_TEMPLATES: [sa.SHEET_TEMPLATES],
        }[sheet]
        self._ensure_tables(needed, lambda: self._open_wizard(sheet, None))

    def edit_record(self, sheet: str, rec: dict) -> None:
        needed = {
            sa.SHEET_JOBS: [sa.SHEET_JOBS, sa.SHEET_STYLES, sa.SHEET_TEMPLATES],
            sa.SHEET_ITEMS: [sa.SHEET_ITEMS, sa.SHEET_JOBS],
            sa.SHEET_STYLES: [sa.SHEET_STYLES],
            sa.SHEET_TEMPLATES: [sa.SHEET_TEMPLATES],
        }[sheet]
        self._ensure_tables(needed, lambda: self._open_wizard(sheet, rec))

    def delete_record(self, sheet: str, rec: dict) -> None:
        rid = _clean(rec.get(sa.ID_COLUMN[sheet]))
        _, title, _ = self._record_lines(sheet, rec)

        def got_refs(refs):
            msg = f"„{rid}“" + (f" ({title})" if title else "") + " wirklich löschen?"
            if refs:
                shown = "\n".join(f"  • {r}" for r in refs[:8])
                more = f"\n  … und {len(refs) - 8} weitere" if len(refs) > 8 else ""
                msg += ("\n\n⚠️ Wird noch verwendet von:\n" + shown + more +
                        "\n\nTrotzdem löschen?")
            if not messagebox.askyesno("Löschen", msg):
                return
            self._run_async(
                lambda: self._get_admin().delete_record(sheet, rid),
                lambda _: self._after_write(sheet, f"🗑 {rid} gelöscht."))

        self.status.configure(text=f"Prüfe Verweise auf {rid} …")
        self._run_async(lambda: self._get_admin().references_to(sheet, rid),
                        got_refs)

    # -- Assistenten -----------------------------------------------------------

    def _open_wizard(self, sheet: str, rec: dict | None) -> None:
        self.status.configure(text="Bereit.")
        if sheet == sa.SHEET_JOBS:
            self._open_job_wizard(rec)
        elif sheet == sa.SHEET_ITEMS:
            self._open_item_wizard(rec)
        elif sheet == sa.SHEET_STYLES:
            self._open_style_wizard(rec)
        else:
            self._open_template_wizard(rec)

    @staticmethod
    def _strip_id(display: str) -> str:
        return display.split(" — ")[0].strip()

    def _open_job_wizard(self, rec: dict | None) -> None:
        jobs_tbl = self.tables[sa.SHEET_JOBS]
        styles_tbl = self.tables[sa.SHEET_STYLES]
        tpl_tbl = self.tables[sa.SHEET_TEMPLATES]

        style_opts = [f"{_clean(r.get('style_preset_id'))} — {_clean(r.get('preset_name'))}"
                      for r in styles_tbl.records if _clean(r.get("style_preset_id"))]

        def tpl_opts(dlg):
            jt = _clean(dlg.values.get("job_type"))
            rows = [r for r in tpl_tbl.records if _clean(r.get("prompt_template_id"))]
            match = [r for r in rows
                     if not _clean(r.get("job_type")) or _clean(r.get("job_type")) == jt]
            return [f"{_clean(r.get('prompt_template_id'))} — {_clean(r.get('template_name'))}"
                    for r in (match or rows)]

        def tpl_default(col, fallback):
            def default(dlg):
                tpl = tpl_tbl.by_id(_clean(dlg.values.get("prompt_template_id")))
                return _clean((tpl or {}).get(col)) or fallback
            return default

        def folder_default(dlg):
            name = _clean(dlg.values.get("job_name")).lower()
            for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
                name = name.replace(a, b)
            slug = re.sub(r"[^a-z0-9]+", "_", name).strip("_") or "job"
            return f"outputs/{slug}"

        steps = [
            {"title": "Grundlagen", "fields": [
                {"key": "job_type", "label": "Job-Typ", "type": "option",
                 "values": JOB_TYPES, "default": "batch_theme",
                 "help": "batch_theme: das Skript plant Motive zum Thema selbst · "
                         "content_linked: genau ein Bild pro Content Item (z. B. pro Witz)"},
                {"key": "job_name", "label": "Job-Name", "type": "entry",
                 "required": True,
                 "help": "Kurzer, sprechender Name — z. B. „Food Content Demo“"},
                {"key": "asset_goal", "label": "Ziel / Beschreibung", "type": "text",
                 "height": 70, "required": True,
                 "help": "Was soll entstehen? Z. B. „80 hochwertige Food-Bilder für Social/Web“"},
                {"key": "source_collection", "label": "Quelle (source_collection)",
                 "type": "entry", "default": "manual_briefing",
                 "help": "Frei wählbarer Sammlungsname, z. B. manual_briefing oder jokes_batch_01"},
            ]},
            {"title": "Stil & Vorlage", "fields": [
                {"key": "style_preset_id", "label": "Style Preset", "type": "option",
                 "values": style_opts, "required": True, "transform": self._strip_id,
                 "help": "Wiederverwendbare Stilregeln — verwalten im Tab „Styles“"},
                {"key": "prompt_template_id", "label": "Prompt Template",
                 "type": "option", "values": tpl_opts, "required": True,
                 "transform": self._strip_id,
                 "help": "Vorlage für den finalen Prompt — passend zum Job-Typ vorgefiltert"},
            ]},
            {"title": "Bildparameter", "fields": [
                {"key": "aspect_ratio", "label": "Seitenverhältnis", "type": "option",
                 "values": ASPECT_RATIOS,
                 "default": tpl_default("default_aspect_ratio", "1:1")},
                {"key": "image_size", "label": "Bildgröße", "type": "option",
                 "values": IMAGE_SIZES,
                 "default": tpl_default("default_image_size", "1K"),
                 "help": "1K nutzt das schnelle, günstige Lite-Bildmodell. "
                         "Bei 2K/4K wechselt das Skript automatisch auf das große Modell."},
                {"key": "qc_enabled", "label": "Qualitätskontrolle", "type": "option",
                 "values": ["ja", "nein"], "default": "ja",
                 "transform": lambda v: v.lower(),
                 "help": "ja: KI bewertet jede Variante und legt das beste Bild in "
                         "selected/ ab. nein: spart Zeit & Tokens — alle Bilder "
                         "landen unbewertet im Ordner images/."},
                {"key": "target_count", "label": "Zielanzahl Bilder", "type": "int",
                 "help": "Bei content_linked leer lassen — dann zählt die Anzahl der Inhalte"},
                {"key": "variants_per_item", "label": "Varianten pro Motiv",
                 "type": "int", "default": "2",
                 "help": "Wie viele Kandidaten pro Motiv erzeugt werden. Ohne "
                         "Qualitätskontrolle reicht meist 1 (keine Auswahl nötig)."},
            ]},
            {"title": "Ausgabe & Status", "fields": [
                {"key": "output_folder", "label": "Ausgabeordner", "type": "folder",
                 "default": folder_default, "required": True,
                 "help": "Über 'Ordner wählen…' auswählen oder neu anlegen. Existiert "
                         "der Pfad beim Generieren nicht, wird er automatisch erstellt."},
                {"key": "status", "label": "Status", "type": "option",
                 "values": STATUS_VALUES, "default": "todo",
                 "help": "todo/redo wird vom Skript verarbeitet — done wird übersprungen"},
                {"key": "notes", "label": "Notizen", "type": "text", "height": 60},
            ]},
            {"title": "Inhalte",
             "skip": lambda dlg: (rec is not None
                                  or _clean(dlg.values.get("job_type")) != "content_linked"),
             "builder": self._build_items_step,
             "summary": lambda dlg: [("Neue Inhalte",
                                      str(len(dlg.extra.get("items", []))))]},
        ]

        def id_preview(dlg, _kuerzel=None):
            return sa.next_numeric_id(jobs_tbl.ids(), "JOB")

        StepDialog(self,
                   "Neuen Job anlegen" if rec is None
                   else f"Job bearbeiten — {_clean(rec.get('job_id'))}",
                   steps, on_save=self._save_job, initial=rec,
                   fixed_id=_clean((rec or {}).get("job_id")) or None,
                   id_preview=id_preview)

    def _build_items_step(self, dlg: StepDialog, parent) -> None:
        items = dlg.extra.setdefault("items", [])
        ctk.CTkLabel(parent, wraplength=560, justify="left", anchor="w",
                     text="Optional: Inhalte (z. B. Witze) direkt miterfassen — "
                          "je Inhalt entsteht eine Zeile in „Content Items“. "
                          "Weitere Inhalte lassen sich jederzeit im Tab "
                          "„Inhalte“ ergänzen.",
                     text_color=("gray35", "gray65"),
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=12, pady=(8, 2))

        form = ctk.CTkFrame(parent, fg_color="transparent")
        form.pack(fill="x", padx=12, pady=(4, 2))
        form.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(form, text="Titel:").grid(row=0, column=0, sticky="w",
                                               padx=(0, 8), pady=2)
        title_e = ctk.CTkEntry(form)
        title_e.grid(row=0, column=1, sticky="ew", pady=2)
        ctk.CTkLabel(form, text="Typ:").grid(row=1, column=0, sticky="w",
                                             padx=(0, 8), pady=2)
        type_c = ctk.CTkComboBox(form, values=CONTENT_TYPES, width=200)
        type_c.set("joke")
        type_c.grid(row=1, column=1, sticky="w", pady=2)
        ctk.CTkLabel(form, text="Text/Thema:").grid(row=2, column=0, sticky="nw",
                                                    padx=(0, 8), pady=2)
        text_t = ctk.CTkTextbox(form, height=60)
        text_t.grid(row=2, column=1, sticky="ew", pady=2)

        list_frame = ctk.CTkFrame(parent, fg_color="transparent")
        list_frame.pack(fill="x", padx=12, pady=(6, 8))

        def render_items():
            for child in list_frame.winfo_children():
                child.destroy()
            if not items:
                ctk.CTkLabel(list_frame, text="Noch keine Inhalte erfasst.",
                             text_color=("gray35", "gray65"),
                             font=ctk.CTkFont(size=11)).pack(anchor="w")
                return
            for i, it in enumerate(items):
                row = ctk.CTkFrame(list_frame, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkLabel(row,
                             text=f"{i + 1}. {it['title']}  ({it['content_type']})"
                                  f"  —  {_shorten(it['source_text_or_topic'], 46)}",
                             anchor="w", font=ctk.CTkFont(size=11)).pack(
                    side="left", fill="x", expand=True)
                ctk.CTkButton(row, text="🗑", width=30, height=22,
                              font=ctk.CTkFont(size=11),
                              fg_color=RED, hover_color=RED_HOVER,
                              command=lambda i=i: (items.pop(i), render_items())
                              ).pack(side="right")

        def add_item():
            title = title_e.get().strip()
            text = text_t.get("1.0", "end").strip()
            if not title or not text:
                messagebox.showwarning(APP_TITLE,
                                       "Bitte Titel und Text/Thema ausfüllen.",
                                       parent=dlg)
                return
            items.append({"title": title,
                          "content_type": type_c.get().strip() or "joke",
                          "source_text_or_topic": text, "notes": ""})
            title_e.delete(0, "end")
            text_t.delete("1.0", "end")
            render_items()

        ctk.CTkButton(form, text="➕  Inhalt hinzufügen", width=170,
                      fg_color=WINE, hover_color=WINE_HOVER,
                      command=add_item).grid(row=3, column=1, sticky="w",
                                             pady=(6, 2))
        render_items()

    def _save_job(self, dlg: StepDialog) -> None:
        values = {k: v for k, v in dlg.values.items() if not k.startswith("_")}
        items = list(dlg.extra.get("items", []))

        def work():
            admin = self._get_admin()
            if dlg.fixed_id:
                admin.update_record(sa.SHEET_JOBS, dlg.fixed_id, values)
                return dlg.fixed_id
            table = admin.load(sa.SHEET_JOBS)
            rid = sa.next_numeric_id(table.ids(), "JOB")
            admin.append_record(sa.SHEET_JOBS, {**values, "job_id": rid},
                                table=table)
            if items:
                items_tbl = admin.load(sa.SHEET_ITEMS)
                known = items_tbl.ids()
                for it in items:
                    iid = sa.next_numeric_id(known, "ITEM")
                    known.append(iid)
                    admin.append_record(sa.SHEET_ITEMS,
                                        {**it, "item_id": iid, "job_id": rid})
            return rid

        def done(rid):
            dlg.destroy()
            self._after_write(sa.SHEET_JOBS, f"💾 Job {rid} gespeichert.")
            if items:
                self._load_table(sa.SHEET_ITEMS, force=True)

        self._run_async(work, done, on_error=lambda e: self._save_failed(dlg, e))

    def _save_failed(self, dlg: StepDialog, e: Exception) -> None:
        dlg.fail_save()
        messagebox.showerror(APP_TITLE, f"Speichern fehlgeschlagen:\n{e}",
                             parent=dlg)

    def _open_style_wizard(self, rec: dict | None) -> None:
        styles_tbl = self.tables[sa.SHEET_STYLES]

        def id_preview(dlg, kuerzel=None):
            base = kuerzel if kuerzel is not None else dlg.values.get("_kuerzel", "")
            base = _clean(base) or _clean(dlg.values.get("preset_name")) or "NEU"
            return sa.next_slug_id(styles_tbl.ids(), "STYLE", base)

        steps = [
            {"title": "Grundlagen", "fields": [
                {"key": "preset_name", "label": "Name", "type": "entry",
                 "required": True, "help": "Z. B. „Premium Food Photography“"},
                {"key": "_kuerzel", "label": "ID-Kürzel", "type": "kuerzel",
                 "create_only": True,
                 "help": "Wird Teil der ID: STYLE-<KÜRZEL>-NNN (z. B. FOOD → STYLE-FOOD-001)"},
                {"key": "use_case", "label": "Einsatzzweck", "type": "entry",
                 "help": "Wofür ist der Stil gedacht? Z. B. „Food-Batch / Social Media“"},
                {"key": "maturity_level", "label": "Tonalität / Zielgruppe",
                 "type": "combo", "values": MATURITY_SUGGESTIONS},
            ]},
            {"title": "Look", "fields": [
                {"key": "visual_style", "label": "Visueller Stil", "type": "text",
                 "height": 70, "required": True,
                 "help": "Z. B. „fotorealistische hochwertige Food-Fotografie, weiches Tageslicht“"},
                {"key": "color_palette", "label": "Farbpalette", "type": "text",
                 "height": 50},
                {"key": "composition_rules", "label": "Komposition", "type": "text",
                 "height": 60},
                {"key": "ui_safe_area", "label": "UI-Schutzzonen", "type": "text",
                 "height": 50,
                 "help": "Welche Bildbereiche müssen ruhig/frei bleiben (für Overlays und UI)?"},
            ]},
            {"title": "Prompts", "fields": [
                {"key": "positive_style_prompt",
                 "label": "Positiver Stil-Prompt (EN)", "type": "text",
                 "height": 90, "required": True},
                {"key": "negative_style_prompt", "label": "Negativ-Prompt (EN)",
                 "type": "text", "height": 70,
                 "default": "No text, no logos, no watermarks, no brand names."},
            ]},
            {"title": "Referenzen & Notizen", "fields": [
                {"key": "reference_images", "label": "Referenzbilder",
                 "type": "files",
                 "help": "Pfade relativ zum Projekt, mit „;“ getrennt — z. B. references/anchor_01.png"},
                {"key": "notes", "label": "Notizen", "type": "text", "height": 60},
            ]},
        ]

        StepDialog(self,
                   "Neues Style Preset anlegen" if rec is None
                   else f"Style Preset bearbeiten — {_clean(rec.get('style_preset_id'))}",
                   steps,
                   on_save=lambda dlg: self._save_simple(
                       dlg, sa.SHEET_STYLES,
                       lambda tbl, v: sa.next_slug_id(
                           tbl.ids(), "STYLE",
                           _clean(v.get("_kuerzel")) or _clean(v.get("preset_name")) or "NEU")),
                   initial=rec,
                   fixed_id=_clean((rec or {}).get("style_preset_id")) or None,
                   id_preview=id_preview)

    def _open_template_wizard(self, rec: dict | None) -> None:
        tpl_tbl = self.tables[sa.SHEET_TEMPLATES]

        def id_preview(dlg, kuerzel=None):
            base = kuerzel if kuerzel is not None else dlg.values.get("_kuerzel", "")
            base = _clean(base) or _clean(dlg.values.get("template_name")) or "NEU"
            return sa.next_slug_id(tpl_tbl.ids(), "TPL", base)

        steps = [
            {"title": "Grundlagen", "fields": [
                {"key": "template_name", "label": "Name", "type": "entry",
                 "required": True, "help": "Z. B. „Theme Batch Image“"},
                {"key": "_kuerzel", "label": "ID-Kürzel", "type": "kuerzel",
                 "create_only": True,
                 "help": "Wird Teil der ID: TPL-<KÜRZEL>-NNN (z. B. JOKE-BG → TPL-JOKE-BG-001)"},
                {"key": "job_type", "label": "Job-Typ", "type": "option",
                 "values": JOB_TYPES, "default": "batch_theme",
                 "help": "Für welchen Job-Typ ist die Vorlage gedacht?"},
                {"key": "model", "label": "Modell", "type": "combo",
                 "values": [DEFAULT_MODEL], "default": DEFAULT_MODEL},
                {"key": "template_purpose", "label": "Zweck", "type": "text",
                 "height": 50},
            ]},
            {"title": "Standardwerte", "fields": [
                {"key": "default_aspect_ratio", "label": "Standard-Seitenverhältnis",
                 "type": "option", "values": ASPECT_RATIOS, "default": "1:1",
                 "help": "Wird im Job-Assistenten als Vorschlag übernommen"},
                {"key": "default_image_size", "label": "Standard-Bildgröße",
                 "type": "option", "values": IMAGE_SIZES, "default": "1K",
                 "help": "1K nutzt das schnelle Lite-Bildmodell; 2K/4K das große Modell"},
            ]},
            {"title": "Prompt", "fields": [
                {"key": "prompt_template", "label": "Prompt-Vorlage (EN)",
                 "type": "text", "height": 150, "required": True,
                 "placeholders": True,
                 "help": "Platzhalter in {geschweiften Klammern} ersetzt das Skript "
                         "beim Generieren durch Job-, Inhalt- und Stil-Daten."},
                {"key": "negative_rules", "label": "Negativ-Regeln", "type": "text",
                 "height": 60, "default": "{negative_style_prompt}",
                 "help": "{negative_style_prompt} übernimmt den Negativ-Prompt des Style Presets"},
            ]},
            {"title": "Hinweise", "fields": [
                {"key": "output_notes", "label": "Output-Notizen", "type": "text",
                 "height": 60,
                 "help": "Freitext-Hinweise zur Verwendung der Ergebnisse"},
            ]},
        ]

        StepDialog(self,
                   "Neues Prompt Template anlegen" if rec is None
                   else f"Prompt Template bearbeiten — {_clean(rec.get('prompt_template_id'))}",
                   steps,
                   on_save=lambda dlg: self._save_simple(
                       dlg, sa.SHEET_TEMPLATES,
                       lambda tbl, v: sa.next_slug_id(
                           tbl.ids(), "TPL",
                           _clean(v.get("_kuerzel")) or _clean(v.get("template_name")) or "NEU")),
                   initial=rec,
                   fixed_id=_clean((rec or {}).get("prompt_template_id")) or None,
                   id_preview=id_preview)

    def _open_item_wizard(self, rec: dict | None) -> None:
        items_tbl = self.tables[sa.SHEET_ITEMS]
        jobs_tbl = self.tables[sa.SHEET_JOBS]
        job_opts = [f"{_clean(r.get('job_id'))} — {_clean(r.get('job_name'))}"
                    for r in jobs_tbl.records if _clean(r.get("job_id"))]

        steps = [
            {"title": "Inhalt", "fields": [
                {"key": "job_id", "label": "Gehört zu Job", "type": "option",
                 "values": job_opts, "required": True, "transform": self._strip_id,
                 "help": "Der Job, für den dieser Inhalt ein Bild bekommen soll"},
                {"key": "content_type", "label": "Inhaltstyp", "type": "combo",
                 "values": CONTENT_TYPES, "default": "joke"},
                {"key": "title", "label": "Titel", "type": "entry",
                 "required": True, "help": "Kurzer Name, z. B. „Geister-Witz“"},
                {"key": "source_text_or_topic", "label": "Text / Thema",
                 "type": "text", "height": 100, "required": True,
                 "help": "Der eigentliche Inhalt — z. B. der Witztext oder das Motiv-Thema"},
                {"key": "notes", "label": "Notizen", "type": "text", "height": 50},
            ]},
        ]

        def id_preview(dlg, _kuerzel=None):
            return sa.next_numeric_id(items_tbl.ids(), "ITEM")

        StepDialog(self,
                   "Neuen Inhalt anlegen" if rec is None
                   else f"Inhalt bearbeiten — {_clean(rec.get('item_id'))}",
                   steps,
                   on_save=lambda dlg: self._save_simple(
                       dlg, sa.SHEET_ITEMS,
                       lambda tbl, v: sa.next_numeric_id(tbl.ids(), "ITEM")),
                   initial=rec,
                   fixed_id=_clean((rec or {}).get("item_id")) or None,
                   id_preview=id_preview)

    def _save_simple(self, dlg: StepDialog, sheet: str, make_id) -> None:
        record = {k: v for k, v in dlg.values.items() if not k.startswith("_")}

        def work():
            admin = self._get_admin()
            if dlg.fixed_id:
                admin.update_record(sheet, dlg.fixed_id, record)
                return dlg.fixed_id
            table = admin.load(sheet)
            rid = make_id(table, dlg.values)
            admin.append_record(sheet, {**record, sa.ID_COLUMN[sheet]: rid},
                                table=table)
            return rid

        def done(rid):
            dlg.destroy()
            self._after_write(sheet, f"💾 {rid} gespeichert.")

        self._run_async(work, done, on_error=lambda e: self._save_failed(dlg, e))

    # -- QoL (Scrolling, resizable Log) ----------------------------------------

    def _setup_qol(self) -> None:
        """Inner-first Mausrad-Scrolling für das Log (wie Converter/TTS)."""
        inner = getattr(self.log_box, "_textbox", None)
        if inner is not None:
            inner.bind("<MouseWheel>", lambda e: self._inner_wheel(e, inner))
            inner.bind("<Button-4>", lambda e: self._inner_wheel(e, inner))
            inner.bind("<Button-5>", lambda e: self._inner_wheel(e, inner))

    def _wheel_units(self, event):
        n = getattr(event, "num", None)
        if n == 4:
            return -1
        if n == 5:
            return 1
        d = getattr(event, "delta", 0)
        if d == 0:
            return 0
        if abs(d) >= 120:      # Windows: Vielfache von 120
            return -int(d / 120)
        return -1 if d > 0 else 1   # macOS: kleine Integer-Deltas

    def _outer_scroll(self, units):
        cv = getattr(self.scroll, "_parent_canvas", None)
        if cv is not None and units:
            cv.yview_scroll(units, "units")

    def _inner_wheel(self, event, target):
        units = self._wheel_units(event)
        if not units:
            return "break"
        try:
            top, bottom = target.yview()
        except Exception:
            self._outer_scroll(units)
            return "break"
        # inneres Widget scrollen bis zum Rand, dann an das Fenster übergeben
        if units < 0 and top <= 0.0001:
            self._outer_scroll(units)
        elif units > 0 and bottom >= 0.9999:
            self._outer_scroll(units)
        else:
            target.yview_scroll(units, "units")
        return "break"

    # -- Resizable panels --------------------------------------------------

    def _make_grip(self, parent, key, container, min_h):
        grip = ctk.CTkFrame(parent, height=8, corner_radius=4,
                            fg_color=("gray70", "gray35"),
                            cursor="sb_v_double_arrow")
        grip.bind("<Button-1>", lambda e: self._grip_press(e, key))
        grip.bind("<B1-Motion>", lambda e: self._grip_drag(e, container, key, min_h))
        return grip

    def _grip_press(self, event, key):
        self._drag_y0 = event.y_root
        self._drag_h0 = self._heights[key]

    def _grip_drag(self, event, container, key, min_h):
        new_h = max(min_h, self._drag_h0 + (event.y_root - self._drag_y0))
        self._heights[key] = new_h
        container.configure(height=new_h)
        self._update_reset_visibility()

    def _update_reset_visibility(self):
        bigger = any(self._heights[k] > self._default_heights[k] + 2
                     for k in self._heights)
        if bigger:
            self.reset_btn.place(relx=0.0, y=10, x=16, anchor="nw")
            self.reset_btn.lift()
        else:
            self.reset_btn.place_forget()

    def reset_layout(self):
        for key, container in self._containers.items():
            self._heights[key] = self._default_heights[key]
            container.configure(height=self._heights[key])
        self._update_reset_visibility()
        try:
            self.scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    # -- Aktionen (Generierung) ----------------------------------------------

    def start_generation(self) -> None:
        if self.process is not None:
            messagebox.showinfo(APP_TITLE, "Es läuft bereits ein Generierungsprozess.")
            return

        missing = self._get_missing_requirements()
        if missing:
            messagebox.showerror(
                APP_TITLE,
                "Folgende Dinge fehlen noch:\n\n" + "\n".join(f"• {m}" for m in missing))
            self._refresh_status()
            return

        cmd = (
            [str(self.venv_python), "--run-pipeline"]
            if self.frozen
            else [str(self.venv_python), str(self.generator_script)]
        )

        self._append_log("\n" + "=" * 60)
        self._append_log("Starte Generierungsprozess …")
        self._append_log(f"Projekt: {self.project_root}")
        self._append_log(f"Befehl: {' '.join(cmd)}")
        self._append_log("=" * 60 + "\n")

        self.running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.configure(text="Generierung läuft …")
        self.progress.configure(mode="indeterminate")
        self.progress.start()

        threading.Thread(target=self._run_process, args=(cmd,), daemon=True).start()

    def stop_generation(self) -> None:
        if self.process is None:
            return

        self._append_log("\nStoppe Generierungsprozess …")
        self.process.terminate()

        def kill_later() -> None:
            time.sleep(3)
            if self.process is not None and self.process.poll() is None:
                self.log_queue.put("Prozess reagiert nicht. Erzwinge Abbruch.")
                self.process.kill()

        threading.Thread(target=kill_later, daemon=True).start()

    def open_google_sheet(self) -> None:
        env = self._read_env()
        sheet_id = env.get("GOOGLE_SHEET_ID", "").strip()
        if not sheet_id:
            messagebox.showerror(APP_TITLE, "GOOGLE_SHEET_ID fehlt in deiner .env.")
            return
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        subprocess.run(["open", url], check=False)

    def open_path(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(path)], check=False)

    def clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _git_env(self) -> dict:
        # git nicht auf eine Passwort-Eingabe warten lassen, wenn kein Terminal da ist.
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def check_update(self) -> None:
        """Sucht auf GitHub nach einer neueren Version. Ist eine verfügbar,
        wird gefragt, ob geladen und die App neu gestartet werden soll
        (wie in „Folder Converter"/„TTS Studio")."""
        if self.running:
            messagebox.showinfo("Update", "Bitte warten, bis der aktuelle Lauf beendet ist.")
            return
        repo = str(self.project_root)
        if not os.path.isdir(os.path.join(repo, ".git")):
            messagebox.showinfo(
                "Update suchen",
                "Diese Version ist noch nicht mit Git verbunden.\n\n"
                "Führe einmal den Installer aus (install.sh bzw. install.bat) — "
                "danach funktioniert die Update-Suche.")
            return
        self.update_btn.configure(state="disabled", text="Suche …")
        self.status.configure(text="Suche nach Updates …")
        self._run_async(lambda: self._fetch_behind(repo),
                        self._update_check_done, self._update_check_error)

    def _fetch_behind(self, repo: str) -> int:
        """Holt den Remote-Stand und liefert die Anzahl Commits, die lokal
        hinter GitHub liegen. Wirft bei Git-Fehlern."""
        fetch = subprocess.run(["git", "-C", repo, "fetch", "--quiet"],
                               capture_output=True, text=True,
                               env=self._git_env(), timeout=60)
        if fetch.returncode != 0:
            raise RuntimeError((fetch.stderr or fetch.stdout or "git fetch fehlgeschlagen").strip())
        count = subprocess.run(
            ["git", "-C", repo, "rev-list", "--count", "HEAD..@{u}"],
            capture_output=True, text=True, timeout=30)
        if count.returncode != 0:
            raise RuntimeError((count.stderr or count.stdout or "Vergleich fehlgeschlagen").strip())
        return int((count.stdout or "0").strip() or "0")

    def _update_check_error(self, e: Exception) -> None:
        self.update_btn.configure(state="normal", text="Update suchen")
        self.status.configure(text="Bereit.")
        messagebox.showwarning("Update suchen",
                               f"Konnte nicht nach Updates suchen.\n\n{e}")

    def _update_check_done(self, behind: int) -> None:
        self.update_btn.configure(state="normal", text="Update suchen")
        self.status.configure(text="Bereit.")
        if behind <= 0:
            messagebox.showinfo("Update suchen", "Die App ist auf dem neuesten Stand.")
            return
        word = "Änderung" if behind == 1 else "Änderungen"
        if messagebox.askyesno(
                "Update verfügbar",
                f"Eine neuere Version ist verfügbar ({behind} {word}).\n\n"
                "Jetzt herunterladen und die App neu starten?"):
            self._pull_and_restart(str(self.project_root))

    def _pull_and_restart(self, repo: str) -> None:
        self.status.configure(text="Aktualisiere …")
        try:
            r = subprocess.run(["git", "-C", repo, "pull", "--ff-only"],
                               capture_output=True, text=True,
                               env=self._git_env(), timeout=120)
        except Exception as e:
            self.status.configure(text="Bereit.")
            messagebox.showwarning("Update", f"Update fehlgeschlagen:\n{e}")
            return
        out = (r.stdout + "\n" + r.stderr).strip()
        self._append_log(f"\n⬇ git pull:\n{out}\n")
        if r.returncode != 0:
            self.status.configure(text="Bereit.")
            messagebox.showwarning("Update", f"Update fehlgeschlagen:\n\n{out[-400:]}")
            return
        self.status.configure(text="Neustart …")
        self.after(400, self._restart_app)

    def _restart_app(self) -> None:
        """Startet die laufende UI neu, damit die aktualisierte Version greift."""
        try:
            if self.frozen:
                os.execv(sys.executable, [sys.executable])
            else:
                script = os.path.abspath(__file__)
                os.execv(sys.executable, [sys.executable, script])
        except Exception as e:
            messagebox.showinfo(
                "Update",
                "Update geladen. Bitte die App einmal schließen und neu starten.\n\n"
                f"(Automatischer Neustart nicht möglich: {e})")

    # -- Prozess -----------------------------------------------------------

    def _run_process(self, cmd: list[str]) -> None:
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )

            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.log_queue.put(line.rstrip("\n"))

            return_code = self.process.wait()
            if return_code == 0:
                self.log_queue.put("\n✔ Prozess beendet.")
            else:
                self.log_queue.put(f"\n❌ Prozess mit Fehlercode {return_code} beendet.")

        except Exception as exc:
            self.log_queue.put(f"\nUI-Fehler beim Starten des Prozesses: {exc}")

        finally:
            self.process = None
            self.log_queue.put("__PROCESS_FINISHED__")

    def _poll_log_queue(self) -> None:
        self._drain_ui_queue()
        try:
            while True:
                line = self.log_queue.get_nowait()
                if line == "__PROCESS_FINISHED__":
                    self._run_finished()
                else:
                    self._append_log(line)
        except queue.Empty:
            pass
        self.after(150, self._poll_log_queue)

    def _run_finished(self) -> None:
        self.running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1.0)
        self._refresh_status()
        # Nach einem Lauf können sich Status/Queue geändert haben → Jobs neu laden
        if sa is not None and sa.SHEET_JOBS in self.tables:
            self._load_table(sa.SHEET_JOBS, force=True)

    def _append_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # -- Status / .env -----------------------------------------------------

    def _read_env(self) -> dict[str, str]:
        if dotenv_values is not None and self.env_path.exists():
            return {k: str(v or "") for k, v in dotenv_values(self.env_path).items()}

        values: dict[str, str] = {}
        if not self.env_path.exists():
            return values
        for line in self.env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values

    def _get_missing_requirements(self) -> list[str]:
        missing: list[str] = []
        if not self.env_path.exists():
            missing.append(".env im Projektordner")
        if not self.generator_script.exists():
            missing.append("scripts/generate_images.py")
        if not self.venv_python.exists():
            missing.append(".venv/bin/python — bitte vorher venv erstellen und requirements installieren")

        env = self._read_env()
        for key in ["GEMINI_API_KEY", "GOOGLE_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_FILE"]:
            if not env.get(key, "").strip():
                missing.append(f"{key} in .env")

        service_account = env.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        if service_account:
            service_path = Path(service_account).expanduser()
            if not service_path.is_absolute():
                service_path = self.project_root / service_path
            if not service_path.exists():
                missing.append(f"Service-Account-Datei nicht gefunden: {service_path}")

        return missing

    def _refresh_status(self) -> None:
        env = self._read_env()
        dry_run = env.get("DRY_RUN", "").strip().lower()
        dry_run_text = "aktiv" if dry_run in {"1", "true", "yes"} else "aus"

        missing = self._get_missing_requirements()
        if missing:
            self.status.configure(
                text=f"Nicht bereit — {len(missing)} Problem(e). DRY_RUN: {dry_run_text}")
        else:
            self.status.configure(text=f"Bereit. DRY_RUN: {dry_run_text}")


if __name__ == "__main__":
    ImageGeneratorApp().mainloop()
