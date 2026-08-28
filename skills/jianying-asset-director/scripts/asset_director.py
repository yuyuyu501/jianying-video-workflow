#!/usr/bin/env python3
"""Deterministic catalog, matching, and validation helpers for JianYing assets."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_TAXONOMY = Path(__file__).resolve().parents[1] / "references" / "asset_taxonomy.json"
EFFECT_TREATMENTS = {"scene_effect", "character_effect"}


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
        # Production taxonomies opt into these gates. The defaults preserve
        # deliberately plain edits that use this helper outside the workflow.
        "minimum_selected": 0,
        "required_purposes": [],
        "require_visual_evidence": False,
        "require_no_effect_reason": False,
    }
    rules = dict(defaults)
    rules.update(selection.get(asset_type, {}))
    return rules


def requires_selection(beat: Dict[str, Any], asset_type: str, rules: Dict[str, Any]) -> bool:
    """Whether a creative decision must materialize an asset for this beat."""
    treatments = set(beat.get("approved_effect_treatments", []))
    if "visual_treatment" in beat:
        expected = "scene_effect" if asset_type == "visual" else "character_effect"
        return asset_type in ("visual", "character") and expected in treatments
    return asset_type == "visual" and str(beat.get("purpose", "general")) in {
        str(value) for value in rules.get("required_purposes", [])
    }


def visual_evidence_problem(selection: Dict[str, Any], asset_type: str, beat: Dict[str, Any], rules: Dict[str, Any]) -> str:
    """Require an observation grounded in a representative frame."""
    if asset_type not in ("visual", "character") or not rules.get("require_visual_evidence", False):
        return ""
    if "visual_treatment" in beat:
        expected = "scene_effect" if asset_type == "visual" else "character_effect"
        if expected not in set(beat.get("approved_effect_treatments", [])):
            return ""
    evidence = str(selection.get(f"{asset_type}_evidence", "")).strip()
    try:
        evidence_time = float(selection.get(f"{asset_type}_evidence_time"))
    except (TypeError, ValueError):
        return f"{asset_type} selection requires an evidence timestamp for beat {beat['beat_id']}"
    if len(evidence) < 8:
        return f"{asset_type} selection requires a concrete frame observation for beat {beat['beat_id']}"
    if not float(beat["start"]) <= evidence_time <= float(beat["end"]):
        return f"{asset_type} evidence timestamp is outside beat {beat['beat_id']}"
    return ""


def creative_coverage_problems(data: Dict[str, Any], taxonomy: Dict[str, Any]) -> List[str]:
    """Reject formally valid plans that skipped meaningful visual decisions."""
    problems: List[str] = []
    rules = selection_rules(taxonomy, "visual")
    decisions = {str(item.get("beat_id")): item for item in data.get("decisions", []) if isinstance(item, dict)}
    treatment_driven = bool(data.get("visual_treatment_plan"))
    opportunities = [beat for beat in data.get("beats", []) if beat.get("visual_candidates")]
    minimum = min(max(0, int(rules.get("minimum_selected", 0))), len(opportunities))
    selected_count = len(data.get("visual_effects", []))
    if not treatment_driven and selected_count < minimum:
        problems.append(f"visual-effect coverage below minimum: selected {selected_count}, required {minimum}")
    for beat in opportunities:
        decision = decisions.get(str(beat.get("beat_id")), {})
        status = decision.get("visual_status")
        if requires_selection(beat, "visual", rules) and status != "selected":
            problems.append(f"priority beat requires a visual effect: {beat.get('beat_id')}")
        if status == "no_effect" and rules.get("require_no_effect_reason", False):
            if len(str(decision.get("visual_no_effect_reason", "")).strip()) < 8:
                problems.append(f"visual no-effect decision lacks a grounded reason: {beat.get('beat_id')}")
        if status in ("selected", "no_effect") and rules.get("require_visual_evidence", False):
            if len(str(decision.get("visual_evidence", "")).strip()) < 8:
                problems.append(f"visual decision lacks frame evidence: {beat.get('beat_id')}")
    return problems


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
    treatment = beat.get("visual_treatment") or {}
    requested_treatments = {treatment.get("primary_treatment"), treatment.get("secondary_treatment")}
    if (
        treatment.get("review_status") == "approved"
        and "character_effect" in requested_treatments
        and str(treatment.get("character_intent", "")).strip()
        and str(treatment.get("representative_frame", "")).strip()
        and len(str(treatment.get("visual_evidence", "")).strip()) >= 8
    ):
        return True, "approved frame-grounded character treatment"
    return False, "no confirmed visible person or face"


def load_visual_treatments(path: Path | None, beats: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any] | None]:
    if path is None:
        return {}, None
    module_path = Path(__file__).resolve().with_name("visual_treatment_director.py")
    spec = importlib.util.spec_from_file_location("jianying_visual_treatment_director", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("visual treatment validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = load_json(path)
    report = module.validate_payload(payload, beats)
    if not report.get("valid"):
        raise ValueError("invalid visual treatment plan: " + "; ".join(report.get("problems", [])))
    return {
        str(item["beat_id"]): item
        for item in payload.get("treatments", [])
        if isinstance(item, dict) and item.get("beat_id") is not None
    }, {"source": str(path), "status": "approved", "review": report}


def load_sfx_opportunities(
    path: Path | None,
    beats: List[Dict[str, Any]],
    taxonomy_path: Path,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any] | None]:
    if path is None:
        return {}, None
    script_path = Path(__file__).resolve().with_name("sfx_timing_director.py")
    spec = importlib.util.spec_from_file_location("sfx_timing_director", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("SFX timing validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = load_json(path)
    report = module.validate_payload(payload, beats, load_json(taxonomy_path))
    if not report.get("valid"):
        raise ValueError("invalid SFX timing plan: " + "; ".join(report.get("problems", [])))
    selected = {
        str(item["beat_id"]): item
        for item in payload.get("opportunities", [])
        if isinstance(item, dict) and item.get("use_sfx") is True
    }
    return selected, {"source": str(path), "status": "approved", "review": report}


def plan(
    beats_path: Path,
    catalog_path: Path,
    taxonomy_path: Path,
    style_name: str,
    visual_treatments_path: Path | None = None,
    sfx_opportunities_path: Path | None = None,
) -> Dict[str, Any]:
    beats = beat_list(load_json(beats_path))
    visual_treatments, treatment_record = load_visual_treatments(visual_treatments_path, beats)
    sfx_opportunities, sfx_record = load_sfx_opportunities(sfx_opportunities_path, beats, taxonomy_path)
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
        treatment = visual_treatments.get(str(beat["beat_id"]))
        approved_effect_treatments = set()
        if treatment:
            approved_effect_treatments = {
                treatment.get("primary_treatment"), treatment.get("secondary_treatment")
            } & EFFECT_TREATMENTS
            beat["visual_treatment"] = treatment
            beat["approved_effect_treatments"] = sorted(approved_effect_treatments)
        allow_scene = not treatment_record or "scene_effect" in approved_effect_treatments
        allow_character = not treatment_record or "character_effect" in approved_effect_treatments
        ranked_visual = rank_candidates(visual_assets, purpose, style, taxonomy, "visual") if allow_scene else []
        sfx_opportunity = sfx_opportunities.get(str(beat["beat_id"]))
        if sfx_opportunity:
            beat["sfx_opportunity"] = sfx_opportunity
            beat["sound_track"] = "SFX"
            beat["volume"] = float(sfx_opportunity["volume"])
        cue_type = str((sfx_opportunity or {}).get("cue_type", ""))
        sound_purpose = cue_type if cue_type in taxonomy.get("beat_purposes", {}) else purpose
        allow_sound = not sfx_record or sfx_opportunity is not None
        ranked_sound = rank_candidates(sound_assets, sound_purpose, style, taxonomy, "sound") if allow_sound else []
        character_allowed, character_reason = character_effect_eligibility(beat)
        if not allow_character:
            character_allowed, character_reason = False, "approved visual treatment does not request a character effect"
        if allow_character and treatment and not treatment.get("character_intent"):
            character_allowed, character_reason = False, "visual treatment lacks face-specific character_intent"
        ranked_character = rank_candidates(character_assets, purpose, style, taxonomy, "character") if character_allowed and allow_character else []
        visual_shortlist = shortlist_candidates(
            ranked_visual, purpose, style, taxonomy, int(visual_rules["candidate_limit"])
        )
        sound_shortlist = shortlist_candidates(
            ranked_sound, sound_purpose, style, taxonomy, int(sound_rules["candidate_limit"])
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
            "sound_status": "awaiting_ai_review" if allow_sound else "timing_review_skipped",
            "character_status": "awaiting_ai_review" if character_allowed else "ineligible_no_effect",
        })
    return {
        "version": 4,
        "style": style_name,
        "selection": {"visual": visual_rules, "sound": sound_rules, "character": character_rules},
        "beats": beats,
        "visual_effects": visual_plan,
        "sound_effects": sound_plan,
        "character_effects": [],
        "decisions": decisions,
        "visual_treatment_plan": treatment_record,
        "sfx_timing_plan": sfx_record,
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
    selection: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    purpose_rule = taxonomy.get("beat_purposes", {}).get(beat["purpose"], {})
    max_duration = float(purpose_rule.get("max_duration", 1.2))
    selection = selection or {}
    start = float(selection.get(f"{asset_type}_effect_start", beat["start"]))
    item = {
        "asset_id": candidate["asset_id"],
        "name": candidate["name"],
        "start": start,
        "score": candidate["score"],
        "reasons": candidate["reasons"],
        "beat_id": beat["beat_id"],
        "selection_source": "ai_shortlist_review",
    }
    if asset_type in ("visual", "character"):
        default_duration = min(max_duration, max(0.1, beat["end"] - start))
        item["duration"] = float(selection.get(f"{asset_type}_effect_duration", default_duration))
        item["zone"] = beat.get("effect_zone", "full_frame") if asset_type == "visual" else "face_target"
        item["evidence_time"] = selection.get(f"{asset_type}_evidence_time")
        item["evidence"] = str(selection.get(f"{asset_type}_evidence", "")).strip()
        item["treatment"] = "scene_effect" if asset_type == "visual" else "character_effect"
        if asset_type == "character":
            item["character_intent"] = beat.get("visual_treatment", {}).get("character_intent")
        layering_reason = str(beat.get("visual_treatment", {}).get("layering_reason", "")).strip()
        if layering_reason:
            item["layering_reason"] = layering_reason
    else:
        track = str(beat.get("sound_track", "SFX"))
        if track != "SFX":
            raise ValueError(f"sound effect track must be 'SFX' for the validated draft skeleton, got {track!r}")
        opportunity = beat.get("sfx_opportunity", {})
        start = float(selection.get("sound_effect_start", opportunity.get("trigger_time", beat["start"])))
        duration = float(selection.get("sound_effect_duration", opportunity.get("suggested_duration", max_duration)))
        item["start"] = start
        item["duration"] = min(max_duration, duration)
        item["track"] = track
        item["volume"] = float(opportunity.get("volume", beat.get("volume", 0.12)))
        item["cue_type"] = opportunity.get("cue_type")
        item["evidence"] = str(opportunity.get("evidence", "")).strip()
        item["timing_reason"] = str(opportunity.get("reason", "")).strip()
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
                if requires_selection(beat, asset_type, rules):
                    raise ValueError(f"priority beat requires a {asset_type} selection: {beat_id}")
                evidence_problem = visual_evidence_problem(selection, asset_type, beat, rules)
                if evidence_problem:
                    raise ValueError(evidence_problem)
                if not rules["allow_no_effect"]:
                    raise ValueError(f"{asset_type} selection is required for beat: {beat_id}")
                no_effect_reason = str(selection.get(f"{asset_type}_no_effect_reason", "")).strip()
                treatment_does_not_request_effect = (
                    "visual_treatment" in beat
                    and asset_type in ("visual", "character")
                    and ("scene_effect" if asset_type == "visual" else "character_effect")
                    not in set(beat.get("approved_effect_treatments", []))
                )
                if candidates and rules.get("require_no_effect_reason", False) and not treatment_does_not_request_effect and len(no_effect_reason) < 8:
                    raise ValueError(f"{asset_type} no-effect decision requires a grounded reason for beat: {beat_id}")
                decision[f"{asset_type}_selected"] = None
                decision[f"{asset_type}_status"] = "no_effect"
                decision[f"{asset_type}_no_effect_reason"] = no_effect_reason
                if asset_type in ("visual", "character"):
                    decision[f"{asset_type}_evidence"] = str(selection.get(f"{asset_type}_evidence", "")).strip()
                    decision[f"{asset_type}_evidence_time"] = selection.get(f"{asset_type}_evidence_time")
                continue
            if selected_id not in candidates:
                raise ValueError(f"{asset_type} selection is not in shortlist for beat {beat_id}: {selected_id}")
            evidence_problem = visual_evidence_problem(selection, asset_type, beat, rules)
            if evidence_problem:
                raise ValueError(evidence_problem)
            problem = diversity_problem(str(selected_id), float(beat["start"]), history, rules, asset_type)
            if problem:
                raise ValueError(problem)
            item = selected_item(beat, candidates[selected_id], asset_type, taxonomy, selection)
            if asset_type in ("visual", "character"):
                item_start = float(item["start"])
                item_end = item_start + float(item["duration"])
                if item_start < float(beat["start"]) or item_end > float(beat["end"]) + 0.001:
                    raise ValueError(f"{asset_type} effect timing is outside beat {beat_id}")
                evidence_time = float(selection.get(f"{asset_type}_evidence_time", item_start))
                if not item_start - 0.25 <= evidence_time <= item_end + 0.25:
                    raise ValueError(f"{asset_type} effect timing does not align with evidence for beat {beat_id}")
            elif asset_type == "sound":
                item_start = float(item["start"])
                item_end = item_start + float(item["duration"])
                if item_start < float(beat["start"]) or item_end > float(beat["end"]) + 0.001:
                    raise ValueError(f"sound effect timing is outside beat {beat_id}")
                opportunity = beat.get("sfx_opportunity", {})
                trigger_time = float(opportunity.get("trigger_time", item_start))
                approved_duration = float(opportunity.get("suggested_duration", item["duration"]))
                if abs(item_start - trigger_time) > 0.25:
                    raise ValueError(f"sound effect timing is detached from the approved trigger for beat {beat_id}")
                if float(item["duration"]) > approved_duration + 0.001:
                    raise ValueError(f"sound effect duration exceeds the approved opportunity for beat {beat_id}")
            output.append(item)
            decision[f"{asset_type}_selected"] = selected_id
            decision[f"{asset_type}_status"] = "selected"
            if asset_type in ("visual", "character"):
                decision[f"{asset_type}_evidence"] = str(selection.get(f"{asset_type}_evidence", "")).strip()
                decision[f"{asset_type}_evidence_time"] = selection.get(f"{asset_type}_evidence_time")
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
    style = taxonomy.get("style_profiles", {}).get(data.get("style"), {})
    if len(visual_effects) > int(style.get("max_scene_effects", 10**9)):
        raise ValueError("scene-effect style budget exceeded")
    if len(character_effects) > int(style.get("max_character_effects", 10**9)):
        raise ValueError("character-effect style budget exceeded")
    if len(sound_effects) > int(style.get("max_sound_effects", 10**9)):
        raise ValueError("sound-effect style budget exceeded")
    overlap_problems = effect_overlap_problems(data)
    if overlap_problems:
        raise ValueError("; ".join(overlap_problems))
    sound_problems = sound_plan_problems(data, taxonomy)
    if sound_problems:
        raise ValueError("; ".join(sound_problems))
    coverage_problems = creative_coverage_problems(data, taxonomy)
    if coverage_problems:
        raise ValueError("; ".join(coverage_problems))
    return data


def timeline_overlap(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    return min(float(left["start"]) + float(left["duration"]), float(right["start"]) + float(right["duration"])) - max(float(left["start"]), float(right["start"]))


def effect_overlap_problems(data: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    for scene in data.get("visual_effects", []):
        for character in data.get("character_effects", []):
            if timeline_overlap(scene, character) > 0.01:
                reason = str(scene.get("layering_reason") or character.get("layering_reason") or "").strip()
                if len(reason) < 12:
                    problems.append(
                        f"scene and character effects overlap without reviewed layering_reason: {scene.get('beat_id')}/{character.get('beat_id')}"
                    )
    return problems


def sound_plan_problems(data: Dict[str, Any], taxonomy: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    rules = selection_rules(taxonomy, "sound")
    sounds = sorted(data.get("sound_effects", []), key=lambda item: float(item.get("start", 0)))
    style = taxonomy.get("style_profiles", {}).get(data.get("style"), {})
    max_total = int(style.get("max_sound_effects", rules.get("max_total", 10**9)))
    max_volume = float(style.get("max_sfx_volume", rules.get("max_volume", 0.18)))
    min_gap = float(style.get("min_sfx_gap_seconds", rules.get("min_gap_seconds", 3.0)))
    if len(sounds) > max_total:
        problems.append(f"sound-effect style budget exceeded: {len(sounds)} > {max_total}")
    for index, item in enumerate(sounds, start=1):
        if item.get("track") != "SFX":
            problems.append(f"sound effect {index} must target SFX")
        if float(item.get("volume", 1.0)) > max_volume:
            problems.append(f"sound effect {index} exceeds narration-safe volume")
        if len(str(item.get("evidence", "")).strip()) < 8 or len(str(item.get("timing_reason", "")).strip()) < 8:
            problems.append(f"sound effect {index} lacks approved timing evidence")
    for left, right in zip(sounds, sounds[1:]):
        if timeline_overlap(left, right) > 0:
            problems.append(f"sound effects overlap: {left.get('beat_id')} and {right.get('beat_id')}")
        if float(right.get("start", 0)) - float(left.get("start", 0)) < min_gap:
            problems.append(f"sound effects are too close: {left.get('beat_id')} and {right.get('beat_id')}")
    return problems


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


def effect_visual_review(
    data: Dict[str, Any],
    review_path: Path | None,
    review_video: Path | None,
) -> Dict[str, Any]:
    selected = data.get("visual_effects", []) + data.get("character_effects", [])
    if not selected:
        return {"status": "not_required", "approved": True, "problems": []}
    if review_path is None or review_video is None:
        return {
            "status": "effect_visual_review_required",
            "approved": False,
            "problems": ["a real JianYing-rendered preview/export and approved effect visual review are required"],
        }
    problems: List[str] = []
    if not review_video.is_file():
        problems.append(f"effect review video not found: {review_video}")
    payload = load_json(review_path)
    if not isinstance(payload, dict) or payload.get("status") != "approved":
        problems.append("effect visual review status must be approved")
        payload = payload if isinstance(payload, dict) else {}
    recorded_video = str(payload.get("rendered_video", "")).strip()
    if not recorded_video:
        problems.append("effect visual review must record rendered_video")
    else:
        try:
            if Path(recorded_video).resolve() != review_video.resolve():
                problems.append("effect visual review rendered_video does not match supplied review video")
        except OSError:
            problems.append("effect visual review rendered_video is invalid")
    reviews = payload.get("effects", [])
    if not isinstance(reviews, list):
        reviews = []
        problems.append("effect visual review requires effects array")
    by_key = {
        (str(item.get("beat_id")), str(item.get("asset_id"))): item
        for item in reviews if isinstance(item, dict)
    }
    for item in selected:
        key = (str(item.get("beat_id")), str(item.get("asset_id")))
        review = by_key.get(key)
        if review is None:
            problems.append(f"missing rendered visual review for effect {key[0]}/{key[1]}")
            continue
        if review.get("status") != "approved":
            problems.append(f"effect visual review is not approved for {key[0]}/{key[1]}")
        evidence = str(review.get("pixel_observation", "")).strip()
        if len(evidence) < 12:
            problems.append(f"effect visual review lacks concrete pixel observation for {key[0]}/{key[1]}")
        for field in ("effect_visible", "captions_readable", "face_unobstructed", "style_appropriate"):
            if review.get(field) is not True:
                problems.append(f"effect visual review failed {field} for {key[0]}/{key[1]}")
        if review.get("excessive_flash_or_glitch") is not False:
            problems.append(f"effect visual review must reject excessive flash/glitch for {key[0]}/{key[1]}")
    return {
        "status": "effect_visual_approved" if not problems else "effect_visual_review_required",
        "approved": not problems,
        "review": str(review_path),
        "rendered_video": str(review_video),
        "problems": problems,
    }


def validate(
    plan_path: Path,
    draft_path: Path | None,
    catalog_path: Path | None = None,
    taxonomy_path: Path = DEFAULT_TAXONOMY,
    visual_review_path: Path | None = None,
    review_video_path: Path | None = None,
) -> Dict[str, Any]:
    data = load_json(plan_path)
    problems: List[str] = []
    taxonomy = load_json(taxonomy_path)
    catalog_data = load_json(catalog_path) if catalog_path else None
    catalog_ids = {item.get("asset_id") for item in (catalog_data or {}).get("assets", [])}
    visual_rules = selection_rules(taxonomy, "visual")
    sound_rules = selection_rules(taxonomy, "sound")
    character_rules = selection_rules(taxonomy, "character")
    problems.extend(creative_coverage_problems(data, taxonomy))
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
        beat = next((beat for beat in data.get("beats", []) if str(beat.get("beat_id")) == str(item.get("beat_id"))), None)
        if beat is None:
            problems.append(f"effect references unknown beat: {item.get('beat_id')}")
        else:
            start = float(item.get("start", 0))
            end = start + float(item.get("duration", 0))
            if start < float(beat["start"]) or end > float(beat["end"]) + 0.001:
                problems.append(f"effect timing is outside beat: {item.get('asset_id')}")
        if (data.get("visual_treatment_plan") or visual_rules.get("require_visual_evidence")) and len(str(item.get("evidence", "")).strip()) < 8:
            problems.append(f"effect lacks preserved frame evidence: {item.get('asset_id')}")
    for item in data.get("character_effects", []):
        beat = next((beat for beat in data.get("beats", []) if str(beat.get("beat_id")) == str(item.get("beat_id"))), {})
        eligible, reason = character_effect_eligibility(beat)
        if not eligible:
            problems.append(f"character effect uses ineligible beat {item.get('beat_id')}: {reason}")
        if data.get("visual_treatment_plan") and not item.get("character_intent"):
            problems.append(f"character effect lacks approved character_intent: {item.get('asset_id')}")
    problems.extend(effect_overlap_problems(data))
    problems.extend(sound_plan_problems(data, taxonomy))
    style = taxonomy.get("style_profiles", {}).get(data.get("style"), {})
    if len(data.get("visual_effects", [])) > int(style.get("max_scene_effects", 10**9)):
        problems.append("scene-effect style budget exceeded")
    if len(data.get("character_effects", [])) > int(style.get("max_character_effects", 10**9)):
        problems.append("character-effect style budget exceeded")
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
    structural_valid = not problems
    review_report = effect_visual_review(data, visual_review_path, review_video_path) if draft_path and structural_valid else {
        "status": "not_evaluated" if not draft_path else "structural_qc_failed",
        "approved": False,
        "problems": [],
    }
    if not structural_valid:
        workflow_status = "structural_failed"
    elif draft_path and review_report.get("approved"):
        workflow_status = "effect_visual_approved"
    elif draft_path:
        workflow_status = "effect_visual_review_required"
    else:
        workflow_status = "structural_passed"
    return {
        "valid": structural_valid,
        "final_approved": workflow_status == "effect_visual_approved",
        "workflow_status": workflow_status,
        "problems": problems,
        "draft": draft_report,
        "composition_qc": qc_report,
        "effect_visual_review": review_report,
    }


def preview(video_path: Path, plan_path: Path, output_dir: Path) -> Dict[str, Any]:
    """Extract short review clips from an exported JianYing preview."""
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    data = load_json(plan_path)
    effects = data.get("visual_effects", []) + data.get("character_effects", [])
    output_dir.mkdir(parents=True, exist_ok=True)
    clips: List[Dict[str, Any]] = []
    for index, effect in enumerate(effects, start=1):
        effect_start = float(effect["start"])
        effect_duration = float(effect["duration"])
        start = max(0.0, effect_start - 0.5)
        duration = min(6.0, max(1.2, effect_start + effect_duration + 0.5 - start))
        output = output_dir / f"effect_{index:03d}.mp4"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-c:a", "aac", "-b:a", "96k", str(output),
        ]
        subprocess.run(command, check=True)
        clips.append({
            "beat_id": effect.get("beat_id"), "asset_id": effect.get("asset_id"),
            "effect_start": effect_start, "effect_duration": effect_duration,
            "review_window_start": start, "review_window_duration": duration,
            "phases": {"pre": effect_start - start, "during": effect_duration, "post": 0.5},
            "path": str(output),
        })
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
    p_plan.add_argument("--visual-treatments", type=Path, help="Approved visual-treatment plan; only requested effect families receive candidates")
    p_plan.add_argument("--sfx-opportunities", type=Path, help="Approved SFX timing plan; only approved opportunities receive sound candidates")
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
    p_validate.add_argument("--visual-review", type=Path, help="Approved per-effect pixel review of a real JianYing render")
    p_validate.add_argument("--review-video", type=Path, help="Real JianYing-rendered preview/export inspected by --visual-review")
    p_validate.add_argument("--output", type=Path, help="Optional JSON report path for the validation gate")
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
            result = plan(args.beats, args.catalog, args.taxonomy, args.style, args.visual_treatments, args.sfx_opportunities)
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
            result = validate(args.plan, args.draft, args.catalog, args.taxonomy, args.visual_review, args.review_video)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
