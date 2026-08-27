#!/usr/bin/env python3
"""Plan and validate one editorial visual treatment for every content beat."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TREATMENTS = {
    "none",
    "broll",
    "chapter_title",
    "information_card",
    "caption_highlight",
    "sticker",
    "instructional_illustration",
    "motion_graphic",
    "scene_effect",
    "character_effect",
}
EFFECT_TREATMENTS = {"scene_effect", "character_effect"}
PRIORITY_PURPOSES = {"warning", "chapter", "correct_action", "time_pressure", "emergency_call"}
CHARACTER_INTENTS = {
    "emotion_emphasis",
    "identity_emphasis",
    "face_highlight",
    "reaction_accent",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def beat_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("beats") or value.get("segments") or []
    if not isinstance(value, list):
        raise ValueError("beats JSON must be a list or an object containing beats")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        start = float(item.get("start", item.get("start_time", 0)))
        end = max(start + 0.1, float(item.get("end", item.get("end_time", start + 1))))
        result.append({**item, "beat_id": item.get("beat_id", index + 1), "start": start, "end": end})
    return result


def overlaps(start: float, end: float, item: dict[str, Any]) -> bool:
    item_start = float(item.get("start", 0))
    item_end = item_start + float(item.get("duration", max(0.0, float(item.get("end", item_start)) - item_start)))
    return min(end, item_end) - max(start, item_start) > 0.05


def template(beats_path: Path, broll_path: Path | None = None, caption_path: Path | None = None) -> dict[str, Any]:
    beats = beat_list(load_json(beats_path))
    broll = load_json(broll_path).get("segments", []) if broll_path else []
    caption_items = load_json(caption_path).get("captions", []) if caption_path else []
    treatments = []
    for beat in beats:
        already = []
        if any(overlaps(beat["start"], beat["end"], item) for item in broll if isinstance(item, dict)):
            already.append("B_Roll")
        if any(overlaps(beat["start"], beat["end"], item) for item in caption_items if isinstance(item, dict)):
            already.append("CaptionHighlights")
        treatments.append({
            "beat_id": beat["beat_id"],
            "start": beat["start"],
            "end": beat["end"],
            "purpose": beat.get("purpose", "general"),
            "spoken_text": beat.get("spoken_text", beat.get("text", "")),
            "primary_treatment": None,
            "secondary_treatment": None,
            "already_emphasized_by": already,
            "representative_frame": beat.get("representative_frame", ""),
            "visual_evidence_time": None,
            "visual_evidence": "",
            "review_status": "pending",
            "layering_reason": "",
            "character_intent": None,
        })
    return {
        "version": 1,
        "status": "pending_review",
        "policy": {
            "one_primary_treatment_per_beat": True,
            "prefer_editorial_composition_over_effect_stacking": True,
            "effect_families": sorted(EFFECT_TREATMENTS),
            "allowed_treatments": sorted(TREATMENTS),
        },
        "treatments": treatments,
    }


def validate_payload(payload: Any, beats: list[dict[str, Any]]) -> dict[str, Any]:
    problems: list[str] = []
    if not isinstance(payload, dict):
        return {"valid": False, "status": "failed", "problems": ["visual treatment plan must be a JSON object"]}
    items = payload.get("treatments")
    if not isinstance(items, list):
        return {"valid": False, "status": "failed", "problems": ["visual treatment plan requires treatments array"]}
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("beat_id") is None:
            problems.append("every visual treatment requires beat_id")
            continue
        beat_id = str(item["beat_id"])
        if beat_id in by_id:
            problems.append(f"duplicate visual treatment for beat: {beat_id}")
        by_id[beat_id] = item
    beat_ids = {str(beat["beat_id"]) for beat in beats}
    for extra in sorted(set(by_id) - beat_ids):
        problems.append(f"visual treatment references unknown beat: {extra}")
    for beat in beats:
        beat_id = str(beat["beat_id"])
        item = by_id.get(beat_id)
        if item is None:
            problems.append(f"missing visual treatment for beat: {beat_id}")
            continue
        primary = item.get("primary_treatment")
        secondary = item.get("secondary_treatment")
        if primary not in TREATMENTS:
            problems.append(f"invalid primary_treatment for beat {beat_id}: {primary!r}")
        if str(beat.get("purpose", "general")) in PRIORITY_PURPOSES and primary == "none":
            problems.append(f"priority beat requires an editorial visual treatment: {beat_id}")
        if secondary is not None and secondary not in TREATMENTS:
            problems.append(f"invalid secondary_treatment for beat {beat_id}: {secondary!r}")
        if secondary == primary and secondary is not None:
            problems.append(f"secondary treatment duplicates primary for beat {beat_id}")
        if item.get("review_status") != "approved":
            problems.append(f"visual treatment is not approved for beat: {beat_id}")
        evidence = str(item.get("visual_evidence", "")).strip()
        try:
            evidence_time = float(item.get("visual_evidence_time"))
        except (TypeError, ValueError):
            problems.append(f"visual treatment requires evidence timestamp for beat: {beat_id}")
            evidence_time = float(beat["start"])
        if len(evidence) < 8:
            problems.append(f"visual treatment requires concrete frame evidence for beat: {beat_id}")
        if not float(beat["start"]) <= evidence_time <= float(beat["end"]):
            problems.append(f"visual treatment evidence is outside beat: {beat_id}")
        treatments = {primary, secondary} - {None}
        if EFFECT_TREATMENTS <= treatments and len(str(item.get("layering_reason", "")).strip()) < 12:
            problems.append(f"scene and character effect layering requires a reviewed reason for beat: {beat_id}")
        if "character_effect" in treatments and item.get("character_intent") not in CHARACTER_INTENTS:
            problems.append(f"character effect requires an approved face-specific intent for beat: {beat_id}")
    valid = not problems
    return {
        "valid": valid,
        "status": "approved" if valid else "failed",
        "problems": problems,
        "beat_count": len(beats),
        "treatment_count": len(items),
    }


def validate(plan_path: Path, beats_path: Path) -> dict[str, Any]:
    return validate_payload(load_json(plan_path), beat_list(load_json(beats_path)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    template_parser = sub.add_parser("template")
    template_parser.add_argument("--beats", type=Path, required=True)
    template_parser.add_argument("--broll-plan", type=Path)
    template_parser.add_argument("--caption-plan", type=Path)
    template_parser.add_argument("--output", type=Path, required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--plan", type=Path, required=True)
    validate_parser.add_argument("--beats", type=Path, required=True)
    validate_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "template":
            result = template(args.beats, args.broll_plan, args.caption_plan)
            status = "succeeded"
        else:
            result = validate(args.plan, args.beats)
            status = "succeeded" if result["valid"] else "failed"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("RESULT: " + json.dumps({"status": status, "output": str(args.output), **result}, ensure_ascii=False))
        return 0 if status == "succeeded" else 2
    except Exception as error:
        print("RESULT: " + json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
