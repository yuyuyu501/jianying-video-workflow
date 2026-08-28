import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "jianying-asset-director" / "scripts" / "sfx_timing_director.py"
spec = importlib.util.spec_from_file_location("sfx_timing_director", SCRIPT)
director = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(director)


TAXONOMY = {
    "selection": {"sound": {
        "max_per_minute": 2.0, "max_total": 4, "min_gap_seconds": 3.0,
        "max_same_cue_type_per_plan": 2, "min_volume": 0.05, "max_volume": 0.15,
        "default_volume": 0.12, "max_duration": 1.5, "opening_guard_seconds": 0.8,
    }},
    "style_profiles": {"medical_education": {}},
}


class SfxTimingTests(unittest.TestCase):
    def beats(self):
        return [
            {"beat_id": "one", "start": 1.0, "end": 5.0, "purpose": "warning"},
            {"beat_id": "two", "start": 8.0, "end": 12.0, "purpose": "chapter"},
        ]

    def approved(self):
        return {
            "style": "medical_education", "skip_reason": "",
            "ai_review": {"required": True, "status": "approved"},
            "opportunities": [
                {"beat_id": "one", "use_sfx": True, "status": "approved", "cue_type": "warning", "trigger_time": 2.0, "suggested_duration": 0.7, "volume": 0.12, "evidence": "The warning phrase lands at this exact point.", "reason": "A brief low cue reinforces the safety warning."},
                {"beat_id": "two", "use_sfx": False, "status": "skipped", "reason": "The chapter change is already clear from picture and caption."},
            ],
        }

    def test_approved_sparse_plan_passes(self):
        report = director.validate_payload(self.approved(), self.beats(), TAXONOMY)
        self.assertTrue(report["valid"], report["problems"])

    def test_rejects_cues_that_are_too_close(self):
        plan = self.approved()
        plan["opportunities"][1] = {"beat_id": "two", "use_sfx": True, "status": "approved", "cue_type": "chapter_transition", "trigger_time": 4.0, "suggested_duration": 0.5, "volume": 0.1, "evidence": "The B-roll changes at this exact frame boundary.", "reason": "A short whoosh supports the visible transition."}
        beats = self.beats()
        beats[1]["start"] = 3.5
        report = director.validate_payload(plan, beats, TAXONOMY)
        self.assertFalse(report["valid"])
        self.assertTrue(any("too close" in item for item in report["problems"]))

    def test_empty_plan_requires_explicit_skip_reason(self):
        plan = self.approved()
        for item in plan["opportunities"]:
            item.update({"use_sfx": False, "status": "skipped", "reason": "Narration and visual treatment already carry this beat clearly."})
        report = director.validate_payload(plan, self.beats(), TAXONOMY)
        self.assertFalse(report["valid"])
        plan["skip_reason"] = "No reviewed beat benefits from an additional sound effect without cluttering narration."
        self.assertTrue(director.validate_payload(plan, self.beats(), TAXONOMY)["valid"])

    def test_event_mode_supports_multiple_events_in_one_beat(self):
        taxonomy = json.loads(json.dumps(TAXONOMY))
        taxonomy["selection"]["sound"].update({
            "max_per_minute": 20.0,
            "min_gap_seconds": 0.2,
            "same_tier_min_gap_seconds": {"light": 0.2, "medium": 0.5, "strong": 2.0},
            "tier_max_per_minute": {"light": 20.0, "medium": 10.0, "strong": 4.0},
            "tier_max_total": {"light": 10, "medium": 10, "strong": 4},
            "tier_max_duration": {"light": 0.5, "medium": 0.9, "strong": 1.2},
            "tier_max_volume": {"light": 0.08, "medium": 0.12, "strong": 0.14},
            "sync_tolerance_seconds": 0.12,
        })
        beats = [{"beat_id": "one", "start": 1.0, "end": 7.0, "purpose": "warning"}]
        events = [
            {"event_id": "caption:one", "time": 2.0, "beat_id": "one"},
            {"event_id": "effect:one", "time": 4.0, "beat_id": "one"},
        ]
        plan = {
            "style": "medical_education", "source_mode": "av_events", "skip_reason": "",
            "ai_review": {"required": True, "status": "approved"},
            "opportunities": [
                {"event_id": "caption:one", "linked_event_id": "caption:one", "beat_id": "one", "use_sfx": True, "status": "approved", "cue_type": "caption_pop", "intensity_tier": "light", "trigger_time": 2.08, "suggested_duration": 0.25, "volume": 0.06, "evidence": "The caption visibly pops at this frame.", "reason": "A quiet micro cue reinforces the text entrance."},
                {"event_id": "effect:one", "linked_event_id": "effect:one", "beat_id": "one", "use_sfx": True, "status": "approved", "cue_type": "effect_sync", "intensity_tier": "medium", "trigger_time": 4.0, "suggested_duration": 0.55, "volume": 0.1, "evidence": "The selected scene effect begins at this frame.", "reason": "A restrained scan cue binds sound to the visual onset."},
            ],
        }
        report = director.validate_payload(plan, beats, taxonomy, events)
        self.assertTrue(report["valid"], report["problems"])
        self.assertEqual(report["selected_by_tier"], {"light": 1, "medium": 1})

    def test_event_mode_rejects_trigger_detached_from_visual_event(self):
        beats = [{"beat_id": "one", "start": 1.0, "end": 5.0, "purpose": "warning"}]
        events = [{"event_id": "caption:one", "time": 2.0, "beat_id": "one"}]
        plan = {
            "style": "medical_education", "source_mode": "av_events", "skip_reason": "",
            "ai_review": {"required": True, "status": "approved"},
            "opportunities": [{
                "event_id": "caption:one", "linked_event_id": "caption:one", "beat_id": "one",
                "use_sfx": True, "status": "approved", "cue_type": "caption_pop", "intensity_tier": "light",
                "trigger_time": 2.13, "suggested_duration": 0.25, "volume": 0.06,
                "evidence": "The caption visibly pops at this frame.",
                "reason": "A quiet micro cue reinforces the text entrance.",
            }],
        }
        report = director.validate_payload(plan, beats, TAXONOMY, events)
        self.assertFalse(report["valid"])
        self.assertTrue(any("detached" in item for item in report["problems"]))


if __name__ == "__main__":
    unittest.main()
