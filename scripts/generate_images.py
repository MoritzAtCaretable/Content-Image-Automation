from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import gspread
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from google.oauth2.service_account import Credentials
from PIL import Image, ImageOps

from restoration_defaults import DEFAULT_RESTORATION_PROMPT

# -----------------------------------------------------------------------------
# Configuration / constants
# -----------------------------------------------------------------------------

SHEET_JOBS = "01_Jobs_Batches"
SHEET_ITEMS = "02_Content_Items"
SHEET_STYLES = "03_Style_Presets"
SHEET_TEMPLATES = "04_Prompt_Templates"
SHEET_QUEUE = "05_Generation_Queue"  # optional, script can create/use if present

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Standard ist das schnelle/günstige Lite-Bildmodell — es kann aber NUR 1K.
# Verlangt ein Job 2K/4K, wechselt generate_image automatisch auf das große
# Modell (FULL_IMAGE_MODEL), siehe _resolve_image_model.
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-lite-image"
FULL_IMAGE_MODEL = "gemini-3.1-flash-image"     # Nano Banana 2
LITE_ONLY_IMAGE_SIZE = "1K"
FULL_MODEL_IMAGE_SIZES = {"1K", "2K", "4K"}
DEFAULT_PLANNER_MODEL = "gemini-3.5-flash"
DEFAULT_QC_MODEL = "gemini-3.1-flash-lite"
ALLOWED_JOB_STATUSES = {"todo", "redo"}
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_ASPECT_RATIOS = ("1:1", "9:16", "16:9", "4:3", "3:4", "3:2", "2:3", "21:9")


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------

@dataclass
class AppConfig:
    project_root: Path
    gemini_api_key: str
    google_sheet_id: str
    google_service_account_file: str
    references_dir: Path
    outputs_dir: Path
    image_model: str = DEFAULT_IMAGE_MODEL
    planner_model: str = DEFAULT_PLANNER_MODEL
    qc_model: str = DEFAULT_QC_MODEL
    sleep_between_generations_sec: float = 0.5
    max_retries_per_variant: int = 1
    image_aspect_ratio_default: str = "1:1"
    image_size_default: str = "1K"
    default_variants_per_item: int = 2
    log_level: str = "INFO"
    dry_run: bool = False


@dataclass
class StylePreset:
    style_preset_id: str
    style_name: str
    style_description: str = ""
    tone: str = ""
    color_palette: str = ""
    composition: str = ""
    lighting: str = ""
    do_include: str = ""
    avoid: str = ""
    ui_safe: str = ""
    extra_notes: str = ""
    reference_images: List[str] = field(default_factory=list)


@dataclass
class PromptTemplate:
    prompt_template_id: str
    template_name: str
    template_body: str
    extra_instructions: str = ""


@dataclass
class ContentItem:
    item_id: str
    job_id: str
    content_type: str
    title: str
    source_text_or_topic: str
    notes: str = ""
    reference_files: List[str] = field(default_factory=list)
    output_name_hint: str = ""
    source_image_path: str = ""
    source_relative_path: str = ""
    source_width: int = 0
    source_height: int = 0
    source_extension: str = ".png"


@dataclass
class Job:
    job_id: str
    status: str
    job_type: str
    job_name: str
    asset_goal: str
    output_folder: str
    target_count: int
    variants_per_item: int
    aspect_ratio: str
    style_preset_id: str
    prompt_template_id: str
    reference_files: List[str] = field(default_factory=list)
    notes: str = ""
    batch_seed_topics: str = ""
    image_size: str = ""          # leer = DEFAULT_IMAGE_SIZE aus der .env
    qc_enabled: bool = True       # Sheet-Spalte qc_enabled (ja/nein); leer = ja
    restore_source_folder: str = ""
    restore_prompt: str = ""
    restore_model: str = DEFAULT_IMAGE_MODEL
    restore_max_image_size: str = "1K"
    restore_transparency_background: str = "green"


@dataclass
class CandidateResult:
    item_id: str
    variant_index: int
    prompt: str
    image_path: Path
    qc_score: float
    qc_decision: str
    qc_reason: str
    qc_details: Dict[str, Any]


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------


def load_config() -> AppConfig:
    configured_root = os.getenv("CIA_PROJECT_ROOT", "").strip()
    project_root = (
        Path(configured_root).expanduser().resolve()
        if configured_root
        else Path(__file__).resolve().parent.parent
    )
    load_dotenv(project_root / ".env")

    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    google_sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    google_service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()

    if not gemini_api_key:
        raise ValueError("GEMINI_API_KEY fehlt in .env")
    if not google_sheet_id:
        raise ValueError("GOOGLE_SHEET_ID fehlt in .env")
    if not google_service_account_file:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_FILE fehlt in .env")

    references_dir = Path(os.getenv("REFERENCE_DIR", "references")).expanduser()
    outputs_dir = Path(os.getenv("OUTPUT_DIR", "outputs")).expanduser()
    if not references_dir.is_absolute():
        references_dir = (project_root / references_dir).resolve()
    if not outputs_dir.is_absolute():
        outputs_dir = (project_root / outputs_dir).resolve()

    service_account_path = Path(google_service_account_file).expanduser()
    if not service_account_path.is_absolute():
        service_account_path = (project_root / service_account_path).resolve()

    return AppConfig(
        project_root=project_root,
        gemini_api_key=gemini_api_key,
        google_sheet_id=google_sheet_id,
        google_service_account_file=str(service_account_path),
        references_dir=references_dir,
        outputs_dir=outputs_dir,
        image_model=os.getenv("IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
        planner_model=os.getenv("PLANNER_MODEL", DEFAULT_PLANNER_MODEL),
        qc_model=os.getenv("QC_MODEL", DEFAULT_QC_MODEL),
        sleep_between_generations_sec=float(os.getenv("SLEEP_BETWEEN_GENERATIONS_SEC", "0.5")),
        max_retries_per_variant=int(os.getenv("MAX_RETRIES_PER_VARIANT", "1")),
        image_aspect_ratio_default=os.getenv("DEFAULT_ASPECT_RATIO", "1:1"),
        image_size_default=os.getenv("DEFAULT_IMAGE_SIZE", "1K"),
        default_variants_per_item=int(os.getenv("DEFAULT_VARIANTS_PER_ITEM", "2")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        dry_run=os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"},
    )



def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )



def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalize_key(col) for col in df.columns]
    return df



def normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")



def clean_string(value: Any) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()



def parse_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "" or pd.isna(value):
            return default
        return int(float(value))
    except Exception:
        return default



def parse_csv(value: Any) -> List[str]:
    if value is None or value == "" or pd.isna(value):
        return []
    text = str(value)
    parts = re.split(r"[,;\n]", text)
    return [p.strip() for p in parts if p.strip()]



def parse_bool(value: Any, default: bool = True) -> bool:
    """ja/nein-Zellen aus dem Sheet (auch yes/true/1 bzw. no/false/0)."""
    text = clean_string(value).lower()
    if text in {"ja", "yes", "true", "1", "an", "on", "x"}:
        return True
    if text in {"nein", "no", "false", "0", "aus", "off"}:
        return False
    return default



def slugify(value: str, max_len: int = 60) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value[:max_len] or "item"



def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path



def _strip_code_fences(text: str) -> str:
    """Entfernt ```json … ``` bzw. ``` … ``` um den eigentlichen Inhalt."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_span(text: str) -> Optional[str]:
    """Liefert den Teilstring vom ersten '{' bis zur passenden '}'-Klammer.
    Strings/Escapes werden mitgezählt, damit geschweifte Klammern innerhalb
    von Strings nicht mitzählen. So wird führende/abschließende Prosa
    (z. B. "Here is the JSON: …") entfernt."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]  # unausgeglichen: Rest für einen Best-Effort-Versuch


