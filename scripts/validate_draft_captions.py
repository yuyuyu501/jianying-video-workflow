#!/usr/bin/env python3
"""Verify that a JianYing subtitle track exactly materializes an approved SRT."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def default_draft_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not set; pass --draft-path explicitly")
    return Path(local_app_data) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def parse_time(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
    if not match:
        raise ValueError(f"invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, millis = (int(item) for item in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def read_srt(path: Path) -> list[dict]:
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8").strip())
    entries = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines()]
        if len(lines) < 3 or " --> " not in lines[1]:
            raise ValueError(f"invalid SRT block: {block[:80]!r}")
        start, end = (parse_time(value) for value in lines[1].split(" --> ", 1))
        entries.append({"start": start, "end": end, "text": "\n".join(lines[2:]).strip()})
    return entries


def material_text(material: dict) -> str:
    value = material.get("content", "")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value.strip()
    return str(value.get("text", "")).strip() if isinstance(value, dict) else ""


def validate(document: dict, srt_entries: list[dict], track_name: str) -> dict:
    text_by_id = {item.get("id"): material_text(item) for item in document.get("materials", {}).get("texts", [])}
    track = next((item for item in document.get("tracks", []) if item.get("type") == "text" and item.get("name") == track_name), None)
    errors = []
    if track is None:
        return {"status": "failed", "errors": [f"subtitle track does not exist: {track_name}"]}
    segments = sorted(track.get("segments", []), key=lambda item: item.get("target_timerange", {}).get("start", 0))
    if len(segments) != len(srt_entries):
        errors.append(f"subtitle count differs: draft={len(segments)} SRT={len(srt_entries)}")
    for index, (segment, entry) in enumerate(zip(segments, srt_entries), start=1):
        timerange = segment.get("target_timerange", {})
        start = float(timerange.get("start", 0)) / 1_000_000
        end = start + float(timerange.get("duration", 0)) / 1_000_000
        if abs(start - entry["start"]) > 0.001 or abs(end - entry["end"]) > 0.001:
            errors.append(f"subtitle time differs at entry {index}")
        if text_by_id.get(segment.get("material_id"), "") != entry["text"]:
            errors.append(f"subtitle text differs at entry {index}")
    return {"status": "passed" if not errors else "failed", "subtitle_count": len(segments), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft-path", type=Path)
    source.add_argument("--draft-name")
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--track-name", default="Subtitles")
    args = parser.parse_args()
    try:
        draft_path = args.draft_path.resolve() if args.draft_path else (default_draft_root() / args.draft_name).resolve()
        document = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
        result = validate(document, read_srt(args.srt.resolve()), args.track_name)
        result["draft_path"] = str(draft_path)
        result["srt"] = str(args.srt.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {"status": "failed", "errors": [str(error)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
