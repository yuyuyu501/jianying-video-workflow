import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "caption_presentation.py"


def load_module():
    spec = importlib.util.spec_from_file_location("caption_presentation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


presentation = load_module()


class CaptionPresentationTests(unittest.TestCase):
    def test_plan_uses_high_frequency_emphasis_and_varies_position(self):
        entries = [{"text": f"普通字幕{index}"} for index in range(8)]
        plan = presentation.plan_caption_styles(entries, available_effect_ids=("flower-a", "flower-b"))
        self.assertIn("lower_center", {item["position"] for item in plan})
        self.assertIn("mid_low", {item["position"] for item in plan})
        self.assertEqual(sum(item["variant"] == "flower" for item in plan), 2)
        self.assertEqual(next(item for item in plan if item["variant"] == "flower")["flower_effect_id"], "flower-a")
        self.assertNotIn("base_bottom", {item["variant"] for item in plan})

    def test_alert_uses_middle_emphasis(self):
        plan = presentation.plan_caption_styles([{"text": "立刻拨打120"}])
        self.assertEqual(plan[0]["variant"], "alert_impact")
        self.assertEqual(plan[0]["position"], "mid_low")

    def test_broll_with_pip_uses_lower_caption_zone(self):
        entries = [{"start": 2.0, "end": 3.0, "text": "立刻处理"}]
        broll = [{"start": 1.5, "duration": 3.0, "speaker_pip": {"enabled": True}}]
        plan = presentation.plan_caption_styles(entries, broll_segments=broll)
        self.assertEqual(plan[0]["visual_context"], "broll_with_pip")
        self.assertEqual(plan[0]["position"], "lower_center")

    def test_visual_layout_review_overrides_default_position(self):
        entries = [{"start": 2.0, "end": 3.0, "text": "画面分析决定位置"}]
        layout_review = {"layouts": [{
            "index": 0, "visual_context": "talking_head", "caption_zone": "mid_low", "transform_y": -0.34,
        }]}
        plan = presentation.plan_caption_styles(entries, layout_review=layout_review)
        self.assertEqual(plan[0]["position"], "mid_low")
        self.assertEqual(plan[0]["transform_y"], -0.34)
