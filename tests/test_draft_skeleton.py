import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_draft_skeleton.py"


def load_module():
    spec = importlib.util.spec_from_file_location("validate_draft_skeleton", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


skeleton = load_module()


def track(kind, name, *, muted=False):
    return {"type": kind, "name": name, "attribute": 1 if muted else 0, "segments": []}


class DraftSkeletonTests(unittest.TestCase):
    def valid_document(self):
        return {"tracks": [
            track("video", "MainVisual"),
            track("video", "B_Roll", muted=True),
            track("video", "SpeakerPiP"),
            track("audio", "Narration"),
            track("audio", "SFX"),
            track("effect", "Effects"),
            track("effect", "CharacterEffects"),
            track("text", "Subtitles"),
        ]}

    def test_accepts_empty_named_track_skeleton(self):
        report = skeleton.validate(self.valid_document())
        self.assertEqual(report["status"], "passed")

    def test_rejects_unmuted_broll(self):
        document = self.valid_document()
        document["tracks"][1]["attribute"] = 0
        report = skeleton.validate(document)
        self.assertEqual(report["status"], "failed")
        self.assertIn("B_Roll must be explicitly muted before source material is added", report["errors"])

    def test_rejects_wrong_type_or_materialized_track(self):
        document = self.valid_document()
        document["tracks"][6]["type"] = "video"
        document["tracks"][7]["segments"] = [{"material_id": "caption"}]
        report = skeleton.validate(document)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("CharacterEffects" in error for error in report["errors"]))
        self.assertTrue(any("Subtitles" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
