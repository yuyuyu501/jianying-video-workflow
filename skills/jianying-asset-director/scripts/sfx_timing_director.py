#!/usr/bin/env python3
"""Prepare and validate reviewed SFX opportunities from AV events or legacy beats."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_TAXONOMY = Path(__file__).resolve().parents[1] / "references" / "asset_taxonomy.json"
ALLOWED_CUE_TYPES = {
    "semantic_emphasis", "chapter_transition", "visual_transition", "action_hit",
    "warning", "confirmation", "timer", "notification", "outro",
    "caption_pop", "keyword_hit", "card_entrance", "sticker_pop", "effect_sync",
}
ALLOWED_TIERS = {"light", "medium", "strong"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def beat_list(value: Any) -> list[dict[str, Any]]:
    beats = value.get("beats", value) if isinstance(value, dict) else value
    if not isinstance(beats, list) or not beats:
        raise ValueError("beats JSON must contain a non-empty beats array")
    result = []
    for index, raw in enumerate(beats, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"beat {index} must be an object")
        start, end = float(raw.get("start", -1)), float(raw.get("end", -1))
        if start < 0 or end <= start:
            raise ValueError(f"beat {index} has invalid start/end")
        item = dict(raw)
        item["beat_id"] = str(raw.get("beat_id", f"beat-{index:03d}"))
        item["start"], item["end"] = start, end
        result.append(item)
    return result


def event_list(value: Any) -> list[dict[str, Any]]:
    events = value.get("events", value) if isinstance(value, dict) else value
    if not isinstance(events, list) or not events:
        raise ValueError("AV event timeline must contain a non-empty events array")
    result, seen = [], set()
    for index, raw in enumerate(events, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"AV event {index} must be an object")
        event_id = str(raw.get("event_id", f"event-{index:04d}"))
        if event_id in seen:
            raise ValueError(f"duplicate AV event_id: {event_id}")
        seen.add(event_id)
        timestamp = float(raw.get("time", -1))
        if timestamp < 0:
            raise ValueError(f"AV event {event_id} has invalid time")
        item = dict(raw)
        item["event_id"], item["time"] = event_id, timestamp
        item["beat_id"] = str(item.get("beat_id", ""))
        result.append(item)
    return result


def sound_rules(taxonomy: dict[str, Any], style_name: str) -> dict[str, Any]:
    rules = {
        "max_per_minute": 5.0, "max_total": 24, "min_gap_seconds": 0.35,
        "same_tier_min_gap_seconds": {"light": 0.35, "medium": 1.25, "strong": 4.0},
        "tier_max_per_minute": {"light": 4.0, "medium": 2.0, "strong": 1.0},
        "tier_max_total": {"light": 18, "medium": 8, "strong": 4},
        "tier_max_duration": {"light": 0.5, "medium": 0.9, "strong": 1.2},
        "tier_max_volume": {"light": 0.08, "medium": 0.12, "strong": 0.14},
        "tier_default_duration": {"light": 0.25, "medium": 0.55, "strong": 0.8},
        "tier_default_volume": {"light": 0.06, "medium": 0.1, "strong": 0.12},
        "max_same_cue_type_per_plan": 6, "min_volume": 0.03, "max_volume": 0.18,
        "default_volume": 0.1, "max_duration": 1.5, "opening_guard_seconds": 0.8,
        "sync_tolerance_seconds": 0.12,
    }
    rules.update(taxonomy.get("selection", {}).get("sound", {}))
    style = taxonomy.get("style_profiles", {}).get(style_name, {})
    mapping = {
        "max_sfx_per_minute": "max_per_minute", "max_sound_effects": "max_total",
        "max_sfx_volume": "max_volume", "min_sfx_gap_seconds": "min_gap_seconds",
        "max_same_sfx_cue_type": "max_same_cue_type_per_plan",
        "sfx_tier_max_per_minute": "tier_max_per_minute", "sfx_tier_max_total": "tier_max_total",
        "sfx_tier_max_duration": "tier_max_duration", "sfx_tier_max_volume": "tier_max_volume",
        "sfx_tier_min_gap_seconds": "same_tier_min_gap_seconds",
        "sfx_sync_tolerance_seconds": "sync_tolerance_seconds",
    }
    for source, target in mapping.items():
        if source in style:
            rules[target] = style[source]
    return rules


def broll_boundaries(path: Path | None) -> list[float]:
    if path is None:
        return []
    payload = load_json(path)
    segments = payload.get("segments", payload) if isinstance(payload, dict) else payload
    if not isinstance(segments, list):
        raise ValueError("B-roll plan must contain a segments array")
    boundaries = []
    for segment in segments:
        if isinstance(segment, dict):
            start, duration = float(segment.get("start", -1)), float(segment.get("duration", 0))
            if start >= 0 and duration > 0:
                boundaries.extend([start, start + duration])
    return boundaries


def cue_for_event(event_type: str, purpose: str) -> str:
    return {
        "chapter_transition": "chapter_transition", "broll_transition": "visual_transition",
        "flower_text_pop": "caption_pop", "caption_entrance": "caption_pop",
        "keyword_hit": "keyword_hit", "caption_highlight": "keyword_hit",
        "card_entrance": "card_entrance", "sticker_entrance": "sticker_pop",
        "scene_effect_onset": "effect_sync", "character_effect_onset": "effect_sync",
        "action_hit": "action_hit", "outro": "outro",
    }.get(event_type, "warning" if purpose in {"warning", "time_pressure", "emergency_call"} else "semantic_emphasis")


def make_template(
    beats_path: Path, taxonomy_path: Path, style_name: str,
    broll_plan: Path | None = None, av_events_path: Path | None = None,
) -> dict[str, Any]:
    beats = beat_list(load_json(beats_path))
    rules = sound_rules(load_json(taxonomy_path), style_name)
    opportunities = []
    if av_events_path:
        events = event_list(load_json(av_events_path))
        for event in events:
            tier = str(event.get("suggested_intensity_tier", "medium"))
            if tier not in ALLOWED_TIERS:
                tier = "medium"
            opportunities.append({
                "event_id": event["event_id"], "linked_event_id": event["event_id"],
                "beat_id": event.get("beat_id", ""), "start": event["time"], "end": event["time"],
                "purpose": str(event.get("purpose", "general")), "spoken_text": str(event.get("spoken_text", "")),
                "event_type": str(event.get("event_type", "semantic_emphasis")),
                "event_source": str(event.get("source", "")), "event_label": str(event.get("label", "")),
                "event_evidence": str(event.get("evidence", "")), "intensity_tier": tier,
                "status": "pending_review", "use_sfx": None,
                "cue_type": cue_for_event(str(event.get("event_type", "")), str(event.get("purpose", "general"))),
                "trigger_time": event["time"], "suggested_duration": float(rules["tier_default_duration"][tier]),
                "volume": float(rules["tier_default_volume"][tier]), "evidence": "", "reason": "",
            })
        duration, source_mode = max(max(float(item["end"]) for item in beats), max(float(item["time"]) for item in events)), "av_events"
    else:
        boundaries = broll_boundaries(broll_plan)
        for beat in beats:
            opportunities.append({
                "event_id": f"beat:{beat['beat_id']}", "linked_event_id": f"beat:{beat['beat_id']}",
                "beat_id": beat["beat_id"], "start": beat["start"], "end": beat["end"],
                "purpose": str(beat.get("purpose", "general")), "spoken_text": str(beat.get("spoken_text", beat.get("text", ""))),
                "event_type": "semantic_emphasis", "event_source": "beats", "event_label": str(beat.get("purpose", "general")),
                "event_evidence": "", "detected_transition_times": [value for value in boundaries if beat["start"] - 0.15 <= value <= beat["end"] + 0.15],
                "intensity_tier": "strong" if beat.get("purpose") in {"warning", "time_pressure", "emergency_call"} else "medium",
                "status": "pending_review", "use_sfx": None, "cue_type": None, "trigger_time": None,
                "suggested_duration": None, "volume": None, "evidence": "", "reason": "",
            })
        duration, source_mode = max(item["end"] for item in beats), "legacy_beats"
    density_cap = max(0, math.ceil(duration / 60.0 * float(rules["max_per_minute"])))
    return {
        "version": 2, "style": style_name, "source_mode": source_mode, "timeline_duration": duration,
        "av_event_timeline": str(av_events_path.resolve()) if av_events_path else None,
        "rules": {**rules, "calculated_density_cap": min(int(rules["max_total"]), density_cap)},
        "opportunities": opportunities, "skip_reason": "",
        "ai_review": {"required": True, "status": "pending", "instructions": (
            "Review every audiovisual event after caption, card, B-roll, sticker, scene-effect, and character-effect planning. "
            "Use light micro-SFX for text/UI entrances, medium cues for transitions and effect onsets, and strong cues only for critical warnings. "
            "Bind every approved cue to linked_event_id; prefer no sound when the event is already overloaded."
        )},
    }


def validate_payload(
    payload: dict[str, Any], beats: list[dict[str, Any]], taxonomy: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    problems = []
    taxonomy = taxonomy or load_json(DEFAULT_TAXONOMY)
    rules = sound_rules(taxonomy, str(payload.get("style", "medical_education")))
    expected_beats = {str(item["beat_id"]): item for item in beats}
    expected_events = {str(item["event_id"]): item for item in events or []}
    items = payload.get("opportunities", [])
    if not isinstance(items, list):
        return {"valid": False, "problems": ["opportunities must be an array"]}
    if payload.get("ai_review", {}).get("required") is not True or payload.get("ai_review", {}).get("status") != "approved":
        problems.append("ai_review must be required and approved")
    event_mode, seen, selected = bool(expected_events) or payload.get("source_mode") == "av_events", set(), []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            problems.append(f"opportunity {index} must be an object")
            continue
        identity = str(item.get("event_id") or item.get("beat_id") or "")
        if not identity or identity in seen:
            problems.append(f"missing or duplicate SFX opportunity identity: {identity}")
            continue
        seen.add(identity)
        beat_id = str(item.get("beat_id", ""))
        if beat_id not in expected_beats:
            problems.append(f"unknown beat_id: {beat_id}")
            continue
        linked = str(item.get("linked_event_id", item.get("event_id", ""))) if event_mode else str(item.get("linked_event_id", f"beat:{beat_id}"))
        if event_mode and linked not in expected_events:
            problems.append(f"unknown linked_event_id: {linked}")
            continue
        anchor = float(expected_events[linked]["time"]) if event_mode else float(item.get("trigger_time", expected_beats[beat_id]["start"]))
        reason = str(item.get("reason", "")).strip()
        if item.get("use_sfx") is True:
            if item.get("status") != "approved":
                problems.append(f"selected SFX opportunity must be approved: {identity}")
            cue_type, tier = str(item.get("cue_type", "")), str(item.get("intensity_tier", "medium"))
            if cue_type not in ALLOWED_CUE_TYPES:
                problems.append(f"invalid cue_type for {identity}: {cue_type}")
            if tier not in ALLOWED_TIERS:
                problems.append(f"invalid intensity_tier for {identity}: {tier}")
                tier = "medium"
            try:
                trigger, duration, volume = float(item.get("trigger_time")), float(item.get("suggested_duration")), float(item.get("volume"))
            except (TypeError, ValueError):
                problems.append(f"selected SFX opportunity needs numeric timing, duration, and volume: {identity}")
                continue
            beat = expected_beats[beat_id]
            if not float(beat["start"]) - 0.05 <= trigger <= float(beat["end"]) + 0.05:
                problems.append(f"SFX trigger is outside beat: {identity}")
            if event_mode and abs(trigger - anchor) > float(rules["sync_tolerance_seconds"]):
                problems.append(f"SFX trigger is detached from linked audiovisual event: {identity}")
            max_duration = min(float(rules["max_duration"]), float(rules["tier_max_duration"][tier])) if event_mode else float(rules["max_duration"])
            max_volume = min(float(rules["max_volume"]), float(rules["tier_max_volume"][tier])) if event_mode else float(rules["max_volume"])
            if duration <= 0 or duration > max_duration:
                problems.append(f"SFX duration exceeds {'event-tier' if event_mode else 'legacy'} policy: {identity}")
            if not float(rules["min_volume"]) <= volume <= max_volume:
                problems.append(f"SFX volume exceeds narration-safe policy: {identity}")
            if trigger < float(rules["opening_guard_seconds"]) and cue_type not in {"semantic_emphasis", "caption_pop", "keyword_hit"}:
                problems.append(f"opening SFX lacks an intentional hook classification: {identity}")
            if len(str(item.get("evidence", "")).strip()) < 8 or len(reason) < 8:
                problems.append(f"SFX opportunity lacks concrete evidence or reason: {identity}")
            selected.append({**item, "linked_event_id": linked, "intensity_tier": tier})
        elif item.get("use_sfx") is False:
            if item.get("status") != "skipped" or len(reason) < 8:
                problems.append(f"skipped SFX opportunity needs status=skipped and a concrete reason: {identity}")
        else:
            problems.append(f"SFX opportunity remains undecided: {identity}")
    expected_ids = set(expected_events) if event_mode else set(expected_beats)
    covered_ids = {str(item.get("event_id")) for item in items} if event_mode else {str(item.get("beat_id")) for item in items}
    if expected_ids != covered_ids:
        problems.append("SFX review does not cover every source audiovisual event" if event_mode else "SFX review does not cover every final-timeline beat")
    duration = max(float(item["end"]) for item in beats)
    total_cap = min(int(rules["max_total"]), max(0, math.ceil(duration / 60.0 * float(rules["max_per_minute"]))))
    if len(selected) > total_cap:
        problems.append(f"SFX density exceeds limit: selected {len(selected)}, maximum {total_cap}")
    tier_counts = Counter(str(item["intensity_tier"]) for item in selected)
    for tier in ALLOWED_TIERS:
        tier_cap = min(int(rules["tier_max_total"][tier]), max(0, math.ceil(duration / 60.0 * float(rules["tier_max_per_minute"][tier]))))
        if tier_counts[tier] > tier_cap:
            problems.append(f"{tier} SFX density exceeds limit: {tier_counts[tier]} > {tier_cap}")
    selected.sort(key=lambda item: float(item.get("trigger_time", 0)))
    for left, right in zip(selected, selected[1:]):
        gap, minimum = float(right["trigger_time"]) - float(left["trigger_time"]), float(rules["min_gap_seconds"])
        if event_mode and left["intensity_tier"] == right["intensity_tier"]:
            minimum = max(minimum, float(rules["same_tier_min_gap_seconds"][left["intensity_tier"]]))
        if gap < minimum:
            problems.append(f"SFX cues are too close: {left.get('event_id', left['beat_id'])} and {right.get('event_id', right['beat_id'])}")
    for cue_type, count in Counter(str(item.get("cue_type")) for item in selected).items():
        if count > int(rules["max_same_cue_type_per_plan"]):
            problems.append(f"SFX cue type repeats beyond limit: {cue_type} ({count})")
    if not selected and len(str(payload.get("skip_reason", "")).strip()) < 8:
        problems.append("an empty SFX plan requires a specific skip_reason")
    return {"valid": not problems, "problems": problems, "source_mode": "av_events" if event_mode else "legacy_beats", "selected_opportunities": len(selected), "selected_by_tier": dict(tier_counts), "maximum_opportunities": total_cap, "rules": rules}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser("template")
    template.add_argument("--beats", type=Path, required=True)
    template.add_argument("--broll-plan", type=Path)
    template.add_argument("--av-events", type=Path)
    template.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    template.add_argument("--style", default="medical_education")
    template.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--beats", type=Path, required=True)
    validate.add_argument("--av-events", type=Path)
    validate.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    validate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = make_template(args.beats, args.taxonomy, args.style, args.broll_plan, args.av_events) if args.command == "template" else validate_payload(load_json(args.plan), beat_list(load_json(args.beats)), load_json(args.taxonomy), event_list(load_json(args.av_events)) if args.av_events else None)
        write_json(args.output, result)
        print("RESULT: " + json.dumps(result, ensure_ascii=False))
        return 2 if args.command == "validate" and not result["valid"] else 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print("RESULT: " + json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
