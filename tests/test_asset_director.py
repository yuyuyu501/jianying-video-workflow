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


def character_asset(asset_id, name, tags):
    item = asset(asset_id, name, tags)
    item["asset_type"] = "character_effect"
    return item


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

    def test_character_effect_requires_visible_person_and_rejects_full_height_broll(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            catalog = self.write_json(directory, "catalog.json", {"assets": [
                asset("scene", "故障A", ["warning"]),
                character_asset("face", "高光人物", ["person", "highlight"]),
            ]})
            visible_beats = self.write_json(directory, "visible.json", {"beats": [
                {"beat_id": "one", "start": 0, "end": 1, "purpose": "warning", "person_visible": True},
            ]})
            visible_plan = asset_director.plan(visible_beats, catalog, taxonomy, "medical_education")
            self.assertEqual([item["asset_id"] for item in visible_plan["beats"][0]["character_candidates"]], ["face"])
            selected = self.write_json(directory, "selected.json", {
                "selections": [{"beat_id": "one", "visual_asset_id": None, "sound_asset_id": None, "character_asset_id": "face"}]
            })
            result = asset_director.apply_selections(self.write_json(directory, "plan.json", visible_plan), selected, taxonomy)
            self.assertEqual(result["character_effects"][0]["zone"], "face_target")

            broll_beats = self.write_json(directory, "broll.json", {"beats": [
                {"beat_id": "two", "start": 0, "end": 1, "purpose": "warning", "person_visible": True, "pip_zone": "center_full_height"},
            ]})
            broll_plan = asset_director.plan(broll_beats, catalog, taxonomy, "medical_education")
            self.assertEqual(broll_plan["beats"][0]["character_candidates"], [])
            invalid = self.write_json(directory, "invalid-character.json", {
                "selections": [{"beat_id": "two", "visual_asset_id": None, "sound_asset_id": None, "character_asset_id": "face"}]
            })
            with self.assertRaisesRegex(ValueError, "ineligible"):
                asset_director.apply_selections(self.write_json(directory, "broll-plan.json", broll_plan), invalid, taxonomy)

    def test_composition_qc_separates_scene_and_face_materials(self):
        data = {
            "beats": [{"beat_id": "one", "start": 0, "end": 1, "purpose": "warning", "person_visible": True}],
            "visual_effects": [{"asset_id": "scene", "resource_id": "scene", "source_identifier": "故障A", "start": 0, "duration": 0.5, "beat_id": "one"}],
            "character_effects": [{"asset_id": "face", "resource_id": "face", "source_identifier": "高光人物", "start": 0, "duration": 0.5, "zone": "face_target", "beat_id": "one"}],
        }
        document = {
            "materials": {"video_effects": [
                {"id": "scene-material", "resource_id": "scene", "type": "video_effect"},
                {"id": "face-material", "resource_id": "face", "type": "face_effect"},
            ]},
            "tracks": [
                {"type": "effect", "name": "Effects", "segments": [{"material_id": "scene-material"}]},
                {"type": "effect", "name": "CharacterEffects", "segments": [{"material_id": "face-material"}]},
            ],
        }
        report = asset_director.composition_qc(data, document)
        self.assertTrue(report["passed"], report["problems"])


if __name__ == "__main__":
    unittest.main()
