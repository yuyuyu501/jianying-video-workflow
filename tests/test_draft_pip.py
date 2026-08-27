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

    def test_visual_review_requires_face_centered_mask(self):
        document = self.document()
        document["materials"]["masks"][0]["config"] = {"centerX": 0.0, "centerY": -0.2, "height": 0.5}
        review = {"status": "succeeded", "pip_reviews": [{
            "final_start": 2.0, "face_center_x": 0.0, "face_center_y": -0.2, "mask_size": 0.5,
            "face_fill_ratio": 0.52, "head_envelope": {"x": 1},
            "selected_candidate": {"safe": True, "placement_transform_x": 0.56, "placement_transform_y": 0.53},
        }]}
        report = pip.validate(document, require_pip=True, pip_visual_review=review, require_visual_review=True)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("effective circular size" in error for error in report["errors"]))

    def test_visual_review_rejects_unanchored_or_torso_crop(self):
        document = self.document()
        document["materials"]["masks"][0]["config"] = {"centerX": 0.0, "centerY": -0.2, "height": 0.24}
        document["tracks"][1]["segments"][0]["clip"]["scale"] = {"x": 0.92, "y": 0.92}
        review = {"status": "succeeded", "pip_reviews": [{
            "final_start": 2.0, "face_center_x": 0.0, "face_center_y": -0.2, "mask_size": 0.24, "status": "approved",
            "face_fill_ratio": 0.72, "selected_candidate": {"safe": False, "placement_transform_x": 0.50, "placement_transform_y": 0.71},
        }]}
        report = pip.validate(document, require_pip=True, pip_visual_review=review, require_visual_review=True)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("complete-head" in error for error in report["errors"]))
        self.assertTrue(any("anchored" in error for error in report["errors"]))

    def test_baked_head_crop_uses_square_material_and_centered_mask(self):
        document = self.document()
        document["materials"]["videos"][0] = {
            "id": "visual",
            "path": "C:/pip-head.mp4",
            "width": 720,
            "height": 720,
        }
        document["materials"]["masks"][0]["config"] = {"centerX": 0.0, "centerY": 0.0, "height": 0.90}
        document["tracks"][1]["segments"][0]["material_id"] = "visual"
        document["tracks"][1]["segments"][0]["clip"]["scale"] = {"x": 0.2444, "y": 0.2444}
        review = {"status": "succeeded", "pip_reviews": [{
            "final_start": 2.0, "status": "approved", "crop_mode": "baked_head", "crop_video": "C:/pip-head.mp4",
            "face_center_x": 0.0, "face_center_y": -0.2, "mask_size": 0.5,
            "face_fill_ratio": 0.52, "head_envelope": {"x": 1},
            "selected_candidate": {"safe": True, "placement_transform_x": 0.56, "placement_transform_y": 0.53},
        }]}
        report = pip.validate(document, expected_visual="C:/approved.visual.mp4", require_pip=True,
                              pip_visual_review=review, require_visual_review=True)
        self.assertEqual(report["status"], "passed", report["errors"])
