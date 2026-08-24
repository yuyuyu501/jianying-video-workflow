#!/usr/bin/env python3
"""Validate a JianYing draft's independent narration track."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


TOLERANCE_US = 250_000


def draft_path(args: argparse.Namespace) -> Path:
    if args.draft_path:
        return args.draft_path.resolve()
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RuntimeError("LOCALAPPDATA is not set; pass --draft-path")
    return (Path(root) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft" / args.draft_name).resolve()


def validate(document: dict, narration_track: str, expected_seconds: float) -> dict:
    expected_us = int(round(expected_seconds * 1_000_000))
    tracks = document.get("tracks", [])
    narration = next((track for track in tracks if track.get("type") == "audio" and track.get("name") == narration_track), None)
    audio_materials = document.get("materials", {}).get("audios", [])
    material_ids = {material.get("id") for material in audio_materials}
    segments = narration.get("segments", []) if narration else []
    missing_materials = [segment.get("material_id") for segment in segments if segment.get("material_id") not in material_ids]
    coverage_start = min((segment.get("target_timerange", {}).get("start", expected_us) for segment in segments), default=None)
    coverage_end = max((segment.get("target_timerange", {}).get("start", 0) + segment.get("target_timerange", {}).get("duration", 0) for segment in segments), default=None)
    errors = []
    if narration is None:
        errors.append(f"Narration track {narration_track!r} is missing")
    if not audio_materials:
        errors.append("No audio materials exist")
    if not segments:
        errors.append("Narration track has no segments")
    if missing_materials:
        errors.append("Narration segments reference missing audio materials")
    if coverage_start is not None and coverage_start > TOLERANCE_US:
        errors.append("Narration does not start at the timeline origin")
    if coverage_end is not None and coverage_end < expected_us - TOLERANCE_US:
        errors.append("Narration does not cover the approved timeline duration")
    return {
        "status": "passed" if not errors else "failed",
        "narration_track": narration_track,
        "audio_material_count": len(audio_materials),
        "narration_segment_count": len(segments),
        "coverage_start_seconds": round((coverage_start or 0) / 1_000_000, 3),
        "coverage_end_seconds": round((coverage_end or 0) / 1_000_000, 3),
        "expected_duration_seconds": round(expected_seconds, 3),
        "missing_material_ids": missing_materials,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft-path", type=Path)
    source.add_argument("--draft-name")
    parser.add_argument("--expected-duration", type=float, required=True)
    parser.add_argument("--narration-track", default="Narration")
    args = parser.parse_args()
    try:
        path = draft_path(args)
        info = path / "draft_info.json"
        if not info.is_file():
            raise RuntimeError(f"draft_info.json not found: {info}")
        result = validate(json.loads(info.read_text(encoding="utf-8")), args.narration_track, args.expected_duration)
        result["draft_path"] = str(path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
