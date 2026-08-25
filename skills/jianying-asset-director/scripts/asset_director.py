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
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple


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


def _effect_type(effect_kind: str):
    """Load the local JianYing enum used to resolve real effect metadata."""
    enum_name = "VideoSceneEffectType" if effect_kind == "video" else "VideoCharacterEffectType"
    try:
        from pyJianYingDraft import VideoCharacterEffectType, VideoSceneEffectType

        return VideoSceneEffectType if effect_kind == "video" else VideoCharacterEffectType
    except ImportError:
        pass

    roots = []
    env_root = os.environ.get("JY_SKILL_ROOT", "").strip()
    if env_root:
        roots.append(Path(env_root))
    roots.extend([
        Path(__file__).resolve().parents[2] / "jianying-editor",
        Path.home() / ".codex" / "skills" / "jianying-editor",
    ])
    for root in roots:
        vendor = root / "scripts" / "vendor"
        if vendor.exists() and str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
            try:
                from pyJianYingDraft import VideoCharacterEffectType, VideoSceneEffectType

                return VideoSceneEffectType if effect_kind == "video" else VideoCharacterEffectType
            except ImportError:
                continue
    raise RuntimeError(f"无法加载 pyJianYingDraft.{enum_name}，不能验证剪映特效 ID")


def resolve_video_effect(name: str) -> Dict[str, str]:
    """Resolve a catalog name to JianYing's real resource metadata."""
    effect = _effect_type("video").from_name(name)
    metadata = effect.value
    return {
        "resource_id": str(metadata.resource_id),
        "effect_id": str(metadata.effect_id),
        "md5": str(metadata.md5),
    }


def resolve_character_effect(name: str) -> Dict[str, str]:
    """Resolve a person-effect name to JianYing's real face-effect metadata."""
    effect = _effect_type("character").from_name(name)
    metadata = effect.value
    return {
        "resource_id": str(metadata.resource_id),
        "effect_id": str(metadata.effect_id),
        "md5": str(metadata.md5),
    }


def classify(name: str, taxonomy: Dict[str, Any]) -> Dict[str, Any]:
    exact = taxonomy.get("asset_tags", {}).get(name)
    text = normalize(name)
    tags: List[str] = []
    default_keyword_tags = {
        "故障": "glitch", "震闪": "impact", "冲击": "impact", "波": "impact",
        "警": "warning", "危险": "warning", "不对劲": "warning", "扫描": "scan",
        "光线": "highlight", "倒计时": "timer", "时间": "time_pressure",
        "提示": "notification", "科技": "notification", "咻": "whoosh",
        "转场": "transition", "确认": "confirmation", "正确": "confirmation",
        "卡通": "cartoon", "滑稽": "comedy", "搞怪": "comedy", "笑": "laugh",
        "烟花": "fireworks", "爆炸": "explosion", "庆祝": "celebration",
        "雷": "low_frequency", "低沉": "low_frequency", "救护": "emergency",
        "人物": "person", "轮廓": "person", "高光": "person", "光环": "person",
        "声波": "person", "爱心": "cartoon", "猪": "cartoon", "狐": "cartoon",
        "圣诞": "celebration",
    }
    keyword_tags = {**default_keyword_tags, **taxonomy.get("keyword_tags", {})}
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
    unresolved_video_effects: List[Dict[str, str]] = []
    for filename, asset_type, id_key in sources:
        for row in read_csv_rows(data_dir / filename):
            raw_asset_id = str(row.get(id_key) or "").strip()
            if asset_type == "video_effect":
                name = str(row.get("identifier") or row.get("title") or "").strip()
                if not name:
                    continue
                try:
                    metadata = resolve_video_effect(name)
                except (RuntimeError, ValueError) as exc:
                    unresolved_video_effects.append({"name": name, "reason": str(exc)})
                    continue
                asset_id = metadata["resource_id"]
            else:
                name = str(row.get("title") or row.get("description") or raw_asset_id).strip()
                asset_id = raw_asset_id
            if not asset_id:
                continue
            semantic = classify(name, taxonomy)
            duration = row.get("duration_s") or row.get("duration") or ""
            try:
                duration_s = float(duration) if duration else None
            except ValueError:
                duration_s = None
            asset = {
                "asset_id": asset_id,
                "name": name,
                "asset_type": asset_type,
                "duration_s": duration_s,
                "source_file": filename,
                "tags": semantic.get("tags", []),
                "intensity": float(semantic.get("intensity", 0.5)),
                "categories": row.get("categories", ""),
            }
            if asset_type == "video_effect":
                asset.update(metadata)
                asset["source_identifier"] = name
            else:
                asset["effect_id"] = asset_id
            assets.append(asset)
    unresolved_character_effects: List[Dict[str, str]] = []
    try:
        for effect in _effect_type("character"):
            metadata = effect.value
            name = str(metadata.name).strip()
            if not name:
                continue
            semantic = classify(name, taxonomy)
            assets.append({
                "asset_id": str(metadata.resource_id),
                "name": name,
                "asset_type": "character_effect",
                "duration_s": None,
                "source_file": "pyJianYingDraft.VideoCharacterEffectType",
                "tags": list(dict.fromkeys(semantic.get("tags", []) + ["person"])),
                "intensity": float(semantic.get("intensity", 0.5)),
                "categories": "person_face",
                "resource_id": str(metadata.resource_id),
                "effect_id": str(metadata.effect_id),
                "md5": str(metadata.md5),
                # The enum member is the only stable input accepted by from_name().
                # Display names can contain punctuation (for example, "BOOM！").
                "source_identifier": effect.name,
            })
    except RuntimeError as exc:
        unresolved_character_effects.append({"name": "VideoCharacterEffectType", "reason": str(exc)})
    return {
        "version": 3,
        "data_dir": str(data_dir),
        "asset_count": len(assets),
        "assets": assets,
        "unresolved_video_effects": unresolved_video_effects,
        "unresolved_character_effects": unresolved_character_effects,
    }


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
    if asset.get("asset_type") == "character_effect":
        preferred.add("person")
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


