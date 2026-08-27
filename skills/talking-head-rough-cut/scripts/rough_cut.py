#!/usr/bin/env python3
"""Plan, render, and validate conservative talking-head rough cuts."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SILENCE_DB = -25.0
DEFAULT_SILENCE_MIN = 0.15
DEFAULT_PRESERVE_AIR = 0.18
MIN_REMOVAL = 0.06
DEFAULT_MAX_SILENCE = 1.5
DEFAULT_MAX_RESIDUAL_PAUSE = 0.35
DEFAULT_DURATION_TOLERANCE = 0.25
DEFAULT_TARGET_CPM = 285.0
DEFAULT_MIN_CPM = 260.0
DEFAULT_MAX_SPEED = 1.35
MIN_SPEED_CHANGE = 1.05


def emit(status: str, command: str, **payload: Any) -> None:
    print("RESULT: " + json.dumps({"status": status, "command": command, **payload}, ensure_ascii=False))


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def require_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        raise RuntimeError("Missing required tools: " + ", ".join(missing))


def probe(video: Path, *, require_audio: bool = True) -> dict[str, Any]:
    result = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-show_streams", "-of", "json", str(video),
    ], capture=True)
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0))
    has_audio = any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))
    if duration <= 0:
        raise RuntimeError("Video duration is missing or zero")
    if require_audio and not has_audio:
        raise RuntimeError("Talking-head rough cut requires an audio stream")
    return {"duration": duration, "streams": data.get("streams", [])}


def find_silences(video: Path, noise_db: float, minimum_seconds: float) -> list[tuple[float, float]]:
    result = run([
        "ffmpeg", "-hide_banner", "-nostdin", "-i", str(video), "-vn",
        "-af", f"silencedetect=noise={noise_db:g}dB:d={minimum_seconds:.3f}",
        "-f", "null", os.devnull,
    ], capture=True)
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)]
    return [(start, end) for start, end in zip(starts, ends) if end > start]


def pause_exclusion(silence_start: float, silence_end: float, target_pause: float) -> tuple[float, float] | None:
    silence_duration = silence_end - silence_start
    retained_edge = min(silence_duration, target_pause) / 2.0
    start = silence_start + retained_edge
    end = silence_end - retained_edge
    return (start, end) if end - start >= MIN_REMOVAL else None


def audio_duration(media: dict[str, Any]) -> float:
    for stream in media.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        try:
            value = float(stream.get("duration", 0.0))
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return float(media["duration"])


def audio_qc(path: Path, expected_duration: float, max_silence: float) -> dict[str, Any]:
    media = probe(path)
    duration = audio_duration(media)
    silences = find_silences(path, -45.0, max_silence)
    silence_duration = sum(end - start for start, end in silences)
    active_duration = max(0.0, duration - silence_duration)
    return {
        "path": str(path),
        "duration": round(duration, 3),
        "expected_duration": round(expected_duration, 3),
        "duration_delta": round(duration - expected_duration, 3),
        "active_duration": round(active_duration, 3),
        "long_silences": [{"start": round(start, 3), "end": round(end, 3)} for start, end in silences],
    }


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text).lower()


def transcript_characters(analysis_path: Path | None, kept_ranges: list[dict[str, float]] | None = None) -> int:
    if not analysis_path or not analysis_path.exists():
        return 0
    transcript = read_json(analysis_path).get("transcript", [])
    total = 0.0
    for item in transcript:
        text_length = len(normalize_text(str(item.get("text", ""))))
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        duration = end - start
        if text_length <= 0 or duration <= 0:
            continue
        if kept_ranges is None:
            total += text_length
            continue
        overlap = sum(max(0.0, min(end, float(kept["end"])) - max(start, float(kept["start"]))) for kept in kept_ranges)
        total += text_length * min(1.0, overlap / duration)
    return int(round(total))


def pace_analysis(
    analysis_path: Path | None,
    kept_ranges: list[dict[str, float]],
    *,
    mode: str,
    target_cpm: float,
    minimum_cpm: float,
    maximum_speed: float,
    reference_analysis: Path | None = None,
    character_ranges: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    duration = sum(float(item["end"]) - float(item["start"]) for item in kept_ranges)
    characters = transcript_characters(analysis_path, character_ranges or kept_ranges)
    reference = None
    if reference_analysis:
        reference_payload = read_json(reference_analysis)
        reference_duration = float(reference_payload.get("duration", 0.0))
        reference_characters = transcript_characters(reference_analysis)
        if reference_duration <= 0 or reference_characters <= 0:
            raise ValueError("Pace reference analysis needs a positive duration and transcript text")
        target_cpm = reference_characters / reference_duration * 60.0
        reference = {
            "path": str(reference_analysis.resolve()),
            "duration": round(reference_duration, 3),
            "characters": reference_characters,
            "chars_per_minute": round(target_cpm, 1),
        }
    current_cpm = characters / duration * 60.0 if duration > 0 and characters > 0 else 0.0
    raw_speed = target_cpm / current_cpm if current_cpm > 0 and current_cpm < minimum_cpm else 1.0
    recommended_speed = max(1.0, raw_speed)
    needs_speed = recommended_speed >= MIN_SPEED_CHANGE
    applied_speed = round(min(recommended_speed, maximum_speed), 3) if mode == "auto" and needs_speed else 1.0
    if characters <= 0:
        decision = "review_required"
        reason = "Transcript character count is unavailable; pace cannot be measured safely"
    elif not needs_speed:
        decision = "no_change"
        reason = "Rough-cut speech density is already within the target pace band"
    elif recommended_speed > maximum_speed:
        decision = "speed_up_and_review_content"
        reason = "Reaching the target pace would exceed the safe speed cap; apply the cap and review semantic cuts"
    elif mode == "off":
        decision = "speed_recommended_but_disabled"
        reason = "The measured pace is slow, but automatic speed adjustment is disabled"
    elif mode == "review":
        decision = "speed_review_required"
        reason = "The measured pace is slow; approve the recommended speed before rendering"
    else:
        decision = "speed_up"
        reason = "The measured rough-cut pace is below the minimum target"
    return {
        "mode": mode,
        "decision": decision,
        "reason": reason,
        "characters": characters,
        "pre_speed_duration": round(duration, 3),
        "current_chars_per_minute": round(current_cpm, 1),
        "minimum_chars_per_minute": round(minimum_cpm, 1),
        "target_chars_per_minute": round(target_cpm, 1),
        "recommended_speed": round(recommended_speed, 3),
        "maximum_speed": round(maximum_speed, 3),
        "applied_speed": applied_speed,
        "estimated_output_duration": round(duration / applied_speed, 3),
        "reference": reference,
    }


def reference_anchor(reference_script: Path) -> str:
    """Return the first on-topic phrase used to audit opening chatter."""
    raw = reference_script.read_text(encoding="utf-8")
    topic = re.search(r"(?:话题|主题)\s*[:：]\s*([^\n。！？]+)", raw)
    if topic:
        value = topic.group(1).strip()
    else:
        value = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    value = re.sub(r"^(?:文案|稿件)\s*[:：]\s*", "", value)
    value = re.split(r"[，,；;。.!！?？]", value, maxsplit=1)[0]
    return normalize_text(value)


def script_alignment_candidates(analysis_path: Path | None, reference_script: Path | None) -> list[dict[str, Any]]:
    """Find speech before the first recognizable on-topic script phrase."""
    if not analysis_path or not analysis_path.exists() or not reference_script:
        return []
    transcript = read_json(analysis_path).get("transcript", [])
    anchor = reference_anchor(reference_script)
    if not anchor or not transcript:
        return []

    best: tuple[float, int] | None = None
    for index, current in enumerate(transcript):
        if float(current.get("start", 0)) > 25.0:
            break
        for end_index in range(index, min(len(transcript), index + 4)):
            joined = normalize_text("".join(str(item.get("text", "")) for item in transcript[index:end_index + 1]))
            if not joined:
                continue
            score = difflib.SequenceMatcher(None, anchor, joined).ratio()
            if best is None or score > best[0]:
                best = (score, index)

    if best is None or best[0] < 0.42:
        # ASR can corrupt a short opening phrase. A first utterance followed by
        # a clear gap is still useful as a conservative setup-chatter review
        # candidate; it is never removed without an approved exclusion.
        first = transcript[0]
        if float(first.get("start", 0)) <= 0.15 and len(transcript) > 1:
            next_start = float(transcript[1].get("start", 0))
            first_end = float(first.get("end", 0))
            if next_start - first_end >= 0.5:
                return [{
                    "start": round(float(first.get("start", 0)), 3),
                    "end": round(next_start, 3),
                    "reason": "Possible production/setup speech before the first topic line; review against the reference script",
                    "category": "off_topic",
                    "decision": "review",
                    "confidence": 0.5,
                    "matches": {"text": anchor},
                    "text": str(first.get("text", "")),
                }]
        return []
    first_on_topic = float(transcript[best[1]].get("start", 0))
    if first_on_topic < 0.15:
        return []
    opening_text = " ".join(str(item.get("text", "")).strip() for item in transcript[:best[1]]).strip()
    return [{
        "start": 0.0,
        "end": round(first_on_topic, 3),
        "reason": "Speech before the first recognizable on-topic reference-script line",
        "category": "off_topic",
        "decision": "review",
        "confidence": round(best[0], 3),
        "matches": {"text": anchor},
        "text": opening_text,
    }]


def duplicate_candidates(analysis_path: Path | None) -> list[dict[str, Any]]:
    if not analysis_path or not analysis_path.exists():
        return []
    transcript = read_json(analysis_path).get("transcript", [])
    candidates: list[dict[str, Any]] = []
    for index, current in enumerate(transcript):
        current_text = normalize_text(str(current.get("text", "")))
        if len(current_text) < 8:
            continue
        for previous in transcript[max(0, index - 6):index]:
            previous_text = normalize_text(str(previous.get("text", "")))
            if len(previous_text) < 8:
                continue
            if float(current.get("start", 0)) - float(previous.get("end", 0)) > 35:
                continue
            ratio = difflib.SequenceMatcher(None, previous_text, current_text).ratio()
            if ratio >= 0.78:
                candidates.append({
                    "start": round(float(current["start"]), 3),
                    "end": round(float(current["end"]), 3),
                    "reason": "Possible repeated content or emphasis; review before removal",
                    "category": "repeat",
                    "decision": "review",
                    "confidence": round(ratio, 3),
                    "matches": {"start": previous.get("start"), "end": previous.get("end"), "text": previous.get("text", "")},
                    "text": current.get("text", ""),
                })
    return candidates


FILLER_ONLY = {"嗯", "啊", "呃", "欸", "那个", "就是", "然后", "对", "这样"}


def filler_candidates(analysis_path: Path | None) -> list[dict[str, Any]]:
    if not analysis_path or not analysis_path.exists():
        return []
    transcript = read_json(analysis_path).get("transcript", [])
    candidates = []
    for item in transcript:
        text = str(item.get("text", "")).strip()
        normalized = normalize_text(text)
        duration = float(item.get("end", 0)) - float(item.get("start", 0))
        if normalized in FILLER_ONLY and duration <= 2.5:
            candidates.append({
                "start": round(float(item.get("start", 0)), 3),
                "end": round(float(item.get("end", 0)), 3),
                "reason": "Standalone filler or verbal dead end; review before removal",
                "category": "filler",
                "decision": "review",
                "confidence": 0.8,
                "matches": {},
                "text": text,
            })
    return candidates


CORRECTION_MARKERS = ("不对", "不是", "应该是", "改一下", "重来", "重新", "普通话", "再说一遍", "刚才")


def retake_candidates(analysis_path: Path | None) -> list[dict[str, Any]]:
    """Flag explicit on-camera corrections for contextual review."""
    if not analysis_path or not analysis_path.exists():
        return []
    transcript = read_json(analysis_path).get("transcript", [])
    candidates = []
    for index, item in enumerate(transcript):
        text = str(item.get("text", "")).strip()
        if not any(marker in text for marker in CORRECTION_MARKERS):
            continue
        end = float(item.get("end", 0))
        if index + 1 < len(transcript):
            end = max(end, float(transcript[index + 1].get("end", end)))
        candidates.append({
            "start": round(float(item.get("start", 0)), 3),
            "end": round(end, 3),
            "reason": "Possible on-camera correction or retake; review the adjacent clean take",
            "category": "retake",
            "decision": "review",
            "confidence": 0.75,
            "matches": {},
            "text": text,
        })
    return candidates


def load_semantic_exclusions(path: Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    payload = read_json(path)
    items = payload.get("exclusions", payload.get("semantic_exclusions", [])) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("Semantic exclusions must be a JSON array or an object with an exclusions array")
    exclusions = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each semantic exclusion must be an object")
        start, end = float(item["start"]), float(item["end"])
        reason = str(item.get("reason", "Approved semantic removal")).strip()
        if end <= start or not reason:
            raise ValueError("Each semantic exclusion needs end > start and a non-empty reason")
        exclusions.append({"start": start, "end": end, "reason": reason, "kind": "semantic"})
    return exclusions


def merge_exclusions(exclusions: Iterable[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    ordered = sorted(exclusions, key=lambda item: (float(item["start"]), float(item["end"])))
    merged: list[dict[str, Any]] = []
    for item in ordered:
        start = max(0.0, min(duration, float(item["start"])))
        end = max(0.0, min(duration, float(item["end"])))
        if end - start < MIN_REMOVAL:
            continue
        current = {**item, "start": round(start, 3), "end": round(end, 3)}
        if merged and start <= float(merged[-1]["end"]) + 0.06:
            previous = merged[-1]
            previous["end"] = round(max(float(previous["end"]), end), 3)
            previous["reason"] = previous["reason"] + "; " + current["reason"]
            previous["kind"] = "mixed" if previous["kind"] != current["kind"] else previous["kind"]
        else:
            merged.append(current)
    return merged


def invert_ranges(duration: float, exclusions: Iterable[dict[str, Any]]) -> list[dict[str, float]]:
    kept: list[dict[str, float]] = []
    cursor = 0.0
    for item in exclusions:
        start, end = float(item["start"]), float(item["end"])
        if start - cursor >= 0.18:
            kept.append({"start": round(cursor, 3), "end": round(start, 3)})
        cursor = max(cursor, end)
    if duration - cursor >= 0.18:
        kept.append({"start": round(cursor, 3), "end": round(duration, 3)})
    return kept


def build_plan(args: argparse.Namespace) -> int:
    require_tools()
    if args.silence_min <= 0 or args.preserve_air < 0:
        raise ValueError("Pause thresholds must be non-negative and silence-min must be positive")
    if args.target_cpm <= 0 or args.minimum_cpm <= 0:
        raise ValueError("Pace targets must be positive")
    if args.max_speed < 1.0 or args.max_speed > 2.0:
        raise ValueError("max-speed must be between 1.0 and 2.0")
    video = args.video.resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    reference_script = args.reference_script.resolve() if args.reference_script else None
    if reference_script and not reference_script.is_file():
        raise FileNotFoundError(reference_script)
    media = probe(video)
    silences = find_silences(video, args.silence_db, args.silence_min)
    automatic = []
    for silence_start, silence_end in silences:
        exclusion = pause_exclusion(silence_start, silence_end, args.preserve_air)
        if exclusion:
            start, end = exclusion
            automatic.append({
                "start": start,
                "end": end,
                "reason": "Excess pause shortened to the target total breathing gap",
                "kind": "silence",
            })
    semantic = load_semantic_exclusions(args.semantic_exclusions)
    exclusions = merge_exclusions([*automatic, *semantic], media["duration"])
    kept = invert_ranges(media["duration"], exclusions)
    semantic_kept = invert_ranges(media["duration"], merge_exclusions(semantic, media["duration"]))
    reference_analysis = args.pace_reference_analysis.resolve() if args.pace_reference_analysis else None
    if reference_analysis and not reference_analysis.is_file():
        raise FileNotFoundError(reference_analysis)
    pace = pace_analysis(
        args.analysis,
        kept,
        mode=args.pace_mode,
        target_cpm=args.target_cpm,
        minimum_cpm=args.minimum_cpm,
        maximum_speed=args.max_speed,
        reference_analysis=reference_analysis,
        character_ranges=semantic_kept,
    )
    plan = {
        "version": 2,
        "source": str(video),
        "source_duration": round(media["duration"], 3),
        "policy": {
            "silence_noise_db": args.silence_db,
            "silence_min_seconds": args.silence_min,
            "target_total_pause_seconds": args.preserve_air,
            "semantic_cuts_require_review": True,
            "reference_script_required_for_script_led_edits": True,
        },
        "detected_silences": [{"start": round(start, 3), "end": round(end, 3)} for start, end in silences],
        "semantic_exclusions": semantic,
        "reference_script": str(reference_script) if reference_script else None,
        "script_alignment": script_alignment_candidates(args.analysis, reference_script),
        "exclusions": exclusions,
        "review_candidates": [
            *script_alignment_candidates(args.analysis, reference_script),
            *duplicate_candidates(args.analysis),
            *filler_candidates(args.analysis),
            *retake_candidates(args.analysis),
        ],
        "kept_ranges": kept,
        "pace_analysis": pace,
        "timeline_speed": pace["applied_speed"],
        "estimated_cut_duration": pace["pre_speed_duration"],
        "estimated_output_duration": pace["estimated_output_duration"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit("succeeded", "plan", output=str(args.output), source_duration=plan["source_duration"], estimated_output_duration=plan["estimated_output_duration"], removed_duration=round(media["duration"] - pace["pre_speed_duration"], 3), pace=pace, exclusions=len(exclusions), review_candidates=len(plan["review_candidates"]), script_alignment_candidates=len(plan["script_alignment"]))
    return 0


def artifact_paths(output: Path, args: argparse.Namespace) -> tuple[Path, Path]:
    visual = args.visual_output.resolve() if args.visual_output else output.with_name(f"{output.stem}.visual.mp4")
    narration = args.narration_output.resolve() if args.narration_output else output.with_name(f"{output.stem}.narration.m4a")
    return visual, narration


def render_filter_graph(ranges: list[dict[str, Any]], speed: float = 1.0) -> str:
    chains: list[str] = []
    inputs: list[str] = []
    for index, item in enumerate(ranges):
        start, end = float(item["start"]), float(item["end"])
        duration = end - start
        fade_out = max(0.0, duration - 0.03)
        chains.extend([
            f"[0:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,fps=30,format=yuv420p[v{index}]",
            f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,aresample=48000,afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.6f}:d=0.03[a{index}]",
        ])
        inputs.extend([f"[v{index}]", f"[a{index}]"])
    chains.append("".join(inputs) + f"concat=n={len(ranges)}:v=1:a=1[visual_joined][narration_joined]")
    chains.append(f"[visual_joined]setpts=PTS/{speed:.6f},fps=30[visual]")
    chains.append(f"[narration_joined]atempo={speed:.6f},loudnorm=I=-16:TP=-1.5:LRA=11[narration]")
    return ";".join(chains)


def pace_requires_review(plan: dict[str, Any]) -> bool:
    return str(plan.get("pace_analysis", {}).get("decision", "")) in {
        "review_required",
        "speed_review_required",
        "speed_up_and_review_content",
    }


def render_plan(args: argparse.Namespace) -> int:
    require_tools()
    plan = read_json(args.plan)
    if pace_requires_review(plan) and not args.approve_pace_review:
        raise ValueError("Pace analysis requires review before rendering; rerun with an approved plan and --approve-pace-review")
    source = Path(plan["source"])
    if not source.is_file():
        raise FileNotFoundError(source)
    ranges = plan.get("kept_ranges", [])
    if not ranges:
        raise ValueError("Plan has no kept ranges")
    output = args.output.resolve()
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"Output already exists: {output}. Pass --overwrite to replace it.")
    output.parent.mkdir(parents=True, exist_ok=True)
    visual, narration = artifact_paths(output, args)
    for artifact in (visual, narration):
        if artifact.exists() and not args.overwrite:
            raise FileExistsError(f"Output already exists: {artifact}. Pass --overwrite to replace it.")
    crf = "22" if args.quality == "preview" else "19"
    ranges = [item for item in ranges if float(item["end"]) - float(item["start"]) >= 0.18]
    if not ranges:
        raise ValueError("No renderable kept ranges")
    speed = float(plan.get("timeline_speed", 1.0))
    if speed < 1.0 or speed > 2.0:
        raise ValueError("Plan timeline_speed must be between 1.0 and 2.0")
    run([
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-i", str(source),
        "-filter_complex", render_filter_graph(ranges, speed),
        "-map", "[visual]", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", crf,
        "-movflags", "+faststart", str(visual),
        "-map", "[narration]", "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(narration),
    ])
    run([
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-i", str(visual), "-i", str(narration),
        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", str(output),
    ])
    output_duration = probe(output)["duration"]
    emit("succeeded", "render", output=str(output), visual=str(visual), narration=str(narration), duration=round(output_duration, 3), speed=round(speed, 3), segments=len(ranges))
    return 0


def validate_plan(args: argparse.Namespace) -> int:
    plan = read_json(args.plan)
    source = Path(plan.get("source", ""))
    ranges = plan.get("kept_ranges", [])
    errors = []
    if not source.is_file():
        errors.append(f"Source video does not exist: {source}")
    previous_end = 0.0
    for index, item in enumerate(ranges):
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"Invalid kept range at index {index}")
            continue
        if end <= start:
            errors.append(f"Non-positive kept range at index {index}")
        if start < previous_end - 0.001:
            errors.append(f"Overlapping kept range at index {index}")
        previous_end = end
    output_metrics = None
    speed = float(plan.get("timeline_speed", 1.0))
    expected_duration = sum(float(item["end"]) - float(item["start"]) for item in ranges) / speed
    duration_tolerance = max(DEFAULT_DURATION_TOLERANCE, len(ranges) * 0.01)
    if args.output:
        if not args.output.is_file():
            errors.append(f"Rendered output does not exist: {args.output}")
        else:
            media = probe(args.output)
            output_metrics = {"duration": round(media["duration"], 3), "has_audio": True}
            if abs(media["duration"] - expected_duration) > duration_tolerance:
                errors.append("Review duration differs from EDL duration")
    narration_report = None
    pause_report = None
    if args.narration:
        if not args.narration.is_file():
            errors.append(f"Narration file does not exist: {args.narration}")
        else:
            narration_report = audio_qc(args.narration, expected_duration, args.max_silence)
            if abs(narration_report["duration_delta"]) > duration_tolerance:
                errors.append("Narration duration differs from EDL duration")
            if narration_report["long_silences"]:
                errors.append("Narration contains an unplanned long silence")
            if narration_report["active_duration"] < min(2.0, expected_duration * 0.05):
                errors.append("Narration has insufficient audible content")
            pauses = find_silences(args.narration, float(plan.get("policy", {}).get("silence_noise_db", DEFAULT_SILENCE_DB)), DEFAULT_SILENCE_MIN)
            excessive_pauses = [(start, end) for start, end in pauses if end - start > args.max_residual_pause]
            pause_report = {
                "detected_count": len(pauses),
                "detected_duration": round(sum(end - start for start, end in pauses), 3),
                "maximum_allowed_seconds": round(args.max_residual_pause, 3),
                "excessive": [{"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)} for start, end in excessive_pauses],
            }
            if excessive_pauses:
                errors.append("Narration contains residual short pauses above the rough-cut limit")
    else:
        errors.append("Validated narration output is required")
    visual_report = None
    if args.visual:
        if not args.visual.is_file():
            errors.append(f"Visual file does not exist: {args.visual}")
        else:
            visual_media = probe(args.visual, require_audio=False)
            visual_report = {"path": str(args.visual), "duration": round(visual_media["duration"], 3), "has_audio": any(stream.get("codec_type") == "audio" for stream in visual_media["streams"])}
            if visual_report["has_audio"]:
                errors.append("Visual rough cut must not contain an embedded audio stream")
            if abs(visual_media["duration"] - expected_duration) > duration_tolerance:
                errors.append("Visual duration differs from EDL duration")
    else:
        errors.append("Validated visual output is required")
    if visual_report and narration_report:
        av_duration_delta = abs(float(visual_report["duration"]) - float(narration_report["duration"]))
        if av_duration_delta > 0.1:
            errors.append("Visual and narration durations differ by more than 0.1 seconds")
    else:
        av_duration_delta = None
    actual_duration = float(output_metrics["duration"]) if output_metrics else (
        float(narration_report["duration"]) if narration_report else expected_duration
    )
    report = {
        "expected_duration": round(expected_duration, 3),
        "duration_tolerance": round(duration_tolerance, 3),
        "visual_narration_duration_delta": round(av_duration_delta, 3) if av_duration_delta is not None else None,
        "review": output_metrics,
        "narration": narration_report,
        "pause_qc": pause_report,
        "pace_qc": {
            **plan.get("pace_analysis", {}),
            "actual_output_duration": round(actual_duration, 3),
            "actual_chars_per_minute": round(float(plan.get("pace_analysis", {}).get("characters", 0)) / actual_duration * 60.0, 1) if actual_duration > 0 else 0.0,
        },
        "visual": visual_report,
        "errors": errors,
    }
    report["status"] = "succeeded" if not errors else "failed"
    if args.qc_output:
        args.qc_output.parent.mkdir(parents=True, exist_ok=True)
        args.qc_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status = report["status"]
    emit(
        status,
        "validate",
        plan=str(args.plan),
        output=str(args.output) if args.output else None,
        kept_ranges=len(ranges),
        **{key: value for key, value in report.items() if key != "status"},
    )
    return 0 if not errors else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Conservative talking-head rough-cut planner and FFmpeg renderer")
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Create an auditable candidate cut plan")
    plan.add_argument("--video", type=Path, required=True)
    plan.add_argument("--analysis", type=Path, help="Optional video-understand JSON")
    plan.add_argument("--reference-script", type=Path, help="Approved script/copy used to audit opening and repeated speech")
    plan.add_argument("--semantic-exclusions", type=Path, help="Reviewed semantic removals JSON")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--silence-db", type=float, default=DEFAULT_SILENCE_DB)
    plan.add_argument("--silence-min", type=float, default=DEFAULT_SILENCE_MIN)
    plan.add_argument("--preserve-air", type=float, default=DEFAULT_PRESERVE_AIR, help="Target total retained duration for a detected pause")
    plan.add_argument("--pace-mode", choices=("auto", "review", "off"), default="auto")
    plan.add_argument("--target-cpm", type=float, default=DEFAULT_TARGET_CPM, help="Target non-punctuation transcript characters per minute")
    plan.add_argument("--minimum-cpm", type=float, default=DEFAULT_MIN_CPM, help="Do not recommend speed-up at or above this speech density")
    plan.add_argument("--max-speed", type=float, default=DEFAULT_MAX_SPEED, help="Maximum automatic synchronized audio/video speed")
    plan.add_argument("--pace-reference-analysis", type=Path, help="Optional video-understand JSON whose transcript density replaces --target-cpm")
    plan.set_defaults(handler=build_plan)
    render = commands.add_parser("render", help="Render an accepted plan with FFmpeg")
    render.add_argument("--plan", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True, help="Review MP4 muxed from the visual and narration artifacts")
    render.add_argument("--visual-output", type=Path, help="Silent visual rough-cut MP4; defaults beside --output")
    render.add_argument("--narration-output", type=Path, help="Narration-only M4A; defaults beside --output")
    render.add_argument("--quality", choices=("preview", "final"), default="preview")
    render.add_argument("--approve-pace-review", action="store_true", help="Confirm a pace decision explicitly marked for review")
    render.add_argument("--overwrite", action="store_true")
    render.set_defaults(handler=render_plan)
    validate = commands.add_parser("validate", help="Validate plan ranges and optional rendered output")
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--output", type=Path)
    validate.add_argument("--visual", type=Path, required=True)
    validate.add_argument("--narration", type=Path, required=True)
    validate.add_argument("--max-silence", type=float, default=DEFAULT_MAX_SILENCE)
    validate.add_argument("--max-residual-pause", type=float, default=DEFAULT_MAX_RESIDUAL_PAUSE)
    validate.add_argument("--qc-output", type=Path, help="Write a machine-readable audio/video QC report")
    validate.set_defaults(handler=validate_plan)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.handler(args)
    except Exception as error:
        emit("failed", args.command, error=str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
