"""Materialization helpers for an approved semantic caption plan."""

from __future__ import annotations

from pathlib import Path


FLOWER_EFFECT_IDS = (
    "7351316503771368713",
    "7187739440347958589",
    "7127669365423508767",
)
KEYWORD_COLORS = {
    "red": (1.0, 0.12, 0.10),
    "orange": (1.0, 0.42, 0.05),
    "white": (1.0, 1.0, 1.0),
}


def available_flower_effects(artist_effect_root: Path | None) -> tuple[str, ...]:
    if artist_effect_root is None:
        return ()
    return tuple(effect_id for effect_id in FLOWER_EFFECT_IDS if (artist_effect_root / effect_id).is_dir())


def font_size(text: str, *, emphasis: bool = False) -> float:
    length = len(text.replace("\n", ""))
    if length <= 8:
        return 12.3 if emphasis else 11.7
    if length <= 13:
        return 11.0 if emphasis else 10.5
    return 9.8 if emphasis else 9.4


def materialization_plan(approved_plan: dict, available_effect_ids: tuple[str, ...] = ()) -> list[dict]:
    """Resolve approved decisions without introducing cue-index style rotation."""
    resolved = []
    flower_index = 0
    for cue in approved_plan.get("cues", []):
        item = dict(cue)
        item["font_size"] = font_size(str(cue["text"]), emphasis=cue.get("presentation") in {"flower", "bubble"})
        if cue.get("presentation") == "flower":
            if available_effect_ids:
                requested = str(cue.get("flower_effect_id", ""))
                if requested and requested not in available_effect_ids:
                    raise ValueError(f"caption cue {int(cue['index']) + 1} requests an unavailable flower effect")
                item["flower_effect_id"] = requested or available_effect_ids[flower_index % len(available_effect_ids)]
                flower_index += 1
            else:
                item["presentation"] = "keyword" if cue.get("keyword_spans") else "base"
                item["flower_fallback"] = "local flower effect unavailable"
        resolved.append(item)
    return resolved
