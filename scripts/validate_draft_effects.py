#!/usr/bin/env python3
"""Validate that JianYing effect-track segments reference real video effects."""

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


def load_draft_path(args: argparse.Namespace) -> Path:
    if args.draft_path:
        return args.draft_path.resolve()
    if not args.draft_name:
        raise RuntimeError("Pass either --draft-path or --draft-name")
    return (default_draft_root() / args.draft_name).resolve()


def validate(document: dict, expected_count: int | None) -> dict:
    materials = document.get("materials", {}).get("video_effects", [])
    effect_tracks = [track for track in document.get("tracks", []) if track.get("type") == "effect"]
    segments = [segment for track in effect_tracks for segment in track.get("segments", [])]
    material_ids = {material.get("id") for material in materials}
    missing_material_ids = sorted({
        segment.get("material_id")
        for segment in segments
        if segment.get("material_id") not in material_ids
    })
    wrong_material_types = sorted({
        material.get("type")
        for material in materials
        if material.get("type") != "video_effect"
    })
    result = {
        "effect_track_count": len(effect_tracks),
        "effect_segment_count": len(segments),
        "video_effect_material_count": len(materials),
        "missing_material_ids": missing_material_ids,
        "wrong_material_types": wrong_material_types,
    }
    errors = []
    if not effect_tracks:
        errors.append("No effect track exists")
    if expected_count is not None and len(segments) != expected_count:
        errors.append(f"Expected {expected_count} effect segments, found {len(segments)}")
    if expected_count is not None and len(materials) != expected_count:
        errors.append(f"Expected {expected_count} video-effect materials, found {len(materials)}")
    if len(materials) != len(segments):
        errors.append("Effect segment and video-effect material counts differ")
    if missing_material_ids:
        errors.append("Effect segments reference missing video-effect materials")
    if wrong_material_types:
        errors.append("Non-video-effect entries found in materials.video_effects")
    result["status"] = "passed" if not errors else "failed"
    result["errors"] = errors
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft-path", type=Path)
    source.add_argument("--draft-name")
    parser.add_argument("--expected-count", type=int, default=None)
    args = parser.parse_args()

    try:
        draft_path = load_draft_path(args)
        draft_info = draft_path / "draft_info.json"
        if not draft_info.is_file():
            raise RuntimeError(f"draft_info.json not found: {draft_info}")
        result = validate(json.loads(draft_info.read_text(encoding="utf-8")), args.expected_count)
        result["draft_path"] = str(draft_path)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        result = {"status": "failed", "errors": [str(exc)]}

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
