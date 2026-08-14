from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import gspread
from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_images import (  # noqa: E402
    AppConfig,
    Job,
    closest_supported_aspect_ratio,
    flatten_transparency,
    original_image_style,
    pad_to_source_aspect_ratio,
    prepare_restoration_items,
    render_restoration_prompt,
    restoration_model_size,
    run_job,
)


def make_config(root: Path) -> AppConfig:
    return AppConfig(
        project_root=root,
        gemini_api_key="test",
        google_sheet_id="test",
        google_service_account_file="test.json",
        references_dir=root / "references",
        outputs_dir=root / "outputs",
    )


def make_job(source: str, output: str, **overrides) -> Job:
    values = dict(
        job_id="JOB-0099",
        status="todo",
        job_type="image_restore",
        job_name="Restaurierungstest",
        asset_goal="Originalgetreu restaurieren",
        output_folder=output,
        target_count=0,
        variants_per_item=1,
        aspect_ratio="1:1",
        style_preset_id="",
        prompt_template_id="",
        qc_enabled=True,
        restore_source_folder=source,
        restore_prompt="",
    )
    values.update(overrides)
    return Job(**values)


class FakeJobsWorksheet:
    def __init__(self):
        self.status = "todo"

    def get_all_values(self):
        return [
            ["job_id", "status", "job_type"],
            ["JOB-0099", self.status, "image_restore"],
        ]

    def update_cell(self, row, col, value):
        self.status = value


class FakeWorkbook:
    def __init__(self):
        self.jobs = FakeJobsWorksheet()

    def worksheet(self, name):
        if name == "01_Jobs_Batches":
            return self.jobs
        raise gspread.WorksheetNotFound(name)


class FakeGemini:
    def __init__(self):
        self.calls = []
        self.qc_calls = []

    def generate_image(self, prompt, aspect_ratio, image_size,
                       reference_images, model_override="",
                       transparency_background="green",
                       pad_first_reference_to_aspect=""):
        self.calls.append((prompt, aspect_ratio, image_size, reference_images,
                           model_override, transparency_background,
                           pad_first_reference_to_aspect))
        return Image.new("RGB", (1024, 1024), (90, 120, 150))

    def qc_restoration(self, source_path, restored_path, prompt, style, job):
        with Image.open(restored_path) as image:
            self.qc_calls.append((source_path, restored_path, image.size))
        return {"score": 92, "decision": "accept", "reason": "treu"}


