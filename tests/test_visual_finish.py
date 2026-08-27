import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


director = load("visual_finish_director", "visual_finish_director.py")
filter_validator = load("validate_draft_filters", "validate_draft_filters.py")
sticker_validator = load("validate_draft_stickers", "validate_draft_stickers.py")


class VisualFinishTests(unittest.TestCase):
    def assets(self):
        return {
            "filters": [{"name": "亮肤", "resource_id": "filter-real"}],
            "stickers": [{"name": "提示", "resource_id": "sticker-real"}],
        }

    def filter_plan(self):
        return {
            "timeline_duration": 10.0,
            "candidates": [{"name": "亮肤", "resource_id": "filter-real"}],
            "filters": [{
                "name": "亮肤", "resource_id": "filter-real", "start": 0.0,
                "duration": 10.0, "intensity": 35.0, "status": "approved",
                "purpose": "skin-tone brightening", "visual_evidence_time": 0.5,
                "visible_person": True,
                "visual_evidence": "Face is visible and skin tone is slightly dull at 0.5s.",
            }],
            "ai_review": {"status": "approved", "reviewed_frames": [0.5]},
        }

    def sticker_plan(self):
        return {
            "timeline_duration": 10.0,
            "candidates": [{"name": "提示", "resource_id": "sticker-real"}],
            "stickers": [{
                "resource_id": "sticker-real", "start": 2.0, "duration": 1.0,
                "scale": 0.4, "transform_x": 0.6, "transform_y": 0.2,
                "rotation": 5.0, "status": "approved",
                "beat_id": "warning-1", "reason": "Reinforce the spoken warning", "visual_evidence_time": 2.2,
                "visual_evidence": "The warning sentence begins at 2.0s.",
                "collision_review": {"status": "passed", "evidence": "Right side is clear of face, captions, PiP, and source text."},
            }],
            "ai_review": {"status": "approved", "reviewed_frames": [2.2]},
        }

    def test_plan_validation_requires_real_resources_and_collision_review(self):
        self.assertTrue(director.validate_filter_plan(self.filter_plan(), self.assets())["valid"])
        self.assertTrue(director.validate_sticker_plan(self.sticker_plan(), self.assets())["valid"])
        invalid = self.sticker_plan()
        invalid["stickers"][0]["resource_id"] = "guessed-id"
        invalid["stickers"][0]["collision_review"] = {"status": "pending"}
        report = director.validate_sticker_plan(invalid, self.assets())
        self.assertFalse(report["valid"])
        self.assertTrue(any("not verified" in error for error in report["errors"]))
        self.assertTrue(any("collision review" in error for error in report["errors"]))

    def test_empty_plans_need_specific_approved_skip_reason(self):
        valid = {"filters": [], "skip_reason": "No talking-head face is visible after B-roll replacement.", "ai_review": {"status": "approved", "reviewed_frames": [0.0]}}
        self.assertTrue(director.validate_filter_plan(valid, self.assets())["valid"])
        invalid = {"stickers": [], "skip_reason": "", "ai_review": {"status": "approved"}}
        self.assertFalse(director.validate_sticker_plan(invalid, self.assets())["valid"])

    def test_catalog_extracts_only_real_sticker_ids_from_drafts(self):
        with tempfile.TemporaryDirectory() as temporary:
            draft = Path(temporary) / "source-draft"
            draft.mkdir()
            (draft / "draft_info.json").write_text(json.dumps({
                "materials": {"stickers": [{"resource_id": "sticker-real", "name": "提示", "type": "sticker"}]}
            }, ensure_ascii=False), encoding="utf-8")
            items = director.sticker_catalog(Path(temporary))
        self.assertEqual(items[0]["resource_id"], "sticker-real")
        self.assertEqual(items[0]["name"], "提示")

    def test_saved_filter_and_sticker_tracks_match_plans(self):
        filter_plan = self.filter_plan()
        sticker_plan = self.sticker_plan()
        document = {
            "materials": {
                "effects": [{"id": "filter-material", "type": "filter", "name": "亮肤", "resource_id": "filter-real", "value": 0.35}],
                "stickers": [{"id": "sticker-material", "type": "sticker", "resource_id": "sticker-real"}],
            },
            "tracks": [
                {"name": "Filters", "type": "filter", "segments": [{"material_id": "filter-material", "target_timerange": {"start": 0, "duration": 10_000_000}}]},
                {"name": "Stickers", "type": "sticker", "segments": [{
                    "material_id": "sticker-material", "target_timerange": {"start": 2_000_000, "duration": 1_000_000},
                    "clip": {"scale": {"x": 0.4, "y": 0.4}, "transform": {"x": 0.6, "y": 0.2}, "rotation": 5.0},
                }]},
            ],
        }
        self.assertEqual(filter_validator.validate(document, filter_plan, 1)["status"], "passed")
        self.assertEqual(sticker_validator.validate(document, sticker_plan, 1)["status"], "passed")

    def test_sticker_shortlist_is_bounded_and_content_ranked(self):
        stickers = [{"name": f"unused-{index}", "resource_id": str(index)} for index in range(30)]
        stickers.append({"name": "急救提示", "resource_id": "relevant"})
        shortlist = director.sticker_shortlist([{"text": "这是急救提示"}], stickers)
        self.assertEqual(len(shortlist), 24)
        self.assertEqual(shortlist[0]["resource_id"], "relevant")


if __name__ == "__main__":
    unittest.main()
