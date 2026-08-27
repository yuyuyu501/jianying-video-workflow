#!/usr/bin/env python3
"""Catalog real JianYing filters/stickers and gate their AI-reviewed plans."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PORTRAIT_FILTERS = (
    "亮肤", "净白肤", "去黄提亮", "去黄增质", "美白嫩肤", "冰肌", "冷白", "初恋",
)


def locate_editor_skill() -> Path:
    configured = os.environ.get("JY_SKILL_ROOT", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([Path.home() / ".codex" / "skills" / "jianying-editor", Path.home() / ".agents" / "skills" / "jianying-editor"])
    for candidate in candidates:
        if (candidate / "scripts" / "vendor" / "pyJianYingDraft").is_dir():
            return candidate.resolve()
    raise RuntimeError("jianying-editor Skill is not installed")


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


def filter_catalog() -> list[dict]:
    editor = locate_editor_skill()
    sys.path.insert(0, str(editor / "scripts" / "vendor"))
    import pyJianYingDraft as draft

    return [
        {
            "name": item.value.name,
            "identifier": item.name,
            "resource_id": item.value.resource_id,
            "effect_id": item.value.effect_id,
            "is_vip": item.value.is_vip,
            "portrait_candidate": item.value.name in PORTRAIT_FILTERS,
        }
        for item in draft.FilterType
    ]


def sticker_catalog(drafts_root: Path) -> list[dict]:
    by_resource: dict[str, dict] = {}
    if not drafts_root.is_dir():
        return []
    for draft_info in list(drafts_root.glob("*/draft_info.json")) + list(drafts_root.glob("*/draft_content.json")):
        try:
            payload = load_json(draft_info)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for material in payload.get("materials", {}).get("stickers", []):
            resource_id = str(material.get("resource_id") or material.get("sticker_id") or "").strip()
            if not resource_id:
                continue
            record = by_resource.setdefault(resource_id, {
                "resource_id": resource_id,
                "name": str(material.get("name") or material.get("title") or resource_id),
                "type": "sticker",
                "verified_sources": [],
            })
            source = str(draft_info.parent.resolve())
            if source not in record["verified_sources"]:
                record["verified_sources"].append(source)
    return sorted(by_resource.values(), key=lambda item: (item["name"], item["resource_id"]))


def catalog(drafts_root: Path) -> dict:
    filters = filter_catalog()
    stickers = sticker_catalog(drafts_root)
    return {
        "version": 1,
        "source": "local_jianying_library",
        "filters": filters,
        "portrait_filters": [item for item in filters if item["portrait_candidate"]],
        "stickers": stickers,
        "counts": {"filters": len(filters), "portrait_filters": sum(item["portrait_candidate"] for item in filters), "stickers": len(stickers)},
    }


def beat_list(payload: dict) -> list[dict]:
    beats = payload.get("beats", payload.get("segments", []))
    if not isinstance(beats, list):
        raise ValueError("beats input requires a beats or segments array")
    return [item for item in beats if isinstance(item, dict)]


def timeline_duration(beats: list[dict]) -> float:
    ends = []
    for beat in beats:
        if "end" in beat:
            ends.append(float(beat["end"]))
        elif "start" in beat and "duration" in beat:
            ends.append(float(beat["start"]) + float(beat["duration"]))
    return max(ends, default=0.0)


def sticker_shortlist(beats: list[dict], stickers: list[dict], limit: int = 24) -> list[dict]:
    context = json.dumps(beats, ensure_ascii=False).lower()

    def score(item: dict) -> tuple[int, str]:
        name = str(item.get("name", "")).lower()
        meaningful = {char for char in name if char.isalnum()}
        overlap = sum(context.count(char) for char in meaningful)
        exact = 1000 if name and name in context else 0
        return exact + overlap, name

    ranked = sorted(stickers, key=lambda item: (-score(item)[0], score(item)[1], str(item.get("resource_id", ""))))
    return ranked[:limit]


def plan_templates(beats_path: Path, catalog_path: Path) -> tuple[dict, dict]:
    beats = beat_list(load_json(beats_path))
    assets = load_json(catalog_path)
    duration = timeline_duration(beats)
    common_review = {"required": True, "status": "pending", "reviewed_frames": []}
    filter_candidates = assets.get("portrait_filters", [])[:8]
    sticker_pool = assets.get("stickers", [])
    sticker_candidates = sticker_shortlist(beats, sticker_pool)
    filter_plan = {
        "version": 1,
        "status": "review_required",
        "timeline_duration": duration,
        "track": "Filters",
        "purpose": "Conservative talking-head beautification and color filtering based on inspected frames.",
        "item_contract": ["name", "resource_id", "start", "duration", "intensity", "purpose", "visible_person", "visual_evidence_time", "visual_evidence", "status"],
        "candidate_limit": 8,
        "candidate_pool_count": len(assets.get("portrait_filters", [])),
        "candidates": filter_candidates,
        "filters": [],
        "skip_reason": "",
        "ai_review": dict(common_review),
    }
    sticker_plan = {
        "version": 1,
        "status": "review_required",
        "timeline_duration": duration,
        "track": "Stickers",
        "purpose": "Content-matched stickers placed without covering faces, captions, PiP, native text, or key actions.",
        "item_contract": ["resource_id", "start", "duration", "scale", "transform_x", "transform_y", "rotation", "beat_id", "reason", "visual_evidence_time", "visual_evidence", "collision_review", "status"],
        "candidate_limit": 24,
        "candidate_pool_count": len(sticker_pool),
        "candidates": sticker_candidates,
        "stickers": [],
        "skip_reason": "",
        "ai_review": dict(common_review),
    }
    return filter_plan, sticker_plan


def validate_filter_plan(plan: dict, assets: dict) -> dict:
    errors: list[str] = []
    items = plan.get("filters", [])
    if not isinstance(items, list):
        return {"valid": False, "errors": ["filters must be an array"]}
    review = plan.get("ai_review", {})
    if not isinstance(review, dict):
        review = {}
    if review.get("status") != "approved":
        errors.append("ai_review.status must be approved")
    if not isinstance(review.get("reviewed_frames"), list) or not review["reviewed_frames"]:
        errors.append("ai_review.reviewed_frames must list the actual inspected final-timeline frames")
    if not items and not str(plan.get("skip_reason", "")).strip():
        errors.append("empty filter plan requires a specific skip_reason")
    available = {str(item["resource_id"]): item for item in assets.get("filters", [])}
    shortlisted = {str(item["resource_id"]) for item in plan.get("candidates", [])}
    ranges: list[tuple[float, float]] = []
    duration_limit = float(plan.get("timeline_duration", 0) or 0)
    for index, item in enumerate(items, start=1):
        try:
            start, duration, intensity = float(item["start"]), float(item["duration"]), float(item["intensity"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"filter {index} has invalid timing or intensity")
            continue
        resource = available.get(str(item.get("resource_id", "")))
        if resource is None or resource.get("name") != item.get("name"):
            errors.append(f"filter {index} is not a matching real catalog resource")
        if str(item.get("resource_id", "")) not in shortlisted:
            errors.append(f"filter {index} was not selected from the generated shortlist")
        if start < 0 or duration <= 0 or not 5 <= intensity <= 60:
            errors.append(f"filter {index} has out-of-range timing or intensity")
        if duration_limit and start + duration > duration_limit + 0.02:
            errors.append(f"filter {index} exceeds the final timeline")
        if item.get("status") != "approved" or not str(item.get("visual_evidence", "")).strip():
            errors.append(f"filter {index} lacks approved frame-grounded evidence")
        try:
            evidence_time = float(item.get("visual_evidence_time", -1))
        except (TypeError, ValueError):
            evidence_time = -1
        if not start <= evidence_time <= start + duration:
            errors.append(f"filter {index} visual_evidence_time is outside its interval")
        if not str(item.get("purpose", "")).strip():
            errors.append(f"filter {index} requires a concrete beautification/filter purpose")
        if item.get("visible_person") is not True:
            errors.append(f"filter {index} must confirm a visible talking-head person")
        ranges.append((start, start + duration))
    for previous, current in zip(sorted(ranges), sorted(ranges)[1:]):
        if current[0] < previous[1] - 0.000001:
            errors.append("Filters track contains overlapping segments")
            break
    return {"valid": not errors, "item_count": len(items), "errors": errors}


def validate_sticker_plan(plan: dict, assets: dict) -> dict:
    errors: list[str] = []
    items = plan.get("stickers", [])
    if not isinstance(items, list):
        return {"valid": False, "errors": ["stickers must be an array"]}
    review = plan.get("ai_review", {})
    if not isinstance(review, dict):
        review = {}
    if review.get("status") != "approved":
        errors.append("ai_review.status must be approved")
    if not isinstance(review.get("reviewed_frames"), list) or not review["reviewed_frames"]:
        errors.append("ai_review.reviewed_frames must list the actual inspected final-timeline frames")
    if not items and not str(plan.get("skip_reason", "")).strip():
        errors.append("empty sticker plan requires a specific skip_reason")
    available = {str(item["resource_id"]) for item in assets.get("stickers", [])}
    shortlisted = {str(item["resource_id"]) for item in plan.get("candidates", [])}
    duration_limit = float(plan.get("timeline_duration", 0) or 0)
    ranges: list[tuple[float, float]] = []
    for index, item in enumerate(items, start=1):
        try:
            start, duration = float(item["start"]), float(item["duration"])
            scale = float(item.get("scale", 1.0))
            transform_x, transform_y = float(item.get("transform_x", 0.0)), float(item.get("transform_y", 0.0))
        except (KeyError, TypeError, ValueError):
            errors.append(f"sticker {index} has invalid timing or transform")
            continue
        if str(item.get("resource_id", "")) not in available:
            errors.append(f"sticker {index} resource_id was not verified in the local JianYing catalog")
        if str(item.get("resource_id", "")) not in shortlisted:
            errors.append(f"sticker {index} was not selected from the generated shortlist")
        rotation = float(item.get("rotation", 0.0))
        if start < 0 or duration <= 0 or not 0.05 <= scale <= 2.5 or not -1 <= transform_x <= 1 or not -1 <= transform_y <= 1 or not -360 <= rotation <= 360:
            errors.append(f"sticker {index} has out-of-range timing or transform")
        if duration_limit and start + duration > duration_limit + 0.02:
            errors.append(f"sticker {index} exceeds the final timeline")
        if item.get("status") != "approved" or not str(item.get("visual_evidence", "")).strip():
            errors.append(f"sticker {index} lacks approved frame-grounded evidence")
        try:
            evidence_time = float(item.get("visual_evidence_time", -1))
        except (TypeError, ValueError):
            evidence_time = -1
        if not start <= evidence_time <= start + duration:
            errors.append(f"sticker {index} visual_evidence_time is outside its interval")
        if not str(item.get("beat_id", "")).strip() or not str(item.get("reason", "")).strip():
            errors.append(f"sticker {index} requires beat_id and a content-specific reason")
        collision = item.get("collision_review", {})
        if collision.get("status") != "passed" or not str(collision.get("evidence", "")).strip():
            errors.append(f"sticker {index} lacks a passed face/caption/PiP/native-text collision review")
        ranges.append((start, start + duration))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1] - 0.000001:
            errors.append("Stickers track contains overlapping segments")
            break
    return {"valid": not errors, "item_count": len(items), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    catalog_parser = subparsers.add_parser("catalog")
    catalog_parser.add_argument("--drafts-root", type=Path)
    catalog_parser.add_argument("--output", type=Path, required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--beats", type=Path, required=True)
    plan_parser.add_argument("--catalog", type=Path, required=True)
    plan_parser.add_argument("--output-dir", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--kind", choices=("filters", "stickers"), required=True)
    validate_parser.add_argument("--plan", type=Path, required=True)
    validate_parser.add_argument("--catalog", type=Path, required=True)
    validate_parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "catalog":
            result = catalog(args.drafts_root.resolve() if args.drafts_root else default_drafts_root())
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            response = {"status": "succeeded", "output": str(args.output), **result["counts"]}
        elif args.command == "plan":
            filters, stickers = plan_templates(args.beats, args.catalog)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            filter_path, sticker_path = args.output_dir / "filter_plan.template.json", args.output_dir / "sticker_plan.template.json"
            filter_path.write_text(json.dumps(filters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            sticker_path.write_text(json.dumps(stickers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            response = {"status": "review_required", "filter_plan": str(filter_path), "sticker_plan": str(sticker_path)}
        else:
            plan, assets = load_json(args.plan), load_json(args.catalog)
            result = validate_filter_plan(plan, assets) if args.kind == "filters" else validate_sticker_plan(plan, assets)
            response = {"status": "succeeded" if result["valid"] else "failed", **result}
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("RESULT: " + json.dumps(response, ensure_ascii=False))
        return 0 if response["status"] in {"succeeded", "review_required"} else 2
    except (OSError, RuntimeError, TypeError, ValueError, ImportError, json.JSONDecodeError) as error:
        print("RESULT: " + json.dumps({"status": "failed", "errors": [str(error)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
