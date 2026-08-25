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


def validate(document: dict, expected_count: int | None, expected_character_count: int | None = None) -> dict:
    materials = document.get("materials", {}).get("video_effects", [])
    effect_tracks = [track for track in document.get("tracks", []) if track.get("type") == "effect"]
    scene_materials = [material for material in materials if material.get("type") == "video_effect"]
    face_materials = [material for material in materials if material.get("type") == "face_effect"]
    scene_track = next((track for track in effect_tracks if track.get("name") == "Effects"), None)
    character_track = next((track for track in effect_tracks if track.get("name") == "CharacterEffects"), None)
    segments = scene_track.get("segments", []) if scene_track else []
    character_segments = character_track.get("segments", []) if character_track else []
    material_ids = {material.get("id") for material in materials}
    material_by_id = {material.get("id"): material for material in materials}
    all_segments = segments + character_segments
    missing_material_ids = sorted({
        segment.get("material_id")
        for segment in all_segments
        if segment.get("material_id") not in material_ids
    })
    wrong_material_types = sorted({
        material.get("type")
        for material in materials
        if material.get("type") not in {"video_effect", "face_effect"}
    })
    result = {
        "effect_track_count": len(effect_tracks),
        "effect_segment_count": len(segments),
        "video_effect_material_count": len(scene_materials),
        "character_effect_segment_count": len(character_segments),
        "face_effect_material_count": len(face_materials),
        "missing_material_ids": missing_material_ids,
        "wrong_material_types": wrong_material_types,
    }
    errors = []
    wrong_scene_types = sorted({
        material_by_id.get(segment.get("material_id"), {}).get("type")
        for segment in segments
        if material_by_id.get(segment.get("material_id"), {}).get("type") != "video_effect"
    })
    wrong_character_types = sorted({
        material_by_id.get(segment.get("material_id"), {}).get("type")
        for segment in character_segments
        if material_by_id.get(segment.get("material_id"), {}).get("type") != "face_effect"
    })
    if scene_track is None:
        errors.append("Effects track does not exist")
    if expected_count is not None and len(segments) != expected_count:
        errors.append(f"Expected {expected_count} effect segments, found {len(segments)}")
    if expected_count is not None and len(scene_materials) != expected_count:
        errors.append(f"Expected {expected_count} video-effect materials, found {len(scene_materials)}")
    if len(scene_materials) != len(segments):
        errors.append("Effect segment and video-effect material counts differ")
    if missing_material_ids:
        errors.append("Effect segments reference missing video-effect materials")
    if expected_character_count is not None:
        if character_track is None:
            errors.append("CharacterEffects track does not exist")
        if len(character_segments) != expected_character_count:
            errors.append(f"Expected {expected_character_count} character-effect segments, found {len(character_segments)}")
        if len(face_materials) != expected_character_count:
            errors.append(f"Expected {expected_character_count} face-effect materials, found {len(face_materials)}")
        if len(face_materials) != len(character_segments):
            errors.append("Character-effect segment and face-effect material counts differ")
    if wrong_material_types:
        errors.append("Unknown entries found in materials.video_effects")
    if wrong_scene_types:
        errors.append(f"Effects track references non-video-effect materials: {wrong_scene_types}")
    if wrong_character_types:
        errors.append(f"CharacterEffects track references non-face-effect materials: {wrong_character_types}")
    result["status"] = "passed" if not errors else "failed"
    result["errors"] = errors
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft-path", type=Path)
    source.add_argument("--draft-name")
    parser.add_argument("--expected-count", type=int, default=None)
    parser.add_argument("--expected-character-count", type=int, default=None)
    args = parser.parse_args()

    try:
        draft_path = load_draft_path(args)
        draft_info = draft_path / "draft_info.json"
        if not draft_info.is_file():
            raise RuntimeError(f"draft_info.json not found: {draft_info}")
        result = validate(json.loads(draft_info.read_text(encoding="utf-8")), args.expected_count, args.expected_character_count)
        result["draft_path"] = str(draft_path)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        result = {"status": "failed", "errors": [str(exc)]}

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
