"""Deterministically vary subtitle presentation without inventing JianYing IDs."""

from __future__ import annotations

from pathlib import Path


FLOWER_EFFECT_IDS = (
    "7351316503771368713",
    "7187739440347958589",
    "7127669365423508767",
)
ALERT_TERMS = ("警", "危险", "不要", "立刻", "马上", "必须", "急救", "120", "分钟", "黄金")


def available_flower_effects(artist_effect_root: Path | None) -> tuple[str, ...]:
    if artist_effect_root is None:
        return ()
    return tuple(effect_id for effect_id in FLOWER_EFFECT_IDS if (artist_effect_root / effect_id).is_dir())


def is_alert(text: str) -> bool:
    return any(term in text for term in ALERT_TERMS)


def plan_caption_styles(entries: list[dict], *, available_effect_ids: tuple[str, ...] = ()) -> list[dict]:
    """Create a restrained, non-repeating style plan for materialized SRT entries."""
    plan = []
    flower_index = 0
    for index, entry in enumerate(entries):
        text = str(entry["text"])
        if is_alert(text):
            variant = "alert_middle"
        else:
            # Use emphasis every other caption at most; flower text stays sparse.
            cycle = index % 6
            if cycle == 1:
                variant = "bubble_middle"
            elif cycle == 3 and available_effect_ids:
                variant = "flower_bottom"
            else:
                variant = "base_bottom"
        style = {
            "index": index,
            "variant": variant,
            "position": "middle" if variant.endswith("middle") else "bottom",
            "transform_y": -0.08 if variant.endswith("middle") else -0.72,
        }
        if variant == "flower_bottom":
            style["flower_effect_id"] = available_effect_ids[flower_index % len(available_effect_ids)]
            flower_index += 1
        plan.append(style)
    # A timeline made entirely of warning phrases still needs one relief beat;
    # otherwise draft QC would correctly report a one-note caption treatment.
    if len(plan) >= 2 and (len({item["variant"] for item in plan}) == 1 or len({item["position"] for item in plan}) == 1):
        plan[-1] = {"index": len(plan) - 1, "variant": "base_bottom", "position": "bottom", "transform_y": -0.72}
    return plan
