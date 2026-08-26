#!/usr/bin/env python3
"""Materialize a reviewed speech-led plan on a verified, empty JianYing skeleton."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from caption_presentation import available_flower_effects, plan_caption_styles
from validate_draft_captions import read_srt, validate as validate_captions
from validate_draft_effects import validate as validate_effects
from validate_draft_narration import validate as validate_narration
from validate_draft_pip import validate as validate_pip
from validate_draft_skeleton import validate as validate_skeleton


ROOT = Path(__file__).resolve().parents[1]


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
        if review_payload.get("status") != "succeeded":
            raise ValueError("PiP visual review did not pass")
        visual_reviews = {int(item["segment_index"]): item for item in review_payload.get("pip_reviews", [])}
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
        enabled = bool(speaker_pip.get("enabled", False))
        review = visual_reviews.get(index)
        if enabled and review is None:
            raise ValueError(f"B-roll segment {index} requires a passing face-driven PiP visual review")
        if review is not None and (abs(float(review.get("final_start", -1)) - start) > 0.02 or abs(float(review.get("final_duration", -1)) - duration) > 0.02):
            raise ValueError(f"PiP visual review does not match B-roll segment {index}")
        scale = float(review.get("scale") if review else speaker_pip.get("scale", 0.34))
        face_center_x = float(review.get("face_center_x") if review else speaker_pip.get("face_center_x", 0.0))
        face_center_y = float(review.get("face_center_y") if review else speaker_pip.get("face_center_y", -0.22))
        mask_size = float(review.get("mask_size") if review else speaker_pip.get("mask_size", 0.52))
        if not 0.18 <= scale <= 1.15 or not 0.16 <= mask_size <= 0.60 or not -1.0 <= face_center_x <= 1.0 or not -1.0 <= face_center_y <= 1.0:
            raise ValueError(f"B-roll segment {index} has invalid speaker_pip scale or face center")
        normalized.append({
            "video": video,
            "start": start,
            "duration": duration,
            "source_start": source_start,
            "speaker_pip": {
                "enabled": enabled,
                "position": str(speaker_pip.get("position", "upper_right")),
                "scale": scale,
                "face_center_x": face_center_x,
                "face_center_y": face_center_y,
                "mask_size": mask_size,
                "visual_review": review,
            },
        })
    return normalized


def add_tracks(project, draft) -> None:
    project.script.add_track(draft.TrackType.video, "MainVisual")
    project.script.add_track(draft.TrackType.video, "B_Roll", mute=True, relative_index=1)
    project.script.add_track(draft.TrackType.video, "SpeakerPiP", relative_index=2)
    project.script.add_track(draft.TrackType.audio, "Narration")
    project.script.add_track(draft.TrackType.audio, "SFX")
    project.script.add_track(draft.TrackType.effect, "Effects")
    project.script.add_track(draft.TrackType.effect, "CharacterEffects")
    project.script.add_track(draft.TrackType.text, "Subtitles")


def add_caption(project, draft, entry: dict, presentation: dict) -> None:
    variant = presentation["variant"]
    size = float(presentation["font_size"])
    if variant == "alert_impact":
        style = draft.TextStyle(size=size, bold=True, color=(1.0, 0.18, 0.16), align=1, auto_wrapping=True, max_line_width=0.88)
        border = draft.TextBorder(color=(1.0, 1.0, 1.0), alpha=1.0, width=62.0)
        background = None
    elif variant == "bubble":
        style = draft.TextStyle(size=size, bold=True, color=(0.08, 0.05, 0.04), align=1, auto_wrapping=True, max_line_width=0.84)
        border = draft.TextBorder(color=(1.0, 1.0, 1.0), alpha=0.85, width=28.0)
        background = draft.TextBackground(color="#FFE34D", alpha=0.98, round_radius=0.95, height=0.27, width=0.92)
    elif variant == "flower":
        style = draft.TextStyle(size=size, bold=True, color=(1.0, 1.0, 1.0), align=1, auto_wrapping=True, max_line_width=0.88)
        border = draft.TextBorder(color=(0.08, 0.02, 0.02), alpha=1.0, width=68.0)
        background = None
    else:
        style = draft.TextStyle(size=size, bold=True, color=(1.0, 1.0, 1.0), align=1, auto_wrapping=True, max_line_width=0.88)
        border = draft.TextBorder(color=(0.0, 0.0, 0.0), alpha=1.0, width=68.0)
        background = None
    segment = draft.TextSegment(
        entry["text"], draft.Timerange(round(entry["start"] * 1_000_000), round((entry["end"] - entry["start"]) * 1_000_000)),
        style=style, border=border, background=background,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-name", required=True)
    parser.add_argument("--drafts-root", type=Path)
    parser.add_argument("--visual", type=Path, required=True, help="Validated silent rough-cut visual MP4")
    parser.add_argument("--narration", type=Path, required=True, help="Validated narration-only M4A")
    parser.add_argument("--captions", type=Path, required=True, help="Final-timeline SRT")
    parser.add_argument("--broll-plan", type=Path, required=True)
    parser.add_argument("--pip-visual-review", type=Path, required=True, help="Face-detection review generated from the final rough-cut visual")
    parser.add_argument("--caption-layout-review", type=Path, required=True, help="Frame-checked caption layout review generated from the final rough-cut visual")
    parser.add_argument("--asset-plan", type=Path, help="Approved scene/character effect plan")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rebuild-empty-skeleton", action="store_true", help="Explicitly rebuild the verified empty skeleton in one editable session")
    args = parser.parse_args()
    draft_info_path: Path | None = None
    empty_skeleton_backup: bytes | None = None
    try:
        if not args.rebuild_empty_skeleton:
            raise ValueError("--rebuild-empty-skeleton is required; materialization never overwrites a populated draft")
        paths = [args.visual, args.narration, args.captions, args.broll_plan, args.pip_visual_review, args.caption_layout_review]
        if args.asset_plan:
            paths.append(args.asset_plan)
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
        broll = load_broll_plan(args.broll_plan.resolve(), args.pip_visual_review.resolve())
        entries = read_srt(captions)
        caption_layout_review = load_json(args.caption_layout_review.resolve())
        if caption_layout_review.get("status") != "succeeded":
            raise ValueError("caption visual layout review did not pass")
        layouts = caption_layout_review.get("layouts", [])
        if not isinstance(layouts, list) or len(layouts) != len(entries):
            raise ValueError("caption visual layout review does not cover every SRT entry")
        editor_skill = locate_editor_skill()
        sys.path.insert(0, str(editor_skill / "scripts"))
        sys.path.insert(0, str(editor_skill / "scripts" / "vendor"))
        import pyJianYingDraft as draft
        from jy_wrapper import JyProject

        project = JyProject(args.draft_name, width=1080, height=1920, drafts_root=str(drafts_root), overwrite=True)
        add_tracks(project, draft)
        project.add_media_safe(str(visual), start_time=0.0, track_name="MainVisual")
        project.add_audio_safe(str(narration), start_time=0.0, track_name="Narration")
        for item in broll:
            project.add_media_safe(str(item["video"]), start_time=item["start"], duration=item["duration"], source_start=item["source_start"], track_name="B_Roll")
            if item["speaker_pip"]["enabled"]:
                source_start = item["start"]
                source_range = draft.Timerange(round(source_start * 1_000_000), round(item["duration"] * 1_000_000))
                pip = item["speaker_pip"]
                position = pip["position"]
                transform_x = -0.56 if position == "upper_left" else 0.56
                segment = draft.VideoSegment(
                    draft.VideoMaterial(str(visual)), draft.Timerange(round(item["start"] * 1_000_000), source_range.duration),
                    source_timerange=source_range, volume=0.0,
                    clip_settings=draft.ClipSettings(scale_x=pip["scale"], scale_y=pip["scale"], transform_x=transform_x, transform_y=0.53),
                )
                segment.add_mask(
                    draft.MaskType.圆形,
                    center_x=pip["face_center_x"] * segment.material_size[0] / 2,
                    center_y=pip["face_center_y"] * segment.material_size[1] / 2,
                    size=pip["mask_size"],
                    feather=1.5,
                )
                project.script.add_segment(segment, "SpeakerPiP")
        flowers = available_flower_effects(editor_skill / "assets" / "artistEffect")
        presentation = plan_caption_styles(
            entries,
            available_effect_ids=flowers,
            broll_segments=broll,
            layout_review=caption_layout_review,
        )
        for entry, style in zip(entries, presentation):
            add_caption(project, draft, entry, style)
        asset_plan = load_json(args.asset_plan.resolve()) if args.asset_plan else {}
        visual_effect_count, character_effect_count = materialize_effects(project, draft, asset_plan)
        saved = project.save()
        document = load_json(Path(saved["draft_path"]) / "draft_info.json")
        expected_duration = max(entry["end"] for entry in entries)
        reports = {
            "captions": validate_captions(
                document,
                entries,
                "Subtitles",
                require_style_variation=True,
                caption_layout_review=caption_layout_review,
                require_visual_layout_review=True,
            ),
            "narration": validate_narration(document, "Narration", expected_duration),
            "pip": validate_pip(document, str(visual), require_pip=any(item["speaker_pip"]["enabled"] for item in broll), pip_visual_review=load_json(args.pip_visual_review.resolve()), require_visual_review=True),
            "effects": validate_effects(document, visual_effect_count, character_effect_count),
        }
        errors = [f"{name}: " + "; ".join(report["errors"]) for name, report in reports.items() if report["status"] != "passed"]
        report = {
            "status": "succeeded" if not errors else "failed",
            "draft_name": args.draft_name,
            "draft_path": saved["draft_path"],
            "caption_presentation": presentation,
            "caption_layout_review": str(args.caption_layout_review.resolve()),
            "broll_segments": len(broll),
            "speaker_pip_segments": sum(item["speaker_pip"]["enabled"] for item in broll),
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
