import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_pip_faces.py"


def load_module():
    spec = importlib.util.spec_from_file_location("analyze_pip_faces", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pip = load_module()


class PipPlanningTests(unittest.TestCase):
    def test_head_envelope_adds_top_and_chin_margin(self):
        face = {"x": 400, "y": 580, "width": 300, "height": 300}
        head = pip.head_envelope(face, 1080, 1920)
        self.assertLess(head["y"], face["y"])
        self.assertGreater(head["y"] + head["height"], face["y"] + face["height"])

    def test_default_title_and_caption_bands_keep_pip_in_middle(self):
        zones = pip.protected_zones({"speaker_pip": {"enabled": True}})
        head = {"x": 350, "y": 500, "width": 400, "height": 520}
        candidates = pip.position_candidates(head, 1080, 1920, 0.6, zones)
        selected = pip.choose_safe_candidate(candidates, "upper_right")
        self.assertIsNotNone(selected)
        self.assertEqual(selected["position"], "middle_right")
        self.assertTrue(selected["safe"])

    def test_resolved_plan_disables_rejected_optional_pip(self):
        plan = {"segments": [{"speaker_pip": {"enabled": True}}]}
        review = {"pip_reviews": [{"segment_index": 1, "status": "rejected"}]}
        resolved = pip.resolve_broll_plan(plan, review)
        self.assertFalse(resolved["segments"][0]["speaker_pip"]["enabled"])

    def test_resolved_plan_uses_only_safe_approved_candidate(self):
        plan = {"segments": [{"speaker_pip": {"enabled": True}}]}
        review = {"pip_reviews": [{
            "segment_index": 1, "status": "approved",
            "selected_candidate": {"position": "middle_left", "safe": True},
        }]}
        resolved = pip.resolve_broll_plan(plan, review)
        self.assertTrue(resolved["segments"][0]["speaker_pip"]["enabled"])
        self.assertEqual(resolved["segments"][0]["speaker_pip"]["position"], "middle_left")

    def test_visual_review_can_move_to_a_safe_candidate(self):
        review = {"pip_reviews": [{
            "segment_index": 1,
            "status": "approved",
            "selected_candidate": {"position": "middle_left", "safe": True},
            "candidates": [
                {"position": "middle_left", "safe": True},
                {"position": "middle_right", "safe": True},
            ],
        }]}
        pip.apply_visual_decisions(
            review,
            {"segments": [{"speaker_pip": {"enabled": True}}]},
            {1: {"segment_index": 1, "status": "approved", "position": "middle_right", "reason": "Avoid text."}},
            "require",
        )
        finding = review["pip_reviews"][0]
        self.assertEqual(finding["status"], "approved")
        self.assertEqual(finding["selected_candidate"]["position"], "middle_right")

    def test_missing_required_visual_review_rejects_pip(self):
        review = {"pip_reviews": [{
            "segment_index": 1,
            "status": "approved",
            "selected_candidate": {"position": "middle_left", "safe": True},
            "candidates": [{"position": "middle_left", "safe": True}],
        }]}
        pip.apply_visual_decisions(review, {"segments": []}, {}, "require")
        self.assertEqual(review["pip_reviews"][0]["status"], "rejected")
