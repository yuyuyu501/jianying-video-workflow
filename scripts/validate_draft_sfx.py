#!/usr/bin/env python3
"""Validate approved sound-effect files and placements on the JianYing SFX track."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


TIMING_TOLERANCE_US = 20_000
VOLUME_TOLERANCE = 0.001


def default_draft_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not set; pass --draft-path explicitly")
    return Path(local_app_data) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def load_draft_path(args: argparse.Namespace) -> Path:
    if args.draft_path:
        return args.draft_path.resolve()
    return (default_draft_root() / args.draft_name).resolve()


def validate(document: dict, asset_plan: dict, expected_count: int | None = None) -> dict:
    expected = asset_plan.get("sound_effects", [])
    if not isinstance(expected, list):
        return {"status": "failed", "sfx_segment_count": 0, "errors": ["asset plan sound_effects must be an array"]}
    track = next(
        (item for item in document.get("tracks", []) if item.get("type") == "audio" and item.get("name") == "SFX"),
        None,
    )
    segments = sorted(
        track.get("segments", []) if track else [],
        key=lambda item: item.get("target_timerange", {}).get("start", 0),
    )
    expected = sorted(expected, key=lambda item: float(item.get("start", 0)))
    materials = {
        item.get("id"): item
        for item in document.get("materials", {}).get("audios", [])
        if item.get("id")
    }
    errors: list[str] = []
    frozen_paths: list[str] = []
    timing_plan = asset_plan.get("sfx_timing_plan") or {}
    event_mode = timing_plan.get("source_mode") == "av_events"
    linked_event_ids: set[str] = set()
    if track is None:
        errors.append("SFX audio track does not exist")
    required_count = len(expected) if expected_count is None else expected_count
    if len(segments) != required_count:
        errors.append(f"Expected {required_count} SFX segments, found {len(segments)}")
    if len(expected) != required_count:
        errors.append(f"Asset plan contains {len(expected)} sound effects, expected {required_count}")

    previous_end = -1
    for index, (planned, segment) in enumerate(zip(expected, segments), start=1):
        if event_mode:
            linked_event_id = str(planned.get("linked_event_id", "")).strip()
            event_id = str(planned.get("event_id", "")).strip()
            if not linked_event_id:
                errors.append(f"SFX {index} lacks linked_event_id from the approved audiovisual event")
            elif linked_event_id in linked_event_ids:
                errors.append(f"SFX {index} reuses linked_event_id {linked_event_id}")
            else:
                linked_event_ids.add(linked_event_id)
            if event_id and linked_event_id and event_id != linked_event_id:
                errors.append(f"SFX {index} event_id does not match linked_event_id")
            if str(planned.get("intensity_tier", "")) not in {"light", "medium", "strong"}:
                errors.append(f"SFX {index} lacks a valid event intensity tier")
        target = segment.get("target_timerange", {})
        start_us = int(target.get("start", -1))
        duration_us = int(target.get("duration", 0))
        planned_start_us = round(float(planned.get("start", 0)) * 1_000_000)
        planned_duration_us = round(float(planned.get("duration", 0)) * 1_000_000)
        if abs(start_us - planned_start_us) > TIMING_TOLERANCE_US:
            errors.append(f"SFX {index} start does not match the approved plan")
        if duration_us <= 0 or duration_us > planned_duration_us + TIMING_TOLERANCE_US:
            errors.append(f"SFX {index} duration is invalid or exceeds the approved plan")
        if start_us < previous_end:
            errors.append(f"SFX {index} overlaps the previous sound effect")
        previous_end = max(previous_end, start_us + duration_us)
        if abs(float(segment.get("volume", -1)) - float(planned.get("volume", 0.12))) > VOLUME_TOLERANCE:
            errors.append(f"SFX {index} volume does not match the approved plan")
        material = materials.get(segment.get("material_id"))
        if material is None:
            errors.append(f"SFX {index} references a missing audio material")
            continue
        path = Path(str(material.get("path", "")))
        frozen_paths.append(str(path))
        if not path.is_file():
            errors.append(f"SFX {index} local cache file is missing")
        asset_id = str(planned.get("asset_id", ""))
        if asset_id and asset_id not in path.name:
            errors.append(f"SFX {index} local file does not match approved asset ID {asset_id}")

    return {
        "status": "passed" if not errors else "failed",
        "sfx_track_count": 1 if track else 0,
        "sfx_segment_count": len(segments),
        "expected_count": required_count,
        "event_linked_count": len(linked_event_ids),
        "frozen_local_paths": frozen_paths,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft-path", type=Path)
    source.add_argument("--draft-name")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-count", type=int)
    args = parser.parse_args()
    try:
        path = load_draft_path(args)
        draft_info = path / "draft_info.json"
        if not draft_info.is_file():
            raise RuntimeError(f"draft_info.json not found: {draft_info}")
        result = validate(
            json.loads(draft_info.read_text(encoding="utf-8")),
            json.loads(args.plan.read_text(encoding="utf-8")),
            args.expected_count,
        )
        result["draft_path"] = str(path)
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
