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

    def test_priority_beat_requires_frame_evidence_and_effect_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy_data = dict(TAXONOMY)
            taxonomy_data["selection"] = dict(TAXONOMY["selection"])
            taxonomy_data["selection"]["visual"] = {
                **TAXONOMY["selection"]["visual"],
                "minimum_selected": 1,
                "required_purposes": ["warning"],
                "require_visual_evidence": True,
                "require_no_effect_reason": True,
            }
            taxonomy = self.write_json(directory, "taxonomy.json", taxonomy_data)
            catalog = self.write_json(directory, "catalog.json", {"assets": [asset("a", "故障A", ["warning"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [{"beat_id": "one", "start": 10, "end": 11, "purpose": "warning"}]})
            plan_path = self.write_json(directory, "plan.json", asset_director.plan(beats, catalog, taxonomy, "medical_education"))
            missing = self.write_json(directory, "missing.json", {"selections": [{"beat_id": "one", "visual_asset_id": None, "sound_asset_id": None}]})
            with self.assertRaisesRegex(ValueError, "priority beat requires"):
                asset_director.apply_selections(plan_path, missing, taxonomy)
            selected = self.write_json(directory, "selected.json", {"selections": [{
                "beat_id": "one", "visual_asset_id": "a", "sound_asset_id": None,
                "visual_evidence_time": 10.5,
                "visual_evidence": "ECG warning graphic is visible and the lower subtitle zone is clear.",
            }]})
            result = asset_director.apply_selections(plan_path, selected, taxonomy)
            self.assertEqual(len(result["visual_effects"]), 1)

    def test_sound_effects_target_the_validated_sfx_track(self):
        candidate = {**asset("sound", "提示音", ["warning"]), "score": 3.0, "reasons": ["warning"]}
        taxonomy = TAXONOMY
        beat = {"beat_id": "one", "start": 0, "end": 1, "purpose": "warning"}
        selected = asset_director.selected_item(beat, candidate, "sound", taxonomy)
        self.assertEqual(selected["track"], "SFX")
        with self.assertRaisesRegex(ValueError, "must be 'SFX'"):
            asset_director.selected_item({**beat, "sound_track": "SFX_Accent"}, candidate, "sound", taxonomy)

    def test_sfx_timing_gate_exposes_candidates_only_for_approved_opportunities(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            sound = asset("sound", "提示音", ["warning"])
            sound["asset_type"] = "sound_effect"
            catalog = self.write_json(directory, "catalog.json", {"assets": [sound]})
            beats = self.write_json(directory, "beats.json", {"beats": [
                {"beat_id": "one", "start": 1, "end": 3, "purpose": "warning"},
                {"beat_id": "two", "start": 5, "end": 7, "purpose": "warning"},
            ]})
            sfx = self.write_json(directory, "sfx.json", {
                "style": "medical_education", "skip_reason": "",
                "ai_review": {"required": True, "status": "approved"},
                "opportunities": [
                    {"beat_id": "one", "use_sfx": True, "status": "approved", "cue_type": "warning", "trigger_time": 1.5, "suggested_duration": 0.5, "volume": 0.12, "evidence": "The warning phrase lands here.", "reason": "A brief cue reinforces this warning."},
                    {"beat_id": "two", "use_sfx": False, "status": "skipped", "reason": "The beat is already clear without an added sound."},
                ],
            })
            output = asset_director.plan(beats, catalog, taxonomy, "medical_education", sfx_opportunities_path=sfx)
            self.assertEqual([item["asset_id"] for item in output["beats"][0]["sound_candidates"]], ["sound"])
            self.assertEqual(output["beats"][1]["sound_candidates"], [])
            self.assertTrue(output["beats"][0]["sfx_opportunity"]["use_sfx"])

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
            "visual_effects": [{"asset_id": "scene", "resource_id": "scene", "source_identifier": "故障A", "start": 0, "duration": 0.5, "beat_id": "one", "layering_reason": "Reviewed complementary face and frame accents."}],
            "character_effects": [{"asset_id": "face", "resource_id": "face", "source_identifier": "高光人物", "start": 0, "duration": 0.5, "zone": "face_target", "beat_id": "one", "layering_reason": "Reviewed complementary face and frame accents."}],
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

    def test_editorial_treatment_satisfies_priority_beat_without_effect_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy_data = json.loads(json.dumps(TAXONOMY))
            taxonomy_data["selection"]["visual"]["required_purposes"] = ["warning"]
            taxonomy = self.write_json(directory, "taxonomy.json", taxonomy_data)
            catalog = self.write_json(directory, "catalog.json", {"assets": [asset("scene", "故障A", ["warning"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [{"beat_id": "one", "start": 0, "end": 1, "purpose": "warning"}]})
            treatments = self.write_json(directory, "treatments.json", {"treatments": [{
                "beat_id": "one", "primary_treatment": "chapter_title", "secondary_treatment": None,
                "review_status": "approved", "visual_evidence_time": 0.5,
                "visual_evidence": "The clean center frame has room for a chapter title.",
                "layering_reason": "", "character_intent": None,
            }]})
            output = asset_director.plan(beats, catalog, taxonomy, "medical_education", treatments)
            self.assertEqual(output["beats"][0]["visual_candidates"], [])
            selected = self.write_json(directory, "selected.json", {"selections": [{
                "beat_id": "one", "visual_asset_id": None, "sound_asset_id": None,
            }]})
            result = asset_director.apply_selections(self.write_json(directory, "plan.json", output), selected, taxonomy)
            self.assertEqual(result["visual_effects"], [])

    def test_selected_effect_preserves_evidence_and_ai_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            catalog = self.write_json(directory, "catalog.json", {"assets": [asset("scene", "故障A", ["warning"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [{"beat_id": "one", "start": 10, "end": 12, "purpose": "warning"}]})
            treatments = self.write_json(directory, "treatments.json", {"treatments": [{
                "beat_id": "one", "primary_treatment": "scene_effect", "secondary_treatment": None,
                "review_status": "approved", "visual_evidence_time": 10.8,
                "visual_evidence": "The warning phrase begins while the speaker remains unobscured.",
                "layering_reason": "", "character_intent": None,
            }]})
            candidate = asset_director.plan(beats, catalog, taxonomy, "medical_education", treatments)
            selections = self.write_json(directory, "selected.json", {"selections": [{
                "beat_id": "one", "visual_asset_id": "scene", "sound_asset_id": None,
                "visual_effect_start": 10.7, "visual_effect_duration": 0.6,
                "visual_evidence_time": 10.8,
                "visual_evidence": "The warning phrase begins while the speaker remains unobscured.",
            }]})
            result = asset_director.apply_selections(self.write_json(directory, "plan.json", candidate), selections, taxonomy)
            effect = result["visual_effects"][0]
            self.assertEqual(effect["start"], 10.7)
            self.assertEqual(effect["duration"], 0.6)
            self.assertIn("warning phrase", effect["evidence"])

    def test_character_candidates_require_face_specific_treatment_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            catalog = self.write_json(directory, "catalog.json", {"assets": [character_asset("face", "高光人物", ["person", "warning"])]})
            beats = self.write_json(directory, "beats.json", {"beats": [{"beat_id": "one", "start": 0, "end": 1, "purpose": "warning", "person_visible": True}]})
            invalid_treatments = self.write_json(directory, "invalid.json", {"treatments": [{
                "beat_id": "one", "primary_treatment": "character_effect", "secondary_treatment": None,
                "review_status": "approved", "visual_evidence_time": 0.5,
                "visual_evidence": "The speaker face is visible in the unobstructed center frame.",
                "layering_reason": "", "character_intent": None,
            }]})
            with self.assertRaisesRegex(ValueError, "face-specific intent"):
                asset_director.plan(beats, catalog, taxonomy, "medical_education", invalid_treatments)

    def test_scene_character_overlap_requires_reviewed_layering_reason(self):
        data = {
            "visual_effects": [{"start": 1, "duration": 1, "beat_id": "one"}],
            "character_effects": [{"start": 1.5, "duration": 1, "beat_id": "one"}],
        }
        self.assertIn("overlap", asset_director.effect_overlap_problems(data)[0])

    def test_approved_frame_grounded_character_treatment_confirms_visible_face(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy = self.write_json(directory, "taxonomy.json", TAXONOMY)
            catalog = self.write_json(directory, "catalog.json", {"assets": [
                character_asset("face", "人物高光", ["person", "highlight"]),
            ]})
            beats = self.write_json(directory, "beats.json", {"beats": [{
                "beat_id": "one", "start": 10, "end": 12, "purpose": "warning",
                "representative_frame": "frame.jpg",
            }]})
            treatments = self.write_json(directory, "treatments.json", {"treatments": [{
                "beat_id": "one", "primary_treatment": "character_effect", "secondary_treatment": None,
                "review_status": "approved", "visual_evidence_time": 10.8,
                "representative_frame": "frame.jpg",
                "visual_evidence": "The reviewed frame shows the speaker's complete unobstructed face.",
                "layering_reason": "", "character_intent": "emotion_emphasis",
            }]})
            output = asset_director.plan(beats, catalog, taxonomy, "medical_education", treatments)
            self.assertTrue(output["beats"][0]["character_effect_eligibility"]["eligible"])
            self.assertEqual([item["asset_id"] for item in output["beats"][0]["character_candidates"]], ["face"])

    def test_full_height_broll_overrides_approved_character_treatment(self):
        beat = {
            "beat_id": "one", "pip_zone": "center_full_height",
            "visual_treatment": {
                "primary_treatment": "character_effect", "secondary_treatment": None,
                "review_status": "approved", "character_intent": "emotion_emphasis",
                "representative_frame": "frame.jpg",
                "visual_evidence": "The reviewed frame shows the complete speaker face.",
            },
        }
        eligible, reason = asset_director.character_effect_eligibility(beat)
        self.assertFalse(eligible)
        self.assertIn("B-roll", reason)

    def test_medical_outro_shortlist_excludes_cartoon_character_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            taxonomy_data = json.loads(json.dumps(TAXONOMY))
            taxonomy_data["style_profiles"]["medical_education"]["forbidden"] = ["cartoon"]
            taxonomy_data["beat_purposes"]["outro"] = {
                "preferred": ["person", "scan"], "forbidden": ["cartoon"], "max_duration": 1.0,
            }
            taxonomy = self.write_json(directory, "taxonomy.json", taxonomy_data)
            catalog = self.write_json(directory, "catalog.json", {"assets": [
                character_asset("safe", "轮廓扫描", ["person", "scan"]),
                character_asset("cartoon", "可爱猪", ["person", "cartoon"]),
            ]})
            beats = self.write_json(directory, "beats.json", {"beats": [{
                "beat_id": "outro", "start": 10, "end": 12, "purpose": "outro", "person_visible": True,
            }]})
            output = asset_director.plan(beats, catalog, taxonomy, "medical_education")
            self.assertEqual([item["asset_id"] for item in output["beats"][0]["character_candidates"]], ["safe"])

    def test_effect_visual_review_is_required_after_materialization(self):
        data = {"visual_effects": [{"beat_id": "one", "asset_id": "scene"}], "character_effects": []}
        report = asset_director.effect_visual_review(data, None, None)
        self.assertEqual(report["status"], "effect_visual_review_required")
        self.assertFalse(report["approved"])


if __name__ == "__main__":
    unittest.main()
