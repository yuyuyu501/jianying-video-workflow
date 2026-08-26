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
    transcripts = parse_mappings(args.rough_cut_transcript, "--rough-cut-transcript")
    qc_reports = parse_mappings(args.rough_cut_qc, "--rough-cut-qc")
    required = [source["id"] for source in sources.values() if source.get("decision", {}).get("requires_rough_cut")]
    missing = [source_id_value for source_id_value in required if source_id_value not in plans or source_id_value not in visuals or source_id_value not in narrations or source_id_value not in reviews or source_id_value not in transcripts or source_id_value not in qc_reports]
    if missing:
        raise ValueError("Missing validated rough-cut artifacts for: " + ", ".join(missing))
    for source_id_value, plan in plans.items():
        sources[source_id_value]["rough_cut_plan"] = str(plan)
        source = sources[source_id_value]
        source["rough_cut_artifacts"] = {
            "visual": str(visuals[source_id_value]),
            "narration": str(narrations[source_id_value]),
            "review": str(reviews[source_id_value]),
            "transcript": str(transcripts[source_id_value]),
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


def require_validated_rough_cut(source: dict[str, Any]) -> None:
    artifacts = source.get("rough_cut_artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError(f"{source['id']}: validated rough-cut artifacts are required before captions")
    for key in ("visual", "narration", "review", "transcript", "qc"):
        if not Path(str(artifacts.get(key, ""))).is_file():
            raise FileNotFoundError(f"{source['id']}: missing rough-cut {key} artifact")
    qc = read_json(Path(artifacts["qc"]))
    if qc.get("status") != "succeeded" or qc.get("errors"):
        raise ValueError(f"{source['id']}: rough-cut QC must pass before captions")


def rough_cut_transcript(source: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    """Read speech intervals generated from the rendered rough-cut review file."""
    require_validated_rough_cut(source)
    data = read_json(Path(source["rough_cut_artifacts"]["transcript"]))
    transcript_video = Path(str(data.get("video", "")))
    review_video = Path(str(source["rough_cut_artifacts"]["review"])).resolve()
    if transcript_video.name != review_video.name:
        raise ValueError(f"{source['id']}: transcript must be generated from the rendered rough-cut review MP4")
    duration = float(data.get("duration", 0.0))
    entries = []
    for item in data.get("transcript", []):
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        text = str(item.get("text", "")).strip()
        if end > start and text:
            entries.append({"start": round(start, 3), "end": round(end, 3), "recognized_text": text})
    if not entries:
        raise ValueError(f"{source['id']}: rough-cut transcript has no speech segments")
    if duration <= 0:
        raise ValueError(f"{source['id']}: rough-cut transcript has no duration")
    return entries, duration


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


def to_simplified(text: str) -> str:
    """Use OpenCC when available; text review remains mandatory either way."""
    try:
        from opencc import OpenCC
        return OpenCC("t2s").convert(text)
    except ImportError:
        return text


def coverage_report(entries: list[dict[str, Any]], speech_ranges: list[dict[str, Any]], tolerance: float = 0.45) -> dict[str, Any]:
    """Verify subtitles cover speech detected on the rendered rough-cut timeline."""
    errors: list[str] = []
    if not speech_ranges:
        return {"status": "failed", "errors": ["rough-cut speech ranges are missing"], "range_count": 0}
    if not entries:
        return {"status": "failed", "errors": ["SRT has no entries to cover rough-cut speech"], "range_count": len(speech_ranges)}
    caption_ranges = [(item["start"], item["end"]) for item in entries]
    first_speech = min(item["start"] for item in speech_ranges)
    last_speech = max(item["end"] for item in speech_ranges)
    if caption_ranges[0][0] > first_speech + tolerance:
        errors.append(f"opening speech is uncovered for {caption_ranges[0][0] - first_speech:.3f}s")
    if caption_ranges[-1][1] < last_speech - tolerance:
        errors.append(f"ending speech is uncovered for {last_speech - caption_ranges[-1][1]:.3f}s")
    for index, speech in enumerate(speech_ranges, start=1):
        start, end = float(speech["start"]), float(speech["end"])
        covered = sum(max(0.0, min(end, cap_end) - max(start, cap_start)) for cap_start, cap_end in caption_ranges)
        if covered < (end - start) * 0.80:
            errors.append(f"rough-cut speech range {index} is not sufficiently subtitle-covered")
    return {
        "status": "passed" if not errors else "failed",
        "range_count": len(speech_ranges),
        "first_speech_start": round(first_speech, 3),
        "last_speech_end": round(last_speech, 3),
        "errors": errors,
    }


CAPTION_BREAK_PUNCTUATION = frozenset("\uFF0C,\u3001\uFF1B;\u3002\uFF01\uFF1F!?\uFF1A:")
CAPTION_MAX_CHARS = 18


def caption_reading_weight(text: str) -> int:
    """Estimate speech time without letting punctuation consume a full share."""
    return max(1, sum(1 for character in text if not character.isspace() and character not in CAPTION_BREAK_PUNCTUATION))


def display_caption_text(text: str) -> str:
    """Keep spoken words while removing punctuation used only as visual breaks."""
    return "".join(character for character in text if character not in CAPTION_BREAK_PUNCTUATION).strip()


def split_caption_entry(entry: dict[str, Any], max_chars: int = CAPTION_MAX_CHARS) -> list[dict[str, Any]]:
    """Split a reviewed rough-cut caption at punctuation without displaying it.

    The review remains the source of truth for text and timing. Splitting happens
    only after review, and proportionally subdivides its existing time range so
    the final SRT remains continuous on the rendered rough-cut timeline.
    """
    if max_chars < 2:
        raise ValueError("max_chars must be at least 2")
    text = str(entry["text"]).strip()
    start, end = float(entry["start"]), float(entry["end"])
    if not text or end <= start:
        raise ValueError("caption entry requires non-empty text and positive range")

    chunks: list[str] = []
    current = ""
    for character in text:
        current += character
        if character in CAPTION_BREAK_PUNCTUATION:
            spoken = display_caption_text(current)
            if spoken:
                chunks.append(spoken)
            current = ""
        elif caption_reading_weight(current) >= max_chars:
            spoken = display_caption_text(current)
            if spoken:
                chunks.append(spoken)
            current = ""
    if display_caption_text(current):
        chunks.append(display_caption_text(current))
    chunks = [chunk for chunk in chunks if chunk]
    if len(chunks) == 1:
        return [{**entry, "start": round(start, 3), "end": round(end, 3), "text": chunks[0]}]

    total_weight = sum(caption_reading_weight(chunk) for chunk in chunks)
    duration = end - start
    cursor = start
    split_entries = []
    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            chunk_end = end
        else:
            chunk_end = cursor + duration * caption_reading_weight(chunk) / total_weight
        split_entries.append({**entry, "start": round(cursor, 3), "end": round(chunk_end, 3), "text": chunk})
        cursor = chunk_end
    # Round-off must never create a gap in an SRT whose timestamps are ms based.
    for previous, current_entry in zip(split_entries, split_entries[1:]):
        current_entry["start"] = previous["end"]
    split_entries[-1]["end"] = round(end, 3)
    return split_entries


def caption_template(args: argparse.Namespace) -> int:
    manifest = read_json(args.manifest.resolve())
    sources = [source for source in manifest.get("sources", []) if source.get("decision", {}).get("requires_rough_cut")]
    sources.sort(key=lambda item: item["decision"]["timeline_order"])
    captions = []
    for source in sources:
        transcript, _ = rough_cut_transcript(source)
        for item in transcript:
            captions.append({
                "source_id": source["id"],
                "rough_cut_start": item["start"],
                "rough_cut_end": item["end"],
                "recognized_text": item["recognized_text"],
                "text": "",
            })
    output = args.output.resolve()
    write_json(output, {
        "version": 1,
        "timestamp_basis": "rendered_rough_cut_output",
        "instructions": "Replace text with reviewed simplified Chinese from the rough-cut video and approved copy. Preserve source_id and rough_cut_start/end. Do not manually split at commas: final SRT generation will split reviewed long entries at commas and sentence punctuation while preserving this time range.",
        "captions": captions,
    })
    emit("succeeded", "caption-template", output=str(output), captions=len(captions))
    return 0


def reviewed_captions(path: Path, sources: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    payload = read_json(path.resolve())
    values = payload.get("captions", []) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("caption review must be an array or an object with a captions array")
    known = {source["id"] for source in sources}
    grouped = {source_id: [] for source_id in known}
    for index, item in enumerate(values, start=1):
        source_id_value = str(item.get("source_id", ""))
        if source_id_value not in known:
            raise ValueError(f"caption review entry {index} has unknown source_id")
        start = float(item.get("rough_cut_start", item.get("start", -1)))
        end = float(item.get("rough_cut_end", item.get("end", -1)))
        text = to_simplified(str(item.get("text", "")).strip())
        if end <= start or not text:
            raise ValueError(f"caption review entry {index} requires non-empty text and positive rough-cut range")
        grouped[source_id_value].append({"start": start, "end": end, "text": text})
    for source in sources:
        entries = sorted(grouped[source["id"]], key=lambda item: item["start"])
        if not entries:
            raise ValueError(f"caption review has no entries for {source['id']}")
        for previous, current in zip(entries, entries[1:]):
            if current["start"] < previous["end"] - 0.001:
                raise ValueError(f"caption review overlaps on {source['id']}")
        grouped[source["id"]] = entries
    return grouped


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
    rough_cut_coverage: dict[str, Any] | None = None
    if not isinstance(timeline, dict) or timeline.get("timestamp_basis") != "rendered_rough_cut_output":
        errors.append("speech timeline must use rendered_rough_cut_output as its timestamp basis")
    elif "speech_ranges" not in timeline:
        errors.append("speech timeline must include rendered rough-cut speech_ranges")
    else:
        rough_cut_coverage = coverage_report(srt_entries, timeline.get("speech_ranges", []))
        if rough_cut_coverage["status"] != "passed":
            errors.extend(rough_cut_coverage["errors"])
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
        "rough_cut_coverage": rough_cut_coverage,
    }


def captions(args: argparse.Namespace) -> int:
    manifest = read_json(args.manifest.resolve())
    if manifest.get("unresolved_sources"):
        raise ValueError("Resolve reviewed media decisions before generating final captions")
    speech_sources = [source for source in manifest.get("sources", []) if source.get("decision", {}).get("requires_rough_cut")]
    if not speech_sources:
        raise ValueError("No keep_original narration sources are available for captions")
    speech_sources.sort(key=lambda item: item["decision"]["timeline_order"])
    if not args.caption_review:
        raise ValueError("--caption-review is required; captions must be reviewed against the rendered rough-cut transcript")
    reviewed = reviewed_captions(args.caption_review, speech_sources)
    output_dir = args.output_dir.resolve()
    entries: list[dict[str, Any]] = []
    cursor = 0.0
    speech_ranges: list[dict[str, Any]] = []
    for source in speech_sources:
        transcript, duration = rough_cut_transcript(source)
        review_entries = reviewed[source["id"]]
        out_of_range = [item for item in review_entries if item["start"] < -0.01 or item["end"] > duration + 0.10]
        if out_of_range:
            raise ValueError(f"{source['id']}: caption review timestamps fall outside rendered rough-cut duration")
        coverage = coverage_report(review_entries, transcript)
        if coverage["status"] != "passed":
            raise ValueError(f"{source['id']}: caption coverage QC failed: " + "; ".join(coverage["errors"]))
        for item in transcript:
            speech_ranges.append({"start": cursor + item["start"], "end": cursor + item["end"]})
        for item in review_entries:
            for split_item in split_caption_entry(item, args.max_chars):
                start = cursor + split_item["start"]
                end = cursor + split_item["end"]
                entries.append({
                    "source_id": source["id"],
                    "source_video": source["video"],
                    "source_start": round(split_item["start"], 3),
                    "source_end": round(split_item["end"], 3),
                    "timeline_start": round(start, 3),
                    "timeline_end": round(max(end, start + 0.08), 3),
                    "role": source["decision"]["role"],
                    "audio_policy": source["decision"]["audio_policy"],
                    "text": split_item["text"],
                })
        cursor += duration
    if not entries:
        raise ValueError("No reviewed caption entries survived the rough-cut timeline")
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / "captions.srt"
    srt_lines = []
    for index, entry in enumerate(entries, start=1):
        srt_lines.extend([str(index), f"{srt_time(entry['timeline_start'])} --> {srt_time(entry['timeline_end'])}", entry["text"], ""])
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    timeline_path = output_dir / "speech_timeline.json"
    timeline = {
        "version": 2,
        "duration": round(cursor, 3),
        "timestamp_basis": "rendered_rough_cut_output",
        "captions": entries,
        "speech_ranges": speech_ranges,
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
    attach_parser.add_argument("--rough-cut-transcript", action="append", required=True, help="SOURCE_ID=ROUGH_CUT_TRANSCRIPT_JSON")
    attach_parser.add_argument("--rough-cut-qc", action="append", required=True, help="SOURCE_ID=QC_JSON_PATH")
    attach_parser.add_argument("--output", type=Path)
    attach_parser.set_defaults(handler=attach_rough_cuts)
    template_parser = commands.add_parser("caption-template", help="Create a review template from rough-cut-timebase transcripts")
    template_parser.add_argument("--manifest", type=Path, required=True)
    template_parser.add_argument("--output", type=Path, required=True)
    template_parser.set_defaults(handler=caption_template)
    captions_parser = commands.add_parser("captions", help="Create final-timeline SRT and source mapping")
    captions_parser.add_argument("--manifest", type=Path, required=True)
    captions_parser.add_argument("--output-dir", type=Path, required=True)
    captions_parser.add_argument("--caption-review", type=Path, required=True, help="Reviewed simplified-Chinese captions with rough-cut timestamps")
    captions_parser.add_argument("--reference-script", type=Path, help="Approved copy used to reject unacknowledged subtitle truncation")
    captions_parser.add_argument("--max-chars", type=int, default=CAPTION_MAX_CHARS, help="Maximum non-punctuation characters per final SRT entry; commas always split")
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
