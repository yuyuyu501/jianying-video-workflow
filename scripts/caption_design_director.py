#!/usr/bin/env python3
"""Create and validate a semantic, frame-reviewed JianYing caption design plan."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from validate_draft_captions import read_srt


ROLES = {"base", "hook", "warning", "step_title", "action", "number", "medicine", "cta"}
PRESENTATIONS = {"base", "keyword", "flower", "bubble"}
POSITIONS = {"lower_center", "mid_low", "center", "upper_safe"}
WARNING_TERMS = ("危险", "警惕", "不要", "不能", "立刻", "马上", "必须", "急救", "120", "黄金", "救命", "心梗", "超过")
ACTION_TERMS = ("坐下", "半卧", "拨打", "含服", "服用", "休息", "呼吸", "等待", "停止")
MEDICINE_TERMS = ("硝酸甘油", "阿司匹林", "救心丸", "药")
CTA_TERMS = ("点赞", "关注", "收藏", "转发", "评论")
STEP_PATTERN = re.compile(r"(?:第[一二三四五六七八九十\d]+(?:步|件事|点)|^[一二三四五六七八九十\d]+[.、])")
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?%?|[一二三四五六七八九十百]+(?:秒|分钟|小时|次|步|件事)")


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def semantic_role(text: str) -> str:
    if STEP_PATTERN.search(text):
        return "step_title"
    if any(term in text for term in MEDICINE_TERMS):
        return "medicine"
    if any(term in text for term in CTA_TERMS):
        return "cta"
    if any(term in text for term in WARNING_TERMS):
        return "warning"
    if any(term in text for term in ACTION_TERMS):
        return "action"
    if NUMBER_PATTERN.search(text):
        return "number"
    return "base"


def keyword_candidates(text: str, role: str) -> list[dict]:
    candidates: list[tuple[int, int, str]] = []
    terms = WARNING_TERMS + ACTION_TERMS + MEDICINE_TERMS
    for term in terms:
        start = text.find(term)
        if start >= 0:
            candidates.append((start, start + len(term), "warning" if term in WARNING_TERMS else role))
    for match in NUMBER_PATTERN.finditer(text):
        candidates.append((match.start(), match.end(), "number"))
    spans = []
    occupied: set[int] = set()
    for start, end, kind in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(position in occupied for position in range(start, end)):
            continue
        occupied.update(range(start, end))
        spans.append({"start": start, "end": end, "text": text[start:end], "kind": kind, "color": "red", "size_scale": 1.16})
        if len(spans) == 2:
            break
    return spans


def create_template(entries: list[dict], layout_review: dict) -> dict:
    layouts = layout_review.get("layouts", [])
    by_index = {int(item["index"]): item for item in layouts if isinstance(item, dict) and "index" in item}
    cues = []
    for index, entry in enumerate(entries):
        if index not in by_index:
            raise ValueError(f"caption layout review does not cover cue {index + 1}")
        layout = by_index[index]
        role = "hook" if index == 0 else semantic_role(entry["text"])
        spans = keyword_candidates(entry["text"], role)
        highlight_enabled = role == "step_title"
        card_enabled = "正确做法" in entry["text"] or "应该这样" in entry["text"]
        cues.append({
            "index": index,
            "start": entry["start"],
            "end": entry["end"],
            "text": entry["text"],
            "role": role,
            "presentation": "keyword" if spans else "base",
            "keyword_spans": spans,
            "position": layout["caption_zone"],
            "transform_y": layout["transform_y"],
            "representative_frame": layout.get("representative_frame"),
            "visual_context": layout.get("visual_context"),
            "highlight": {"enabled": highlight_enabled, "text": entry["text"] if highlight_enabled else "", "position": "upper_safe", "transform_x": 0.0, "transform_y": 0.38},
            "card": {"enabled": card_enabled, "title": "正确做法" if card_enabled else "", "body": entry["text"] if card_enabled else "", "transform_x": 0.0, "transform_y": 0.02},
            "review_status": "pending",
            "visual_evidence": "",
            "collision_review": {"status": "pending", "face": "unchecked", "speaker_pip": "unchecked", "native_text": "unchecked", "key_action": "unchecked", "platform_ui": "unchecked"},
        })
    expressive_budget = int(len(cues) * 0.08)
    candidates = [cue for cue in cues if cue["role"] in {"hook", "cta"}]
    candidates.extend(cue for cue in cues if cue["role"] == "warning" and len(cue["text"]) <= 8)
    for cue in candidates[:expressive_budget]:
        cue["presentation"] = "bubble" if cue["role"] == "cta" else "flower"

    return {
        "version": 1,
        "timestamp_basis": "rendered_rough_cut_output",
        "design_policy": {
            "base_style": "yellow_text_black_outline",
            "base_or_keyword_min_ratio": 0.65,
            "expressive_max_ratio": 0.15,
            "keyword_target_ratio": [0.15, 0.35],
            "rule": "Use semantic hierarchy and reviewed pixels; never rotate styles by cue index.",
        },
        "ai_review": {"status": "pending", "reviewer": "", "notes": "Inspect every representative frame, then approve or revise semantic role, keyword spans, hierarchy, and collision fields."},
        "cues": cues,
        "disclaimer": {"enabled": False, "text": "", "start": 0.0, "end": entries[-1]["end"] if entries else 0.0},
    }


def validate(plan: dict, entries: list[dict]) -> dict:
    errors: list[str] = []
    cues = plan.get("cues", [])
    if plan.get("timestamp_basis") != "rendered_rough_cut_output":
        errors.append("caption plan must use rendered_rough_cut_output timestamps")
    if plan.get("ai_review", {}).get("status") != "approved":
        errors.append("caption plan requires ai_review.status=approved after representative-frame inspection")
    if not isinstance(cues, list) or len(cues) != len(entries):
        errors.append(f"caption plan cue count differs: plan={len(cues) if isinstance(cues, list) else 'invalid'} SRT={len(entries)}")
        cues = cues if isinstance(cues, list) else []

    expressive = 0
    keyword_count = 0
    semantic_opportunities = 0
    highlights = 0
    cards = 0
    previous_overlay_end = {"highlight": -1.0, "card": -1.0}
    for index, (cue, entry) in enumerate(zip(cues, entries)):
        label = f"caption cue {index + 1}"
        if int(cue.get("index", -1)) != index:
            errors.append(f"{label} index differs")
        if cue.get("text") != entry["text"]:
            errors.append(f"{label} text differs from SRT")
        if abs(float(cue.get("start", -1)) - entry["start"]) > 0.001 or abs(float(cue.get("end", -1)) - entry["end"]) > 0.001:
            errors.append(f"{label} timing differs from SRT")
        role = str(cue.get("role", ""))
        presentation = str(cue.get("presentation", ""))
        if role not in ROLES:
            errors.append(f"{label} has invalid semantic role {role!r}")
        if presentation not in PRESENTATIONS:
            errors.append(f"{label} has invalid presentation {presentation!r}")
        if cue.get("position") not in POSITIONS or not -0.9 <= float(cue.get("transform_y", 9)) <= 0.9:
            errors.append(f"{label} has invalid reviewed position")
        if not str(cue.get("representative_frame", "")).strip():
            errors.append(f"{label} has no representative frame for visual review")
        if cue.get("review_status") != "approved" or len(str(cue.get("visual_evidence", "")).strip()) < 12:
            errors.append(f"{label} requires approved review_status and concrete visual_evidence")
        collision = cue.get("collision_review", {})
        if not isinstance(collision, dict) or collision.get("status") != "passed":
            errors.append(f"{label} requires collision_review.status=passed")
        elif any(not str(collision.get(field, "")).strip() or collision.get(field) == "unchecked" for field in ("face", "speaker_pip", "native_text", "key_action", "platform_ui")):
            errors.append(f"{label} collision review must address face, SpeakerPiP, native text, key action, and platform UI")

        spans = cue.get("keyword_spans", [])
        if not isinstance(spans, list):
            errors.append(f"{label} keyword_spans must be an array")
            spans = []
        occupied: set[int] = set()
        for span_index, span in enumerate(spans, start=1):
            start, end = int(span.get("start", -1)), int(span.get("end", -1))
            if start < 0 or end <= start or end > len(entry["text"]) or entry["text"][start:end] != span.get("text"):
                errors.append(f"{label} keyword span {span_index} does not match its text range")
                continue
            if any(position in occupied for position in range(start, end)):
                errors.append(f"{label} keyword spans overlap")
            occupied.update(range(start, end))
            if span.get("color") not in {"red", "orange", "white"} or not 1.0 <= float(span.get("size_scale", 0)) <= 1.35:
                errors.append(f"{label} keyword span {span_index} has an invalid emphasis style")
        if role != "base":
            semantic_opportunities += 1
        if spans:
            keyword_count += 1
            if presentation == "base":
                errors.append(f"{label} has keyword spans but is marked as base presentation")
        elif presentation == "keyword":
            errors.append(f"{label} uses keyword presentation without keyword spans")
        if presentation in {"flower", "bubble"}:
            expressive += 1

        for overlay_name in ("highlight", "card"):
            overlay = cue.get(overlay_name, {})
            if not isinstance(overlay, dict):
                errors.append(f"{label} {overlay_name} must be an object")
                continue
            if overlay.get("enabled"):
                if entry["start"] < previous_overlay_end[overlay_name] - 0.001:
                    errors.append(f"{overlay_name} overlays overlap at {label}")
                previous_overlay_end[overlay_name] = entry["end"]
                highlights += overlay_name == "highlight"
                cards += overlay_name == "card"
        if role == "step_title" and not cue.get("highlight", {}).get("enabled"):
            errors.append(f"{label} is a step title but has no CaptionHighlights treatment")
        if ("正确做法" in entry["text"] or "应该这样" in entry["text"]) and not cue.get("card", {}).get("enabled"):
            errors.append(f"{label} introduces a correct method but has no CaptionCards treatment")

    cue_count = max(1, len(cues))
    expressive_ratio = expressive / cue_count
    stable_ratio = (len(cues) - expressive) / cue_count
    if stable_ratio < 0.65:
        errors.append(f"stable base/keyword caption ratio is too low: {stable_ratio:.3f}")
    if expressive_ratio > 0.15:
        errors.append(f"flower/bubble caption ratio is too high: {expressive_ratio:.3f}")
    for index in range(2, len(cues)):
        if all(cues[offset].get("presentation") in {"flower", "bubble"} for offset in (index - 2, index - 1, index)):
            errors.append("three expressive captions appear consecutively")
            break
    if semantic_opportunities >= 4 and keyword_count / semantic_opportunities < 0.5:
        errors.append("fewer than half of semantic emphasis opportunities use keyword spans")

    disclaimer = plan.get("disclaimer", {})
    if disclaimer.get("enabled"):
        start = float(disclaimer.get("start", -1))
        end = float(disclaimer.get("end", -1))
        timeline_end = entries[-1]["end"] if entries else 0.0
        if not str(disclaimer.get("text", "")).strip() or start < 0 or end <= start or end > timeline_end + 0.05:
            errors.append("enabled disclaimer requires text and timing within the final caption timeline")

    return {
        "status": "passed" if not errors else "failed",
        "cue_count": len(cues),
        "keyword_cues": keyword_count,
        "expressive_cues": expressive,
        "highlight_segments": highlights,
        "card_segments": cards,
        "stable_ratio": round(stable_ratio, 4),
        "expressive_ratio": round(expressive_ratio, 4),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    template = subparsers.add_parser("template")
    template.add_argument("--captions", type=Path, required=True)
    template.add_argument("--layout-review", type=Path, required=True)
    template.add_argument("--output", type=Path, required=True)
    check = subparsers.add_parser("validate")
    check.add_argument("--captions", type=Path, required=True)
    check.add_argument("--plan", type=Path, required=True)
    check.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        entries = read_srt(args.captions.resolve())
        if args.command == "template":
            result = create_template(entries, load_json(args.layout_review.resolve()))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": "review_required", "caption_plan_template": str(args.output.resolve())}, ensure_ascii=False))
            return 0
        result = validate(load_json(args.plan.resolve()), entries)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        result = {"status": "failed", "errors": [str(error)]}
    if getattr(args, "output", None):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