def safe_json_extract(text: str) -> Dict[str, Any]:
    """Parst ein JSON-Objekt aus einer Modell-Antwort und toleriert die
    üblichen LLM-Ausrutscher: Markdown-Codefences, Prosa vor/nach dem JSON
    und abschließende Kommata. Wirft mit Kontext-Snippet, wenn nichts passt."""
    if not text or not text.strip():
        raise ValueError("Modell-Output war leer")

    cleaned = _strip_code_fences(text)
    candidates = [cleaned]
    span = _extract_json_span(cleaned)
    if span and span not in candidates:
        candidates.append(span)
    # zusätzlich jeweils ohne abschließende Kommata (", }" / ", ]")
    candidates += [re.sub(r",(\s*[}\]])", r"\1", c) for c in list(candidates)]

    for candidate in candidates:
        try:
            result = json.loads(candidate)
        except Exception:
            continue
        if isinstance(result, dict):
            return result

    snippet = cleaned[:400].replace("\n", " ")
    raise ValueError(
        f"Konnte kein gültiges JSON-Objekt im Modell-Output finden. "
        f"Anfang der Antwort: {snippet!r}")



def _resolve_reference_path(name: str, reference_root: Path) -> Optional[Path]:
    """Findet ein Referenzbild tolerant — egal ob im Sheet 'foo.png',
    'references/foo.png' oder ein absoluter Pfad steht."""
    name = clean_string(name)
    if not name:
        return None
    p = Path(name).expanduser()
    candidates: List[Path] = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(reference_root / name)          # foo.png unter references/
        candidates.append(reference_root.parent / name)   # references/foo.png ab Projektwurzel
        candidates.append(Path.cwd() / name)
        stripped = re.sub(r"^\.?/*references/+", "", name)  # führendes references/ entfernen
        if stripped != name:
            candidates.append(reference_root / stripped)
    seen = set()
    for c in candidates:
        try:
            rc = c.resolve()
        except Exception:
            continue
        if rc in seen:
            continue
        seen.add(rc)
        if rc.exists() and rc.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            return rc
    return None


def list_reference_images(reference_root: Path, requested_files: Iterable[str]) -> List[Path]:
    paths: List[Path] = []
    for file_name in requested_files:
        resolved = _resolve_reference_path(file_name, reference_root)
        if resolved is not None:
            paths.append(resolved)
        else:
            logging.warning("Referenzbild nicht gefunden oder kein unterstütztes Format: %s", file_name)
    return paths


