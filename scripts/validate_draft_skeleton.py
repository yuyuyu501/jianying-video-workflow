#!/usr/bin/env python3
"""Validate the named empty-track skeleton required before JianYing assembly."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


REQUIRED_TRACKS = {
    "MainVisual": "video",
    "B_Roll": "video",
    "SpeakerPiP": "video",
    "Filters": "filter",
    "Narration": "audio",
    "SFX": "audio",
    "Effects": "effect",
    "CharacterEffects": "effect",
    "Stickers": "sticker",
    "Subtitles": "text",
    "CaptionHighlights": "text",
    "CaptionCards": "text",
    "Disclaimer": "text",
}
REQUIRED_TRACK_ORDER = list(REQUIRED_TRACKS)


def default_draft_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not set; pass --draft-path explicitly")
    return Path(local_app_data) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def validate(document: dict) -> dict:
    tracks = document.get("tracks", [])
    by_name: dict[str, list[dict]] = {}
    for track in tracks:
        by_name.setdefault(str(track.get("name", "")), []).append(track)

    errors = []
    actual_order = [str(track.get("name", "")) for track in tracks]
    if actual_order != REQUIRED_TRACK_ORDER:
        errors.append(f"track order {actual_order!r} does not match required order {REQUIRED_TRACK_ORDER!r}")
    for name, expected_type in REQUIRED_TRACKS.items():
        matches = by_name.get(name, [])
        if len(matches) != 1:
            errors.append(f"required track {name!r} appears {len(matches)} times")
            continue
        track = matches[0]
        if track.get("type") != expected_type:
            errors.append(f"track {name!r} has type {track.get('type')!r}, expected {expected_type!r}")
        if track.get("segments", []):
            errors.append(f"skeleton track {name!r} is not empty")

    broll = by_name.get("B_Roll", [None])[0]
    if broll is not None and broll.get("attribute") != 1:
        errors.append("B_Roll must be explicitly muted before source material is added")

    return {
        "status": "passed" if not errors else "failed",
        "required_tracks": REQUIRED_TRACKS,
        "required_track_order": REQUIRED_TRACK_ORDER,
        "track_count": len(tracks),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft-path", type=Path)
    source.add_argument("--draft-name")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        draft_path = args.draft_path.resolve() if args.draft_path else (default_draft_root() / args.draft_name).resolve()
        document = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
        result = validate(document)
        result["draft_path"] = str(draft_path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        result = {"status": "failed", "errors": [str(error)]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
