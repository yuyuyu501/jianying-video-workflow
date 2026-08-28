import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_draft_sfx.py"
spec = importlib.util.spec_from_file_location("validate_draft_sfx", SCRIPT)
validator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(validator)


class DraftSfxTests(unittest.TestCase):
    def fixture(self, local_path: Path, *, volume: float = 0.12) -> tuple[dict, dict]:
        document = {
            "materials": {"audios": [{"id": "mat", "path": str(local_path)}]},
            "tracks": [{"type": "audio", "name": "SFX", "segments": [{
                "material_id": "mat", "target_timerange": {"start": 1_000_000, "duration": 500_000}, "volume": volume,
            }]}],
        }
        plan = {"sound_effects": [{"asset_id": "asset123", "start": 1.0, "duration": 0.7, "volume": 0.12, "track": "SFX"}]}
        return document, plan

    def test_validates_frozen_local_sfx_timing_and_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset123_cue.mp3"
            path.write_bytes(b"audio")
            document, plan = self.fixture(path)
            report = validator.validate(document, plan, expected_count=1)
            self.assertEqual(report["status"], "passed", report["errors"])

    def test_rejects_missing_local_sfx_file(self):
        with tempfile.TemporaryDirectory() as directory:
            document, plan = self.fixture(Path(directory) / "asset123_missing.mp3")
            report = validator.validate(document, plan, expected_count=1)
            self.assertEqual(report["status"], "failed")

    def test_rejects_unapproved_sfx_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset123_cue.mp3"
            path.write_bytes(b"audio")
            document, plan = self.fixture(path, volume=1.0)
            report = validator.validate(document, plan, expected_count=1)
            self.assertEqual(report["status"], "failed")

    def test_event_mode_requires_event_linkage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset123_cue.mp3"
            path.write_bytes(b"audio")
            document, plan = self.fixture(path)
            plan["sfx_timing_plan"] = {"source_mode": "av_events"}
            report = validator.validate(document, plan, expected_count=1)
            self.assertEqual(report["status"], "failed")
            plan["sound_effects"][0].update({
                "event_id": "caption_entrance:cue-0001",
                "linked_event_id": "caption_entrance:cue-0001",
                "intensity_tier": "light",
            })
            report = validator.validate(document, plan, expected_count=1)
            self.assertEqual(report["status"], "passed", report["errors"])


if __name__ == "__main__":
    unittest.main()