def resolve_job_path(value: str, project_root: Path) -> Path:
    path = Path(clean_string(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def closest_supported_aspect_ratio(width: int, height: int) -> str:
    """Liefert das vom Bildmodell unterstuetzte Verhaeltnis mit der
    geringsten Abweichung zum Quellbild."""
    if width <= 0 or height <= 0:
        return "1:1"
    source_ratio = width / height

    def distance(label: str) -> float:
        left, right = label.split(":", 1)
        return abs(source_ratio - (float(left) / float(right)))

    return min(SUPPORTED_ASPECT_RATIOS, key=distance)


def restoration_model_size(model: str, requested_max_size: str = "1K") -> str:
    """Lite ist technisch auf 1K begrenzt; beim grossen Flash-Modell gilt
    die bewusst gewaehlte Kosten-/Qualitaetsobergrenze."""
    if clean_string(model) != FULL_IMAGE_MODEL:
        return "1K"
    requested = clean_string(requested_max_size).upper()
    return requested if requested in FULL_MODEL_IMAGE_SIZES else "1K"


def crop_to_source_aspect_ratio(image: Image.Image, width: int,
                                height: int) -> Image.Image:
    """Schneidet mittig und ohne Verzerrung auf das exakte Quellverhaeltnis.

    Gemini akzeptiert nur eine begrenzte Menge von Seitenverhaeltnissen. Nach
    der Generierung wird daher lediglich der kleine Ueberstand des
    naechstpassenden Modellformats entfernt. Die maximal erzeugte Aufloesung
    bleibt erhalten; es wird nicht auf die alten Pixelmasse verkleinert.
    """
    if width <= 0 or height <= 0:
        raise ValueError("Ungueltiges Seitenverhaeltnis fuer Restaurierung")
    restored = image.convert("RGB")
    divisor = math.gcd(width, height)
    ratio_width = width // divisor
    ratio_height = height // divisor
    multiplier = min(restored.width // ratio_width,
                     restored.height // ratio_height)
    if multiplier <= 0:
        raise ValueError(
            "Das generierte Bild ist zu klein fuer das Quell-Seitenverhaeltnis")
    crop_width = ratio_width * multiplier
    crop_height = ratio_height * multiplier
    left = (restored.width - crop_width) // 2
    top = (restored.height - crop_height) // 2
    box = (left, top, left + crop_width, top + crop_height)
    return restored.crop(box)


def flatten_transparency(image: Image.Image, background: str) -> Image.Image:
    """Legt transparente Bereiche kontrolliert auf Weiss oder Chroma-Gruen.

    Eine direkte RGB-Konvertierung wuerde transparente Pixel schwarz machen.
    Das ist fuer spaeteres Freistellen unguenstig.
    """
    rgba = image.convert("RGBA")
    if rgba.getextrema()[3] == (255, 255):
        return rgba.convert("RGB")
    color = (255, 255, 255, 255) if background == "white" else (0, 255, 0, 255)
    canvas = Image.new("RGBA", rgba.size, color)
    canvas.alpha_composite(rgba)
    return canvas.convert("RGB")


def save_restored_image(image: Image.Image, path: Path) -> None:
    """Speichert im Quellformat mit hochwertigen Exportparametern."""
    ensure_dir(path.parent)
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        image.convert("RGB").save(path, format="JPEG", quality=95,
                                  subsampling=0, optimize=True)
    elif suffix == ".webp":
        image.convert("RGB").save(path, format="WEBP", quality=95, method=6)
    else:
        image.convert("RGB").save(path, format="PNG", optimize=True)


def prepare_restoration_items(job: Job, config: AppConfig) -> List[ContentItem]:
    source_root = resolve_job_path(job.restore_source_folder,
                                   config.project_root)
    if not source_root.is_dir():
        raise ValueError(
            f"Quellordner fuer Restaurierung nicht gefunden: {source_root}")

    output_root = resolve_job_path(job.output_folder, config.project_root)
    if output_root == source_root:
        raise ValueError(
            "Quell- und Ausgabeordner duerfen bei einer Restaurierung nicht identisch sein.")
    try:
        output_is_inside_source = output_root.is_relative_to(source_root)
    except (OSError, ValueError):
        output_is_inside_source = False
    source_paths: List[Path] = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        relative = path.relative_to(source_root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        try:
            if output_is_inside_source and path.resolve().is_relative_to(output_root):
                continue
        except (OSError, ValueError):
            pass
        source_paths.append(path)

    source_paths.sort(key=lambda p: p.relative_to(source_root).as_posix().lower())
    if not source_paths:
        raise ValueError(
            f"Im Quellordner wurden keine PNG-, JPG- oder WebP-Bilder gefunden: {source_root}")

    items: List[ContentItem] = []
    for idx, source_path in enumerate(source_paths, start=1):
        relative = source_path.relative_to(source_root)
        try:
            with Image.open(source_path) as raw:
                oriented = ImageOps.exif_transpose(raw)
                width, height = oriented.size
        except Exception as exc:
            logging.warning("Ungueltiges Quellbild wird uebersprungen: %s (%s)",
                            source_path, exc)
            continue
        items.append(ContentItem(
            item_id=f"{job.job_id}_RESTORE_{idx:04d}",
            job_id=job.job_id,
            content_type="image_restore",
            title=relative.as_posix(),
            source_text_or_topic=f"Originalgetreue Restaurierung von {relative.name}",
            reference_files=[str(source_path.resolve())],
            output_name_hint=source_path.stem,
            source_image_path=str(source_path.resolve()),
            source_relative_path=relative.as_posix(),
            source_width=width,
            source_height=height,
            source_extension=source_path.suffix.lower(),
        ))

    if not items:
        raise ValueError("Alle gefundenen Quelldateien waren unlesbar.")
    return items



def pil_image_to_bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


# -----------------------------------------------------------------------------
# Google Sheets helpers
# -----------------------------------------------------------------------------


def create_sheet_client(config: AppConfig) -> gspread.Client:
    creds = Credentials.from_service_account_file(config.google_service_account_file, scopes=SCOPES)
    return gspread.authorize(creds)



def detect_header_row(values: List[List[str]], sheet_name: str) -> int:
    """
    Detect the most likely header row within the first 10 rows.

    This is useful because the imported XLSX may contain a title/instruction row
    above the actual table headers.
    """
    expected_by_sheet = {
        SHEET_JOBS: {"job_id", "status", "job_type"},
        SHEET_ITEMS: {"item_id", "job_id", "content_type"},
        SHEET_STYLES: {"style_preset_id"},
        SHEET_TEMPLATES: {"prompt_template_id", "template_name"},
        SHEET_QUEUE: {"job_id", "item_id"},
    }
    expected = expected_by_sheet.get(sheet_name, set())

    best_idx = 0
    best_score = -1

    for idx, row in enumerate(values[:10]):
        headers = {normalize_key(cell) for cell in row if normalize_key(cell)}
        if not headers:
            continue

        score = len(headers)
        if expected:
            score += len(headers.intersection(expected)) * 100

        if score > best_score:
            best_score = score
            best_idx = idx

        if expected and expected.issubset(headers):
            return idx

    logging.warning(
        "Konnte Header-Zeile in Sheet '%s' nicht eindeutig erkennen. Verwende Zeile %s.",
        sheet_name,
        best_idx + 1,
    )
    return best_idx


def read_sheet_df(workbook: gspread.Spreadsheet, sheet_name: str, required: bool = True) -> pd.DataFrame:
    """
    Robustly read a worksheet into a DataFrame.

    - Finds the header row automatically, e.g. row 2 after an imported title row.
    - Ignores empty header columns.
    - Ignores duplicate non-empty headers after the first occurrence.
    """
    try:
        worksheet = workbook.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        if required:
            raise
        return pd.DataFrame()

    values = worksheet.get_all_values()
    if not values:
        return pd.DataFrame()

    header_row_idx = detect_header_row(values, sheet_name)
    raw_headers = values[header_row_idx]
    normalized_headers = [normalize_key(h) for h in raw_headers]

    logging.info("Sheet '%s': Header-Zeile erkannt als Zeile %s", sheet_name, header_row_idx + 1)

    keep_indices: List[int] = []
    seen: set[str] = set()
    final_headers: List[str] = []

    for idx, header in enumerate(normalized_headers):
        if not header:
            continue
        if header in seen:
            logging.warning(
                "Doppelte Spalte '%s' in Sheet '%s' ignoriert. Bitte Header-Zeile prüfen.",
                header,
                sheet_name,
            )
            continue
        seen.add(header)
        keep_indices.append(idx)
        final_headers.append(header)

    if not final_headers:
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    for raw_row in values[header_row_idx + 1:]:
        row_dict: Dict[str, Any] = {}
        row_has_value = False

        for out_header, source_idx in zip(final_headers, keep_indices):
            cell_value = raw_row[source_idx] if source_idx < len(raw_row) else ""
            if str(cell_value).strip():
                row_has_value = True
            row_dict[out_header] = cell_value

        if row_has_value:
            rows.append(row_dict)

    return pd.DataFrame(rows, columns=final_headers)



def update_job_status(workbook: gspread.Spreadsheet, job_id: str, new_status: str) -> None:
    worksheet = workbook.worksheet(SHEET_JOBS)
    values = worksheet.get_all_values()
    if not values:
        logging.warning("Konnte Jobs-Sheet nicht für Statusupdate lesen")
        return

    header_row_idx = detect_header_row(values, SHEET_JOBS)
    header = [normalize_key(h) for h in values[header_row_idx]]
    try:
        job_id_col = header.index("job_id") + 1
        status_col = header.index("status") + 1
    except ValueError:
        logging.warning("job_id oder status Spalte im Jobs-Sheet nicht gefunden")
        return

    first_data_row_number = header_row_idx + 2
    for row_idx, row in enumerate(values[header_row_idx + 1:], start=first_data_row_number):
        if len(row) >= job_id_col and clean_string(row[job_id_col - 1]) == job_id:
            worksheet.update_cell(row_idx, status_col, new_status)
            logging.info("Job %s -> Status aktualisiert auf %s", job_id, new_status)
            return

    logging.warning("Job %s nicht im Sheet gefunden, Status nicht aktualisiert", job_id)



def append_queue_rows(workbook: gspread.Spreadsheet, rows: List[List[Any]]) -> None:
    if not rows:
        return
    try:
        worksheet = workbook.worksheet(SHEET_QUEUE)
    except gspread.WorksheetNotFound:
        logging.info("Sheet %s nicht vorhanden, Queue-Logging wird übersprungen", SHEET_QUEUE)
        return

    worksheet.append_rows(rows, value_input_option="USER_ENTERED")


# -----------------------------------------------------------------------------
# Parsing sheet records into domain objects
# -----------------------------------------------------------------------------


def parse_jobs(df: pd.DataFrame, config: AppConfig) -> List[Job]:
    if df.empty:
        return []

    required_cols = [
        "job_id",
        "status",
        "job_type",
        "job_name",
        "asset_goal",
        "output_folder",
        "target_count",
        "style_preset_id",
        "prompt_template_id",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Im Jobs-Sheet fehlen Spalten: {missing}")

    jobs: List[Job] = []
    for _, row in df.iterrows():
        status = clean_string(row.get("status")).lower()
        if status not in ALLOWED_JOB_STATUSES:
            continue
        restore_background = clean_string(
            row.get("restore_transparency_background")).lower()
        if restore_background not in {"green", "white"}:
            restore_background = "green"

        job = Job(
            job_id=clean_string(row.get("job_id")),
            status=status,
            job_type=clean_string(row.get("job_type")),
            job_name=clean_string(row.get("job_name")),
            asset_goal=clean_string(row.get("asset_goal")),
            output_folder=clean_string(row.get("output_folder")),
            target_count=parse_int(row.get("target_count"), 0),
            variants_per_item=parse_int(row.get("variants_per_item"), config.default_variants_per_item),
            aspect_ratio=clean_string(row.get("aspect_ratio")) or config.image_aspect_ratio_default,
            style_preset_id=clean_string(row.get("style_preset_id")),
            prompt_template_id=clean_string(row.get("prompt_template_id")),
            reference_files=parse_csv(row.get("reference_files")),
            notes=clean_string(row.get("notes")),
            batch_seed_topics=clean_string(row.get("batch_seed_topics")),
            image_size=clean_string(row.get("image_size")).upper()
                       or config.image_size_default,
            qc_enabled=parse_bool(row.get("qc_enabled"), default=True),
            restore_source_folder=clean_string(row.get("restore_source_folder")),
            restore_prompt=clean_string(row.get("restore_prompt")),
            restore_model=(clean_string(row.get("restore_model"))
                           or DEFAULT_IMAGE_MODEL),
            restore_max_image_size=(
                clean_string(row.get("restore_max_image_size")).upper()
                or "1K"),
            restore_transparency_background=restore_background,
        )
        jobs.append(job)
    return jobs



def parse_content_items(df: pd.DataFrame) -> List[ContentItem]:
    if df.empty:
        return []

    required = ["item_id", "job_id", "content_type", "title", "source_text_or_topic"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Im Content-Items-Sheet fehlen Spalten: {missing}")

    items: List[ContentItem] = []
    for _, row in df.iterrows():
        item = ContentItem(
            item_id=clean_string(row.get("item_id")),
            job_id=clean_string(row.get("job_id")),
            content_type=clean_string(row.get("content_type")),
            title=clean_string(row.get("title")),
            source_text_or_topic=clean_string(row.get("source_text_or_topic")),
            notes=clean_string(row.get("notes")),
            reference_files=parse_csv(row.get("reference_files")),
            output_name_hint=clean_string(row.get("output_name_hint")),
        )
        if item.item_id and item.job_id:
            items.append(item)
    return items



def first_non_empty(row: pd.Series, *keys: str) -> str:
    for key in keys:
        value = clean_string(row.get(key))
        if value:
            return value
    return ""


def parse_styles(df: pd.DataFrame) -> Dict[str, StylePreset]:
    """
    Supports both older and newer style preset schemas.

    New schema example:
    style_preset_id, preset_name, use_case, maturity_level, visual_style,
    color_palette, composition_rules, ui_safe_area, positive_style_prompt,
    negative_style_prompt, reference_images, notes

    Older schema example:
    style_preset_id, style_name, style_description, tone, color_palette,
    composition, lighting, do_include, avoid, ui_safe, extra_notes
    """
    if df.empty:
        return {}
    if "style_preset_id" not in df.columns:
        raise ValueError("Im Style-Presets-Sheet fehlt die Spalte style_preset_id")

    styles: Dict[str, StylePreset] = {}
    for _, row in df.iterrows():
        style_id = clean_string(row.get("style_preset_id"))
        if not style_id:
            continue

        use_case = clean_string(row.get("use_case"))
        maturity_level = clean_string(row.get("maturity_level"))
        notes = first_non_empty(row, "extra_notes", "notes")

        styles[style_id] = StylePreset(
            style_preset_id=style_id,
            style_name=first_non_empty(row, "style_name", "preset_name") or style_id,
            style_description=first_non_empty(row, "style_description", "visual_style"),
            tone=first_non_empty(row, "tone", "maturity_level", "use_case"),
            color_palette=clean_string(row.get("color_palette")),
            composition=first_non_empty(row, "composition", "composition_rules"),
            lighting=clean_string(row.get("lighting")),
            do_include=first_non_empty(row, "do_include", "positive_style_prompt"),
            avoid=first_non_empty(row, "avoid", "negative_style_prompt"),
            ui_safe=first_non_empty(row, "ui_safe", "ui_safe_area"),
            extra_notes=" | ".join([part for part in [use_case, maturity_level, notes] if part]),
            reference_images=parse_csv(first_non_empty(row, "reference_images", "reference_files")),
        )
    return styles


def parse_templates(df: pd.DataFrame) -> Dict[str, PromptTemplate]:
    """
    Supports both older and newer prompt template schemas.

    New schema example:
    prompt_template_id, template_name, job_type, model, default_aspect_ratio,
    default_image_size, template_purpose, prompt_template, negative_rules,
    output_notes

    Older schema example:
    prompt_template_id, template_name, template_body, extra_instructions
    """
    if df.empty:
        return {}

    required = ["prompt_template_id", "template_name"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Im Prompt-Templates-Sheet fehlen Spalten: {missing}")

    if "template_body" not in df.columns and "prompt_template" not in df.columns:
        raise ValueError(
            "Im Prompt-Templates-Sheet fehlt eine Prompt-Spalte. "
            "Erwartet wird entweder 'template_body' oder 'prompt_template'."
        )

    templates: Dict[str, PromptTemplate] = {}
    for _, row in df.iterrows():
        template_id = clean_string(row.get("prompt_template_id"))
        if not template_id:
            continue

        template_body = first_non_empty(row, "template_body", "prompt_template")
        extra_parts = [
            clean_string(row.get("extra_instructions")),
            clean_string(row.get("negative_rules")),
            clean_string(row.get("output_notes")),
        ]
        extra_instructions = "\n".join([part for part in extra_parts if part])

        templates[template_id] = PromptTemplate(
            prompt_template_id=template_id,
            template_name=clean_string(row.get("template_name")) or template_id,
            template_body=template_body,
            extra_instructions=extra_instructions,
        )
    return templates


# -----------------------------------------------------------------------------
# Gemini client wrapper
# -----------------------------------------------------------------------------


# Antwort-Schemata für "structured output": Gemini dekodiert damit
# eingeschränkt und liefert garantiert schema-konformes, gültiges JSON —
# das verhindert die kaputten Planner/QC-Antworten, an denen json.loads sonst
# scheitert ("Expecting ',' delimiter …").
CONCEPTS_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["concepts"],
    properties={
        "concepts": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["title", "brief"],
                properties={
                    "title": types.Schema(type=types.Type.STRING),
                    "brief": types.Schema(type=types.Type.STRING),
                    "must_show": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING)),
                    "must_avoid": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING)),
                },
            ),
        ),
    },
)

