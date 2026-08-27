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


def material_content(material: dict) -> dict:
    value = material.get("content", "")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def material_style(material: dict, segment: dict, effects_by_id: dict[str, dict]) -> tuple[str, str]:
    content = material_content(material)
    styles = content.get("styles", []) if isinstance(content, dict) else []
    flower = any(isinstance(style, dict) and style.get("effectStyle") for style in styles)
    flower = flower or any(
        effects_by_id.get(reference, {}).get("type") == "text_effect"
        for reference in segment.get("extra_material_refs", [])
    )
    bubble = bool(material.get("background_style"))
    colors = [style.get("fill", {}).get("content", {}).get("solid", {}).get("color", []) for style in styles if isinstance(style, dict)]
    rich_text = len(styles) > 1 or any(
        len(color) == 3 and (float(color[1]) < 0.65 or float(color[2]) > 0.35)
        for color in colors
    )
    kind = "flower" if flower else "bubble" if bubble else "keyword" if rich_text else "base"
    transform_y = float(segment.get("clip", {}).get("transform", {}).get("y", -0.72))
    position = "middle" if transform_y > -0.4 else "bottom"
    return kind, position


def validate(
    document: dict,
    srt_entries: list[dict],
    track_name: str,
    require_style_variation: bool = False,
    caption_layout_review: dict | None = None,
    require_visual_layout_review: bool = False,
    caption_plan: dict | None = None,
    require_semantic_design: bool = False,
) -> dict:
    materials = document.get("materials", {}).get("texts", [])
    text_by_id = {item.get("id"): material_text(item) for item in materials}
    material_by_id = {item.get("id"): item for item in materials}
    effects_by_id = {item.get("id"): item for item in document.get("materials", {}).get("effects", [])}
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
    presentation = [
        material_style(material_by_id.get(segment.get("material_id"), {}), segment, effects_by_id)
        for segment in segments
    ]
    style_types = sorted({kind for kind, _ in presentation})
    positions = sorted({position for _, position in presentation})
    if require_style_variation and len(segments) >= 2:
        if len(style_types) < 2:
            errors.append("subtitle presentation has fewer than two style types")
        if len(positions) < 2:
            errors.append("subtitle presentation has fewer than two positions")
    for index in range(1, len(presentation) - 2):
        if presentation[index][0] in {"bubble", "flower"} and presentation[index - 1][0] == presentation[index][0] == presentation[index + 1][0]:
            errors.append(f"subtitle presentation repeats {presentation[index][0]!r} three times in a row")
            break
    layouts = (caption_layout_review or {}).get("layouts", [])
    layout_by_index = {int(item["index"]): item for item in layouts if isinstance(item, dict) and "index" in item}
    if require_visual_layout_review and (not caption_layout_review or caption_layout_review.get("status") != "succeeded"):
        errors.append("caption visual layout review is missing or did not pass")
    if require_visual_layout_review:
        for index, segment in enumerate(segments):
            layout = layout_by_index.get(index)
            if layout is None:
                errors.append(f"subtitle {index + 1} has no visual layout review")
                continue
            actual_y = float(segment.get("clip", {}).get("transform", {}).get("y", -0.72))
            plan_cues = (caption_plan or {}).get("cues", [])
            expected_y = float(plan_cues[index]["transform_y"]) if index < len(plan_cues) else float(layout["transform_y"])
            if abs(actual_y - expected_y) > 0.02:
                errors.append(f"subtitle {index + 1} does not match the visual layout review")
    semantic_summary = None
    if require_semantic_design:
        cues = (caption_plan or {}).get("cues", [])
        if not caption_plan or caption_plan.get("ai_review", {}).get("status") != "approved":
            errors.append("an AI-approved semantic caption plan is required")
        if len(cues) != len(segments):
            errors.append("semantic caption plan does not cover every subtitle")
        expected_highlights = 0
        expected_cards = 0
        for index, (segment, cue, entry) in enumerate(zip(segments, cues, srt_entries), start=1):
            material = material_by_id.get(segment.get("material_id"), {})
            content = material_content(material)
            styles = content.get("styles", []) if isinstance(content.get("styles", []), list) else []
            expected = str(cue.get("presentation", "base"))
            actual = presentation[index - 1][0]
            if expected == "keyword" and actual != "keyword":
                errors.append(f"subtitle {index} is missing approved rich-text keyword styling")
            if expected in {"flower", "bubble"} and actual != expected:
                errors.append(f"subtitle {index} does not materialize approved {expected} styling")
            expected_spans = cue.get("keyword_spans", [])
            actual_ranges = [style.get("range") for style in styles if isinstance(style, dict)]
            planned_ranges = [[int(span["start"]), int(span["end"])] for span in expected_spans]
            if any(value not in actual_ranges for value in planned_ranges):
                errors.append(f"subtitle {index} rich-text ranges omit an approved keyword span")
            covered = set()
            for value in actual_ranges:
                if isinstance(value, list) and len(value) == 2:
                    covered.update(range(int(value[0]), int(value[1])))
            if styles and covered != set(range(len(entry["text"]))):
                errors.append(f"subtitle {index} rich-text styles do not cover every character")
            if expected not in {"bubble"} and styles:
                for style in styles:
                    value = style.get("range")
                    if value in planned_ranges:
                        continue
                    color = style.get("fill", {}).get("content", {}).get("solid", {}).get("color", [])
                    if len(color) != 3 or abs(float(color[0]) - 1.0) > 0.08 or not 0.72 <= float(color[1]) <= 0.95 or float(color[2]) > 0.25:
                        errors.append(f"subtitle {index} does not use the stable yellow base style outside keyword spans")
                        break
            expected_highlights += bool(cue.get("highlight", {}).get("enabled"))
            expected_cards += bool(cue.get("card", {}).get("enabled"))

        text_tracks = {track.get("name"): track for track in document.get("tracks", []) if track.get("type") == "text"}
        expected_track_counts = {
            "CaptionHighlights": expected_highlights,
            "CaptionCards": expected_cards,
            "Disclaimer": int(bool((caption_plan or {}).get("disclaimer", {}).get("enabled"))),
        }
        for name, expected_count in expected_track_counts.items():
            actual_count = len(text_tracks.get(name, {}).get("segments", []))
            if actual_count != expected_count:
                errors.append(f"{name} segment count differs: draft={actual_count} plan={expected_count}")
        semantic_summary = {
            "keyword_cues": sum(bool(cue.get("keyword_spans")) for cue in cues),
            "highlight_segments": expected_highlights,
            "card_segments": expected_cards,
            "disclaimer_segments": expected_track_counts["Disclaimer"],
        }
    return {
        "status": "passed" if not errors else "failed",
        "subtitle_count": len(segments),
        "style_types": style_types,
        "positions": positions,
        "semantic_design": semantic_summary,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft-path", type=Path)
    source.add_argument("--draft-name")
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--track-name", default="Subtitles")
    parser.add_argument("--caption-layout-review", type=Path)
    parser.add_argument("--require-style-variation", action="store_true")
    parser.add_argument("--require-visual-layout-review", action="store_true")
    parser.add_argument("--caption-plan", type=Path)
    parser.add_argument("--require-semantic-design", action="store_true")
    args = parser.parse_args()
    try:
        draft_path = args.draft_path.resolve() if args.draft_path else (default_draft_root() / args.draft_name).resolve()
        document = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
        layout_review = json.loads(args.caption_layout_review.read_text(encoding="utf-8")) if args.caption_layout_review else None
        caption_plan = json.loads(args.caption_plan.read_text(encoding="utf-8")) if args.caption_plan else None
        result = validate(
            document, read_srt(args.srt.resolve()), args.track_name,
            args.require_style_variation, layout_review, args.require_visual_layout_review,
            caption_plan, args.require_semantic_design,
        )
        result["draft_path"] = str(draft_path)
        result["srt"] = str(args.srt.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {"status": "failed", "errors": [str(error)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
