#!/usr/bin/env python3
"""Materialize a reviewed speech-led plan on a verified, empty JianYing skeleton."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

from caption_design_director import validate as validate_caption_plan
from caption_presentation import KEYWORD_COLORS, available_flower_effects, materialization_plan
from validate_draft_captions import read_srt, validate as validate_captions
from validate_draft_effects import validate as validate_effects
from validate_draft_filters import validate as validate_filters
from validate_draft_narration import validate as validate_narration
from validate_draft_pip import validate as validate_pip
from validate_draft_skeleton import validate as validate_skeleton
from validate_draft_stickers import validate as validate_stickers
from visual_finish_director import validate_filter_plan, validate_sticker_plan
from analyze_pip_faces import pip_request_mode


ROOT = Path(__file__).resolve().parents[1]


def configure_console_output() -> None:
    """Keep editor-library status output from failing on Windows GBK consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def locate_editor_skill() -> Path:
    configured = os.environ.get("JY_SKILL_ROOT", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([Path.home() / ".codex" / "skills" / "jianying-editor", Path.home() / ".agents" / "skills" / "jianying-editor"])
    for candidate in candidates:
        if (candidate / "scripts" / "jy_wrapper.py").is_file():
            return candidate.resolve()
    raise RuntimeError("jianying-editor Skill is not installed; run scripts/install.py first")


def default_drafts_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not set; pass --drafts-root")
    return Path(local_app_data) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def has_audio_stream(path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return any(stream.get("codec_type") == "audio" for stream in json.loads(result.stdout).get("streams", []))


def prepare_pip_crop(visual: Path, item: dict, output_dir: Path, index: int) -> dict:
    """Bake a face-centered square crop before JianYing materialization.

    JianYing's mask coordinates are relative to the whole source material.
    Applying a non-central mask to a full-frame vertical video makes the crop
    depend on editor transform semantics and is easy to misread in the UI.
    The reviewed head envelope is therefore converted into a real square MP4;
    the draft only applies a centered circle mask and a placement transform.
    """
    review = item["speaker_pip"].get("visual_review") or {}
    envelope = review.get("head_envelope") or item["speaker_pip"].get("head_envelope")
    if not isinstance(envelope, dict):
        raise ValueError(f"PiP segment {index} has no reviewed head envelope")
    frame_size = review.get("frame_size", {})
    width = int(frame_size.get("width", 1080))
    height = int(frame_size.get("height", 1920))
    x, y = float(envelope["x"]), float(envelope["y"])
    box_width, box_height = float(envelope["width"]), float(envelope["height"])
    side = int(math.ceil(max(box_width, box_height) * 1.18))
    side = max(2, min(side, width, height))
    center_x, center_y = x + box_width / 2, y + box_height / 2
    left = int(round(max(0, min(width - side, center_x - side / 2))))
    top = int(round(max(0, min(height - side, center_y - side / 2))))
    if not (left <= x and top <= y and left + side >= x + box_width and top + side >= y + box_height):
        raise ValueError(f"PiP segment {index} crop cannot contain the reviewed complete head")
    side -= side % 2
    left -= left % 2
    top -= top % 2
    duration = float(item["duration"])
    start = float(item["start"])
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"pip_{index:02d}_head.mp4"
    command = [
        "ffmpeg", "-y", "-v", "error", "-i", str(visual), "-ss", f"{start:.6f}",
        "-t", f"{duration:.6f}", "-vf", f"crop={side}:{side}:{left}:{top},scale=720:720:flags=lanczos",
        "-an", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, check=True)
    if not output.is_file() or output.stat().st_size <= 0:
        raise ValueError(f"PiP segment {index} crop output was not created")
    if has_audio_stream(output):
        raise ValueError(f"PiP segment {index} crop unexpectedly contains audio")
    return {"path": str(output.resolve()), "left": left, "top": top, "size": side}


def require_empty_skeleton(document: dict) -> None:
    report = validate_skeleton(document)
    if report["status"] != "passed":
        raise ValueError("draft is not a verified empty workflow skeleton: " + "; ".join(report["errors"]))


def load_broll_plan(path: Path, pip_visual_review: Path | None = None) -> list[dict]:
    payload = load_json(path)
    segments = payload.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("B-roll plan requires a segments array")
    visual_reviews = {}
    if pip_visual_review is not None:
        review_payload = load_json(pip_visual_review)
        if review_payload.get("status") not in {"succeeded", "skipped"}:
            raise ValueError("PiP visual review did not pass")
        visual_reviews = {
            int(item["segment_index"]): item for item in review_payload.get("pip_reviews", [])
            if item.get("status") == "approved"
        }
    normalized = []
    for index, item in enumerate(segments, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"B-roll segment {index} is not an object")
        video = Path(str(item.get("video", ""))).resolve()
        start, duration = float(item.get("start", -1)), float(item.get("duration", 0))
        source_start = float(item.get("source_start", 0))
        if not video.is_file() or start < 0 or duration <= 0 or source_start < 0:
            raise ValueError(f"B-roll segment {index} requires an existing video plus non-negative start/source_start and positive duration")
        speaker_pip = item.get("speaker_pip", {})
        if speaker_pip is True:
            speaker_pip = {"enabled": True}
        if not isinstance(speaker_pip, dict):
            raise ValueError(f"B-roll segment {index} speaker_pip must be an object or true")
        request_mode = pip_request_mode(item)
        review = visual_reviews.get(index)
        enabled = bool(speaker_pip.get("enabled", False)) or (review is not None and request_mode != "off")
        if enabled and review is None:
            raise ValueError(f"B-roll segment {index} requires a passing face-driven PiP visual review")
        if review is not None and (abs(float(review.get("final_start", -1)) - start) > 0.02 or abs(float(review.get("final_duration", -1)) - duration) > 0.02):
            raise ValueError(f"PiP visual review does not match B-roll segment {index}")
        candidate = review.get("selected_candidate", {}) if review else {}
        scale = float(review.get("scale") if review else speaker_pip.get("scale", 0.34))
        face_center_x = float(review.get("face_center_x") if review else speaker_pip.get("face_center_x", 0.0))
        face_center_y = float(review.get("face_center_y") if review else speaker_pip.get("face_center_y", -0.22))
        mask_size = float(review.get("mask_size") if review else speaker_pip.get("mask_size", 0.52))
        visible_diameter_ratio = float(review.get("visible_diameter_ratio", 0.22) if review else speaker_pip.get("visible_diameter_ratio", 0.22))
        placement_x = float(candidate.get("placement_transform_x") if review else (-0.56 if speaker_pip.get("position") == "upper_left" else 0.56))
        placement_y = float(candidate.get("placement_transform_y") if review else 0.20)
        if not 0.18 <= scale <= 1.15 or not 0.16 <= mask_size <= 0.60 or not -1.0 <= face_center_x <= 1.0 or not -1.0 <= face_center_y <= 1.0 or not -0.90 <= placement_x <= 0.90 or not -0.90 <= placement_y <= 0.90:
            raise ValueError(f"B-roll segment {index} has invalid speaker_pip scale or face center")
        normalized.append({
            "video": video,
            "start": start,
            "duration": duration,
            "source_start": source_start,
            "speaker_pip": {
                "enabled": enabled,
                "mode": request_mode,
                "position": str(candidate.get("position") if review else speaker_pip.get("position", "upper_right")),
                "scale": scale,
                "face_center_x": face_center_x,
                "face_center_y": face_center_y,
                "mask_size": mask_size,
                "visible_diameter_ratio": visible_diameter_ratio,
                "placement_transform_x": placement_x,
                "placement_transform_y": placement_y,
                "visual_review": review,
            },
        })
    return normalized


def add_tracks(project, draft) -> None:
    project.script.add_track(draft.TrackType.video, "MainVisual", absolute_index=0)
    project.script.add_track(draft.TrackType.video, "B_Roll", mute=True, absolute_index=1)
    project.script.add_track(draft.TrackType.video, "SpeakerPiP", absolute_index=2)
    project.script.add_track(draft.TrackType.filter, "Filters", absolute_index=3)
    project.script.add_track(draft.TrackType.audio, "Narration", absolute_index=4)
    project.script.add_track(draft.TrackType.audio, "SFX", absolute_index=5)
    project.script.add_track(draft.TrackType.effect, "Effects", absolute_index=6)
    project.script.add_track(draft.TrackType.effect, "CharacterEffects", absolute_index=7)
    project.script.add_track(draft.TrackType.sticker, "Stickers", absolute_index=8)
    project.script.add_track(draft.TrackType.text, "Subtitles", absolute_index=9)
    project.script.add_track(draft.TrackType.text, "CaptionHighlights", absolute_index=10)
    project.script.add_track(draft.TrackType.text, "CaptionCards", absolute_index=11)
    project.script.add_track(draft.TrackType.text, "Disclaimer", absolute_index=12)


def add_caption(project, draft, entry: dict, presentation: dict) -> None:
    variant = presentation["presentation"]
    size = float(presentation["font_size"])
    if variant == "bubble":
        style = draft.TextStyle(size=size, bold=True, color=(0.08, 0.05, 0.04), align=1, auto_wrapping=True, max_line_width=0.84)
        border = draft.TextBorder(color=(0.0, 0.0, 0.0), alpha=0.9, width=36.0)
        background = draft.TextBackground(color="#FFE34D", alpha=0.98, round_radius=0.95, height=0.27, width=0.92)
    elif variant == "flower":
        style = draft.TextStyle(size=size, bold=True, color=(1.0, 0.86, 0.10), align=1, auto_wrapping=True, max_line_width=0.88)
        border = draft.TextBorder(color=(0.08, 0.02, 0.02), alpha=1.0, width=68.0)
        background = None
    else:
        style = draft.TextStyle(size=size, bold=True, color=(1.0, 0.86, 0.10), align=1, auto_wrapping=True, max_line_width=0.88)
        border = draft.TextBorder(color=(0.0, 0.0, 0.0), alpha=1.0, width=74.0)
        background = None
    rich_spans = []
    keyword_spans = sorted(presentation.get("keyword_spans", []), key=lambda span: int(span["start"]))
    if keyword_spans:
        cursor = 0
        for span in keyword_spans:
            start, end = int(span["start"]), int(span["end"])
            if cursor < start:
                rich_spans.append(draft.RichTextSpan(cursor, start, style=style))
            rich_spans.append(draft.RichTextSpan(
                start, end, color=KEYWORD_COLORS[str(span["color"])],
                size=size * float(span["size_scale"]), bold=True,
            ))
            cursor = end
        if cursor < len(entry["text"]):
            rich_spans.append(draft.RichTextSpan(cursor, len(entry["text"]), style=style))
    segment = draft.TextSegment(
        entry["text"], draft.Timerange(round(entry["start"] * 1_000_000), round((entry["end"] - entry["start"]) * 1_000_000)),
        style=style, border=border, background=background,
        shadow=draft.TextShadow(color=(0.0, 0.0, 0.0), alpha=0.72, diffuse=18.0, distance=3.0, angle=-45.0),
        rich_spans=rich_spans,
        clip_settings=draft.ClipSettings(transform_y=presentation["transform_y"]),
    )
    if variant == "flower":
        segment.add_effect(presentation["flower_effect_id"])
        # The current pyJianYingDraft release can serialize TextEffect but does
        # not accept it in its generic material-membership check. Register it
        # once here and clear the temporary segment reference to avoid that
        # incompatible second registration during project.save().
        project.script.materials.filters.append(segment.effect)
        segment.effect = None
    project.script.add_segment(segment, "Subtitles")


def add_caption_overlays(project, draft, entry: dict, presentation: dict) -> tuple[int, int]:
    timerange = draft.Timerange(
        round(entry["start"] * 1_000_000),
        round((entry["end"] - entry["start"]) * 1_000_000),
    )
    highlight_count = 0
    card_count = 0
    highlight = presentation.get("highlight", {})
    if highlight.get("enabled"):
        text = str(highlight.get("text", "")).strip()
        if not text:
            raise ValueError(f"caption cue {int(presentation['index']) + 1} enables an empty highlight")
        segment = draft.TextSegment(
            text, timerange,
            style=draft.TextStyle(size=13.0, bold=True, color=(0.05, 0.04, 0.02), align=1, auto_wrapping=True, max_line_width=0.84),
            border=draft.TextBorder(color=(0.0, 0.0, 0.0), alpha=1.0, width=18.0),
            background=draft.TextBackground(color="#FFD928", alpha=1.0, round_radius=0.12, height=0.34, width=0.96),
            clip_settings=draft.ClipSettings(
                transform_x=float(highlight.get("transform_x", 0.0)),
                transform_y=float(highlight.get("transform_y", 0.38)),
            ),
        )
        project.script.add_segment(segment, "CaptionHighlights")
        highlight_count = 1
    card = presentation.get("card", {})
    if card.get("enabled"):
        title = str(card.get("title", "")).strip()
        body = str(card.get("body", "")).strip()
        if not title or not body:
            raise ValueError(f"caption cue {int(presentation['index']) + 1} enables an incomplete card")
        card_text = f"{title}\n{body}"
        card_style = draft.TextStyle(size=10.4, bold=True, color=(0.08, 0.07, 0.05), align=1, auto_wrapping=True, max_line_width=0.78, line_spacing=10)
        segment = draft.TextSegment(
            card_text, timerange,
            style=card_style,
            background=draft.TextBackground(color="#FFFFFF", alpha=0.96, round_radius=0.18, height=0.48, width=1.0),
            shadow=draft.TextShadow(color=(0.0, 0.0, 0.0), alpha=0.35, diffuse=26.0, distance=4.0, angle=-45.0),
            rich_spans=[
                draft.RichTextSpan(0, len(title), color=(0.96, 0.46, 0.02), size=12.2, bold=True),
                draft.RichTextSpan(len(title), len(card_text), style=card_style),
            ],
            clip_settings=draft.ClipSettings(
                transform_x=float(card.get("transform_x", 0.0)),
                transform_y=float(card.get("transform_y", 0.02)),
            ),
        )
        project.script.add_segment(segment, "CaptionCards")
        card_count = 1
    return highlight_count, card_count


def add_disclaimer(project, draft, disclaimer: dict) -> int:
    if not disclaimer.get("enabled"):
        return 0
    text = str(disclaimer.get("text", "")).strip()
    start, end = float(disclaimer.get("start", 0)), float(disclaimer.get("end", 0))
    if not text or start < 0 or end <= start:
        raise ValueError("enabled disclaimer requires text and a valid start/end")
    segment = draft.TextSegment(
        text, draft.Timerange(round(start * 1_000_000), round((end - start) * 1_000_000)),
        style=draft.TextStyle(size=4.8, color=(1.0, 1.0, 1.0), align=1, auto_wrapping=True, max_line_width=0.84),
        border=draft.TextBorder(color=(0.0, 0.0, 0.0), alpha=0.85, width=48.0),
        clip_settings=draft.ClipSettings(transform_y=-0.82),
    )
    project.script.add_segment(segment, "Disclaimer")
    return 1


def materialize_effects(project, draft, asset_plan: dict) -> tuple[int, int]:
    if asset_plan.get("ai_review", {}).get("status") not in {None, "approved"}:
        raise ValueError("asset plan must have approved AI selections before materialization")
    visual_effects = asset_plan.get("visual_effects", [])
    character_effects = asset_plan.get("character_effects", [])
    for item in visual_effects:
        effect = draft.VideoSceneEffectType.from_name(str(item["source_identifier"]))
        project.script.add_effect(effect, draft.Timerange(round(float(item["start"]) * 1_000_000), round(float(item["duration"]) * 1_000_000)), "Effects")
    for item in character_effects:
        effect = draft.VideoCharacterEffectType.from_name(str(item["source_identifier"]))
        project.script.add_effect(effect, draft.Timerange(round(float(item["start"]) * 1_000_000), round(float(item["duration"]) * 1_000_000)), "CharacterEffects")
    return len(visual_effects), len(character_effects)


def load_finish_plan(path: Path, kind: str) -> dict:
    payload = load_json(path.resolve())
    review = payload.get("ai_review", {})
    if not isinstance(review, dict) or review.get("status") != "approved":
        raise ValueError(f"{kind} plan must have ai_review.status=approved")
    items = payload.get(kind, [])
    if not isinstance(items, list):
        raise ValueError(f"{kind} plan requires a {kind} array")
    if not items and not str(payload.get("skip_reason", "")).strip():
        raise ValueError(f"empty {kind} plan requires a specific skip_reason")
    return payload


def materialize_filters(project, draft, plan: dict) -> int:
    previous_end = 0.0
    for index, item in enumerate(sorted(plan.get("filters", []), key=lambda value: float(value["start"])), start=1):
        start, duration = float(item["start"]), float(item["duration"])
        intensity = float(item["intensity"])
        if start < 0 or duration <= 0 or not 5.0 <= intensity <= 60.0:
            raise ValueError(f"filter {index} has invalid timing or intensity")
        if start < previous_end - 0.000001:
            raise ValueError(f"filter {index} overlaps the previous Filters segment")
        if item.get("status") != "approved" or not str(item.get("visual_evidence", "")).strip():
            raise ValueError(f"filter {index} requires approved status and frame-grounded visual_evidence")
        meta = draft.FilterType.from_name(str(item["name"]))
        requested_resource = str(item.get("resource_id", "")).strip()
        if requested_resource and requested_resource != meta.value.resource_id:
            raise ValueError(f"filter {index} resource_id does not match the resolved JianYing filter")
        project.script.add_filter(
            meta,
            draft.Timerange(round(start * 1_000_000), round(duration * 1_000_000)),
            "Filters",
            intensity=intensity,
        )
        previous_end = start + duration
    return len(plan.get("filters", []))


def materialize_stickers(project, draft, plan: dict) -> int:
    for index, item in enumerate(plan.get("stickers", []), start=1):
        start, duration = float(item["start"]), float(item["duration"])
        resource_id = str(item.get("resource_id", "")).strip()
        if not resource_id or start < 0 or duration <= 0:
            raise ValueError(f"sticker {index} requires a real resource_id and valid timing")
        if item.get("status") != "approved" or not str(item.get("visual_evidence", "")).strip():
            raise ValueError(f"sticker {index} requires approved status and frame-grounded visual_evidence")
        if item.get("collision_review", {}).get("status") != "passed":
            raise ValueError(f"sticker {index} requires a passed collision_review")
        segment = draft.StickerSegment(
            resource_id,
            draft.Timerange(round(start * 1_000_000), round(duration * 1_000_000)),
            clip_settings=draft.ClipSettings(
                scale_x=float(item.get("scale", 1.0)),
                scale_y=float(item.get("scale", 1.0)),
                transform_x=float(item.get("transform_x", 0.0)),
                transform_y=float(item.get("transform_y", 0.0)),
                rotation=float(item.get("rotation", 0.0)),
            ),
        )
        project.script.add_segment(segment, "Stickers")
    return len(plan.get("stickers", []))


def main() -> int:
    configure_console_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-name", required=True)
    parser.add_argument("--drafts-root", type=Path)
    parser.add_argument("--visual", type=Path, required=True, help="Validated silent rough-cut visual MP4")
    parser.add_argument("--narration", type=Path, required=True, help="Validated narration-only M4A")
    parser.add_argument("--captions", type=Path, required=True, help="Final-timeline SRT")
    parser.add_argument("--broll-plan", type=Path, help="Optional approved B-roll plan")
    parser.add_argument("--pip-visual-review", type=Path, help="Optional face-detection review generated from the final rough-cut visual")
    parser.add_argument("--caption-layout-review", type=Path, required=True, help="Frame-checked caption layout review generated from the final rough-cut visual")
    parser.add_argument("--caption-plan", type=Path, required=True, help="AI-approved semantic caption design plan")
    parser.add_argument("--asset-plan", type=Path, help="Approved scene/character effect plan")
    parser.add_argument("--filter-plan", type=Path, required=True, help="Approved portrait filter/beautification plan; an empty plan needs a specific skip_reason")
    parser.add_argument("--sticker-plan", type=Path, required=True, help="Approved content-aware sticker plan; an empty plan needs a specific skip_reason")
    parser.add_argument("--finish-catalog", type=Path, required=True, help="Local JianYing filter/sticker catalog used to prove resource IDs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rebuild-empty-skeleton", action="store_true", help="Explicitly rebuild the verified empty skeleton in one editable session")
    args = parser.parse_args()
    draft_info_path: Path | None = None
    empty_skeleton_backup: bytes | None = None
    try:
        if not args.rebuild_empty_skeleton:
            raise ValueError("--rebuild-empty-skeleton is required; materialization never overwrites a populated draft")
        paths = [args.visual, args.narration, args.captions, args.caption_layout_review, args.caption_plan]
        if args.broll_plan:
            paths.append(args.broll_plan)
        if args.pip_visual_review:
            paths.append(args.pip_visual_review)
        if args.asset_plan:
            paths.append(args.asset_plan)
        paths.extend([args.filter_plan, args.sticker_plan, args.finish_catalog])
        for path in paths:
            if not path.resolve().is_file():
                raise FileNotFoundError(path)
        visual, narration, captions = args.visual.resolve(), args.narration.resolve(), args.captions.resolve()
        if has_audio_stream(visual):
            raise ValueError("rough-cut visual must have no audio stream; use the validated silent visual artifact")
        drafts_root = args.drafts_root.resolve() if args.drafts_root else default_drafts_root().resolve()
        draft_path = drafts_root / args.draft_name
        draft_info_path = draft_path / "draft_info.json"
        empty_skeleton_backup = draft_info_path.read_bytes()
        require_empty_skeleton(load_json(draft_info_path))
        pip_visual_review_payload = load_json(args.pip_visual_review.resolve()) if args.pip_visual_review else None
        broll = load_broll_plan(args.broll_plan.resolve(), args.pip_visual_review.resolve() if args.pip_visual_review else None) if args.broll_plan else []
        entries = read_srt(captions)
        expected_duration = max(entry["end"] for entry in entries)
        caption_layout_review = load_json(args.caption_layout_review.resolve())
        if caption_layout_review.get("status") != "succeeded":
            raise ValueError("caption visual layout review did not pass")
        layouts = caption_layout_review.get("layouts", [])
        if not isinstance(layouts, list) or len(layouts) != len(entries):
            raise ValueError("caption visual layout review does not cover every SRT entry")
        caption_plan = load_json(args.caption_plan.resolve())
        caption_plan_report = validate_caption_plan(caption_plan, entries)
        if caption_plan_report["status"] != "passed":
            raise ValueError("caption design plan validation failed: " + "; ".join(caption_plan_report["errors"]))
        editor_skill = locate_editor_skill()
        sys.path.insert(0, str(editor_skill / "scripts"))
        sys.path.insert(0, str(editor_skill / "scripts" / "vendor"))
        import pyJianYingDraft as draft
        from jy_wrapper import JyProject

        project = JyProject(args.draft_name, width=1080, height=1920, drafts_root=str(drafts_root), overwrite=True)
        add_tracks(project, draft)
        project.add_media_safe(str(visual), start_time=0.0, track_name="MainVisual")
        project.add_audio_safe(str(narration), start_time=0.0, track_name="Narration")
        pip_crop_dir = args.output.resolve().parent / "pip-face-clips"
        for index, item in enumerate(broll, start=1):
            project.add_media_safe(str(item["video"]), start_time=item["start"], duration=item["duration"], source_start=item["source_start"], track_name="B_Roll")
            if item["speaker_pip"]["enabled"]:
                pip = item["speaker_pip"]
                crop = prepare_pip_crop(visual, item, pip_crop_dir, index)
                pip["crop_path"] = crop["path"]
                pip["crop_box"] = crop
                if pip_visual_review_payload is not None:
                    for finding in pip_visual_review_payload.get("pip_reviews", []):
                        if int(finding.get("segment_index", 0)) == index:
                            finding["crop_mode"] = "baked_head"
                            finding["crop_video"] = crop["path"]
                            finding["crop_box"] = crop
                            break
                crop_material = draft.VideoMaterial(crop["path"])
                source_range = draft.Timerange(0, round(item["duration"] * 1_000_000))
                target_range = draft.Timerange(round(item["start"] * 1_000_000), source_range.duration)
                crop_mask_size = 0.90
                segment = draft.VideoSegment(
                    crop_material, target_range,
                    source_timerange=source_range, volume=0.0,
                    clip_settings=draft.ClipSettings(
                        scale_x=pip["visible_diameter_ratio"] / crop_mask_size,
                        scale_y=pip["visible_diameter_ratio"] / crop_mask_size,
                        transform_x=pip["placement_transform_x"], transform_y=pip["placement_transform_y"],
                    ),
                )
                segment.add_mask(
                    draft.MaskType.圆形,
                    center_x=0.0, center_y=0.0, size=crop_mask_size,
                    feather=1.5,
                )
                project.script.add_segment(segment, "SpeakerPiP")
        flowers = available_flower_effects(editor_skill / "assets" / "artistEffect")
        presentation = materialization_plan(caption_plan, available_effect_ids=flowers)
        asset_plan = load_json(args.asset_plan.resolve()) if args.asset_plan else {}
        filter_plan = load_finish_plan(args.filter_plan, "filters")
        sticker_plan = load_finish_plan(args.sticker_plan, "stickers")
        finish_catalog = load_json(args.finish_catalog.resolve())
        for label, finish_plan in (("filter", filter_plan), ("sticker", sticker_plan)):
            plan_duration = float(finish_plan.get("timeline_duration", -1))
            if abs(plan_duration - expected_duration) > 0.05:
                raise ValueError(f"{label} plan uses a stale timeline duration: {plan_duration:.3f}s != {expected_duration:.3f}s")
        plan_report = validate_filter_plan(filter_plan, finish_catalog)
        if not plan_report["valid"]:
            raise ValueError("filter plan validation failed: " + "; ".join(plan_report["errors"]))
        plan_report = validate_sticker_plan(sticker_plan, finish_catalog)
        if not plan_report["valid"]:
            raise ValueError("sticker plan validation failed: " + "; ".join(plan_report["errors"]))
        filter_count = materialize_filters(project, draft, filter_plan)
        visual_effect_count, character_effect_count = materialize_effects(project, draft, asset_plan)
        sticker_count = materialize_stickers(project, draft, sticker_plan)
        highlight_count = 0
        card_count = 0
        for entry, style in zip(entries, presentation):
            add_caption(project, draft, entry, style)
            added_highlights, added_cards = add_caption_overlays(project, draft, entry, style)
            highlight_count += added_highlights
            card_count += added_cards
        disclaimer_count = add_disclaimer(project, draft, caption_plan.get("disclaimer", {}))
        materialized_review_path = args.output.resolve().parent / "pip_visual_review.materialized.json"
        if pip_visual_review_payload is not None:
            pip_visual_review_payload["materialization"] = {
                "mode": "baked_head",
                "crop_dir": str(pip_crop_dir.resolve()),
            }
            materialized_review_path.write_text(
                json.dumps(pip_visual_review_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        saved = project.save()
        document = load_json(Path(saved["draft_path"]) / "draft_info.json")
        reports = {
            "captions": validate_captions(
                document,
                entries,
                "Subtitles",
                require_style_variation=False,
                caption_layout_review=caption_layout_review,
                require_visual_layout_review=True,
                caption_plan=caption_plan,
                require_semantic_design=True,
            ),
            "narration": validate_narration(document, "Narration", expected_duration),
            "pip": validate_pip(
                document, str(visual), require_pip=any(item["speaker_pip"]["enabled"] for item in broll),
                pip_visual_review=pip_visual_review_payload,
                require_visual_review=bool(args.pip_visual_review),
            ),
            "effects": validate_effects(document, visual_effect_count, character_effect_count),
            "filters": validate_filters(document, filter_plan, expected_count=filter_count),
            "stickers": validate_stickers(document, sticker_plan, expected_count=sticker_count),
        }
        errors = [f"{name}: " + "; ".join(report["errors"]) for name, report in reports.items() if report["status"] != "passed"]
        report = {
            "status": "succeeded" if not errors else "failed",
            "draft_name": args.draft_name,
            "draft_path": saved["draft_path"],
            "caption_presentation": presentation,
            "caption_plan": str(args.caption_plan.resolve()),
            "caption_plan_validation": caption_plan_report,
            "caption_highlight_segments": highlight_count,
            "caption_card_segments": card_count,
            "disclaimer_segments": disclaimer_count,
            "caption_layout_review": str(args.caption_layout_review.resolve()),
            "pip_visual_review": str(materialized_review_path) if pip_visual_review_payload is not None else None,
            "broll_segments": len(broll),
            "speaker_pip_segments": sum(item["speaker_pip"]["enabled"] for item in broll),
            "pip_crops": [
                {"segment_index": index, **item["speaker_pip"]["crop_box"]}
                for index, item in enumerate(broll, start=1)
                if item["speaker_pip"].get("enabled") and item["speaker_pip"].get("crop_box")
            ],
            "validation": reports,
            "errors": errors,
        }
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        rollback_error = None
        if draft_info_path is not None and empty_skeleton_backup is not None:
            try:
                draft_info_path.write_bytes(empty_skeleton_backup)
            except OSError as restore_error:
                rollback_error = str(restore_error)
        report = {"status": "failed", "errors": [str(error)]}
        if rollback_error:
            report["rollback_error"] = rollback_error
        elif empty_skeleton_backup is not None:
            report["rollback"] = "restored_empty_skeleton"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RESULT: " + json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