QC_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["score", "decision", "reason"],
    properties={
        "score": types.Schema(type=types.Type.NUMBER),
        "decision": types.Schema(type=types.Type.STRING,
                                 enum=["accept", "retry", "reject"]),
        "reason": types.Schema(type=types.Type.STRING),
        "topic_match": types.Schema(type=types.Type.NUMBER),
        "style_match": types.Schema(type=types.Type.NUMBER),
        "visual_quality": types.Schema(type=types.Type.NUMBER),
        "ui_suitability": types.Schema(type=types.Type.NUMBER),
        "issues": types.Schema(type=types.Type.ARRAY,
                               items=types.Schema(type=types.Type.STRING)),
        "strengths": types.Schema(type=types.Type.ARRAY,
                                  items=types.Schema(type=types.Type.STRING)),
    },
)

RESTORATION_QC_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["score", "decision", "reason"],
    properties={
        "score": types.Schema(type=types.Type.NUMBER),
        "decision": types.Schema(type=types.Type.STRING,
                                 enum=["accept", "retry", "reject"]),
        "reason": types.Schema(type=types.Type.STRING),
        "composition_fidelity": types.Schema(type=types.Type.NUMBER),
        "geometry_fidelity": types.Schema(type=types.Type.NUMBER),
        "detail_quality": types.Schema(type=types.Type.NUMBER),
        "artifact_control": types.Schema(type=types.Type.NUMBER),
        "style_match": types.Schema(type=types.Type.NUMBER),
        "requested_changes": types.Schema(type=types.Type.NUMBER),
        "issues": types.Schema(type=types.Type.ARRAY,
                               items=types.Schema(type=types.Type.STRING)),
        "strengths": types.Schema(type=types.Type.ARRAY,
                                  items=types.Schema(type=types.Type.STRING)),
    },
)


# HTTP-Status, bei denen sich Warten + erneut versuchen lohnt (Überlastung /
# transiente Serverfehler / Rate-Limit). Alles andere ist ein echter Fehler.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
# Wartezeiten zwischen den Versuchen (Sekunden) — insgesamt 5 Versuche.
RETRY_DELAYS_SEC = [10, 30, 60, 120]


