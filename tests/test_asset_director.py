import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "jianying-asset-director" / "scripts" / "asset_director.py"
spec = importlib.util.spec_from_file_location("asset_director", SCRIPT)
asset_director = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(asset_director)


TAXONOMY = {
    "version": 2,
    "selection": {
        "visual": {
            "candidate_limit": 2,
            "min_score": 2.0,
            "max_same_effect_per_plan": 1,
            "repeat_cooldown_seconds": 10,
            "allow_no_effect": True,
        },
        "sound": {
            "candidate_limit": 2,
            "min_score": 2.0,
            "max_same_effect_per_plan": 1,
            "repeat_cooldown_seconds": 10,
            "allow_no_effect": True,
        },
    },
    "style_profiles": {"medical_education": {"preferred": ["warning"], "forbidden": [], "max_visual_intensity": 1.0, "max_audio_intensity": 1.0}},
    "beat_purposes": {"warning": {"preferred": ["warning"], "forbidden": [], "max_duration": 1.0}},
}


def asset(asset_id, name, tags):
    return {
        "asset_id": asset_id,
        "name": name,
        "asset_type": "video_effect",
        "tags": tags,
        "intensity": 0.4,
        "resource_id": asset_id,
        "source_identifier": name,
    }


class AssetDirectorTests(unittest.TestCase):
    def write_json(self, directory, name, value):
        path = Path(directory) / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_plan_emits_shortlists_without_auto_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            catalog = self.write_json(directory, "catalog.json", {"assets": [asset("a", "故障A", ["warning"]), asset("b", "故障B", ["warning"]), asset("c", "故障C", ["warning"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [{"beat_id": "one", "start": 0, "end": 1, "purpose": "warning"}]})
            output = asset_director.plan(beats, catalog, taxonomy, "medical_education")
            self.assertEqual(output["visual_effects"], [])
            self.assertEqual(output["ai_review"]["status"], "pending")
            self.assertEqual([item["asset_id"] for item in output["beats"][0]["visual_candidates"]], ["a", "b"])

    def test_select_rejects_id_outside_shortlist_and_accepts_no_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            catalog = self.write_json(directory, "catalog.json", {"assets": [asset("a", "故障A", ["warning"]), asset("b", "故障B", ["warning"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [{"beat_id": "one", "start": 0, "end": 1, "purpose": "warning"}]})
            candidate_plan = asset_director.plan(beats, catalog, taxonomy, "medical_education")
            plan_path = self.write_json(directory, "plan.json", candidate_plan)
            invalid = self.write_json(directory, "invalid.json", {"selections": [{"beat_id": "one", "visual_asset_id": "invented", "sound_asset_id": None}]})
            with self.assertRaisesRegex(ValueError, "not in shortlist"):
                asset_director.apply_selections(plan_path, invalid, taxonomy)
            valid = self.write_json(directory, "valid.json", {"selections": [{"beat_id": "one", "visual_asset_id": None, "sound_asset_id": None}]})
            selected = asset_director.apply_selections(plan_path, valid, taxonomy)
            self.assertEqual(selected["visual_effects"], [])
            self.assertEqual(selected["ai_review"]["status"], "approved")

    def test_select_enforces_repeat_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            catalog = self.write_json(directory, "catalog.json", {"assets": [asset("a", "故障A", ["warning"]), asset("b", "故障B", ["warning"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [
                {"beat_id": "one", "start": 0, "end": 1, "purpose": "warning"},
                {"beat_id": "two", "start": 5, "end": 6, "purpose": "warning"},
            ]})
            plan_path = self.write_json(directory, "plan.json", asset_director.plan(beats, catalog, taxonomy, "medical_education"))
            selections = self.write_json(directory, "selections.json", {"selections": [
                {"beat_id": "one", "visual_asset_id": "a", "sound_asset_id": None},
                {"beat_id": "two", "visual_asset_id": "a", "sound_asset_id": None},
            ]})
            with self.assertRaisesRegex(ValueError, "cooldown"):
                asset_director.apply_selections(plan_path, selections, taxonomy)

    def test_selected_plan_validates_real_resource_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            catalog = self.write_json(directory, "catalog.json", {"assets": [asset("a", "故障A", ["warning"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [{"beat_id": "one", "start": 0, "end": 1, "purpose": "warning"}]})
            candidate_plan = self.write_json(directory, "candidate-plan.json", asset_director.plan(beats, catalog, taxonomy, "medical_education"))
            selections = self.write_json(directory, "selections.json", {"selections": [{"beat_id": "one", "visual_asset_id": "a", "sound_asset_id": None}]})
            selected_plan = self.write_json(directory, "selected-plan.json", asset_director.apply_selections(candidate_plan, selections, taxonomy))
            report = asset_director.validate(selected_plan, None, catalog, taxonomy)
            self.assertTrue(report["valid"], report["problems"])

    def test_shortlist_covers_purpose_tag_beyond_top_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = dict(TAXONOMY)
            taxonomy["beat_purposes"] = {"time_pressure": {"preferred": ["timer"], "forbidden": [], "max_duration": 1.0}}
            taxonomy["selection"] = {"visual": {"candidate_limit": 2, "min_score": 2.0, "max_same_effect_per_plan": 1, "repeat_cooldown_seconds": 10, "allow_no_effect": True}, "sound": TAXONOMY["selection"]["sound"]}
            taxonomy_path = self.write_json(directory, "taxonomy.json", taxonomy)
            catalog = self.write_json(directory, "catalog.json", {"assets": [asset("a", "冲击A", ["warning"]), asset("b", "冲击B", ["warning"]), asset("timer", "倒计时", ["timer"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [{"beat_id": "one", "start": 0, "end": 1, "purpose": "time_pressure"}]})
            output = asset_director.plan(beats, catalog, taxonomy_path, "medical_education")
            self.assertIn("timer", [item["asset_id"] for item in output["beats"][0]["visual_candidates"]])


if __name__ == "__main__":
    unittest.main()