def selection_rules(taxonomy: Dict[str, Any], asset_type: str) -> Dict[str, Any]:
    selection = taxonomy.get("selection", {})
    defaults = {
        "candidate_limit": 8 if asset_type == "character" else 15,
        "min_score": 2.0,
        "max_same_effect_per_plan": 1 if asset_type == "character" else 2,
        "repeat_cooldown_seconds": 90.0 if asset_type == "character" else 45.0,
        "allow_no_effect": True,
    }
    rules = dict(defaults)
    rules.update(selection.get(asset_type, {}))
    return rules


def rank_candidates(
    assets: List[Dict[str, Any]],
    purpose: str,
    style: Dict[str, Any],
    taxonomy: Dict[str, Any],
    asset_type: str,
) -> List[Tuple[float, List[str], Dict[str, Any]]]:
    rules = selection_rules(taxonomy, asset_type)
    ranked = [
        (score, reasons, asset)
        for score, reasons, asset in (
            score_asset(asset, purpose, style, taxonomy) + (asset,) for asset in assets
        )
        if score >= float(rules["min_score"])
    ]
    # Stable ID ordering makes tied candidates reproducible without favoring one name.
    return sorted(ranked, key=lambda item: (-item[0], str(item[2].get("asset_id", ""))))


def candidate_record(score: float, reasons: List[str], asset: Dict[str, Any]) -> Dict[str, Any]:
    record = {
        "asset_id": asset["asset_id"],
        "name": asset["name"],
        "asset_type": asset["asset_type"],
        "score": round(score, 2),
        "reasons": reasons,
    }
    for key in ("resource_id", "effect_id", "md5", "source_identifier"):
        if asset.get(key):
            record[key] = asset[key]
    return record


def shortlist_candidates(
    ranked: List[Tuple[float, List[str], Dict[str, Any]]],
    purpose: str,
    style: Dict[str, Any],
    taxonomy: Dict[str, Any],
    limit: int,
) -> List[Tuple[float, List[str], Dict[str, Any]]]:
    """Keep the top scores while guaranteeing purpose-tag coverage."""
    if limit <= 0:
        return []
    purpose_rule = taxonomy.get("beat_purposes", {}).get(purpose, {})
    required_tags = set(purpose_rule.get("preferred", []))
    selected: List[Tuple[float, List[str], Dict[str, Any]]] = []
    selected_ids = set()
    for tag in sorted(required_tags):
        match = next((item for item in ranked if tag in set(item[2].get("tags", []))), None)
        if match and match[2].get("asset_id") not in selected_ids:
            selected.append(match)
            selected_ids.add(match[2].get("asset_id"))
    for item in ranked:
        if len(selected) >= limit:
            break
        asset_id = item[2].get("asset_id")
        if asset_id not in selected_ids:
            selected.append(item)
            selected_ids.add(asset_id)
    rank = {item[2].get("asset_id"): index for index, item in enumerate(ranked)}
    return sorted(selected, key=lambda item: rank[item[2].get("asset_id")])


