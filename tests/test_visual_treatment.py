import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "jianying-asset-director" / "scripts" / "visual_treatment_director.py"
spec = importlib.util.spec_from_file_location("visual_treatment_director", SCRIPT)
director = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(director)


class VisualTreatmentTests(unittest.TestCase):
    def test_template_marks_existing_broll_without_auto_selecting_an_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            beats = root / "beats.json"
            broll = root / "broll.json"
            beats.write_text(json.dumps({"beats": [{"beat_id": "one", "start": 1, "end": 3, "purpose": "chapter"}]}), encoding="utf-8")
            broll.write_text(json.dumps({"segments": [{"start": 1.5, "duration": 1}]}), encoding="utf-8")
            result = director.template(beats, broll)
            self.assertIsNone(result["treatments"][0]["primary_treatment"])
            self.assertEqual(result["treatments"][0]["already_emphasized_by"], ["B_Roll"])

    def test_instructional_illustration_is_valid(self):
        beats = [{"beat_id": "one", "start": 0, "end": 2}]
        plan = {"treatments": [{
            "beat_id": "one", "primary_treatment": "instructional_illustration", "secondary_treatment": None,
            "review_status": "approved", "visual_evidence_time": 1,
            "visual_evidence": "The open right side can hold an instructional heart diagram.",
            "layering_reason": "", "character_intent": None,
        }]}
        self.assertTrue(director.validate_payload(plan, beats)["valid"])

    def test_priority_beat_cannot_use_none_as_its_primary_treatment(self):
        beats = [{"beat_id": "one", "start": 0, "end": 2, "purpose": "warning"}]
        plan = {"treatments": [{
            "beat_id": "one", "primary_treatment": "none", "secondary_treatment": None,
            "review_status": "approved", "visual_evidence_time": 1,
            "visual_evidence": "The warning phrase has no planned visual emphasis.",
            "layering_reason": "", "character_intent": None,
        }]}
        report = director.validate_payload(plan, beats)
        self.assertFalse(report["valid"])
        self.assertTrue(any("priority beat" in problem for problem in report["problems"]))

    def test_character_treatment_requires_intent_and_effect_stacking_requires_reason(self):
        beats = [{"beat_id": "one", "start": 0, "end": 2}]
        plan = {"treatments": [{
            "beat_id": "one", "primary_treatment": "scene_effect", "secondary_treatment": "character_effect",
            "review_status": "approved", "visual_evidence_time": 1,
            "visual_evidence": "The face is visible and the frame is otherwise clean.",
            "layering_reason": "", "character_intent": None,
        }]}
        report = director.validate_payload(plan, beats)
        self.assertFalse(report["valid"])
        self.assertTrue(any("layering" in problem for problem in report["problems"]))
        self.assertTrue(any("face-specific" in problem for problem in report["problems"]))


if __name__ == "__main__":
    unittest.main()
