#!/usr/bin/env python3
"""Build a reviewed multi-source media manifest and final-timeline captions."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROLES = {
    "primary_narration",
    "secondary_speech",
    "broll_visual",
    "ambient_broll",
    "music",
    "sfx",
    "exclude",
}
AUDIO_POLICIES = {
    "keep_original",
    "mute",
    "duck_under_primary",
    "extract_as_sfx",
    "not_applicable",
}
SPEECH_ROLES = {"primary_narration", "secondary_speech"}


def emit(status: str, command: str, **payload: Any) -> None:
    print("RESULT: " + json.dumps({"status": status, "command": command, **payload}, ensure_ascii=False))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe_audio(video: Path) -> bool:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe is required")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return any(stream.get("codec_type") == "audio" for stream in json.loads(result.stdout).get("streams", []))


def recommendation(analysis: dict[str, Any], has_audio: bool) -> dict[str, Any]:
    transcript = analysis.get("transcript", [])
    text = str(analysis.get("text", "")).strip()
    text_length = len("".join(str(item.get("text", "")).strip() for item in transcript)) or len(text)
    if not has_audio:
        return {
            "role": "broll_visual",
            "audio_policy": "not_applicable",
            "reason": "No audio stream detected.",
            "review_required": False,
        }
    if text_length >= 24 and transcript:
        return {
            "role": "primary_narration",
            "audio_policy": "keep_original",
            "reason": "Speech transcript detected; verify whether it carries the final narrative.",
            "review_required": True,
        }
    return {
        "role": "ambient_broll",
        "audio_policy": "duck_under_primary",
        "reason": "Audio exists but no reliable speech transcript was detected; judge whether ambience has editorial value.",
        "review_required": True,
    }


def source_id(index: int, video: Path) -> str:
    stem = "".join(character if character.isalnum() else "_" for character in video.stem).strip("_")
    return f"source_{index:02d}_{stem[:32] or 'video'}"


def intake(args: argparse.Namespace) -> int:
    if len(args.video) != len(args.analysis):
        raise ValueError("--video and --analysis must be supplied the same number of times")
    sources = []
    for index, (video_value, analysis_value) in enumerate(zip(args.video, args.analysis), start=1):
        video = video_value.resolve()
        analysis_path = analysis_value.resolve()
        if not video.is_file():
            raise FileNotFoundError(video)
        if not analysis_path.is_file():
            raise FileNotFoundError(analysis_path)
        analysis = read_json(analysis_path)
        has_audio = probe_audio(video)
        sources.append({
            "id": source_id(index, video),
            "video": str(video),
            "analysis": str(analysis_path),
            "duration": float(analysis.get("duration", 0.0)),
            "resolution": analysis.get("resolution", {}),
            "has_audio": has_audio,
            "transcript_segments": len(analysis.get("transcript", [])),
            "recommendation": recommendation(analysis, has_audio),
            "decision": None,
        })
    output = args.output.resolve()
    write_json(output, {"version": 1, "status": "review_required", "sources": sources})
    emit("review_required", "intake", output=str(output), sources=len(sources))
    return 0


def load_decisions(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    decisions = payload.get("decisions", []) if isinstance(payload, dict) else payload
    if not isinstance(decisions, list):
        raise ValueError("Decisions must be an array or an object with a decisions array")
    return decisions


def validate_decision(source: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    role = str(decision.get("role", ""))
    policy = str(decision.get("audio_policy", ""))
    if role not in ROLES:
        raise ValueError(f"{source['id']}: unsupported role {role!r}")
    if policy not in AUDIO_POLICIES:
        raise ValueError(f"{source['id']}: unsupported audio_policy {policy!r}")
    reason = str(decision.get("reason", "")).strip()
    if not reason:
        raise ValueError(f"{source['id']}: a reason is required")
    confidence = float(decision.get("confidence", 0.0))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"{source['id']}: confidence must be between 0 and 1")
    review_required = bool(decision.get("review_required", confidence < 0.8))
    if not source["has_audio"] and policy not in {"not_applicable", "mute"}:
        raise ValueError(f"{source['id']}: source has no audio stream")
    if role == "broll_visual" and policy == "keep_original":
        raise ValueError(f"{source['id']}: broll_visual cannot keep original audio")
    if role in SPEECH_ROLES and policy != "keep_original":
        raise ValueError(f"{source['id']}: speech roles must keep original audio or be reclassified")
    if policy == "keep_original" and role not in SPEECH_ROLES:
        raise ValueError(f"{source['id']}: keep_original is reserved for narration sources")
    normalized = {
        "role": role,
        "audio_policy": policy,
        "reason": reason,
        "confidence": round(confidence, 3),
        "review_required": review_required,
        "requires_rough_cut": role in SPEECH_ROLES and policy == "keep_original",
    }
    if normalized["requires_rough_cut"]:
        order = decision.get("timeline_order")
        if not isinstance(order, int) or order < 1:
            raise ValueError(f"{source['id']}: keep_original narration needs timeline_order >= 1")
        normalized["timeline_order"] = order
    return normalized


def apply_decisions(args: argparse.Namespace) -> int:
    intake_data = read_json(args.intake.resolve())
    sources = intake_data.get("sources", [])
    decisions = load_decisions(args.decisions.resolve())
    by_id = {str(item.get("source_id", "")): item for item in decisions}
    known_ids = {source["id"] for source in sources}
    unknown = sorted(set(by_id) - known_ids)
    if unknown:
        raise ValueError("Unknown source_id values: " + ", ".join(unknown))
    missing = [source["id"] for source in sources if source["id"] not in by_id]
    if missing:
        raise ValueError("Missing decisions for: " + ", ".join(missing))
    orders: set[int] = set()
    for source in sources:
        source["decision"] = validate_decision(source, by_id[source["id"]])
        order = source["decision"].get("timeline_order")
        if order is not None:
            if order in orders:
                raise ValueError(f"Duplicate narration timeline_order: {order}")
            orders.add(order)
    unresolved = [source["id"] for source in sources if source["decision"]["review_required"]]
    manifest = {
        "version": 1,
        "status": "review_required" if unresolved else "approved",
        "sources": sources,
        "unresolved_sources": unresolved,
    }
    output = args.output.resolve()
    write_json(output, manifest)
    emit(manifest["status"], "apply-decisions", output=str(output), sources=len(sources), unresolved_sources=unresolved)
    return 0


def attach_rough_cuts(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path)
    sources = {source["id"]: source for source in manifest.get("sources", [])}
    def parse_mappings(values: list[str], label: str) -> dict[str, Path]:
        mapped: dict[str, Path] = {}
        for mapping in values:
            source_id_value, separator, path_value = mapping.partition("=")
            if not separator or not source_id_value or not path_value:
                raise ValueError(f"{label} must use SOURCE_ID=PATH")
            if source_id_value not in sources:
                raise ValueError(f"Unknown source_id: {source_id_value}")
            path = Path(path_value).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            mapped[source_id_value] = path
        return mapped

    plans = parse_mappings(args.rough_cut, "--rough-cut")
    visuals = parse_mappings(args.rough_cut_visual, "--rough-cut-visual")
    narrations = parse_mappings(args.rough_cut_narration, "--rough-cut-narration")
    reviews = parse_mappings(args.rough_cut_review, "--rough-cut-review")
    qc_reports = parse_mappings(args.rough_cut_qc, "--rough-cut-qc")
    required = [source["id"] for source in sources.values() if source.get("decision", {}).get("requires_rough_cut")]
    missing = [source_id_value for source_id_value in required if source_id_value not in plans or source_id_value not in visuals or source_id_value not in narrations or source_id_value not in reviews or source_id_value not in qc_reports]
    if missing:
        raise ValueError("Missing validated rough-cut artifacts for: " + ", ".join(missing))
    for source_id_value, plan in plans.items():
        sources[source_id_value]["rough_cut_plan"] = str(plan)
        source = sources[source_id_value]
        source["rough_cut_artifacts"] = {
            "visual": str(visuals[source_id_value]),
            "narration": str(narrations[source_id_value]),
            "review": str(reviews[source_id_value]),
            "qc": str(qc_reports[source_id_value]),
        }
        qc = read_json(qc_reports[source_id_value])
        if qc.get("status") != "succeeded" or qc.get("errors"):
            raise ValueError(f"{source_id_value}: rough-cut QC did not pass")
        qc_narration = Path(str(qc.get("narration", {}).get("path", ""))).resolve()
        qc_visual = Path(str(qc.get("visual", {}).get("path", ""))).resolve()
        if qc_narration != narrations[source_id_value]:
            raise ValueError(f"{source_id_value}: rough-cut QC does not belong to the supplied narration artifact")
        if qc_visual != visuals[source_id_value]:
            raise ValueError(f"{source_id_value}: rough-cut QC does not belong to the supplied visual artifact")
        if qc.get("narration", {}).get("long_silences"):
            raise ValueError(f"{source_id_value}: rough-cut narration contains unplanned long silence")
    output = args.output.resolve() if args.output else manifest_path
    write_json(output, manifest)
    emit("succeeded", "attach-rough-cuts", output=str(output), rough_cut_sources=required)
    return 0


def kept_ranges(source: dict[str, Any]) -> list[dict[str, float]]:
    plan_path = Path(str(source.get("rough_cut_plan", "")))
    if not plan_path.is_file():
        raise FileNotFoundError(f"{source['id']}: rough_cut_plan is required before captions")
    plan = read_json(plan_path)
    ranges = plan.get("kept_ranges", [])
    if not ranges:
        raise ValueError(f"{source['id']}: rough-cut plan has no kept ranges")
    normalized = []
    for item in ranges:
        start, end = float(item["start"]), float(item["end"])
        if end > start:
            normalized.append({"start": start, "end": end})
    return normalized


def require_validated_rough_cut(source: dict[str, Any]) -> None:
    artifacts = source.get("rough_cut_artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(f"{source['id']}: validated rough-cut artifacts are required before captions")
    for key in ("visual", "narration", "review", "qc"):
        if not Path(str(artifacts.get(key, ""))).is_file():
            raise FileNotFoundError(f"{source['id']}: missing rough-cut {key} artifact")
    qc = read_json(Path(artifacts["qc"]))
    if qc.get("status") != "succeeded" or qc.get("errors"):
        raise ValueError(f"{source['id']}: rough-cut QC must pass before captions")


def srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_srt_time(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
    if not match:
        raise ValueError(f"invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def read_srt(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8").strip())
    entries = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines()]
        if len(lines) < 3 or not lines[0].isdigit() or " --> " not in lines[1]:
            raise ValueError(f"invalid SRT block: {block[:80]!r}")
        start_text, end_text = lines[1].split(" --> ", 1)
        start, end = parse_srt_time(start_text), parse_srt_time(end_text)
        text = "\n".join(lines[2:]).strip()
        if not text or end <= start:
            raise ValueError(f"invalid SRT entry {lines[0]}")
        entries.append({"index": int(lines[0]), "start": start, "end": end, "text": text})
    return entries


def normalize_caption_text(value: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", value).lower()
    return re.sub(r"[的地了呢啊呀吧嘛]", "", normalized)


def reference_truncations(entries: list[dict[str, Any]], reference_script: Path | None) -> list[dict[str, Any]]:
    """Find an exact reference prefix whose skipped suffix is absent from the next subtitle."""
    if reference_script is None:
        return []
    reference = normalize_caption_text(reference_script.read_text(encoding="utf-8"))
    problems = []
    for index, entry in enumerate(entries[:-1]):
        text = normalize_caption_text(entry["text"])
        next_text = normalize_caption_text(entries[index + 1]["text"])
        if len(text) < 4 or not next_text:
            continue
        start = reference.find(text)
        while start >= 0:
            remaining = reference[start + len(text):]
            if remaining:
                continuation = next_text[: min(4, len(next_text))]
                next_position = remaining.find(continuation)
                # A short skipped suffix immediately before the next subtitle is
                # a likely truncated utterance. Longer script differences are
                # often approved paraphrases or semantic rough-cut removals.
                if 3 <= next_position <= 8:
                    problems.append({
                        "index": entry["index"],
                        "text": entry["text"],
                        "missing_prefix": remaining[:next_position],
                        "next_text": entries[index + 1]["text"],
                    })
                    break
            start = reference.find(text, start + 1)
    return problems


def caption_qc(
    srt_path: Path,
    timeline_path: Path,
    reference_script: Path | None = None,
    max_gap: float = 4.0,
) -> dict[str, Any]:
    srt_entries = read_srt(srt_path)
    timeline = read_json(timeline_path)
    timeline_entries = timeline.get("captions", []) if isinstance(timeline, dict) else []
    errors: list[str] = []
    warnings: list[str] = []
    if not srt_entries:
        errors.append("SRT has no caption entries")
    if not timeline_entries:
        errors.append("speech timeline has no caption entries")
    if len(srt_entries) != len(timeline_entries):
        errors.append(f"SRT/timeline caption count differs: {len(srt_entries)} != {len(timeline_entries)}")
    for index, (srt_entry, timeline_entry) in enumerate(zip(srt_entries, timeline_entries), start=1):
        if srt_entry["index"] != index:
            errors.append(f"SRT index is not sequential at entry {index}")
        if srt_entry["text"] != str(timeline_entry.get("text", "")).strip():
            errors.append(f"SRT/timeline text differs at entry {index}")
        timeline_start = float(timeline_entry.get("timeline_start", timeline_entry.get("start", -1)))
        timeline_end = float(timeline_entry.get("timeline_end", timeline_entry.get("end", -1)))
        if abs(srt_entry["start"] - timeline_start) > 0.001 or abs(srt_entry["end"] - timeline_end) > 0.001:
            errors.append(f"SRT/timeline time differs at entry {index}")
    for previous, current in zip(srt_entries, srt_entries[1:]):
        gap = current["start"] - previous["end"]
        if gap < -0.001:
            errors.append(f"overlapping captions: {previous['index']} and {current['index']}")
        elif gap > max_gap:
            errors.append(f"unexplained caption gap {gap:.3f}s after entry {previous['index']}")
    truncations = reference_truncations(srt_entries, reference_script)
    for problem in truncations:
        errors.append(f"reference text appears truncated at entry {problem['index']}: missing {problem['missing_prefix']!r}")
    duration = float(timeline.get("duration", 0.0)) if isinstance(timeline, dict) else 0.0
    if srt_entries and duration and srt_entries[-1]["end"] > duration + 0.1:
        errors.append("final caption ends after speech timeline duration")
    if srt_entries and duration and duration - srt_entries[-1]["end"] > max_gap:
        errors.append(f"final caption ends {duration - srt_entries[-1]['end']:.3f}s before speech timeline")
    return {
        "status": "succeeded" if not errors else "failed",
        "srt": str(srt_path),
        "speech_timeline": str(timeline_path),
        "reference_script": str(reference_script) if reference_script else None,
        "caption_count": len(srt_entries),
        "timeline_duration": round(duration, 3),
        "last_caption_end": round(srt_entries[-1]["end"], 3) if srt_entries else None,
        "truncations": truncations,
        "warnings": warnings,
        "errors": errors,
    }


def captions(args: argparse.Namespace) -> int:
    manifest = read_json(args.manifest.resolve())
    if manifest.get("unresolved_sources"):
        raise ValueError("Resolve reviewed media decisions before generating final captions")
    speech_sources = [
        source for source in manifest.get("sources", [])
        if source.get("decision", {}).get("requires_rough_cut")
    ]
    if not speech_sources:
        raise ValueError("No keep_original narration sources are available for captions")
    speech_sources.sort(key=lambda item: item["decision"]["timeline_order"])
    output_dir = args.output_dir.resolve()
    entries: list[dict[str, Any]] = []
    cursor = 0.0
    for source in speech_sources:
        require_validated_rough_cut(source)
        analysis = read_json(Path(source["analysis"]))
        transcript = analysis.get("transcript", [])
        ranges = kept_ranges(source)
        range_offsets = []
        source_cursor = 0.0
        for item in ranges:
            range_offsets.append((item, source_cursor))
            source_cursor += item["end"] - item["start"]
        for transcript_item in transcript:
            original_start = float(transcript_item.get("start", 0.0))
            original_end = float(transcript_item.get("end", original_start))
            text = str(transcript_item.get("text", "")).strip()
            if not text or original_end <= original_start:
                continue
            candidates = []
            for item, offset in range_offsets:
                overlap_start = max(original_start, item["start"])
                overlap_end = min(original_end, item["end"])
                if overlap_end > overlap_start:
                    candidates.append((overlap_end - overlap_start, overlap_start, overlap_end, item, offset))
            if not candidates:
                continue
            _, overlap_start, overlap_end, item, offset = max(candidates, key=lambda value: value[0])
            start = cursor + offset + (overlap_start - item["start"])
            end = cursor + offset + (overlap_end - item["start"])
            entries.append({
                "source_id": source["id"],
                "source_video": source["video"],
                "source_start": round(overlap_start, 3),
                "source_end": round(overlap_end, 3),
                "timeline_start": round(start, 3),
                "timeline_end": round(max(end, start + 0.08), 3),
                "role": source["decision"]["role"],
                "audio_policy": source["decision"]["audio_policy"],
                "text": text,
            })
        cursor += source_cursor
    if not entries:
        raise ValueError("No transcript segments survived the rough-cut plans")
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / "captions.srt"
    srt_lines = []
    for index, entry in enumerate(entries, start=1):
        srt_lines.extend([str(index), f"{srt_time(entry['timeline_start'])} --> {srt_time(entry['timeline_end'])}", entry["text"], ""])
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    timeline_path = output_dir / "speech_timeline.json"
    timeline = {
        "version": 1,
        "duration": round(cursor, 3),
        "captions": entries,
        "speech_sources": [
            {
                "source_id": source["id"],
                "timeline_order": source["decision"]["timeline_order"],
                "rough_cut_plan": source["rough_cut_plan"],
                "rough_cut_artifacts": source["rough_cut_artifacts"],
            }
            for source in speech_sources
        ],
    }
    write_json(timeline_path, timeline)
    reference_script = args.reference_script.resolve() if args.reference_script else None
    report = caption_qc(srt_path, timeline_path, reference_script)
    qc_path = output_dir / "captions.qc.json"
    write_json(qc_path, report)
    if report["status"] != "succeeded":
        raise ValueError("caption QC failed: " + "; ".join(report["errors"]))
    emit("succeeded", "captions", srt=str(srt_path), speech_timeline=str(timeline_path), qc=str(qc_path), captions=len(entries), duration=round(cursor, 3))
    return 0


def caption_qc_command(args: argparse.Namespace) -> int:
    reference_script = args.reference_script.resolve() if args.reference_script else None
    report = caption_qc(args.srt.resolve(), args.speech_timeline.resolve(), reference_script, args.max_gap)
    if args.output:
        write_json(args.output.resolve(), report)
    emit(report["status"], "caption-qc", **{key: value for key, value in report.items() if key != "status"})
    return 0 if report["status"] == "succeeded" else 2


def validate(args: argparse.Namespace) -> int:
    manifest = read_json(args.manifest.resolve())
    errors = []
    sources = manifest.get("sources", [])
    if not sources:
        errors.append("manifest has no sources")
    for source in sources:
        if not Path(str(source.get("video", ""))).is_file():
            errors.append(f"missing video: {source.get('id', '<unknown>')}")
        if not Path(str(source.get("analysis", ""))).is_file():
            errors.append(f"missing analysis: {source.get('id', '<unknown>')}")
        if not source.get("decision"):
            errors.append(f"missing decision: {source.get('id', '<unknown>')}")
    emit("succeeded" if not errors else "failed", "validate", manifest=str(args.manifest), errors=errors)
    return 0 if not errors else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Reviewed multi-source media role and caption director")
    commands = root.add_subparsers(dest="command", required=True)
    intake_parser = commands.add_parser("intake", help="Create conservative multi-source recommendations")
    intake_parser.add_argument("--video", type=Path, action="append", required=True)
    intake_parser.add_argument("--analysis", type=Path, action="append", required=True)
    intake_parser.add_argument("--output", type=Path, required=True)
    intake_parser.set_defaults(handler=intake)
    decisions_parser = commands.add_parser("apply-decisions", help="Validate reviewed source roles and audio policies")
    decisions_parser.add_argument("--intake", type=Path, required=True)
    decisions_parser.add_argument("--decisions", type=Path, required=True)
    decisions_parser.add_argument("--output", type=Path, required=True)
    decisions_parser.set_defaults(handler=apply_decisions)
    attach_parser = commands.add_parser("attach-rough-cuts", help="Attach approved rough-cut plans to a media manifest")
    attach_parser.add_argument("--manifest", type=Path, required=True)
    attach_parser.add_argument("--rough-cut", action="append", required=True, help="SOURCE_ID=PLAN_PATH")
    attach_parser.add_argument("--rough-cut-visual", action="append", required=True, help="SOURCE_ID=SILENT_VIDEO_PATH")
    attach_parser.add_argument("--rough-cut-narration", action="append", required=True, help="SOURCE_ID=NARRATION_AUDIO_PATH")
    attach_parser.add_argument("--rough-cut-review", action="append", required=True, help="SOURCE_ID=REVIEW_MP4_PATH")
    attach_parser.add_argument("--rough-cut-qc", action="append", required=True, help="SOURCE_ID=QC_JSON_PATH")
    attach_parser.add_argument("--output", type=Path)
    attach_parser.set_defaults(handler=attach_rough_cuts)
    captions_parser = commands.add_parser("captions", help="Create final-timeline SRT and source mapping")
    captions_parser.add_argument("--manifest", type=Path, required=True)
    captions_parser.add_argument("--output-dir", type=Path, required=True)
    captions_parser.add_argument("--reference-script", type=Path, help="Approved copy used to reject unacknowledged subtitle truncation")
    captions_parser.set_defaults(handler=captions)
    caption_qc_parser = commands.add_parser("caption-qc", help="Verify SRT, speech timeline, and optional approved copy before draft creation")
    caption_qc_parser.add_argument("--srt", type=Path, required=True)
    caption_qc_parser.add_argument("--speech-timeline", type=Path, required=True)
    caption_qc_parser.add_argument("--reference-script", type=Path)
    caption_qc_parser.add_argument("--max-gap", type=float, default=4.0)
    caption_qc_parser.add_argument("--output", type=Path)
    caption_qc_parser.set_defaults(handler=caption_qc_command)
    validate_parser = commands.add_parser("validate", help="Validate a reviewed media manifest")
    validate_parser.add_argument("--manifest", type=Path, required=True)
    validate_parser.set_defaults(handler=validate)
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