def diversity_problem(
    asset_id: str,
    start: float,
    history: List[Dict[str, Any]],
    rules: Dict[str, Any],
    asset_type: str,
) -> Optional[str]:
    counts = Counter(item.get("asset_id") for item in history)
    max_count = int(rules["max_same_effect_per_plan"])
    cooldown = float(rules["repeat_cooldown_seconds"])
    if counts[asset_id] >= max_count:
        if any(
            item.get("asset_id") == asset_id
            and abs(float(item.get("start", 0)) - start) < cooldown
            for item in history
        ):
            return f"{asset_type} violates repeat cooldown: {asset_id}"
        return f"{asset_type} repeats beyond plan limit: {asset_id}"
    if any(
        item.get("asset_id") == asset_id
        and abs(float(item.get("start", 0)) - start) < cooldown
        for item in history
    ):
        return f"{asset_type} violates repeat cooldown: {asset_id}"
    return None


def character_effect_eligibility(beat: Dict[str, Any]) -> Tuple[bool, str]:
    """Require a visible face and reject a beat covered by full-height B-roll."""
    pip_zone = normalize(beat.get("pip_zone", ""))
    if "full" in pip_zone or "全高" in pip_zone or "全屏" in pip_zone:
        return False, "full-height B-roll obscures the main speaker"
    if beat.get("person_visible") is True or beat.get("face_visible") is True:
        return True, "explicit visible-person analysis"
    subject = normalize(beat.get("visual_subject", ""))
    person_terms = ("talkinghead", "speaker", "doctor", "face", "person", "人物", "医生", "口播", "主讲人")
    if any(term in subject for term in person_terms):
        return True, "visible-person subject analysis"
    return False, "no confirmed visible person or face"


def plan(beats_path: Path, catalog_path: Path, taxonomy_path: Path, style_name: str) -> Dict[str, Any]:
    beats = beat_list(load_json(beats_path))
    cat = load_json(catalog_path)
    taxonomy = load_json(taxonomy_path)
    style = taxonomy.get("style_profiles", {}).get(style_name, taxonomy.get("style_profiles", {}).get("general_short_video", {}))
    visual_assets = [a for a in cat.get("assets", []) if a.get("asset_type") == "video_effect"]
    sound_assets = [a for a in cat.get("assets", []) if a.get("asset_type") == "sound_effect"]
    character_assets = [a for a in cat.get("assets", []) if a.get("asset_type") == "character_effect"]
    visual_plan: List[Dict[str, Any]] = []
    sound_plan: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    visual_rules = selection_rules(taxonomy, "visual")
    sound_rules = selection_rules(taxonomy, "sound")
    character_rules = selection_rules(taxonomy, "character")
    for beat in beats:
        purpose = beat["purpose"]
        ranked_visual = rank_candidates(visual_assets, purpose, style, taxonomy, "visual")
        ranked_sound = rank_candidates(sound_assets, purpose, style, taxonomy, "sound")
        character_allowed, character_reason = character_effect_eligibility(beat)
        ranked_character = rank_candidates(character_assets, purpose, style, taxonomy, "character") if character_allowed else []
        visual_shortlist = shortlist_candidates(
            ranked_visual, purpose, style, taxonomy, int(visual_rules["candidate_limit"])
        )
        sound_shortlist = shortlist_candidates(
            ranked_sound, purpose, style, taxonomy, int(sound_rules["candidate_limit"])
        )
        character_shortlist = shortlist_candidates(
            ranked_character, purpose, style, taxonomy, int(character_rules["candidate_limit"])
        )
        beat["visual_candidates"] = [
            candidate_record(score, reasons, asset)
            for score, reasons, asset in visual_shortlist
        ]
        beat["sound_candidates"] = [
            candidate_record(score, reasons, asset)
            for score, reasons, asset in sound_shortlist
        ]
        beat["character_candidates"] = [
            candidate_record(score, reasons, asset)
            for score, reasons, asset in character_shortlist
        ]
        beat["character_effect_eligibility"] = {"eligible": character_allowed, "reason": character_reason}
        decisions.append({
            "beat_id": beat["beat_id"],
            "visual_candidate_count": len(beat["visual_candidates"]),
            "sound_candidate_count": len(beat["sound_candidates"]),
            "character_candidate_count": len(beat["character_candidates"]),
            "visual_selected": None,
            "sound_selected": None,
            "character_selected": None,
            "visual_status": "awaiting_ai_review",
            "sound_status": "awaiting_ai_review",
            "character_status": "awaiting_ai_review" if character_allowed else "ineligible_no_effect",
        })
    return {
        "version": 3,
        "style": style_name,
        "selection": {"visual": visual_rules, "sound": sound_rules, "character": character_rules},
        "beats": beats,
        "visual_effects": visual_plan,
        "sound_effects": sound_plan,
        "character_effects": [],
        "decisions": decisions,
        "rejected": [],
        "preview_required": True,
        "ai_review": {"required": True, "mode": "choose_from_shortlist_or_no_effect", "status": "pending"},
        "validation": {"status": "pending"},
    }


