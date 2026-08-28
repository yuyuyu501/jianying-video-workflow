#!/usr/bin/env python3
"""Build a unified audiovisual event timeline for reviewed sound design."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


EVENT_TYPES = {
    "semantic_emphasis", "chapter_transition", "broll_transition",
    "caption_entrance", "flower_text_pop", "keyword_hit",
    "caption_highlight", "card_entrance", "sticker_entrance",
    "scene_effect_onset", "character_effect_onset", "action_hit", "outro",
}


def load_json(path: Path | None) -> Any:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def beat_list(payload: Any) -> list[dict[str, Any]]:
    values = payload.get("beats", payload) if isinstance(payload, dict) else payload
    if not isinstance(values, list) or not values:
        raise ValueError("beats JSON must contain a non-empty beats array")
    beats = []
    for index, raw in enumerate(values, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"beat {index} must be an object")
        start, end = float(raw.get("start", -1)), float(raw.get("end", -1))
        if start < 0 or end <= start:
            raise ValueError(f"beat {index} has invalid timing")
        item = dict(raw)
        item["beat_id"] = str(item.get("beat_id", f"beat-{index:03d}"))
        item["start"], item["end"] = start, end
        beats.append(item)
    return beats


def containing_beat(beats: list[dict[str, Any]], timestamp: float) -> dict[str, Any]:
    for beat in beats:
        if float(beat["start"]) - 0.05 <= timestamp <= float(beat["end"]) + 0.05:
            return beat
    return min(beats, key=lambda beat: abs(float(beat["start"]) - timestamp))


def default_tier(event_type: str, role: str = "") -> str:
    if event_type in {"flower_text_pop", "keyword_hit", "caption_entrance"}:
        return "light"
    if event_type in {"chapter_transition", "scene_effect_onset", "character_effect_onset", "action_hit"}:
        return "medium"
    if event_type == "semantic_emphasis" and role in {"warning", "hook"}:
        return "strong"
    return "medium"


def add_event(
    events: list[dict[str, Any]], beats: list[dict[str, Any]], event_type: str,
    timestamp: float, source: str, label: str, source_id: str,
    *, role: str = "", evidence: str = "", tier: str | None = None,
) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event type: {event_type}")
    beat = containing_beat(beats, timestamp)
    events.append({
        "event_id": f"{event_type}:{source_id}",
        "event_type": event_type,
        "time": round(float(timestamp), 3),
        "beat_id": beat["beat_id"],
        "beat_start": beat["start"],
        "beat_end": beat["end"],
        "purpose": str(beat.get("purpose", "general")),
        "spoken_text": str(beat.get("spoken_text", beat.get("text", ""))),
        "role": role,
        "source": source,
        "source_id": source_id,
        "label": label,
        "suggested_intensity_tier": tier or default_tier(event_type, role),
        "evidence": evidence or f"{label} begins at {timestamp:.3f}s on the final timeline.",
    })


def build(
    beats: list[dict[str, Any]], caption_plan: dict | None = None,
    broll_plan: dict | None = None, visual_treatments: dict | None = None,
    asset_plan: dict | None = None, sticker_plan: dict | None = None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for beat in beats:
        purpose = str(beat.get("purpose", "general"))
        event_type = "chapter_transition" if purpose == "chapter" else "outro" if purpose == "outro" else "semantic_emphasis"
        tier = "strong" if purpose in {"warning", "time_pressure", "emergency_call"} else "medium"
        add_event(
            events, beats, event_type, float(beat["start"]), "beats", purpose,
            str(beat["beat_id"]), role=purpose, tier=tier,
            evidence=f"The {purpose} beat starts with: {str(beat.get('spoken_text', ''))[:80]}",
        )

    if isinstance(caption_plan, dict):
        for cue in caption_plan.get("cues", []):
            if not isinstance(cue, dict):
                continue
            index = int(cue.get("index", 0))
            start = float(cue.get("start", 0))
            presentation = str(cue.get("presentation", "base"))
            role = str(cue.get("role", "base"))
            text = str(cue.get("text", ""))
            event_type = "flower_text_pop" if presentation in {"flower", "bubble"} else "keyword_hit" if cue.get("keyword_spans") else "caption_entrance"
            add_event(events, beats, event_type, start, "caption_plan", text, f"cue-{index + 1:04d}", role=role)
            if cue.get("highlight", {}).get("enabled"):
                add_event(events, beats, "caption_highlight", start, "caption_plan", text, f"highlight-{index + 1:04d}", role=role)
            if cue.get("card", {}).get("enabled"):
                add_event(events, beats, "card_entrance", start, "caption_plan", text, f"card-{index + 1:04d}", role=role)

    if isinstance(broll_plan, dict):
        for index, segment in enumerate(broll_plan.get("segments", []), start=1):
            if not isinstance(segment, dict):
                continue
            start, duration = float(segment.get("start", -1)), float(segment.get("duration", 0))
            if start >= 0 and duration > 0:
                label = str(segment.get("purpose", segment.get("video", "B-roll")))
                add_event(events, beats, "broll_transition", start, "broll_plan", label, f"broll-{index:03d}-in")
                add_event(events, beats, "broll_transition", start + duration, "broll_plan", label, f"broll-{index:03d}-out")

    if isinstance(visual_treatments, dict):
        for item in visual_treatments.get("treatments", []):
            if not isinstance(item, dict):
                continue
            treatment = str(item.get("primary_treatment", ""))
            if treatment not in {"scene_effect", "character_effect"}:
                continue
            event_type = "scene_effect_onset" if treatment == "scene_effect" else "character_effect_onset"
            timestamp = float(item.get("visual_evidence_time", item.get("start", 0)))
            add_event(events, beats, event_type, timestamp, "visual_treatment_plan", treatment, str(item.get("beat_id", timestamp)))

    if isinstance(asset_plan, dict):
        for family, event_type in (("visual_effects", "scene_effect_onset"), ("character_effects", "character_effect_onset")):
            for index, item in enumerate(asset_plan.get(family, []), start=1):
                if isinstance(item, dict):
                    add_event(events, beats, event_type, float(item.get("start", 0)), "asset_plan", str(item.get("name", family)), f"{family}-{index:03d}")

    if isinstance(sticker_plan, dict):
        for index, item in enumerate(sticker_plan.get("stickers", []), start=1):
            if isinstance(item, dict):
                add_event(events, beats, "sticker_entrance", float(item.get("start", 0)), "sticker_plan", str(item.get("name", item.get("reason", "sticker"))), f"sticker-{index:03d}")

    priority = {"asset_plan": 5, "caption_plan": 4, "sticker_plan": 4, "broll_plan": 3, "visual_treatment_plan": 2, "beats": 1}
    deduped: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: (float(item["time"]), -priority.get(str(item["source"]), 0), str(item["event_id"]))):
        duplicate = next((item for item in deduped if item["event_type"] == event["event_type"] and abs(float(item["time"]) - float(event["time"])) <= 0.04), None)
        if duplicate is None:
            deduped.append(event)
        elif priority.get(str(event["source"]), 0) > priority.get(str(duplicate["source"]), 0):
            deduped[deduped.index(duplicate)] = event
    for index, event in enumerate(deduped, start=1):
        event["sequence"] = index
    return {
        "version": 1,
        "timestamp_basis": "rendered_rough_cut_output",
        "timeline_duration": max(float(beat["end"]) for beat in beats),
        "event_count": len(deduped),
        "event_types": sorted({str(item["event_type"]) for item in deduped}),
        "events": deduped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beats", type=Path, required=True)
    parser.add_argument("--caption-plan", type=Path)
    parser.add_argument("--broll-plan", type=Path)
    parser.add_argument("--visual-treatment-plan", type=Path)
    parser.add_argument("--asset-plan", type=Path)
    parser.add_argument("--sticker-plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(
            beat_list(load_json(args.beats)), load_json(args.caption_plan), load_json(args.broll_plan),
            load_json(args.visual_treatment_plan), load_json(args.asset_plan), load_json(args.sticker_plan),
        )
        write_json(args.output, result)
        print("RESULT: " + json.dumps({"status": "succeeded", "output": str(args.output), "event_count": result["event_count"]}, ensure_ascii=False))
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print("RESULT: " + json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