class GeminiService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)

    def _generate_with_retry(self, purpose: str, **kwargs) -> Any:
        """generate_content mit Backoff-Retry bei Überlastung (z. B. 503
        'high demand'). Das SDK selbst gibt nach wenigen Sekunden auf — hier
        warten wir deutlich länger, damit ein Batch-Lauf Lastspitzen übersteht."""
        for attempt, delay in enumerate(RETRY_DELAYS_SEC + [None], start=1):
            try:
                return self.client.models.generate_content(**kwargs)
            except genai_errors.APIError as e:
                code = getattr(e, "code", None) or getattr(e, "status_code", None)
                if code not in RETRYABLE_STATUS_CODES or delay is None:
                    raise
                logging.warning(
                    "%s: Modell antwortet mit %s (überlastet) — warte %ss und "
                    "versuche erneut (%s/%s) …",
                    purpose, code, delay, attempt, len(RETRY_DELAYS_SEC) + 1)
                time.sleep(delay)

    def plan_batch_concepts(self, job: Job, style: StylePreset, count: int) -> List[Dict[str, Any]]:
        if count <= 0:
            return []

        seed_topics = job.batch_seed_topics or ""
        planner_prompt = f"""
You are planning image-generation concepts for a batch creative job.
Return ONLY valid JSON.

Task:
Create {count} distinct visual concepts for one image-generation batch.
Each concept should be useful as a single final image brief.

Requirements:
- Keep concepts clearly distinct from each other.
- Match the batch goal.
- Respect the target style.
- Avoid duplicates and near-duplicates.
- Keep each concept practical for image generation.
- Do not include any markdown.

Output schema:
{{
  "concepts": [
    {{
      "title": "short concept title",
      "brief": "1-3 sentences describing the image concept",
      "must_show": ["item1", "item2"],
      "must_avoid": ["item1", "item2"]
    }}
  ]
}}

Batch context:
job_name: {job.job_name}
job_type: {job.job_type}
asset_goal: {job.asset_goal}
notes: {job.notes}
seed_topics: {seed_topics}
style_name: {style.style_name}
style_description: {style.style_description}
tone: {style.tone}
color_palette: {style.color_palette}
composition: {style.composition}
lighting: {style.lighting}
do_include: {style.do_include}
avoid: {style.avoid}
ui_safe: {style.ui_safe}
extra_notes: {style.extra_notes}
""".strip()

        last_error: Optional[Exception] = None
        for attempt in range(1, 3):   # ein Wiederholversuch bei kaputtem JSON
            response = self._generate_with_retry(
                "Motiv-Planung",
                model=self.config.planner_model,
                contents=planner_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.6,
                    response_mime_type="application/json",
                    response_schema=CONCEPTS_RESPONSE_SCHEMA,
                ),
            )
            raw_text = getattr(response, "text", "") or ""
            try:
                parsed = safe_json_extract(raw_text)
            except Exception as e:
                last_error = e
                logging.warning(
                    "Planner-Antwort nicht lesbar (Versuch %s/2): %s", attempt, e)
                continue
            concepts = parsed.get("concepts", [])
            if not isinstance(concepts, list):
                last_error = ValueError(
                    "Planner-Modell hat kein gültiges concepts-Array geliefert")
                logging.warning("%s (Versuch %s/2)", last_error, attempt)
                continue
            return concepts[:count]

        raise ValueError(
            f"Planner-Modell lieferte kein verwertbares JSON: {last_error}")

    def _resolve_image_model(self, image_size: str) -> str:
        """Das Lite-Bildmodell beherrscht nur 1K. Verlangt der Job 2K/4K,
        automatisch auf das große Modell wechseln (nur für diesen Aufruf)."""
        model = self.config.image_model
        size = clean_string(image_size).upper() or LITE_ONLY_IMAGE_SIZE
        if "lite" in model.lower() and size != LITE_ONLY_IMAGE_SIZE:
            logging.info(
                "Bildgröße %s > 1K — nutze %s statt %s für diesen Aufruf.",
                size, FULL_IMAGE_MODEL, model)
            return FULL_IMAGE_MODEL
        return model

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str,
        image_size: str = "",
        reference_images: Optional[List[Path]] = None,
        model_override: str = "",
        transparency_background: str = "green",
    ) -> Image.Image:
        parts: List[Any] = [prompt]
        for ref_path in reference_images or []:
            with Image.open(ref_path) as ref:
                oriented = ImageOps.exif_transpose(ref)
                parts.append(flatten_transparency(
                    oriented, transparency_background).copy())

        size = clean_string(image_size).upper() or self.config.image_size_default
        model = clean_string(model_override) or self._resolve_image_model(size)
        response = self._generate_with_retry(
            "Bildgenerierung",
            model=model,
            contents=parts,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                    image_size=size,
                ),
            ),
        )

        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                inline_data = getattr(part, "inline_data", None)
                if not inline_data or not getattr(inline_data, "data", None):
                    continue
                from io import BytesIO
                return Image.open(BytesIO(inline_data.data)).convert("RGB")

        raise RuntimeError("Es wurde kein Bild im Gemini-Response gefunden")

    def qc_image(
        self,
        image_path: Path,
        prompt: str,
        style: StylePreset,
        item: ContentItem,
        job: Job,
    ) -> Dict[str, Any]:
        qc_prompt = f"""
You are reviewing one generated image candidate.
Evaluate whether it fits the requested creative goal.
Return ONLY valid JSON.

Output schema:
{{
  "score": 0-100,
  "decision": "accept" | "retry" | "reject",
  "reason": "short explanation",
  "topic_match": 0-100,
  "style_match": 0-100,
  "visual_quality": 0-100,
  "ui_suitability": 0-100,
  "issues": ["issue 1", "issue 2"],
  "strengths": ["strength 1", "strength 2"]
}}

Job context:
job_name: {job.job_name}
job_type: {job.job_type}
asset_goal: {job.asset_goal}
notes: {job.notes}

Item context:
item_id: {item.item_id}
title: {item.title}
content_type: {item.content_type}
source_text_or_topic: {item.source_text_or_topic}
notes: {item.notes}

Expected style:
style_name: {style.style_name}
style_description: {style.style_description}
tone: {style.tone}
color_palette: {style.color_palette}
composition: {style.composition}
lighting: {style.lighting}
do_include: {style.do_include}
avoid: {style.avoid}
ui_safe: {style.ui_safe}
extra_notes: {style.extra_notes}

Original prompt used:
{prompt}
""".strip()

        # QC darf einen langen Batch NIE abbrechen: schlägt der API-Aufruf trotz
        # Retries fehl oder ist die Antwort unlesbar, wird das Bild behalten und
        # zur manuellen Sichtung markiert, statt den Job zu killen.
        try:
            with Image.open(image_path) as img:
                response = self._generate_with_retry(
                    f"QC {image_path.name}",
                    model=self.config.qc_model,
                    contents=[qc_prompt, img.copy()],
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        response_mime_type="application/json",
                        response_schema=QC_RESPONSE_SCHEMA,
                    ),
                )
            return safe_json_extract(getattr(response, "text", "") or "")
        except Exception as e:
            logging.warning("QC für %s fehlgeschlagen (%s) — Bild wird "
                            "zur Sichtung übernommen.", image_path.name, e)
            return {"score": 0, "decision": "accept",
                    "reason": f"QC fehlgeschlagen: {e}"}

    def qc_restoration(
        self,
        source_path: Path,
        restored_path: Path,
        prompt: str,
        style: StylePreset,
        job: Job,
    ) -> Dict[str, Any]:
        qc_prompt = f"""
You are comparing a SOURCE image and its RESTORED candidate.
The first image is SOURCE. The second image is RESTORED.
Return ONLY valid JSON.

Judge the candidate against the exact restoration instruction. A standard
restoration must preserve composition, crop, object positions, geometry,
identity, colors and style while improving clarity and fine detail. If the
instruction explicitly requests a removal or alteration, judge that requested
change as correct rather than penalizing it.

Use a strict score. Choose retry when another generation attempt could likely
fix visible drift or artifacts. Reject severe structural changes.

Job: {job.job_name}
Instruction: {prompt}
Optional style: {style.style_name if job.style_preset_id else "original source style"}

Output fields:
- score: 0-100
- decision: accept | retry | reject
- reason: concise explanation
- composition_fidelity, geometry_fidelity, detail_quality, artifact_control,
  style_match, requested_changes: each 0-100
- issues: list
- strengths: list
""".strip()
        try:
            with Image.open(source_path) as source_raw, \
                    Image.open(restored_path) as restored_raw:
                source = flatten_transparency(
                    ImageOps.exif_transpose(source_raw),
                    job.restore_transparency_background).copy()
                restored = restored_raw.convert("RGB").copy()
            response = self._generate_with_retry(
                f"Restaurierungs-QC {restored_path.name}",
                model=self.config.qc_model,
                contents=[qc_prompt, source, restored],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=RESTORATION_QC_RESPONSE_SCHEMA,
                ),
            )
            return safe_json_extract(getattr(response, "text", "") or "")
        except Exception as e:
            logging.warning(
                "Restaurierungs-QC fuer %s fehlgeschlagen (%s) — Bild wird "
                "zur manuellen Sichtung uebernommen.", restored_path.name, e)
            return {"score": 0, "decision": "accept",
                    "reason": f"Restaurierungs-QC fehlgeschlagen: {e}"}