def selected_item(
    beat: Dict[str, Any],
    candidate: Dict[str, Any],
    asset_type: str,
    taxonomy: Dict[str, Any],
) -> Dict[str, Any]:
    purpose_rule = taxonomy.get("beat_purposes", {}).get(beat["purpose"], {})
    max_duration = float(purpose_rule.get("max_duration", 1.2))
    item = {
        "asset_id": candidate["asset_id"],
        "name": candidate["name"],
        "start": beat["start"],
        "score": candidate["score"],
        "reasons": candidate["reasons"],
        "beat_id": beat["beat_id"],
        "selection_source": "ai_shortlist_review",
    }
    if asset_type in ("visual", "character"):
        item["duration"] = min(max_duration, max(0.4, beat["end"] - beat["start"]))
        item["zone"] = beat.get("effect_zone", "full_frame") if asset_type == "visual" else "face_target"
    else:
        item["duration"] = max_duration
        item["track"] = beat.get("sound_track", "SFX_Accent")
        item["volume"] = beat.get("volume", 0.12)
    for key in ("resource_id", "effect_id", "md5", "source_identifier"):
        if candidate.get(key):
            item[key] = candidate[key]
    return item


def apply_selections(plan_path: Path, selections_path: Path, taxonomy_path: Path) -> Dict[str, Any]:
    """Apply structured AI selections that are constrained to the plan shortlists."""
    data = load_json(plan_path)
    selections_payload = load_json(selections_path)
    selections = selections_payload.get("selections", selections_payload) if isinstance(selections_payload, dict) else selections_payload
    if not isinstance(selections, list):
        raise ValueError("selections JSON must be a list or an object containing selections")
    selection_by_beat = {}
    for selection in selections:
        if not isinstance(selection, dict) or not selection.get("beat_id"):
            raise ValueError("every selection requires beat_id")
        beat_id = str(selection["beat_id"])
        if beat_id in selection_by_beat:
            raise ValueError(f"duplicate selection for beat: {beat_id}")
        selection_by_beat[beat_id] = selection

    taxonomy = load_json(taxonomy_path)
    visual_rules = selection_rules(taxonomy, "visual")
    sound_rules = selection_rules(taxonomy, "sound")
    character_rules = selection_rules(taxonomy, "character")
    visual_effects: List[Dict[str, Any]] = []
    sound_effects: List[Dict[str, Any]] = []
    character_effects: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    for beat in data.get("beats", []):
        beat_id = str(beat["beat_id"])
        if beat_id not in selection_by_beat:
            raise ValueError(f"missing AI selection for beat: {beat_id}")
        selection = selection_by_beat[beat_id]
        decision = {"beat_id": beat_id, "reason": selection.get("reason", "")}
        for asset_type, output, history, rules in (
            ("visual", visual_effects, visual_effects, visual_rules),
            ("sound", sound_effects, sound_effects, sound_rules),
            ("character", character_effects, character_effects, character_rules),
        ):
            selected_id = selection.get(f"{asset_type}_asset_id")
            candidates = {item["asset_id"]: item for item in beat.get(f"{asset_type}_candidates", [])}
            if asset_type == "character" and selected_id is not None and not beat.get("character_effect_eligibility", {}).get("eligible"):
                reason = beat.get("character_effect_eligibility", {}).get("reason", "not eligible")
                raise ValueError(f"character selection is ineligible for beat {beat_id}: {reason}")
            if selected_id is None:
                if not rules["allow_no_effect"]:
                    raise ValueError(f"{asset_type} selection is required for beat: {beat_id}")
                decision[f"{asset_type}_selected"] = None
                decision[f"{asset_type}_status"] = "no_effect"
                continue
            if selected_id not in candidates:
                raise ValueError(f"{asset_type} selection is not in shortlist for beat {beat_id}: {selected_id}")
            problem = diversity_problem(str(selected_id), float(beat["start"]), history, rules, asset_type)
            if problem:
                raise ValueError(problem)
            item = selected_item(beat, candidates[selected_id], asset_type, taxonomy)
            output.append(item)
            decision[f"{asset_type}_selected"] = selected_id
            decision[f"{asset_type}_status"] = "selected"
        decisions.append(decision)

    data["visual_effects"] = visual_effects
    data["sound_effects"] = sound_effects
    data["character_effects"] = character_effects
    data["decisions"] = decisions
    data["ai_review"] = {
        "required": True,
        "mode": "choose_from_shortlist_or_no_effect",
        "status": "approved",
        "selection_source": str(selections_path),
    }
    data["validation"] = {"status": "pending"}
    return data


