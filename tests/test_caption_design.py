import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("caption_design_director", SCRIPTS / "caption_design_director.py")
design = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(design)


class CaptionDesignTests(unittest.TestCase):
    def entries(self):
        return [
            {"start": 0.0, "end": 1.5, "text": "普通介绍"},
            {"start": 1.5, "end": 3.0, "text": "第一件事立刻坐下"},
            {"start": 3.0, "end": 4.5, "text": "疼痛超过15分钟"},
            {"start": 4.5, "end": 6.0, "text": "正确做法是拨打120"},
        ]

    def layout(self):
        return {"layouts": [
            {"index": index, "caption_zone": "lower_center", "transform_y": -0.54, "visual_context": "talking_head", "representative_frame": f"cue_{index}.jpg"}
            for index in range(4)
        ]}

    def approve(self, plan):
        plan["ai_review"]["status"] = "approved"
        for cue in plan["cues"]:
            cue["review_status"] = "approved"
            cue["visual_evidence"] = "Face is above the caption safe zone; no native text or PiP overlaps."
            cue["collision_review"]["status"] = "passed"
            for field in ("face", "speaker_pip", "native_text", "key_action", "platform_ui"):
                cue["collision_review"][field] = "clear"
        return plan

    def test_template_adds_semantic_roles_keywords_and_hierarchy(self):
        plan = design.create_template(self.entries(), self.layout())
        self.assertEqual(plan["cues"][1]["role"], "step_title")
        self.assertTrue(plan["cues"][1]["highlight"]["enabled"])
        self.assertTrue(plan["cues"][2]["keyword_spans"])
        self.assertTrue(plan["cues"][3]["card"]["enabled"])

    def test_validation_requires_pixel_review(self):
        plan = design.create_template(self.entries(), self.layout())
        report = design.validate(plan, self.entries())
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("representative-frame" in error for error in report["errors"]))

    def test_approved_semantic_plan_passes(self):
        plan = self.approve(design.create_template(self.entries(), self.layout()))
        report = design.validate(plan, self.entries())
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(report["highlight_segments"], 1)
        self.assertEqual(report["card_segments"], 1)

    def test_validation_rejects_excessive_flower_rotation(self):
        plan = self.approve(design.create_template(self.entries(), self.layout()))
        for cue in plan["cues"]:
            cue["presentation"] = "flower"
        report = design.validate(plan, self.entries())
        self.assertEqual(report["status"], "failed")
        self.assertTrue(any("ratio is too high" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
