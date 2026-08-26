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


def validate(
    document: dict,
    expected_visual: str | None = None,
    require_pip: bool = False,
    pip_visual_review: dict | None = None,
    require_visual_review: bool = False,
) -> dict:
    tracks = document.get("tracks", [])
    pip_track = next((track for track in tracks if track.get("type") == "video" and track.get("name") == "SpeakerPiP"), None)
    broll_track = next((track for track in tracks if track.get("type") == "video" and track.get("name") == "B_Roll"), None)
    video_by_id = {item.get("id"): item for item in document.get("materials", {}).get("videos", [])}
    masks_by_id = {item.get("id"): item for item in document.get("materials", {}).get("masks", [])}
    pip_segments = pip_track.get("segments", []) if pip_track else []
    broll_segments = broll_track.get("segments", []) if broll_track else []
    errors = []
    review_by_start = {
        round(float(item.get("final_start", -1)), 3): item
        for item in (pip_visual_review or {}).get("pip_reviews", [])
        if item.get("status") == "approved"
    }
    if require_visual_review and (not pip_visual_review or pip_visual_review.get("status") != "succeeded"):
        errors.append("PiP visual review is missing or did not pass")
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
        circle = next((mask for mask in mask_refs if mask and mask.get("name") in {"circle", "Circle", "圆形"}), None)
        if circle is None:
            errors.append(f"PiP segment {index} has no circular mask")
        clip = segment.get("clip", {})
        scale = clip.get("scale", {})
        transform = clip.get("transform", {})
        if float(scale.get("x", 1.0)) > 1.15 or float(scale.get("y", 1.0)) > 1.15:
            errors.append(f"PiP segment {index} exceeds the maximum source scale")
        if abs(float(transform.get("x", 0.0))) < 0.2 and abs(float(transform.get("y", 0.0))) < 0.2:
            errors.append(f"PiP segment {index} is still centered")
        if broll_segments and not any(overlaps(segment, broll) for broll in broll_segments):
            errors.append(f"PiP segment {index} does not overlap B-roll")
        if circle is not None and require_visual_review:
            config = circle.get("config", {})
            visible_diameter = float(config.get("height", 0)) * float(scale.get("y", 1.0))
            if not 0.18 <= visible_diameter <= 0.40:
                errors.append(f"PiP segment {index} has an invalid effective circular size")
            start = round(float(segment.get("target_timerange", {}).get("start", 0)) / 1_000_000, 3)
            review = review_by_start.get(start)
            if review is None:
                errors.append(f"PiP segment {index} has no matching face-detection review")
            else:
                expected_x, expected_y = float(review["face_center_x"]), float(review["face_center_y"])
                actual_x, actual_y = float(config.get("centerX", 0)), float(config.get("centerY", 0))
                if abs(actual_x - expected_x) > 0.04 or abs(actual_y - expected_y) > 0.04:
                    errors.append(f"PiP segment {index} circular mask is not centered on the detected face")
                if abs(float(config.get("height", 0)) - float(review["mask_size"])) > 0.03:
                    errors.append(f"PiP segment {index} circular mask size differs from face-driven review")
                face_fill = float(review.get("face_fill_ratio", 0))
                if not 0.66 <= face_fill <= 0.78:
                    errors.append(f"PiP segment {index} does not use a head-focused crop")
                if abs(float(transform.get("x", 0)) - float(review["placement_transform_x"])) > 0.04 or abs(float(transform.get("y", 0)) - float(review["placement_transform_y"])) > 0.04:
                    errors.append(f"PiP segment {index} is not anchored to the detected face")
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
    parser.add_argument("--pip-visual-review", type=Path)
    parser.add_argument("--require-pip", action="store_true")
    parser.add_argument("--require-visual-review", action="store_true")
    args = parser.parse_args()
    try:
        path = args.draft_path.resolve() if args.draft_path else (default_draft_root() / args.draft_name).resolve()
        document = json.loads((path / "draft_info.json").read_text(encoding="utf-8"))
        visual_review = json.loads(args.pip_visual_review.read_text(encoding="utf-8")) if args.pip_visual_review else None
        result = validate(document, str(args.expected_visual.resolve()) if args.expected_visual else None, args.require_pip, visual_review, args.require_visual_review)
        result["draft_path"] = str(path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        result = {"status": "failed", "errors": [str(error)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
