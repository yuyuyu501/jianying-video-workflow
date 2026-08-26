"""Plan expressive, visually aware caption presentations for short-form video."""

from __future__ import annotations

from pathlib import Path


FLOWER_EFFECT_IDS = (
    "7351316503771368713",
    "7187739440347958589",
    "7127669365423508767",
)
ALERT_TERMS = ("警", "危险", "不要", "立刻", "马上", "必须", "急救", "120", "分钟", "黄金", "救命", "心梗")


def available_flower_effects(artist_effect_root: Path | None) -> tuple[str, ...]:
    if artist_effect_root is None:
        return ()
    return tuple(effect_id for effect_id in FLOWER_EFFECT_IDS if (artist_effect_root / effect_id).is_dir())


def is_alert(text: str) -> bool:
    return any(term in text for term in ALERT_TERMS)


def overlaps(entry: dict, segment: dict) -> bool:
    return float(entry["start"]) < float(segment["start"]) + float(segment["duration"]) and float(segment["start"]) < float(entry["end"])


def visual_context(entry: dict, broll_segments: list[dict]) -> str:
    for segment in broll_segments:
        if not overlaps(entry, segment):
            continue
        pip = segment.get("speaker_pip", {})
        if isinstance(pip, dict) and pip.get("enabled"):
            return "broll_with_pip"
        return "full_broll"
    return "talking_head"


def font_size(text: str, *, emphasis: bool) -> float:
    length = len(text.replace("\n", ""))
    if length <= 8:
        return 12.2 if emphasis else 11.4
    if length <= 13:
        return 10.8 if emphasis else 10.1
    return 9.6 if emphasis else 9.1


def style_position(variant: str, context: str) -> tuple[str, float]:
    if context == "broll_with_pip":
        return "lower_center", -0.48
    if context == "full_broll":
        return "mid_low", -0.34
    if variant in {"alert_impact", "punch_mid"}:
        return "mid_low", -0.34
    return "lower_center", -0.54


def plan_caption_styles(
    entries: list[dict], *, available_effect_ids: tuple[str, ...] = (), broll_segments: list[dict] = (), layout_review: dict | None = None
) -> list[dict]:
    """Use a high-frequency, non-repeating short-video caption treatment.

    Every cue receives a presentation decision. Visual context keeps captions
    clear of circular speaker PiP and lets full-height B-roll carry larger text.
    """
    plan = []
    flower_index = 0
    cycle = ("punch_mid", "bubble", "flower", "punch_mid", "bubble", "flower")
    reviewed_layouts = (layout_review or {}).get("layouts", [])
    layout_by_index = {int(item["index"]): item for item in reviewed_layouts if isinstance(item, dict) and "index" in item}
    for index, entry in enumerate(entries):
        text = str(entry["text"])
        reviewed = layout_by_index.get(index)
        context = str(reviewed["visual_context"]) if reviewed else visual_context(entry, broll_segments)
        if is_alert(text):
            variant = "alert_impact"
        else:
            variant = cycle[index % len(cycle)]
            if variant == "flower" and not available_effect_ids:
                variant = "bubble"
        position, transform_y = style_position(variant, context)
        if reviewed:
            position = str(reviewed["caption_zone"])
            transform_y = float(reviewed["transform_y"])
        style = {
            "index": index,
            "variant": variant,
            "position": position,
            "transform_y": transform_y,
            "visual_context": context,
            "font_size": font_size(text, emphasis=variant in {"alert_impact", "punch_mid", "flower"}),
        }
        if variant == "flower":
            style["flower_effect_id"] = available_effect_ids[flower_index % len(available_effect_ids)]
            flower_index += 1
        plan.append(style)
    return plan
