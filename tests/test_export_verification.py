import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_export.py"
spec = importlib.util.spec_from_file_location("verify_export", SCRIPT)
verify_export = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verify_export)


class ExportVerificationTests(unittest.TestCase):
    def test_caption_qc_rejects_single_character_tail(self):
        report = verify_export.validate_caption_entries([
            {"start": 0, "end": 1, "text": "完整字幕"},
            {"start": 1, "end": 2, "text": "缓"},
        ], 2)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["single_character_cues"], [2])

    def test_circle_geometry_uses_export_canvas_coordinates(self):
        circle = verify_export.circle_geometry({"speaker_pip": {
            "placement_transform_x": 0.5,
            "placement_transform_y": 0.0,
            "mask_size": 0.25,
        }}, 1080, 1920)
        self.assertEqual(circle["center_x"], 810.0)
        self.assertEqual(circle["center_y"], 960.0)
        self.assertEqual(circle["diameter"], 480.0)

    def test_head_outside_circle_fails(self):
        circle = {"center_x": 500, "center_y": 500, "diameter": 200}
        self.assertFalse(verify_export.inside_circle({"x": 420, "y": 420, "width": 180, "height": 180}, circle))


if __name__ == "__main__":
    unittest.main()