def composition_qc(data: Dict[str, Any], document: Dict[str, Any]) -> Dict[str, Any]:
    """Check draft materialization and layout risks; rendered pixels remain a manual review."""
    problems: List[str] = []
    tracks = document.get("tracks", [])
    materials = document.get("materials", {}).get("video_effects", [])
    material_by_id = {item.get("id"): item for item in materials}
    expected = (
        ("Effects", "video_effect", data.get("visual_effects", [])),
        ("CharacterEffects", "face_effect", data.get("character_effects", [])),
    )
    report_tracks: Dict[str, Any] = {}
    for track_name, material_type, selected in expected:
        track = next((item for item in tracks if item.get("type") == "effect" and item.get("name") == track_name), None)
        if track is None:
            problems.append(f"missing required effect track: {track_name}")
            continue
        segments = track.get("segments", [])
        actual = []
        for segment in segments:
            material = material_by_id.get(segment.get("material_id"))
            if material is None:
                problems.append(f"{track_name} segment references missing material: {segment.get('material_id')}")
                continue
            if material.get("type") != material_type:
                problems.append(f"{track_name} material has type {material.get('type')}, expected {material_type}")
            actual.append(str(material.get("resource_id")))
        expected_ids = [str(item.get("resource_id")) for item in selected]
        if Counter(actual) != Counter(expected_ids):
            problems.append(f"{track_name} resource IDs do not match approved plan")
        report_tracks[track_name] = {"segments": len(segments), "materials": len(actual), "expected": len(expected_ids)}

    beats = {str(beat.get("beat_id")): beat for beat in data.get("beats", [])}
    for item in data.get("character_effects", []):
        beat = beats.get(str(item.get("beat_id")), {})
        eligible, reason = character_effect_eligibility(beat)
        if not eligible:
            problems.append(f"character effect on ineligible beat {item.get('beat_id')}: {reason}")
        if item.get("zone") != "face_target":
            problems.append(f"character effect must use face_target zone: {item.get('asset_id')}")
    return {
        "passed": not problems,
        "tracks": report_tracks,
        "problems": problems,
        "rendered_preview": "manual_review_required_in_jianying",
    }


