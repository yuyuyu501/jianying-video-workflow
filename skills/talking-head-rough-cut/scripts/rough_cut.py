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
import tempfile
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SILENCE_DB = -25.0
DEFAULT_SILENCE_MIN = 0.85
DEFAULT_PRESERVE_AIR = 0.25
MIN_REMOVAL = 0.12


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


def probe(video: Path) -> dict[str, Any]:
    result = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-show_streams", "-of", "json", str(video),
    ], capture=True)
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0))
    has_audio = any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))
    if duration <= 0:
        raise RuntimeError("Video duration is missing or zero")
    if not has_audio:
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


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text).lower()


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
            if ratio >= 0.88:
                candidates.append({
                    "start": round(float(current["start"]), 3),
                    "end": round(float(current["end"]), 3),
                    "reason": "Possible repeated phrase; review before removal",
                    "decision": "review",
                    "confidence": round(ratio, 3),
                    "matches": {"start": previous.get("start"), "end": previous.get("end"), "text": previous.get("text", "")},
                    "text": current.get("text", ""),
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
    video = args.video.resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    media = probe(video)
    silences = find_silences(video, args.silence_db, args.silence_min)
    automatic = []
    for silence_start, silence_end in silences:
        start = silence_start + args.preserve_air
        end = silence_end - args.preserve_air
        if end - start >= MIN_REMOVAL:
            automatic.append({
                "start": start,
                "end": end,
                "reason": "Excess pause shortened while retaining natural air",
                "kind": "silence",
            })
    semantic = load_semantic_exclusions(args.semantic_exclusions)
    exclusions = merge_exclusions([*automatic, *semantic], media["duration"])
    kept = invert_ranges(media["duration"], exclusions)
    plan = {
        "version": 1,
        "source": str(video),
        "source_duration": round(media["duration"], 3),
        "policy": {
            "silence_noise_db": args.silence_db,
            "silence_min_seconds": args.silence_min,
            "preserve_air_seconds": args.preserve_air,
            "semantic_cuts_require_review": True,
        },
        "detected_silences": [{"start": round(start, 3), "end": round(end, 3)} for start, end in silences],
        "semantic_exclusions": semantic,
        "exclusions": exclusions,
        "review_candidates": duplicate_candidates(args.analysis),
        "kept_ranges": kept,
        "estimated_output_duration": round(sum(item["end"] - item["start"] for item in kept), 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit("succeeded", "plan", output=str(args.output), source_duration=plan["source_duration"], estimated_output_duration=plan["estimated_output_duration"], removed_duration=round(media["duration"] - plan["estimated_output_duration"], 3), exclusions=len(exclusions), review_candidates=len(plan["review_candidates"]))
    return 0


def ffmpeg_quote(path: Path) -> str:
    return str(path.resolve()).replace("'", "\\\\'")


def render_plan(args: argparse.Namespace) -> int:
    require_tools()
    plan = read_json(args.plan)
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
    work_dir = Path(tempfile.mkdtemp(prefix=f"{output.stem}-rough-cut-", dir=output.parent))
    crf = "22" if args.quality == "preview" else "19"
    segments: list[Path] = []
    for index, item in enumerate(ranges):
        start, end = float(item["start"]), float(item["end"])
        duration = end - start
        if duration < 0.18:
            continue
        segment = work_dir / f"segment_{index:03d}.mp4"
        fade_out = max(0.0, duration - 0.03)
        run([
            "ffmpeg", "-y", "-hide_banner", "-nostdin", "-i", str(source),
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "0:a:0",
            "-vf", "fps=30,format=yuv420p",
            "-af", f"aresample=async=1:first_pts=0,afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03",
            "-c:v", "libx264", "-preset", "fast", "-crf", crf,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-avoid_negative_ts", "make_zero", str(segment),
        ])
        segments.append(segment)
    if not segments:
        raise ValueError("No renderable kept ranges")
    concat_list = work_dir / "concat.txt"
    concat_list.write_text("".join(f"file '{ffmpeg_quote(path)}'\n" for path in segments), encoding="utf-8")
    base = work_dir / "base.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(base)])
    run([
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-i", str(base),
        "-af", "aresample=async=1:first_pts=0,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(output),
    ])
    output_duration = probe(output)["duration"]
    shutil.rmtree(work_dir)
    emit("succeeded", "render", output=str(output), duration=round(output_duration, 3), segments=len(segments), temporary_artifacts_removed=True)
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
    if args.output:
        if not args.output.is_file():
            errors.append(f"Rendered output does not exist: {args.output}")
        else:
            media = probe(args.output)
            output_metrics = {"duration": round(media["duration"], 3), "has_audio": True}
    status = "succeeded" if not errors else "failed"
    emit(status, "validate", plan=str(args.plan), output=str(args.output) if args.output else None, kept_ranges=len(ranges), output_metrics=output_metrics, errors=errors)
    return 0 if not errors else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Conservative talking-head rough-cut planner and FFmpeg renderer")
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Create an auditable candidate cut plan")
    plan.add_argument("--video", type=Path, required=True)
    plan.add_argument("--analysis", type=Path, help="Optional video-understand JSON")
    plan.add_argument("--semantic-exclusions", type=Path, help="Reviewed semantic removals JSON")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--silence-db", type=float, default=DEFAULT_SILENCE_DB)
    plan.add_argument("--silence-min", type=float, default=DEFAULT_SILENCE_MIN)
    plan.add_argument("--preserve-air", type=float, default=DEFAULT_PRESERVE_AIR)
    plan.set_defaults(handler=build_plan)
    render = commands.add_parser("render", help="Render an accepted plan with FFmpeg")
    render.add_argument("--plan", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--quality", choices=("preview", "final"), default="preview")
    render.add_argument("--overwrite", action="store_true")
    render.set_defaults(handler=render_plan)
    validate = commands.add_parser("validate", help="Validate plan ranges and optional rendered output")
    validate.add_argument("--plan", type=Path, required=True)
    validate.add_argument("--output", type=Path)
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
