#!/usr/bin/env python3
"""Prepare and validate a reviewed sound-effect opportunity plan."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


DEFAULT_TAXONOMY = Path(__file__).resolve().parents[1] / "references" / "asset_taxonomy.json"
ALLOWED_CUE_TYPES = {
    "semantic_emphasis", "chapter_transition", "visual_transition", "action_hit",
    "warning", "confirmation", "timer", "notification", "outro",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def beat_list(value: Any) -> list[dict[str, Any]]:
    beats = value.get("beats", value) if isinstance(value, dict) else value
    if not isinstance(beats, list) or not beats:
        raise ValueError("beats JSON must contain a non-empty beats array")
    result: list[dict[str, Any]] = []
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


def sound_rules(taxonomy: dict[str, Any], style_name: str) -> dict[str, Any]:
    rules = {
        "max_per_minute": 2.0,
        "max_total": 12,
        "min_gap_seconds": 3.0,
        "max_same_cue_type_per_plan": 3,
        "min_volume": 0.05,
        "max_volume": 0.18,
        "default_volume": 0.12,
        "max_duration": 1.5,
        "opening_guard_seconds": 0.8,
    }
    rules.update(taxonomy.get("selection", {}).get("sound", {}))
    style = taxonomy.get("style_profiles", {}).get(style_name, {})
    for key in (
        "max_sfx_per_minute", "max_sound_effects", "max_sfx_volume",
        "min_sfx_gap_seconds", "max_same_sfx_cue_type",
    ):
        if key in style:
            mapped = {
                "max_sfx_per_minute": "max_per_minute",
                "max_sound_effects": "max_total",
                "max_sfx_volume": "max_volume",
                "min_sfx_gap_seconds": "min_gap_seconds",
                "max_same_sfx_cue_type": "max_same_cue_type_per_plan",
            }[key]
            rules[mapped] = style[key]
    return rules


def broll_boundaries(path: Path | None) -> list[float]:
    if path is None:
        return []
    payload = load_json(path)
    segments = payload.get("segments", payload) if isinstance(payload, dict) else payload
    if not isinstance(segments, list):
        raise ValueError("B-roll plan must contain a segments array")
    boundaries: list[float] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        start = float(segment.get("start", -1))
        duration = float(segment.get("duration", 0))
        if start >= 0 and duration > 0:
            boundaries.extend([start, start + duration])
    return boundaries


def make_template(beats_path: Path, taxonomy_path: Path, style_name: str, broll_plan: Path | None = None) -> dict[str, Any]:
    beats = beat_list(load_json(beats_path))
    taxonomy = load_json(taxonomy_path)
    boundaries = broll_boundaries(broll_plan)
    opportunities: list[dict[str, Any]] = []
    for beat in beats:
        transition_times = [value for value in boundaries if beat["start"] - 0.15 <= value <= beat["end"] + 0.15]
        opportunities.append({
            "beat_id": beat["beat_id"],
            "start": beat["start"],
            "end": beat["end"],
            "purpose": str(beat.get("purpose", "general")),
            "spoken_text": str(beat.get("spoken_text", beat.get("text", ""))),
            "action": str(beat.get("action", "")),
            "representative_frame": str(beat.get("representative_frame", beat.get("frame", ""))),
            "detected_transition_times": transition_times,
            "status": "pending_review",
            "use_sfx": None,
            "cue_type": None,
            "trigger_time": None,
            "suggested_duration": None,
            "volume": None,
            "evidence": "",
            "reason": "",
        })
    duration = max(item["end"] for item in beats)
    rules = sound_rules(taxonomy, style_name)
    density_cap = max(0, math.ceil(duration / 60.0 * float(rules["max_per_minute"])))
    return {
        "version": 1,
        "style": style_name,
        "timeline_duration": duration,
        "rules": {**rules, "calculated_density_cap": min(int(rules["max_total"]), density_cap)},
        "opportunities": opportunities,
        "skip_reason": "",
        "ai_review": {
            "required": True,
            "status": "pending",
            "instructions": (
                "Inspect final-timeline speech, representative frames, actions, chapter changes, and B-roll boundaries. "
                "Approve SFX only where a precise semantic or visual trigger benefits comprehension; otherwise skip it."
            ),
        },
    }


def validate_payload(payload: dict[str, Any], beats: list[dict[str, Any]], taxonomy: dict[str, Any] | None = None) -> dict[str, Any]:
    problems: list[str] = []
    taxonomy = taxonomy or load_json(DEFAULT_TAXONOMY)
    style_name = str(payload.get("style", "medical_education"))
    rules = sound_rules(taxonomy, style_name)
    expected = {str(item["beat_id"]): item for item in beats}
    items = payload.get("opportunities", [])
    if not isinstance(items, list):
        return {"valid": False, "problems": ["opportunities must be an array"]}
    review = payload.get("ai_review", {})
    if not isinstance(review, dict) or review.get("required") is not True or review.get("status") != "approved":
        problems.append("ai_review must be required and approved")
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            problems.append(f"opportunity {index} must be an object")
            continue
        beat_id = str(item.get("beat_id", ""))
        if beat_id not in expected:
            problems.append(f"unknown beat_id: {beat_id}")
            continue
        if beat_id in seen:
            problems.append(f"duplicate beat_id: {beat_id}")
            continue
        seen.add(beat_id)
        beat = expected[beat_id]
        use_sfx = item.get("use_sfx")
        status = item.get("status")
        reason = str(item.get("reason", "")).strip()
        if use_sfx is True:
            if status != "approved":
                problems.append(f"selected SFX opportunity must be approved: {beat_id}")
            cue_type = str(item.get("cue_type", ""))
            if cue_type not in ALLOWED_CUE_TYPES:
                problems.append(f"invalid cue_type for {beat_id}: {cue_type}")
            try:
                trigger = float(item.get("trigger_time"))
                duration = float(item.get("suggested_duration"))
                volume = float(item.get("volume"))
            except (TypeError, ValueError):
                problems.append(f"selected SFX opportunity needs numeric timing, duration, and volume: {beat_id}")
                continue
            if not float(beat["start"]) <= trigger <= float(beat["end"]):
                problems.append(f"SFX trigger is outside beat: {beat_id}")
            if duration <= 0 or duration > float(rules["max_duration"]):
                problems.append(f"SFX duration exceeds policy: {beat_id}")
            if not float(rules["min_volume"]) <= volume <= float(rules["max_volume"]):
                problems.append(f"SFX volume exceeds narration-safe policy: {beat_id}")
            if trigger < float(rules["opening_guard_seconds"]) and cue_type != "semantic_emphasis":
                problems.append(f"opening SFX lacks an intentional hook classification: {beat_id}")
            if len(str(item.get("evidence", "")).strip()) < 8:
                problems.append(f"SFX opportunity lacks concrete semantic/transition evidence: {beat_id}")
            if len(reason) < 8:
                problems.append(f"SFX opportunity lacks a concrete reason: {beat_id}")
            selected.append(item)
        elif use_sfx is False:
            if status != "skipped" or len(reason) < 8:
                problems.append(f"skipped SFX opportunity needs status=skipped and a concrete reason: {beat_id}")
        else:
            problems.append(f"SFX opportunity remains undecided: {beat_id}")
    if seen != set(expected):
        problems.append("SFX review does not cover every final-timeline beat")
    duration = max(item["end"] for item in beats)
    density_cap = min(int(rules["max_total"]), max(0, math.ceil(duration / 60.0 * float(rules["max_per_minute"]))))
    if len(selected) > density_cap:
        problems.append(f"SFX density exceeds limit: selected {len(selected)}, maximum {density_cap}")
    selected.sort(key=lambda item: float(item.get("trigger_time", 0)))
    for left, right in zip(selected, selected[1:]):
        if float(right["trigger_time"]) - float(left["trigger_time"]) < float(rules["min_gap_seconds"]):
            problems.append(f"SFX cues are too close: {left['beat_id']} and {right['beat_id']}")
    counts: dict[str, int] = {}
    for item in selected:
        cue_type = str(item.get("cue_type"))
        counts[cue_type] = counts.get(cue_type, 0) + 1
    for cue_type, count in counts.items():
        if count > int(rules["max_same_cue_type_per_plan"]):
            problems.append(f"SFX cue type repeats beyond limit: {cue_type} ({count})")
    if not selected and len(str(payload.get("skip_reason", "")).strip()) < 8:
        problems.append("an empty SFX plan requires a specific skip_reason")
    return {
        "valid": not problems,
        "problems": problems,
        "selected_opportunities": len(selected),
        "maximum_opportunities": density_cap,
        "rules": rules,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser("template")
    template.add_argument("--beats", type=Path, required=True)
    template.add_argument("--broll-plan", type=Path)
    template.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    template.add_argument("--style", default="medical_education")
    template.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--beats", type=Path, required=True)
    validate.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    validate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "template":
            result = make_template(args.beats, args.taxonomy, args.style, args.broll_plan)
        else:
            beats = beat_list(load_json(args.beats))
            result = validate_payload(load_json(args.plan), beats, load_json(args.taxonomy))
        write_json(args.output, result)
        print("RESULT: " + json.dumps(result, ensure_ascii=False))
        if args.command == "validate" and not result["valid"]:
            return 2
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print("RESULT: " + json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