def validate(plan_path: Path, draft_path: Path | None, catalog_path: Path | None = None, taxonomy_path: Path = DEFAULT_TAXONOMY) -> Dict[str, Any]:
    data = load_json(plan_path)
    problems: List[str] = []
    taxonomy = load_json(taxonomy_path)
    catalog_data = load_json(catalog_path) if catalog_path else None
    catalog_ids = {item.get("asset_id") for item in (catalog_data or {}).get("assets", [])}
    visual_rules = selection_rules(taxonomy, "visual")
    sound_rules = selection_rules(taxonomy, "sound")
    character_rules = selection_rules(taxonomy, "character")
    for item in data.get("visual_effects", []) + data.get("sound_effects", []) + data.get("character_effects", []):
        if float(item.get("duration", 0)) <= 0:
            problems.append(f"non-positive duration: {item.get('asset_id')}")
        if float(item.get("start", 0)) < 0:
            problems.append(f"negative start: {item.get('asset_id')}")
        if catalog_data and item.get("asset_id") not in catalog_ids:
            problems.append(f"asset_id not found in catalog: {item.get('asset_id')}")
    for item in data.get("visual_effects", []) + data.get("character_effects", []):
        if not item.get("resource_id"):
            problems.append(f"effect lacks real resource_id: {item.get('asset_id')}")
        if item.get("asset_id") != item.get("resource_id"):
            problems.append(f"effect asset_id must equal resource_id: {item.get('asset_id')}")
        if not item.get("source_identifier"):
            problems.append(f"effect lacks source_identifier: {item.get('asset_id')}")
    for item in data.get("character_effects", []):
        beat = next((beat for beat in data.get("beats", []) if str(beat.get("beat_id")) == str(item.get("beat_id"))), {})
        eligible, reason = character_effect_eligibility(beat)
        if not eligible:
            problems.append(f"character effect uses ineligible beat {item.get('beat_id')}: {reason}")
    for asset_type, rules, key in (("visual", visual_rules, "visual_effects"), ("sound", sound_rules, "sound_effects"), ("character", character_rules, "character_effects")):
        items = data.get(key, [])
        counts = Counter(item.get("asset_id") for item in items)
        for asset_id, count in counts.items():
            if count > int(rules["max_same_effect_per_plan"]):
                problems.append(f"{asset_type} repeats beyond limit: {asset_id} ({count})")
        cooldown = float(rules["repeat_cooldown_seconds"])
        for index, item in enumerate(items):
            for other in items[index + 1:]:
                if item.get("asset_id") == other.get("asset_id") and abs(float(item.get("start", 0)) - float(other.get("start", 0))) < cooldown:
                    problems.append(f"{asset_type} violates repeat cooldown: {item.get('asset_id')}")
    if data.get("ai_review", {}).get("required") is not True:
        problems.append("ai_review.required must remain true")
    if data.get("ai_review", {}).get("status") != "approved":
        problems.append("ai_review.status must be approved before draft handoff")
    if not data.get("preview_required", True):
        problems.append("preview_required must remain true")
    draft_report: Dict[str, Any] = {}
    qc_report: Dict[str, Any] = {"passed": False, "rendered_preview": "manual_review_required_in_jianying"}
    if draft_path:
        content = draft_path / "draft_info.json"
        if not content.exists():
            problems.append(f"draft_info.json not found: {content}")
        else:
            raw = content.read_text(encoding="utf-8")
            draft_report = {"draft_info": str(content), "has_generated_sfx": "generated_sfx" in raw, "has_medical_filter": "Medical_Filter" in raw}
            if draft_report["has_generated_sfx"]:
                problems.append("draft references generated_sfx")
            qc_report = composition_qc(data, json.loads(raw))
            problems.extend(qc_report["problems"])
    return {"valid": not problems, "problems": problems, "draft": draft_report, "composition_qc": qc_report}


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
    p_select = sub.add_parser("select", help="apply AI selections constrained to a plan's candidate shortlists")
    p_select.add_argument("--plan", type=Path, required=True)
    p_select.add_argument("--selections", type=Path, required=True)
    p_select.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    p_select.add_argument("--output", type=Path, required=True)
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--plan", type=Path, required=True)
    p_validate.add_argument("--draft", type=Path)
    p_validate.add_argument("--catalog", type=Path)
    p_validate.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
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
            emit("succeeded", args.command, {
                "output": str(args.output),
                "visual_count": len(result["visual_effects"]),
                "sound_count": len(result["sound_effects"]),
                "character_candidate_count": sum(len(beat.get("character_candidates", [])) for beat in result["beats"]),
                "visual_candidate_count": sum(len(beat.get("visual_candidates", [])) for beat in result["beats"]),
                "sound_candidate_count": sum(len(beat.get("sound_candidates", [])) for beat in result["beats"]),
                "rejected_count": len(result["rejected"]),
            })
        elif args.command == "validate":
            result = validate(args.plan, args.draft, args.catalog, args.taxonomy)
            emit("succeeded" if result["valid"] else "failed", args.command, result)
            return 0 if result["valid"] else 2
        elif args.command == "select":
            result = apply_selections(args.plan, args.selections, args.taxonomy)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            emit("succeeded", args.command, {"output": str(args.output), "visual_count": len(result["visual_effects"]), "sound_count": len(result["sound_effects"]), "character_count": len(result.get("character_effects", []))})
        else:
            result = preview(args.video, args.plan, args.output_dir)
            emit("succeeded", args.command, result)
        return 0
    except Exception as exc:
        emit("failed", args.command, {}, str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