# -----------------------------------------------------------------------------
# Prompt building / item preparation
# -----------------------------------------------------------------------------


def original_image_style() -> StylePreset:
    return StylePreset(
        style_preset_id="",
        style_name="Originalstil",
        style_description="Preserve the visual style of each source image.",
    )


def render_restoration_prompt(style: StylePreset, item: ContentItem,
                              job: Job) -> str:
    prompt = clean_string(job.restore_prompt) or DEFAULT_RESTORATION_PROMPT
    replacements = {
        "source_filename": Path(item.source_image_path).name,
        "source_width": str(item.source_width),
        "source_height": str(item.source_height),
        "source_dimensions": f"{item.source_width}x{item.source_height}",
        "job_name": job.job_name,
        "asset_goal": job.asset_goal,
        "notes": job.notes,
    }
    for key, value in replacements.items():
        prompt = prompt.replace("{" + key + "}", value)

    if job.style_preset_id:
        style_instruction = f"""
Apply this optional unifying style while keeping the source composition and
all requested content constraints intact:
- style: {style.style_name}
- visual direction: {style.style_description}
- tone: {style.tone}
- colors: {style.color_palette}
- composition rules: {style.composition}
- include: {style.do_include}
- avoid: {style.avoid}
- notes: {style.extra_notes}
""".strip()
    else:
        style_instruction = (
            "Preserve the individual visual style, colors, lighting and "
            "rendering character of this source image."
        )

    technical = f"""
The FIRST provided image is the authoritative source image.
Preserve the exact source aspect ratio {item.source_width}:{item.source_height}.
Generate at the highest resolution supported by the selected model.
The original pixel dimensions {item.source_width} x {item.source_height} are
reference dimensions only and must not limit the output resolution.
Preserve the full frame and do not change the orientation.
{style_instruction}
""".strip()
    return f"{prompt.strip()}\n\nTechnical restoration constraints:\n{technical}"


def render_prompt(template: PromptTemplate, style: StylePreset, item: ContentItem, job: Job) -> str:
    values = {
        "job_id": job.job_id,
        "job_name": job.job_name,
        "job_type": job.job_type,
        "asset_goal": job.asset_goal,
        "job_notes": job.notes,
        "item_id": item.item_id,
        "content_type": item.content_type,
        "title": item.title,
        "source_text_or_topic": item.source_text_or_topic,
        "item_notes": item.notes,
        "aspect_ratio": job.aspect_ratio,

        # Older style placeholders
        "style_name": style.style_name,
        "style_description": style.style_description,
        "tone": style.tone,
        "color_palette": style.color_palette,
        "composition": style.composition,
        "lighting": style.lighting,
        "do_include": style.do_include,
        "avoid": style.avoid,
        "ui_safe": style.ui_safe,
        "extra_notes": style.extra_notes,

        # Newer style placeholders from your current Sheet
        "preset_name": style.style_name,
        "visual_style": style.style_description,
        "maturity_level": style.tone,
        "composition_rules": style.composition,
        "ui_safe_area": style.ui_safe,
        "positive_style_prompt": style.do_include,
        "negative_style_prompt": style.avoid,
        "notes": style.extra_notes,
    }

    prompt = template.template_body
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value or "")

    unresolved = sorted(set(re.findall(r"\{[a-zA-Z0-9_]+\}", prompt)))
    if unresolved:
        logging.warning(
            "Prompt enthält nicht ersetzte Platzhalter: %s. Prüfe Template-Spalten/Platzhalter.",
            ", ".join(unresolved),
        )

    extra = clean_string(template.extra_instructions)
    if extra:
        prompt = f"{prompt}\n\nAdditional instructions:\n{extra}".strip()

    return prompt.strip()



def prepare_items_for_job(
    job: Job,
    content_items: List[ContentItem],
    gemini: GeminiService,
    style: StylePreset,
    config: AppConfig,
) -> List[ContentItem]:
    if job.job_type == "content_linked":
        items = [item for item in content_items if item.job_id == job.job_id]
        if not items:
            raise ValueError(f"Job {job.job_id} ist content_linked, aber es wurden keine Content Items gefunden")
        return items

    if job.job_type == "batch_theme":
        count = job.target_count
        if count <= 0:
            raise ValueError(f"Job {job.job_id} hat target_count <= 0")
        concepts = gemini.plan_batch_concepts(job=job, style=style, count=count)
        items: List[ContentItem] = []
        for idx, concept in enumerate(concepts, start=1):
            title = clean_string(concept.get("title")) or f"concept_{idx:03d}"
            brief = clean_string(concept.get("brief"))
            must_show = concept.get("must_show", []) or []
            must_avoid = concept.get("must_avoid", []) or []
            notes = ""
            if must_show:
                notes += f"Must show: {', '.join(map(str, must_show))}. "
            if must_avoid:
                notes += f"Must avoid: {', '.join(map(str, must_avoid))}."
            items.append(
                ContentItem(
                    item_id=f"{job.job_id}_ITEM_{idx:03d}",
                    job_id=job.job_id,
                    content_type="planned_batch_item",
                    title=title,
                    source_text_or_topic=brief,
                    notes=notes.strip(),
                    output_name_hint=slugify(title),
                )
            )
        return items

    if job.job_type == "image_restore":
        return prepare_restoration_items(job, config)

    raise ValueError(f"Unbekannter job_type für Job {job.job_id}: {job.job_type}")


# -----------------------------------------------------------------------------
# Generation / selection workflow
# -----------------------------------------------------------------------------


def save_image(image: Image.Image, path: Path) -> None:
    ensure_dir(path.parent)
    image.save(path, format="PNG")