class ImageRestoreTests(unittest.TestCase):
    def test_ratio_and_maximum_size_are_derived_from_model(self):
        self.assertEqual(closest_supported_aspect_ratio(1920, 1080), "16:9")
        self.assertEqual(closest_supported_aspect_ratio(1080, 1920), "9:16")
        self.assertEqual(restoration_model_size("gemini-3.1-flash-lite-image"),
                         "1K")
        self.assertEqual(restoration_model_size(
            "gemini-3.1-flash-lite-image", "4K"), "1K")
        self.assertEqual(restoration_model_size(
            "gemini-3.1-flash-image", "1K"), "1K")
        self.assertEqual(restoration_model_size(
            "gemini-3.1-flash-image", "2K"), "2K")
        self.assertEqual(restoration_model_size(
            "gemini-3.1-flash-image", "4K"), "4K")
        self.assertEqual(restoration_model_size(
            "gemini-3.1-flash-image", "invalid"), "1K")

    def test_output_keeps_complete_image_and_adds_padding_instead_of_cropping(self):
        source = Image.new("RGB", (1024, 1536), (20, 30, 40))
        source.putpixel((0, 0), (255, 0, 0))
        source.putpixel((1023, 1535), (0, 0, 255))
        restored = pad_to_source_aspect_ratio(
            source, 277, 429, background="white",
            source_has_transparency=True)
        self.assertEqual(restored.size, (1024, 1586))
        top = (restored.height - source.height) // 2
        self.assertEqual(restored.getpixel((0, top)), (255, 0, 0))
        self.assertEqual(restored.getpixel((1023, top + 1535)), (0, 0, 255))
        self.assertEqual(restored.getpixel((0, 0)), (255, 255, 255))

    def test_transparency_uses_green_or_white_instead_of_black(self):
        transparent = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
        transparent.putpixel((1, 1), (200, 10, 20, 255))
        green = flatten_transparency(transparent, "green")
        white = flatten_transparency(transparent, "white")
        self.assertEqual(green.getpixel((0, 0)), (0, 255, 0))
        self.assertEqual(white.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(green.getpixel((1, 1)), (200, 10, 20))

    def test_source_scan_is_recursive_and_preserves_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            (source / "unterordner").mkdir(parents=True)
            Image.new("RGB", (640, 360)).save(source / "a.jpg")
            Image.new("RGBA", (333, 500), (0, 0, 0, 0)).save(
                source / "unterordner" / "b.png")
            output = source / "results"
            output.mkdir()
            Image.new("RGB", (10, 10)).save(output / "ignore.png")

            items = prepare_restoration_items(
                make_job("input", "input/results"), make_config(root))

            self.assertEqual(len(items), 2)
            self.assertEqual(items[0].source_relative_path, "a.jpg")
            self.assertEqual((items[0].source_width, items[0].source_height),
                             (640, 360))
            self.assertEqual(items[1].source_relative_path,
                             "unterordner/b.png")
            self.assertTrue(items[1].source_has_transparency)

    def test_prompt_uses_default_or_custom_instruction(self):
        from generate_images import ContentItem

        content = ContentItem(
            item_id="I", job_id="J", content_type="image_restore",
            title="bild.jpg", source_text_or_topic="restore",
            source_image_path="/tmp/bild.jpg", source_width=800,
            source_height=600,
        )
        default_prompt = render_restoration_prompt(
            original_image_style(), content, make_job("in", "out"))
        self.assertIn("Do not add, remove", default_prompt)
        self.assertIn("exact source aspect ratio 800:600", default_prompt)
        self.assertIn("must not limit the output resolution", default_prompt)

        custom_job = make_job("in", "out",
                              restore_prompt="Remove the cable, keep everything else.")
        custom_prompt = render_restoration_prompt(
            original_image_style(), content, custom_job)
        self.assertIn("Remove the cable", custom_prompt)
        self.assertNotIn("Do not add, remove", custom_prompt)

    def test_pipeline_writes_max_resolution_at_exact_ratio_and_preserves_subfolders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input" / "set_a"
            source.mkdir(parents=True)
            Image.new("RGB", (321, 197), (20, 30, 40)).save(source / "foto.jpg")

            workbook = FakeWorkbook()
            gemini = FakeGemini()
            result = run_job(
                workbook=workbook,
                config=make_config(root),
                gemini=gemini,
                job=make_job("input", "output"),
                styles={},
                templates={},
                content_items=[],
            )

            selected = root / "output" / "selected" / "set_a" / "foto_RESTORED.jpg"
            self.assertTrue(selected.exists())
            with Image.open(selected) as image:
                self.assertEqual(image.size, (1669, 1024))
                self.assertAlmostEqual(image.width / image.height,
                                       321 / 197, places=3)
            self.assertEqual(gemini.calls[0][1], "3:2")
            self.assertEqual(gemini.calls[0][2], "1K")
            self.assertEqual(gemini.calls[0][4],
                             "gemini-3.1-flash-lite-image")
            self.assertEqual(gemini.calls[0][5], "green")
            self.assertEqual(gemini.calls[0][6], "3:2")
            self.assertEqual(gemini.qc_calls[0][2], (1669, 1024))
            self.assertEqual(workbook.jobs.status, "done")
            self.assertEqual(result["items_processed"], 1)

            metadata = root / "output" / "metadata" / "set_a" / "foto.json"
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_dimensions"], [321, 197])
            self.assertEqual(payload["final_dimensions"], [1669, 1024])
            self.assertEqual(payload["restoration_model"],
                             "gemini-3.1-flash-lite-image")
            self.assertEqual(payload["requested_max_image_size"], "1K")
            self.assertEqual(payload["aspect_fit_mode"], "contain_with_padding")

    def test_pipeline_passes_selected_flash_resolution_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input"
            source.mkdir()
            Image.new("RGB", (400, 300)).save(source / "bild.png")
            gemini = FakeGemini()

            run_job(
                workbook=FakeWorkbook(),
                config=make_config(root),
                gemini=gemini,
                job=make_job(
                    "input", "output",
                    restore_model="gemini-3.1-flash-image",
                    restore_max_image_size="2K",
                    qc_enabled=False,
                ),
                styles={}, templates={}, content_items=[],
            )

            self.assertEqual(gemini.calls[0][4], "gemini-3.1-flash-image")
            self.assertEqual(gemini.calls[0][2], "2K")


if __name__ == "__main__":
    unittest.main()
