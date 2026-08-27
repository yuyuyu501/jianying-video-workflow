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
    def test_materialization_preserves_semantic_styles_without_index_rotation(self):
        plan = {"cues": [
            {"index": 0, "text": "普通字幕", "presentation": "base", "keyword_spans": []},
            {"index": 1, "text": "超过15分钟", "presentation": "keyword", "keyword_spans": [{"start": 2, "end": 6}]},
            {"index": 2, "text": "普通字幕", "presentation": "base", "keyword_spans": []},
        ]}
        resolved = presentation.materialization_plan(plan)
        self.assertEqual([item["presentation"] for item in resolved], ["base", "keyword", "base"])

    def test_unavailable_flower_falls_back_to_keyword(self):
        plan = {"cues": [{
            "index": 0, "text": "立刻拨打120", "presentation": "flower",
            "keyword_spans": [{"start": 0, "end": 2}],
        }]}
        resolved = presentation.materialization_plan(plan)
        self.assertEqual(resolved[0]["presentation"], "keyword")
        self.assertIn("flower_fallback", resolved[0])

    def test_available_flower_id_must_be_from_local_catalog(self):
        plan = {"cues": [{"index": 0, "text": "重点", "presentation": "flower", "keyword_spans": [], "flower_effect_id": "bad"}]}
        with self.assertRaises(ValueError):
            presentation.materialization_plan(plan, ("known",))


if __name__ == "__main__":
    unittest.main()
