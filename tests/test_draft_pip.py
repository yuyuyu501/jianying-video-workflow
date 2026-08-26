import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_draft_pip.py"


def load_module():
    spec = importlib.util.spec_from_file_location("validate_draft_pip", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pip = load_module()


class DraftPipTests(unittest.TestCase):
    def document(self):
        return {
            "materials": {
                "videos": [{"id": "visual", "path": "C:/approved.visual.mp4"}],
                "masks": [{"id": "circle-mask", "name": "圆形"}],
            },
            "tracks": [
                {"type": "video", "name": "B_Roll", "segments": [{"target_timerange": {"start": 2_000_000, "duration": 2_000_000}}]},
                {"type": "video", "name": "SpeakerPiP", "segments": [{
                    "material_id": "visual", "volume": 0.0, "extra_material_refs": ["circle-mask"],
                    "target_timerange": {"start": 2_000_000, "duration": 2_000_000},
                    "clip": {"scale": {"x": 0.34, "y": 0.34}, "transform": {"x": 0.56, "y": 0.53}},
                }]},
            ],
        }

    def test_accepts_circular_silent_pip_over_broll(self):
        report = pip.validate(self.document(), require_pip=True)
        self.assertEqual(report["status"], "passed", report["errors"])

    def test_rejects_missing_circle_or_audio(self):
        document = self.document()
        document["tracks"][1]["segments"][0]["volume"] = 1.0
        document["tracks"][1]["segments"][0]["extra_material_refs"] = []
        report = pip.validate(document, require_pip=True)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("muted" in error for error in report["errors"]))
        self.assertTrue(any("circular" in error for error in report["errors"]))