def run_job(
    workbook: gspread.Spreadsheet,
    config: AppConfig,
    gemini: GeminiService,
    job: Job,
    styles: Dict[str, StylePreset],
    templates: Dict[str, PromptTemplate],
    content_items: List[ContentItem],
) -> Dict[str, Any]:
    logging.info("=" * 80)
    logging.info("Starte Job %s | %s", job.job_id, job.job_name)

    is_restoration = job.job_type == "image_restore"

    if job.style_preset_id and job.style_preset_id not in styles:
        raise ValueError(f"Style Preset {job.style_preset_id} für Job {job.job_id} nicht gefunden")
    if not is_restoration and not job.style_preset_id:
        raise ValueError(f"Style Preset für Job {job.job_id} fehlt")
    if not is_restoration and job.prompt_template_id not in templates:
        raise ValueError(f"Prompt Template {job.prompt_template_id} für Job {job.job_id} nicht gefunden")

    style = (styles[job.style_preset_id]
             if job.style_preset_id else original_image_style())
    template = templates.get(job.prompt_template_id)

    base_output_dir = Path(job.output_folder)
    if not base_output_dir.is_absolute():
        base_output_dir = (config.project_root / base_output_dir).resolve()
    # Sicherer Fallback: fehlenden Output-Ordner anlegen statt abzubrechen
    # (z. B. wenn der Pfad nur ins Sheet getippt wurde).
    if not base_output_dir.exists():
        logging.info("[%s] Output-Ordner existiert nicht — wird angelegt: %s",
                     job.job_id, base_output_dir)
    ensure_dir(base_output_dir)

    # Mit QC: Varianten nach candidates/, das beste Bild nach selected/.
    # Ohne QC: ALLE erstellten Bilder in einen gemeinsamen Ordner images/.
    if job.qc_enabled:
        candidates_dir = ensure_dir(base_output_dir / "candidates")
        selected_dir = ensure_dir(base_output_dir / "selected")
    else:
        candidates_dir = ensure_dir(base_output_dir / "images")
        selected_dir = None
        logging.info("[%s] Qualitätskontrolle AUS — alle Bilder landen in %s",
                     job.job_id, candidates_dir)
    metadata_dir = ensure_dir(base_output_dir / "metadata")

    job_items = prepare_items_for_job(job, content_items, gemini, style, config)

    # Referenzbilder, die an JEDES Motiv dieses Jobs mitgegeben werden:
    # zuerst die Stil-Anker aus dem Style Preset, dann job-weite Referenzen.
    # Item-eigene Referenzen kommen weiter unten pro Motiv dazu.
    style_reference_paths = list_reference_images(config.references_dir, style.reference_images)
    job_reference_paths = list_reference_images(config.references_dir, job.reference_files)
    base_reference_paths = list(dict.fromkeys(style_reference_paths + job_reference_paths))
    if base_reference_paths:
        logging.info("[%s] %s Referenzbild(er) werden pro Motiv als Stilvorlage "
                     "mitgegeben: %s", job.job_id, len(base_reference_paths),
                     ", ".join(p.name for p in base_reference_paths))
    queue_rows: List[List[Any]] = []
    results_summary: List[Dict[str, Any]] = []
    # Schutzschalter: einzelne fehlgeschlagene Varianten werden übersprungen,
    # aber ab dieser Zahl Fehlschläge IN FOLGE ist die API offenbar down —
    # dann sauber abbrechen statt stundenlang gegen die Wand zu laufen.
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3

    for item_index, item in enumerate(job_items, start=1):
        logging.info("[%s] Bearbeite Item %s/%s: %s", job.job_id, item_index, len(job_items), item.title)

        if is_restoration:
            item_prompt = render_restoration_prompt(style=style, item=item, job=job)
            # Das Original muss als erste und damit autoritative Bildreferenz
            # vor moeglichen Stilankern an das Modell gehen.
            item_reference_paths = list(dict.fromkeys(
                list_reference_images(config.references_dir, item.reference_files)
                + base_reference_paths))
            generation_aspect_ratio = closest_supported_aspect_ratio(
                item.source_width, item.source_height)
            generation_model = job.restore_model or DEFAULT_IMAGE_MODEL
            generation_image_size = restoration_model_size(
                generation_model, job.restore_max_image_size)
            logging.info(
                "[%s | %s] Restaurierung %sx%s -> %s, Format %s/%s; "
                "Ausgabe behaelt das exakte Seitenverhaeltnis bei maximaler Aufloesung.",
                job.job_id, item.item_id, item.source_width, item.source_height,
                generation_model, generation_aspect_ratio, generation_image_size)
        else:
            if template is None:  # nur fuer den Type-Checker; oben validiert
                raise ValueError(f"Prompt Template für Job {job.job_id} fehlt")
            item_prompt = render_prompt(template=template, style=style,
                                        item=item, job=job)
            item_reference_paths = list(dict.fromkeys(
                base_reference_paths
                + list_reference_images(config.references_dir, item.reference_files)))
            generation_aspect_ratio = job.aspect_ratio
            generation_image_size = job.image_size
            generation_model = ""

        candidate_results: List[CandidateResult] = []
        variant_count = max(1, job.variants_per_item or config.default_variants_per_item)

        for variant_idx in range(1, variant_count + 1):
            attempt = 0
            final_image_path: Optional[Path] = None
            qc_payload: Dict[str, Any] = {}
            qc_score = -1.0
            qc_decision = "reject"
            qc_reason = ""

            while attempt <= config.max_retries_per_variant:
                attempt += 1
                logging.info(
                    "[%s | %s] Generiere Variante %s (Versuch %s)",
                    job.job_id,
                    item.item_id,
                    variant_idx,
                    attempt,
                )

                if config.dry_run:
                    logging.info("DRY_RUN aktiv: Generierung wird übersprungen")
                    break

                try:
                    image = gemini.generate_image(
                        prompt=item_prompt,
                        aspect_ratio=generation_aspect_ratio,
                        image_size=generation_image_size,
                        reference_images=item_reference_paths,
                        model_override=generation_model,
                        transparency_background=(
                            job.restore_transparency_background
                            if is_restoration else "green"),
                    )
                except Exception as e:
                    consecutive_failures += 1
                    logging.error(
                        "[%s | %s] Variante %s endgültig fehlgeschlagen (%s) — "
                        "wird übersprungen (%s/%s Fehlschläge in Folge).",
                        job.job_id, item.item_id, variant_idx, e,
                        consecutive_failures, MAX_CONSECUTIVE_FAILURES)
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        raise RuntimeError(
                            f"{MAX_CONSECUTIVE_FAILURES} Varianten in Folge "
                            "fehlgeschlagen — API scheint nicht erreichbar. "
                            "Job abgebrochen; Status bleibt auf todo/redo, "
                            "einfach später erneut starten.") from e
                    break  # nächste Variante / nächstes Item
                consecutive_failures = 0

                file_stem = item.output_name_hint or slugify(item.title or item.item_id)
                if is_restoration:
                    image = crop_to_source_aspect_ratio(
                        image, item.source_width, item.source_height)
                    relative_parent = Path(item.source_relative_path).parent
                    extension = (item.source_extension
                                 if item.source_extension in SUPPORTED_IMAGE_EXTENSIONS
                                 else ".png")
                    final_image_path = (
                        candidates_dir / relative_parent /
                        f"{file_stem}_RESTORED_v{variant_idx:02d}_try{attempt:02d}{extension}"
                    )
                    save_restored_image(image, final_image_path)
                else:
                    final_image_path = candidates_dir / f"{item.item_id}_{file_stem}_v{variant_idx:02d}_try{attempt:02d}.png"
                    save_image(image, final_image_path)

                if not job.qc_enabled:
                    # QC ausgeschaltet: Bild ist gespeichert, keine Bewertung.
                    qc_payload = {}
                    qc_score = 0.0
                    qc_decision = "qc_off"
                    qc_reason = ""
                    logging.info("[%s | %s] Variante %s gespeichert (QC aus): %s",
                                 job.job_id, item.item_id, variant_idx,
                                 final_image_path.name)
                    break

                if is_restoration:
                    qc_payload = gemini.qc_restoration(
                        source_path=Path(item.source_image_path),
                        restored_path=final_image_path,
                        prompt=item_prompt,
                        style=style,
                        job=job,
                    )
                else:
                    qc_payload = gemini.qc_image(
                        image_path=final_image_path,
                        prompt=item_prompt,
                        style=style,
                        item=item,
                        job=job,
                    )
                qc_score = float(qc_payload.get("score", 0) or 0)
                qc_decision = clean_string(qc_payload.get("decision")).lower() or "reject"
                qc_reason = clean_string(qc_payload.get("reason"))

                logging.info(
                    "[%s | %s] QC für Variante %s: score=%s decision=%s",
                    job.job_id,
                    item.item_id,
                    variant_idx,
                    qc_score,
                    qc_decision,
                )

                if qc_decision != "retry":
                    break

                time.sleep(config.sleep_between_generations_sec)

            if final_image_path is None:
                continue

            candidate_results.append(
                CandidateResult(
                    item_id=item.item_id,
                    variant_index=variant_idx,
                    prompt=item_prompt,
                    image_path=final_image_path,
                    qc_score=qc_score,
                    qc_decision=qc_decision,
                    qc_reason=qc_reason,
                    qc_details=qc_payload,
                )
            )

            queue_rows.append(
                [
                    datetime.now().isoformat(timespec="seconds"),
                    job.job_id,
                    item.item_id,
                    item.title,
                    variant_idx,
                    str(final_image_path),
                    qc_score,
                    qc_decision,
                    qc_reason,
                ]
            )

            time.sleep(config.sleep_between_generations_sec)

        if not candidate_results:
            logging.warning("[%s | %s] Keine Kandidaten erzeugt", job.job_id, item.item_id)
            continue

        item_meta = {
            "job_id": job.job_id,
            "job_name": job.job_name,
            "item_id": item.item_id,
            "item_title": item.title,
            "source_text_or_topic": item.source_text_or_topic,
            "qc_enabled": job.qc_enabled,
            "prompt": candidate_results[0].prompt,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        if is_restoration:
            with Image.open(candidate_results[0].image_path) as restored_meta_image:
                restored_dimensions = list(restored_meta_image.size)
            item_meta.update({
                "source_image": item.source_image_path,
                "source_relative_path": item.source_relative_path,
                "source_dimensions": [item.source_width, item.source_height],
                "source_aspect_ratio": f"{item.source_width}:{item.source_height}",
                "final_dimensions": restored_dimensions,
                "model_aspect_ratio": generation_aspect_ratio,
                "model_image_size": generation_image_size,
                "restoration_model": generation_model,
                "requested_max_image_size": job.restore_max_image_size,
                "transparency_background": job.restore_transparency_background,
                "style_mode": (style.style_preset_id
                               if job.style_preset_id else "original_source_style"),
            })

        if job.qc_enabled:
            # Bestes Bild wählen und nach selected/ kopieren.
            best = sorted(
                candidate_results,
                key=lambda r: (
                    1 if r.qc_decision == "accept" else 0,
                    r.qc_score,
                ),
                reverse=True,
            )[0]

            if is_restoration:
                relative = Path(item.source_relative_path)
                extension = (item.source_extension
                             if item.source_extension in SUPPORTED_IMAGE_EXTENSIONS
                             else ".png")
                selected_name = f"{relative.stem}_RESTORED{extension}"
                selected_path = selected_dir / relative.parent / selected_name
                ensure_dir(selected_path.parent)
            else:
                selected_name = f"{item.item_id}_{slugify(item.title or item.item_id)}_BEST.png"
                selected_path = selected_dir / selected_name
            shutil.copy2(best.image_path, selected_path)

            item_meta.update({
                "selected_image": str(selected_path),
                "selected_from_candidate": str(best.image_path),
                "selected_score": best.qc_score,
                "selected_decision": best.qc_decision,
                "selected_reason": best.qc_reason,
                "all_candidates": [
                    {
                        "variant_index": c.variant_index,
                        "image_path": str(c.image_path),
                        "qc_score": c.qc_score,
                        "qc_decision": c.qc_decision,
                        "qc_reason": c.qc_reason,
                        "qc_details": c.qc_details,
                    }
                    for c in candidate_results
                ],
            })
        else:
            # QC aus: keine Auswahl — alle Bilder gleichwertig in images/.
            item_meta["images"] = [str(c.image_path) for c in candidate_results]
        if is_restoration:
            relative = Path(item.source_relative_path)
            meta_path = metadata_dir / relative.parent / f"{relative.stem}.json"
            ensure_dir(meta_path.parent)
        else:
            meta_path = metadata_dir / f"{item.item_id}.json"
        meta_path.write_text(json.dumps(item_meta, ensure_ascii=False, indent=2), encoding="utf-8")

        results_summary.append(item_meta)

    append_queue_rows(workbook, queue_rows)

    summary_path = metadata_dir / f"{job.job_id}_summary.json"
    summary_path.write_text(json.dumps(results_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # mark done only if we got at least one result
    if results_summary:
        update_job_status(workbook, job.job_id, "done")
    else:
        logging.warning("Job %s hat keine Ergebnisse erzeugt. Status bleibt unverändert.", job.job_id)

    logging.info("Job %s abgeschlossen. %s Items verarbeitet.", job.job_id, len(results_summary))
    return {
        "job_id": job.job_id,
        "items_processed": len(results_summary),
        "output_dir": str(base_output_dir),
    }


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------


def main() -> int:
    try:
        config = load_config()
        setup_logging(config.log_level)

        logging.info("Lade Google Sheet und Konfiguration …")
        sheet_client = create_sheet_client(config)
        workbook = sheet_client.open_by_key(config.google_sheet_id)

        jobs_df = read_sheet_df(workbook, SHEET_JOBS, required=True)
        items_df = read_sheet_df(workbook, SHEET_ITEMS, required=False)
        styles_df = read_sheet_df(workbook, SHEET_STYLES, required=True)
        templates_df = read_sheet_df(workbook, SHEET_TEMPLATES, required=True)

        jobs = parse_jobs(jobs_df, config=config)
        items = parse_content_items(items_df)
        styles = parse_styles(styles_df)
        templates = parse_templates(templates_df)

        if not jobs:
            logging.info("Keine Jobs mit Status todo oder redo gefunden.")
            return 0

        logging.info("Gefundene Jobs: %s", len(jobs))
        gemini = GeminiService(config)

        overall_summary = []
        for job in jobs:
            try:
                summary = run_job(
                    workbook=workbook,
                    config=config,
                    gemini=gemini,
                    job=job,
                    styles=styles,
                    templates=templates,
                    content_items=items,
                )
                overall_summary.append(summary)
            except Exception as job_error:
                logging.exception("Fehler bei Job %s: %s", job.job_id, job_error)
                # leave status as todo/redo to allow re-run later
                continue

        logging.info("Alle verarbeiteten Jobs: %s", len(overall_summary))
        for summary in overall_summary:
            logging.info(" - %s | %s Items | %s", summary["job_id"], summary["items_processed"], summary["output_dir"])
        return 0

    except Exception as exc:
        logging.error("Fataler Fehler: %s", exc)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
