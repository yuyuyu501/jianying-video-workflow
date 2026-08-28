import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "jianying-asset-director" / "scripts" / "av_event_timeline.py"
spec = importlib.util.spec_from_file_location("av_event_timeline", SCRIPT)
timeline = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(timeline)


class AvEventTimelineTests(unittest.TestCase):
    def beats(self):
        return [
            {"beat_id": "one", "start": 0.0, "end": 4.0, "purpose": "warning", "spoken_text": "这是重点"},
            {"beat_id": "two", "start": 4.0, "end": 8.0, "purpose": "chapter", "spoken_text": "下一部分"},
        ]

    def test_merges_caption_broll_sticker_and_effect_events(self):
        result = timeline.build(
            self.beats(),
            {"cues": [{"index": 0, "start": 1.0, "text": "重点", "presentation": "flower", "role": "warning"}]},
            {"segments": [{"start": 2.0, "duration": 1.0, "purpose": "示意素材"}]},
            None,
            {"visual_effects": [{"start": 5.0, "name": "扫描"}], "character_effects": []},
            {"stickers": [{"start": 6.0, "name": "箭头"}]},
        )
        event_types = {item["event_type"] for item in result["events"]}
        self.assertTrue({"flower_text_pop", "broll_transition", "scene_effect_onset", "sticker_entrance"} <= event_types)
        self.assertEqual(result["timestamp_basis"], "rendered_rough_cut_output")
        self.assertEqual([item["sequence"] for item in result["events"]], list(range(1, result["event_count"] + 1)))

    def test_prefers_selected_asset_event_over_treatment_duplicate(self):
        result = timeline.build(
            self.beats(), None, None,
            {"treatments": [{"beat_id": "two", "primary_treatment": "scene_effect", "visual_evidence_time": 5.0}]},
            {"visual_effects": [{"start": 5.0, "name": "真实选择"}]},
        )
        events = [item for item in result["events"] if item["event_type"] == "scene_effect_onset" and item["time"] == 5.0]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "asset_plan")


if __name__ == "__main__":
    unittest.main()
