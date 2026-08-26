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
    def test_plan_varies_position_and_keeps_flower_sparse(self):
        entries = [{"text": f"普通字幕{index}"} for index in range(8)]
        plan = presentation.plan_caption_styles(entries, available_effect_ids=("flower-a", "flower-b"))
        self.assertIn("bottom", {item["position"] for item in plan})
        self.assertIn("middle", {item["position"] for item in plan})
        self.assertEqual(sum(item["variant"] == "flower_bottom" for item in plan), 1)
        self.assertEqual(next(item for item in plan if item["variant"] == "flower_bottom")["flower_effect_id"], "flower-a")

    def test_alert_uses_middle_emphasis(self):
        plan = presentation.plan_caption_styles([{"text": "立刻拨打120"}])
        self.assertEqual(plan[0]["variant"], "alert_middle")
        self.assertEqual(plan[0]["position"], "middle")

    def test_all_alerts_still_have_a_safe_relief_variant(self):
        plan = presentation.plan_caption_styles([{"text": "立刻处理"}, {"text": "立刻拨打120"}])
        self.assertEqual({item["position"] for item in plan}, {"bottom", "middle"})
        self.assertEqual({item["variant"] for item in plan}, {"alert_middle", "base_bottom"})
