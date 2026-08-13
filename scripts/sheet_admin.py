"""
sheet_admin — schlanke Lese-/Schreibschicht für das Steuer-Google-Sheet.

Wird von der GUI (image_generator_ui.py) benutzt, um Jobs, Content Items,
Style Presets und Prompt Templates direkt in der App anzulegen, zu bearbeiten
und zu löschen. Das Google Sheet bleibt der Speicherort; diese Schicht kapselt
nur die gspread-Zugriffe.

Bewusst OHNE Import von generate_images.py gehalten: das würde die komplette
Pipeline-Abhängigkeitskette (google-genai, PIL, pandas, …) in den UI-Prozess
ziehen. Die Header-Erkennung ist deshalb hier minimal nachgebaut — kompatibel
zum Leser der Pipeline (Titelzeile in Zeile 1, Header typischerweise Zeile 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_JOBS = "01_Jobs_Batches"
SHEET_ITEMS = "02_Content_Items"
SHEET_STYLES = "03_Style_Presets"
SHEET_TEMPLATES = "04_Prompt_Templates"

# Kanonische Spaltenreihenfolge (Stand des aktuellen Sheets). Dient als
# Fallback, falls ein Worksheet noch keinen Header hat; geschrieben wird sonst
# immer anhand des tatsächlich vorgefundenen Headers.
CANONICAL_COLUMNS: Dict[str, List[str]] = {
    SHEET_JOBS: [
        "job_id", "status", "job_type", "job_name", "asset_goal",
        "source_collection", "style_preset_id", "prompt_template_id",
        "aspect_ratio", "image_size", "target_count", "variants_per_item",
        "output_folder", "notes", "qc_enabled", "restore_source_folder",
        "restore_prompt", "restore_model", "restore_transparency_background",
    ],
    SHEET_ITEMS: [
        "item_id", "job_id", "content_type", "title",
        "source_text_or_topic", "notes",
    ],
    SHEET_STYLES: [
        "style_preset_id", "preset_name", "use_case", "maturity_level",
        "visual_style", "color_palette", "composition_rules", "ui_safe_area",
        "positive_style_prompt", "negative_style_prompt",
        "reference_images", "notes",
    ],
    SHEET_TEMPLATES: [
        "prompt_template_id", "template_name", "job_type", "model",
        "default_aspect_ratio", "default_image_size", "template_purpose",
        "prompt_template", "negative_rules", "output_notes",
    ],
}

ID_COLUMN: Dict[str, str] = {
    SHEET_JOBS: "job_id",
    SHEET_ITEMS: "item_id",
    SHEET_STYLES: "style_preset_id",
    SHEET_TEMPLATES: "prompt_template_id",
}

# Erwartete Schlüsselspalten je Sheet — für die Header-Zeilen-Erkennung
# (identisch zur Pipeline, siehe generate_images.detect_header_row).
_EXPECTED_HEADERS: Dict[str, set] = {
    SHEET_JOBS: {"job_id", "status", "job_type"},
    SHEET_ITEMS: {"item_id", "job_id", "content_type"},
    SHEET_STYLES: {"style_preset_id"},
    SHEET_TEMPLATES: {"prompt_template_id", "template_name"},
}


def normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def slugify_kuerzel(text: str) -> str:
    """Macht aus freiem Text ein ID-Kürzel: Großbuchstaben, A-Z/0-9,
    Wortgrenzen als Bindestrich (z. B. 'Joke BG' -> 'JOKE-BG')."""
    text = clean_string(text).upper()
    # Umlaute grob transliterieren, dann alles Nicht-Alphanumerische bündeln
    for a, b in (("Ä", "AE"), ("Ö", "OE"), ("Ü", "UE"), ("ß", "SS")):
        text = text.replace(a, b)
    text = re.sub(r"[^A-Z0-9]+", "-", text).strip("-")
    return text


def next_numeric_id(existing_ids: List[str], prefix: str, width: int = 4) -> str:
    """JOB-0001 / ITEM-0007 — nächste freie laufende Nummer über alle
    Einträge, die exakt dem Muster PREFIX-<Zahl> folgen."""
    highest = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for eid in existing_ids:
        m = pattern.match(clean_string(eid))
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}-{highest + 1:0{width}d}"


def next_slug_id(existing_ids: List[str], prefix: str, kuerzel: str,
                 width: int = 3) -> str:
    """STYLE-<KÜRZEL>-001 / TPL-<KÜRZEL>-001 — nächste freie Nummer für das
    gegebene Kürzel (analog zu STYLE-JOKES-001, TPL-JOKE-BG-001)."""
    slug = slugify_kuerzel(kuerzel) or "NEU"
    highest = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-{re.escape(slug)}-(\d+)$")
    for eid in existing_ids:
        m = pattern.match(clean_string(eid))
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}-{slug}-{highest + 1:0{width}d}"


@dataclass
class SheetTable:
    """Ein gelesenes Worksheet: Header + Datensätze inkl. Zeilennummern."""
    sheet_name: str
    header_row: int                       # 1-basierte Zeilennummer des Headers
    headers: List[str]                    # normalisierte, nicht-leere Header
    col_index: Dict[str, int]             # header -> 1-basierte Spaltennummer
    records: List[Dict[str, str]] = field(default_factory=list)
    # jeder Record enthält zusätzlich "_row": 1-basierte Zeilennummer im Sheet

    def ids(self) -> List[str]:
        id_col = ID_COLUMN[self.sheet_name]
        return [clean_string(r.get(id_col)) for r in self.records
                if clean_string(r.get(id_col))]

    def by_id(self, record_id: str) -> Optional[Dict[str, str]]:
        id_col = ID_COLUMN[self.sheet_name]
        for r in self.records:
            if clean_string(r.get(id_col)) == clean_string(record_id):
                return r
        return None


def _detect_header_row(values: List[List[str]], sheet_name: str) -> int:
    """0-basierter Index der Header-Zeile (kompatibel zur Pipeline-Logik)."""
    expected = _EXPECTED_HEADERS.get(sheet_name, set())
    best_idx, best_score = 0, -1
    for idx, row in enumerate(values[:10]):
        headers = {normalize_key(c) for c in row if normalize_key(c)}
        if not headers:
            continue
        score = len(headers) + len(headers & expected) * 100
        if score > best_score:
            best_score, best_idx = score, idx
        if expected and expected.issubset(headers):
            return idx
    return best_idx


class SheetAdmin:
    """Kapselt alle Lese-/Schreiboperationen auf dem Steuer-Sheet."""

    def __init__(self, sheet_id: str, service_account_file: str) -> None:
        self.sheet_id = sheet_id
        self.service_account_file = service_account_file
        self._workbook: Optional[gspread.Spreadsheet] = None

    # -- Verbindung ---------------------------------------------------------

    def _wb(self) -> gspread.Spreadsheet:
        if self._workbook is None:
            creds = Credentials.from_service_account_file(
                self.service_account_file, scopes=SCOPES)
            self._workbook = gspread.authorize(creds).open_by_key(self.sheet_id)
        return self._workbook

    def _ws(self, sheet_name: str) -> gspread.Worksheet:
        return self._wb().worksheet(sheet_name)

    # -- Lesen ---------------------------------------------------------------

    def load(self, sheet_name: str) -> SheetTable:
        ws = self._ws(sheet_name)
        values = ws.get_all_values()
        if not values:
            headers = list(CANONICAL_COLUMNS.get(sheet_name, []))
            return SheetTable(sheet_name=sheet_name, header_row=1,
                              headers=headers,
                              col_index={h: i + 1 for i, h in enumerate(headers)})

        header_idx = _detect_header_row(values, sheet_name)
        raw_headers = values[header_idx]

        headers: List[str] = []
        col_index: Dict[str, int] = {}
        for col, raw in enumerate(raw_headers, start=1):
            key = normalize_key(raw)
            if not key or key in col_index:
                continue  # leere/doppelte Header ignorieren (wie Pipeline)
            headers.append(key)
            col_index[key] = col

        records: List[Dict[str, str]] = []
        for row_no, raw_row in enumerate(values[header_idx + 1:],
                                         start=header_idx + 2):
            record: Dict[str, str] = {"_row": row_no}
            has_value = False
            for key in headers:
                col = col_index[key] - 1
                cell = raw_row[col] if col < len(raw_row) else ""
                if clean_string(cell):
                    has_value = True
                record[key] = cell
            if has_value:
                records.append(record)

        return SheetTable(sheet_name=sheet_name, header_row=header_idx + 1,
                          headers=headers, col_index=col_index, records=records)

    def load_all(self) -> Dict[str, SheetTable]:
        return {name: self.load(name) for name in
                (SHEET_JOBS, SHEET_ITEMS, SHEET_STYLES, SHEET_TEMPLATES)}

    # -- Schreiben -----------------------------------------------------------

    def ensure_columns(self, sheet_name: str, columns,
                       table: Optional[SheetTable] = None) -> SheetTable:
        """Ergaenzt neue kanonische Spalten, ohne bestehende Sheets umzubauen.

        Alte Steuer-Sheets besitzen die Restaurierungsfelder noch nicht. Beim
        ersten Speichern eines entsprechenden Jobs werden nur die benoetigten
        Header rechts angehaengt; vorhandene Daten und Spalten bleiben an Ort
        und Stelle.
        """
        if table is None:
            table = self.load(sheet_name)
        allowed = set(CANONICAL_COLUMNS.get(sheet_name, []))
        missing = [clean_string(c) for c in columns
                   if clean_string(c) in allowed
                   and clean_string(c) not in table.col_index]
        if not missing:
            return table

        ws = self._ws(sheet_name)
        next_col = max(table.col_index.values(), default=0) + 1
        payload = []
        for offset, key in enumerate(missing):
            payload.append({
                "range": rowcol_to_a1(table.header_row, next_col + offset),
                "values": [[key]],
            })
        ws.batch_update(payload, value_input_option="RAW")
        return self.load(sheet_name)

    def append_record(self, sheet_name: str, record: Dict[str, str],
                      table: Optional[SheetTable] = None) -> None:
        """Hängt einen Datensatz als neue Zeile unter der Tabelle an.
        value_input_option=RAW, damit z. B. '9:16' nicht als Uhrzeit
        interpretiert wird. `table` kann übergeben werden, wenn das Sheet
        unmittelbar zuvor bereits frisch geladen wurde (spart einen Request)."""
        if table is None:
            table = self.load(sheet_name)   # frisch: Zeilennummern/IDs aktuell
        table = self.ensure_columns(sheet_name, record.keys(), table=table)
        ws = self._ws(sheet_name)
        row_values = [clean_string(record.get(h, "")) for h in table.headers]
        last_row = (table.records[-1]["_row"] if table.records
                    else table.header_row)
        ws.insert_row(row_values, index=last_row + 1,
                      value_input_option="RAW")

    def update_record(self, sheet_name: str, record_id: str,
                      record: Dict[str, str]) -> None:
        """Aktualisiert die Zeile mit der gegebenen ID (nur bekannte Spalten,
        eine Batch-Anfrage)."""
        table = self.load(sheet_name)
        table = self.ensure_columns(sheet_name, record.keys(), table=table)
        existing = table.by_id(record_id)
        if existing is None:
            raise KeyError(f"{record_id} nicht in {sheet_name} gefunden")
        row = existing["_row"]
        payload = []
        for key, value in record.items():
            col = table.col_index.get(key)
            if col is None:
                continue
            payload.append({
                "range": rowcol_to_a1(row, col),
                "values": [[clean_string(value)]],
            })
        if payload:
            self._ws(sheet_name).batch_update(payload,
                                              value_input_option="RAW")

    def delete_record(self, sheet_name: str, record_id: str) -> None:
        table = self.load(sheet_name)
        existing = table.by_id(record_id)
        if existing is None:
            raise KeyError(f"{record_id} nicht in {sheet_name} gefunden")
        self._ws(sheet_name).delete_rows(existing["_row"])

    # -- Referenz-Checks (Löschschutz) ----------------------------------------

    def references_to(self, sheet_name: str, record_id: str) -> List[str]:
        """Liefert menschenlesbare Verweise auf den Datensatz, z. B.
        ['JOB-0001 (Witze App)'] — leer, wenn nichts darauf zeigt."""
        record_id = clean_string(record_id)
        refs: List[str] = []

        def scan(src_sheet: str, ref_col: str, label_col: str) -> None:
            table = self.load(src_sheet)
            id_col = ID_COLUMN[src_sheet]
            for r in table.records:
                if clean_string(r.get(ref_col)) == record_id:
                    label = clean_string(r.get(label_col))
                    rid = clean_string(r.get(id_col))
                    refs.append(f"{rid} ({label})" if label else rid)

        if sheet_name == SHEET_STYLES:
            scan(SHEET_JOBS, "style_preset_id", "job_name")
        elif sheet_name == SHEET_TEMPLATES:
            scan(SHEET_JOBS, "prompt_template_id", "job_name")
        elif sheet_name == SHEET_JOBS:
            scan(SHEET_ITEMS, "job_id", "title")
        return refs
