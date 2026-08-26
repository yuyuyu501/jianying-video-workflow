#!/usr/bin/env python3
"""Pixel-level acceptance checks for a manually exported JianYing MP4."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return json.loads(result.stdout)


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_caption_entries(entries: list[dict], duration: float) -> dict:
    errors: list[str] = []
    single_character: list[int] = []
    punctuation_only: list[int] = []
    previous_end = 0.0
    for index, entry in enumerate(entries, start=1):
        start, end = number(entry.get("start"), -1), number(entry.get("end"), -1)
        text = str(entry.get("text", "")).strip()
        if start < 0 or end <= start or end > duration + 0.05:
            errors.append(f"caption {index} is outside exported duration: {start:.3f}-{end:.3f}")
        if start + 0.01 < previous_end:
            errors.append(f"caption {index} overlaps the previous caption")
        previous_end = max(previous_end, end)
        display_text = text.replace(" ", "")
        if len(display_text) == 1:
            single_character.append(index)
        if display_text and all(char in "，。！？、；：,.!?;:" for char in display_text):
            punctuation_only.append(index)
    if single_character:
        errors.append("single-character subtitle cues: " + ", ".join(map(str, single_character)))
    if punctuation_only:
        errors.append("punctuation-only subtitle cues: " + ", ".join(map(str, punctuation_only)))
    return {"status": "failed" if errors else "passed", "count": len(entries), "single_character_cues": single_character, "punctuation_only_cues": punctuation_only, "errors": errors}


def norm_to_pixel(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    return (x + 1.0) * width / 2.0, (1.0 - y) * height / 2.0


def circle_geometry(item: dict, width: int, height: int) -> dict:
    pip = item.get("speaker_pip", {}) if isinstance(item, dict) else {}
    x = number(pip.get("placement_transform_x"), 0.0)
    y = number(pip.get("placement_transform_y"), 0.0)
    size = number(pip.get("mask_size"), 0.28)
    center_x, center_y = norm_to_pixel(x, y, width, height)
    diameter = max(1.0, size * height)
    return {"center_x": round(center_x, 2), "center_y": round(center_y, 2), "diameter": round(diameter, 2), "left": round(center_x - diameter / 2, 2), "top": round(center_y - diameter / 2, 2), "right": round(center_x + diameter / 2, 2), "bottom": round(center_y + diameter / 2, 2)}


def inside_circle(box: dict, circle: dict, margin: float = 0.08) -> bool:
    cx, cy = circle["center_x"], circle["center_y"]
    radius = circle["diameter"] * (0.5 - margin)
    x, y, w, h = (number(box.get(key)) for key in ("x", "y", "width", "height"))
    return all(math.hypot(px - cx, py - cy) <= radius for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)))


def frame_at(capture, timestamp: float):
    import cv2
    capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
    ok, frame = capture.read()
    return frame if ok else None


def inspect_black_frames(video: Path, duration: float, sample_every: float) -> dict:
    try:
        import cv2
    except ImportError:
        return {"status": "skipped", "reason": "OpenCV is unavailable", "samples": [], "errors": []}
    capture = cv2.VideoCapture(str(video))
    samples, errors = [], []
    timestamp = 0.0
    while timestamp <= duration + 0.001:
        frame = frame_at(capture, timestamp)
        if frame is None:
            errors.append(f"frame unavailable at {timestamp:.3f}s")
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean, dark_ratio = float(gray.mean()), float((gray < 4).mean())
            black = mean < 4.0 and dark_ratio > 0.995
            samples.append({"timestamp": round(timestamp, 3), "mean_luma": round(mean, 3), "dark_ratio": round(dark_ratio, 4), "black": black})
            if black:
                errors.append(f"black frame at {timestamp:.3f}s")
        timestamp += max(0.5, sample_every)
    capture.release()
    return {"status": "failed" if errors else "passed", "samples": samples, "errors": errors}


def inspect_pip(video: Path, broll_plan: Path) -> dict:
    try:
        import cv2
    except ImportError:
        return {"status": "skipped", "reason": "OpenCV is unavailable", "segments": [], "errors": []}
    plan = load_json(broll_plan)
    segments = plan.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("B-roll plan requires a segments array")
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open exported video: {video}")
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
    findings, errors = [], []
    for index, segment in enumerate(segments, start=1):
        pip = segment.get("speaker_pip", {}) if isinstance(segment, dict) else {}
        if not isinstance(pip, dict) or not pip.get("enabled"):
            continue
        start, duration = number(segment.get("start")), number(segment.get("duration"))
        circle = circle_geometry(segment, width, height)
        samples = []
        for offset in (0.25, 0.50, 0.75):
            timestamp = start + duration * offset
            frame = frame_at(capture, timestamp)
            if frame is None:
                sample = {"timestamp": round(timestamp, 3), "status": "failed", "reason": "frame unavailable"}
            else:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                boxes = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5, minSize=(max(48, width // 24), max(48, height // 24)))
                candidates = []
                for x, y, w, h in boxes:
                    if math.hypot(x + w / 2 - circle["center_x"], y + h / 2 - circle["center_y"]) <= circle["diameter"] * 0.65:
                        candidates.append({"x": int(x), "y": int(y), "width": int(w), "height": int(h)})
                face = max(candidates, key=lambda item: item["width"] * item["height"], default=None)
                complete = bool(face and inside_circle(face, circle))
                sample = {"timestamp": round(timestamp, 3), "face_detected": face is not None, "head_inside_expected_circle": complete, "face_bbox": face}
            samples.append(sample)
            if not sample.get("head_inside_expected_circle"):
                errors.append(f"PiP segment {index} failed exported-frame head/circle check at {timestamp:.3f}s")
        findings.append({"segment_index": index, "timeline": {"start": start, "duration": duration}, "expected_circle": circle, "samples": samples, "status": "passed" if all(item.get("head_inside_expected_circle") for item in samples) else "failed"})
    capture.release()
    return {"status": "failed" if errors else "passed", "segments": findings, "errors": errors}


def inspect_native_text(video: Path, broll_plan: Path | None) -> dict:
    if broll_plan is None:
        return {"status": "skipped", "reason": "no B-roll plan supplied", "findings": [], "errors": []}
    if not shutil.which("tesseract"):
        return {"status": "skipped", "reason": "tesseract is unavailable; native B-roll text requires manual review", "findings": [], "errors": []}
    try:
        import cv2
        import pytesseract
    except ImportError:
        return {"status": "skipped", "reason": "OCR dependencies are unavailable; native B-roll text requires manual review", "findings": [], "errors": []}
    plan = load_json(broll_plan)
    capture = cv2.VideoCapture(str(video))
    findings = []
    for index, segment in enumerate(plan.get("segments", []), start=1):
        if not isinstance(segment, dict):
            continue
        start, duration = number(segment.get("start")), number(segment.get("duration"))
        frame = frame_at(capture, start + duration / 2)
        if frame is None:
            continue
        try:
            text = pytesseract.image_to_string(frame, lang="chi_sim+eng").strip()
        except Exception as error:
            capture.release()
            return {"status": "skipped", "reason": f"OCR unavailable: {error}", "findings": [], "errors": []}
        if text:
            findings.append({"segment_index": index, "timestamp": round(start + duration / 2, 3), "text": text, "status": "review_required"})
    capture.release()
    return {"status": "review_required" if findings else "passed", "findings": findings, "errors": []}


def verify(video: Path, captions: Path | None, broll_plan: Path | None, sample_every: float = 10.0) -> dict:
    metadata = probe(video)
    streams = metadata.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    duration = number(metadata.get("format", {}).get("duration"))
    media_errors = []
    if len(video_streams) != 1:
        media_errors.append(f"expected one video stream, found {len(video_streams)}")
    if not audio_streams:
        media_errors.append("export has no audio stream")
    checks: dict[str, Any] = {
        "media": {"status": "failed" if media_errors else "passed", "duration": duration, "video_streams": len(video_streams), "audio_streams": len(audio_streams), "resolution": {"width": video_streams[0].get("width"), "height": video_streams[0].get("height")} if video_streams else None, "errors": media_errors},
        "black_frames": inspect_black_frames(video, duration, sample_every),
        "captions": {"status": "skipped", "reason": "no captions supplied"},
        "pip": {"status": "skipped", "reason": "no B-roll plan supplied"},
        "native_text": inspect_native_text(video, broll_plan),
    }
    if captions is not None:
        from validate_draft_captions import read_srt
        checks["captions"] = validate_caption_entries(read_srt(captions), duration)
    if broll_plan is not None:
        checks["pip"] = inspect_pip(video, broll_plan)
    hard_failures = [name for name, report in checks.items() if report.get("status") in {"failed", "review_required"}]
    return {"version": 1, "status": "failed" if hard_failures else "succeeded", "video": str(video.resolve()), "checks": checks, "hard_failures": hard_failures, "note": "Flattened export pixels were inspected; draft JSON QC alone is insufficient."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--captions", type=Path)
    parser.add_argument("--broll-plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-every", type=float, default=10.0)
    args = parser.parse_args()
    try:
        report = verify(args.video.resolve(), args.captions.resolve() if args.captions else None, args.broll_plan.resolve() if args.broll_plan else None, args.sample_every)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        report = {"version": 1, "status": "failed", "video": str(args.video.resolve()), "checks": {}, "hard_failures": [str(error)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RESULT: " + json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
