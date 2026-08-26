#!/usr/bin/env python3
"""Validate circular, silent speaker PiP segments placed over B-roll."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def default_draft_root() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RuntimeError("LOCALAPPDATA is not set; pass --draft-path")
    return Path(root) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def overlaps(first: dict, second: dict) -> bool:
    first_range, second_range = first.get("target_timerange", {}), second.get("target_timerange", {})
    first_start = int(first_range.get("start", 0))
    first_end = first_start + int(first_range.get("duration", 0))
    second_start = int(second_range.get("start", 0))
    second_end = second_start + int(second_range.get("duration", 0))
    return first_start < second_end and second_start < first_end


def validate(document: dict, expected_visual: str | None = None, require_pip: bool = False) -> dict:
    tracks = document.get("tracks", [])
    pip_track = next((track for track in tracks if track.get("type") == "video" and track.get("name") == "SpeakerPiP"), None)
    broll_track = next((track for track in tracks if track.get("type") == "video" and track.get("name") == "B_Roll"), None)
    video_by_id = {item.get("id"): item for item in document.get("materials", {}).get("videos", [])}
    masks_by_id = {item.get("id"): item for item in document.get("materials", {}).get("masks", [])}
    pip_segments = pip_track.get("segments", []) if pip_track else []
    broll_segments = broll_track.get("segments", []) if broll_track else []
    errors = []
    if pip_track is None:
        errors.append("SpeakerPiP track does not exist")
    if require_pip and not pip_segments:
        errors.append("B-roll plan requires SpeakerPiP but no PiP segments were materialized")
    if pip_segments and broll_track is None:
        errors.append("SpeakerPiP exists but B_Roll track does not exist")
    expected = str(Path(expected_visual).resolve()) if expected_visual else None
    for index, segment in enumerate(pip_segments, start=1):
        if float(segment.get("volume", 1.0)) > 0.001:
            errors.append(f"PiP segment {index} is not muted")
        material = video_by_id.get(segment.get("material_id"))
        if material is None:
            errors.append(f"PiP segment {index} references a missing video material")
        elif expected and str(Path(str(material.get("path", ""))).resolve()) != expected:
            errors.append(f"PiP segment {index} does not use the approved silent rough-cut visual")
        mask_refs = [masks_by_id.get(item) for item in segment.get("extra_material_refs", [])]
        if not any(mask and mask.get("name") in {"circle", "Circle", "圆形"} for mask in mask_refs):
            errors.append(f"PiP segment {index} has no circular mask")
        clip = segment.get("clip", {})
        scale = clip.get("scale", {})
        transform = clip.get("transform", {})
        if float(scale.get("x", 1.0)) >= 0.8 or float(scale.get("y", 1.0)) >= 0.8:
            errors.append(f"PiP segment {index} is not scaled down")
        if abs(float(transform.get("x", 0.0))) < 0.2 and abs(float(transform.get("y", 0.0))) < 0.2:
            errors.append(f"PiP segment {index} is still centered")
        if broll_segments and not any(overlaps(segment, broll) for broll in broll_segments):
            errors.append(f"PiP segment {index} does not overlap B-roll")
    return {
        "status": "passed" if not errors else "failed",
        "pip_segment_count": len(pip_segments),
        "broll_segment_count": len(broll_segments),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft-path", type=Path)
    source.add_argument("--draft-name")
    parser.add_argument("--expected-visual", type=Path)
    parser.add_argument("--require-pip", action="store_true")
    args = parser.parse_args()
    try:
        path = args.draft_path.resolve() if args.draft_path else (default_draft_root() / args.draft_name).resolve()
        document = json.loads((path / "draft_info.json").read_text(encoding="utf-8"))
        result = validate(document, str(args.expected_visual.resolve()) if args.expected_visual else None, args.require_pip)
        result["draft_path"] = str(path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        result = {"status": "failed", "errors": [str(error)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
