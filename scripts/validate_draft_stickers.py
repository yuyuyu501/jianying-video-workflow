#!/usr/bin/env python3
"""Validate the dedicated JianYing sticker track against its approved plan."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def default_draft_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not set; pass --draft-path explicitly")
    return Path(local_app_data) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def _seconds(segment: dict) -> tuple[float, float]:
    timerange = segment.get("target_timerange", {})
    return float(timerange.get("start", -1)) / 1_000_000, float(timerange.get("duration", 0)) / 1_000_000


def validate(document: dict, plan: dict, expected_count: int | None = None) -> dict:
    tracks = [track for track in document.get("tracks", []) if track.get("name") == "Stickers"]
    track = tracks[0] if len(tracks) == 1 else None
    segments = track.get("segments", []) if track else []
    materials = document.get("materials", {}).get("stickers", [])
    by_id = {material.get("id"): material for material in materials}
    planned = plan.get("stickers", [])
    errors: list[str] = []
    if plan.get("ai_review", {}).get("status") != "approved":
        errors.append("Sticker plan AI review is not approved")
    if not planned and not str(plan.get("skip_reason", "")).strip():
        errors.append("Empty sticker plan has no specific skip_reason")
    if track is None or track.get("type") != "sticker":
        errors.append("Stickers track does not exist once with type sticker")
    if expected_count is not None and len(segments) != expected_count:
        errors.append(f"Expected {expected_count} sticker segments, found {len(segments)}")
    if len(segments) != len(planned):
        errors.append("Sticker segment count does not match the approved plan")
    for index, (segment, item) in enumerate(zip(segments, planned), start=1):
        material = by_id.get(segment.get("material_id"))
        if material is None or material.get("type") != "sticker":
            errors.append(f"Sticker segment {index} references no real sticker material")
            continue
        if material.get("resource_id") != item.get("resource_id"):
            errors.append(f"Sticker segment {index} resource_id differs from the approved plan")
        start, duration = _seconds(segment)
        if abs(start - float(item["start"])) > 0.001 or abs(duration - float(item["duration"])) > 0.001:
            errors.append(f"Sticker segment {index} timing differs from the approved plan")
        clip = segment.get("clip", {})
        scale = clip.get("scale", {})
        transform = clip.get("transform", {})
        checks = {
            "scale": float(scale.get("x", -999)),
            "transform_x": float(transform.get("x", -999)),
            "transform_y": float(transform.get("y", -999)),
            "rotation": float(clip.get("rotation", -999)),
        }
        for key, actual in checks.items():
            expected = float(item.get(key, 1.0 if key == "scale" else 0.0))
            if abs(actual - expected) > 0.001:
                errors.append(f"Sticker segment {index} {key} differs from the approved plan")
        if item.get("collision_review", {}).get("status") != "passed":
            errors.append(f"Sticker segment {index} has no passed collision review")
    referenced = {segment.get("material_id") for segment in segments}
    real_sticker_ids = {material.get("id") for material in materials if material.get("type") == "sticker"}
    if not referenced.issubset(real_sticker_ids):
        errors.append("Stickers track contains unresolved material references")
    if len(real_sticker_ids) != len(segments):
        errors.append("Sticker material count differs from the Stickers track segment count")
    return {
        "status": "passed" if not errors else "failed",
        "sticker_track_count": len(tracks),
        "sticker_segment_count": len(segments),
        "skip_reason": plan.get("skip_reason") if not planned else None,
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
        draft_path = args.draft_path.resolve() if args.draft_path else (default_draft_root() / args.draft_name).resolve()
        document = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        result = validate(document, plan, args.expected_count)
        result["draft_path"] = str(draft_path)
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as error:
        result = {"status": "failed", "errors": [str(error)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
