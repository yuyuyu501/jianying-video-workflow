#!/usr/bin/env python3
"""Deterministic catalog, matching, and validation helpers for JianYing assets."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


DEFAULT_TAXONOMY = Path(__file__).resolve().parents[1] / "references" / "asset_taxonomy.json"


def find_jianying_data_dir() -> Path:
    """Find JianYing's indexed data without assuming a username or install path."""
    explicit = os.environ.get("JY_ASSET_DIRECTOR_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    skill_root = os.environ.get("JY_SKILL_ROOT", "").strip()
    candidates = []
    if skill_root:
        candidates.append(Path(skill_root).expanduser() / "data")

    # Installed sibling skills: .../skills/jianying-asset-director/scripts/...
    skills_dir = Path(__file__).resolve().parents[2]
    candidates.append(skills_dir / "jianying-editor" / "data")

    # Repository checkouts may keep the two skills under a local skills folder.
    current = Path.cwd()
    candidates.extend([
        current / "skills" / "jianying-editor" / "data",
        current / "jianying-editor" / "data",
        Path.home() / ".codex" / "skills" / "jianying-editor" / "data",
    ])
    for candidate in candidates:
        if (candidate / "video_scene_effects.csv").exists() or (candidate / "cloud_sound_effects.csv").exists():
            return candidate
    return candidates[0] if candidates else Path("data")


DEFAULT_DATA_DIR = find_jianying_data_dir()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def emit(status: str, command: str, payload: Dict[str, Any], error: str = "") -> None:
    result = {"status": status, "command": command, **payload}
    if error:
        result["error"] = error
    print("RESULT: " + json.dumps(result, ensure_ascii=False))


def read_csv_rows(path: Path) -> Iterable[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        lines = [line for line in handle if not line.lstrip().startswith("#")]
    return csv.DictReader(lines)


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def classify(name: str, taxonomy: Dict[str, Any]) -> Dict[str, Any]:
    exact = taxonomy.get("asset_tags", {}).get(name)
    text = normalize(name)
    tags: List[str] = []
    keyword_tags = {
        "故障": "glitch", "震闪": "impact", "冲击": "impact", "波": "impact",
        "警": "warning", "危险": "warning", "不对劲": "warning", "扫描": "scan",
        "光线": "highlight", "倒计时": "timer", "时间": "time_pressure",
        "提示": "notification", "科技": "notification", "咻": "whoosh",
        "转场": "transition", "确认": "confirmation", "正确": "confirmation",
        "卡通": "cartoon", "滑稽": "comedy", "搞怪": "comedy", "笑": "laugh",
        "烟花": "fireworks", "爆炸": "explosion", "庆祝": "celebration",
        "雷": "low_frequency", "低沉": "low_frequency", "救护": "emergency",
    }
    for keyword, tag in keyword_tags.items():
        if keyword in text and tag not in tags:
            tags.append(tag)
    if exact:
        tags = list(dict.fromkeys(list(exact.get("tags", [])) + tags))
        return {"tags": tags, "intensity": float(exact.get("intensity", 0.55))}
    return {"tags": tags, "intensity": 0.55 if tags else 0.5}


def catalog(data_dir: Path, taxonomy_path: Path) -> Dict[str, Any]:
    taxonomy = load_json(taxonomy_path)
    assets: List[Dict[str, Any]] = []
    sources = [
        ("video_scene_effects.csv", "video_effect", "identifier"),
        ("cloud_sound_effects.csv", "sound_effect", "effect_id"),
    ]
    for filename, asset_type, id_key in sources:
        for row in read_csv_rows(data_dir / filename):
            asset_id = str(row.get(id_key) or "").strip()
            if asset_type == "video_effect":
                name = str(row.get("identifier") or row.get("title") or asset_id).strip()
            else:
                name = str(row.get("title") or row.get("description") or asset_id).strip()
            if not asset_id:
                continue
            semantic = classify(name, taxonomy)
            duration = row.get("duration_s") or row.get("duration") or ""
            try:
                duration_s = float(duration) if duration else None
            except ValueError:
                duration_s = None
            assets.append({
                "asset_id": asset_id,
                "name": name,
                "asset_type": asset_type,
                "duration_s": duration_s,
                "source_file": filename,
                "tags": semantic.get("tags", []),
                "intensity": float(semantic.get("intensity", 0.5)),
                "categories": row.get("categories", ""),
            })
    return {"version": 1, "data_dir": str(data_dir), "asset_count": len(assets), "assets": assets}


def beat_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("beats") or value.get("segments") or value.get("transcript") or []
    if not isinstance(value, list):
        raise ValueError("beats JSON must be a list or an object containing beats")
    normalized = []
    for index, beat in enumerate(value):
        if not isinstance(beat, dict):
            continue
        start = float(beat.get("start", beat.get("start_time", 0)))
        end = float(beat.get("end", beat.get("end_time", start + 1)))
        normalized.append({
            **beat,
            "beat_id": beat.get("beat_id", index + 1),
            "start": start,
            "end": max(end, start + 0.1),
            "purpose": beat.get("purpose", "general"),
            "emotion": beat.get("emotion", "neutral"),
            "spoken_text": beat.get("spoken_text", beat.get("text", "")),
        })
    return normalized


def score_asset(asset: Dict[str, Any], purpose: str, style: Dict[str, Any], taxonomy: Dict[str, Any]) -> tuple[float, List[str]]:
    tags = set(asset.get("tags", []))
    rule = taxonomy.get("beat_purposes", {}).get(purpose, {})
    preferred = set(rule.get("preferred", [])) | set(style.get("preferred", []))
    forbidden = set(rule.get("forbidden", [])) | set(style.get("forbidden", []))
    intensity = float(asset.get("intensity", 0.5))
    max_intensity = float(style.get("max_visual_intensity", 1.0))
    if asset.get("asset_type") == "sound_effect":
        max_intensity = float(style.get("max_audio_intensity", 1.0))
    score = 0.0
    reasons: List[str] = []
    matched = tags & preferred
    if matched:
        score += 3.0 * len(matched)
        reasons.append("preferred:" + ",".join(sorted(matched)))
    blocked = tags & forbidden
    if blocked:
        score -= 8.0 * len(blocked)
        reasons.append("forbidden:" + ",".join(sorted(blocked)))
    if intensity > max_intensity:
        score -= 4.0 * (intensity - max_intensity + 0.1)
        reasons.append("intensity_above_profile")
    if not tags:
        score -= 1.0
        reasons.append("untagged")
    return score, reasons


def plan(beats_path: Path, catalog_path: Path, taxonomy_path: Path, style_name: str) -> Dict[str, Any]:
    beats = beat_list(load_json(beats_path))
    cat = load_json(catalog_path)
    taxonomy = load_json(taxonomy_path)
    style = taxonomy.get("style_profiles", {}).get(style_name, taxonomy.get("style_profiles", {}).get("general_short_video", {}))
    visual_assets = [a for a in cat.get("assets", []) if a.get("asset_type") == "video_effect"]
    sound_assets = [a for a in cat.get("assets", []) if a.get("asset_type") == "sound_effect"]
    visual_plan: List[Dict[str, Any]] = []
    sound_plan: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for beat in beats:
        purpose = beat["purpose"]
        ranked_visual = sorted((score_asset(a, purpose, style, taxonomy) + (a,) for a in visual_assets), reverse=True, key=lambda x: x[0])
        ranked_sound = sorted((score_asset(a, purpose, style, taxonomy) + (a,) for a in sound_assets), reverse=True, key=lambda x: x[0])
        max_dur = taxonomy.get("beat_purposes", {}).get(purpose, {}).get("max_duration", 1.2)
        visual = ranked_visual[0] if ranked_visual and ranked_visual[0][0] >= 2.0 else None
        sound = ranked_sound[0] if ranked_sound and ranked_sound[0][0] >= 2.0 else None
        if visual:
            score, reasons, asset = visual
            duration = min(max_dur, max(0.4, beat["end"] - beat["start"]))
            visual_plan.append({"asset_id": asset["asset_id"], "name": asset["name"], "start": beat["start"], "duration": duration, "zone": beat.get("effect_zone", "full_frame"), "score": round(score, 2), "reasons": reasons, "beat_id": beat["beat_id"]})
        if sound:
            score, reasons, asset = sound
            duration = min(max_dur, asset.get("duration_s") or max_dur)
            track = beat.get("sound_track", "SFX_Accent")
            sound_plan.append({"asset_id": asset["asset_id"], "name": asset["name"], "start": beat["start"], "duration": duration, "track": track, "volume": beat.get("volume", 0.12), "score": round(score, 2), "reasons": reasons, "beat_id": beat["beat_id"]})
        for score, reasons, asset in (ranked_visual[1:4] + ranked_sound[1:4]):
            if score < 0:
                rejected.append({"asset_id": asset["asset_id"], "name": asset["name"], "beat_id": beat["beat_id"], "reason": reasons})
    return {"version": 1, "style": style_name, "beats": beats, "visual_effects": visual_plan, "sound_effects": sound_plan, "rejected": rejected, "preview_required": True, "validation": {"status": "pending"}}


def validate(plan_path: Path, draft_path: Path | None) -> Dict[str, Any]:
    data = load_json(plan_path)
    problems: List[str] = []
    for item in data.get("visual_effects", []) + data.get("sound_effects", []):
        if float(item.get("duration", 0)) <= 0:
            problems.append(f"non-positive duration: {item.get('asset_id')}")
        if float(item.get("start", 0)) < 0:
            problems.append(f"negative start: {item.get('asset_id')}")
    if not data.get("preview_required", True):
        problems.append("preview_required must remain true")
    draft_report: Dict[str, Any] = {}
    if draft_path:
        content = draft_path / "draft_info.json"
        if not content.exists():
            problems.append(f"draft_info.json not found: {content}")
        else:
            raw = content.read_text(encoding="utf-8")
            draft_report = {"draft_info": str(content), "has_generated_sfx": "generated_sfx" in raw, "has_medical_filter": "Medical_Filter" in raw}
            if draft_report["has_generated_sfx"]:
                problems.append("draft references generated_sfx")
    return {"valid": not problems, "problems": problems, "draft": draft_report}


def preview(video_path: Path, plan_path: Path, output_dir: Path) -> Dict[str, Any]:
    """Extract short review clips from an exported JianYing preview."""
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    data = load_json(plan_path)
    beats = beat_list(data.get("beats", []))
    output_dir.mkdir(parents=True, exist_ok=True)
    clips: List[Dict[str, Any]] = []
    for index, beat in enumerate(beats, start=1):
        start = max(0.0, beat["start"] - 0.4)
        duration = min(6.0, max(1.0, beat["end"] - start + 0.8))
        output = output_dir / f"beat_{index:03d}.mp4"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-c:a", "aac", "-b:a", "96k", str(output),
        ]
        subprocess.run(command, check=True)
        clips.append({"beat_id": beat["beat_id"], "start": start, "duration": duration, "path": str(output)})
    manifest = output_dir / "manifest.json"
    manifest.write_text(json.dumps({"video": str(video_path), "clips": clips}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_dir": str(output_dir), "clip_count": len(clips), "manifest": str(manifest)}


def main() -> int:
    parser = argparse.ArgumentParser(description="JianYing asset catalog, semantic plan, and validation")
    sub = parser.add_subparsers(dest="command", required=True)
    p_catalog = sub.add_parser("catalog")
    p_catalog.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="JianYing data directory; defaults to environment and sibling-skill discovery",
    )
    p_catalog.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    p_catalog.add_argument("--output", type=Path, required=True)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--beats", type=Path, required=True)
    p_plan.add_argument("--catalog", type=Path, required=True)
    p_plan.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    p_plan.add_argument("--style", default="medical_education")
    p_plan.add_argument("--output", type=Path, required=True)
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--plan", type=Path, required=True)
    p_validate.add_argument("--draft", type=Path)
    p_preview = sub.add_parser("preview")
    p_preview.add_argument("--video", type=Path, required=True)
    p_preview.add_argument("--plan", type=Path, required=True)
    p_preview.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            result = catalog(args.data_dir, args.taxonomy)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            emit("succeeded", args.command, {"output": str(args.output), "asset_count": result["asset_count"]})
        elif args.command == "plan":
            result = plan(args.beats, args.catalog, args.taxonomy, args.style)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            emit("succeeded", args.command, {"output": str(args.output), "visual_count": len(result["visual_effects"]), "sound_count": len(result["sound_effects"]), "rejected_count": len(result["rejected"])})
        elif args.command == "validate":
            result = validate(args.plan, args.draft)
            emit("succeeded" if result["valid"] else "failed", args.command, result)
            return 0 if result["valid"] else 2
        else:
            result = preview(args.video, args.plan, args.output_dir)
            emit("succeeded", args.command, result)
        return 0
    except Exception as exc:
        emit("failed", args.command, {}, str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
