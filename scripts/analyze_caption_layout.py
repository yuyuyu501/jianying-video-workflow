#!/usr/bin/env python3
"""Create a frame-checked caption layout review for the final narration timeline."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from analyze_pip_faces import PIP_ANCHORS, add_circle_preview, canvas_frame, choose_face, load_json
from caption_design_director import semantic_role
from validate_draft_captions import read_srt


def write_jpeg(path: Path, frame) -> None:
    """Write through Python so Windows Unicode paths do not reach cv2.imwrite."""
    import cv2

    ok, encoded = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError(f"cannot encode representative caption frame: {path}")
    path.write_bytes(encoded.tobytes())


def ffmpeg_frame(video: Path, timestamp: float):
    """Decode one frame when OpenCV cannot seek a source reliably."""
    import cv2
    import numpy as np

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
            "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    frame = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"cannot decode B-roll representative frame: {video} at {timestamp:.3f}s")
    return frame


def overlaps(entry: dict, segment: dict) -> bool:
    return entry["start"] < float(segment["start"]) + float(segment["duration"]) and float(segment["start"]) < entry["end"]


def active_broll(entry: dict, broll_segments: list[dict]) -> dict | None:
    return next((segment for segment in broll_segments if overlaps(entry, segment)), None)


def classify(entry: dict, broll_segments: list[dict]) -> tuple[str, str, float]:
    for segment in broll_segments:
        if not overlaps(entry, segment):
            continue
        pip = segment.get("speaker_pip", {})
        if isinstance(pip, dict) and pip.get("enabled"):
            return "broll_with_pip", "lower_center", -0.48
        return "full_broll", "mid_low", -0.34
    return "talking_head", "lower_center", -0.54


def talking_head_zone(entry: dict, face: tuple[int, int, int, int] | None, height: int) -> tuple[str, float]:
    """Choose a stronger mid-low caption only when the detected face leaves room."""
    text = str(entry["text"]).replace("\n", "")
    face_bottom = (face[1] + face[3]) / height if face else 0.0
    short_or_urgent = len(text) <= 8 or semantic_role(text) in {"warning", "step_title", "medicine"}
    if short_or_urgent and face_bottom < 0.58:
        return "mid_low", -0.34
    return "lower_center", -0.54


def review(video: Path, captions: Path, broll_plan: Path, frame_dir: Path) -> dict:
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("OpenCV is required for caption visual layout review") from error
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
    if cascade.empty():
        raise RuntimeError("OpenCV frontal-face cascade is unavailable")
    plan = load_json(broll_plan)
    broll = plan.get("segments", [])
    if not isinstance(broll, list):
        raise ValueError("B-roll plan requires a segments array")
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open visual: {video}")
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_dir.mkdir(parents=True, exist_ok=True)
    layouts = []
    for index, entry in enumerate(read_srt(captions), start=1):
        context, zone, transform_y = classify(entry, broll)
        broll_segment = active_broll(entry, broll)
        timestamp = (entry["start"] + entry["end"]) / 2
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        speaker_ok, speaker_frame = capture.read()
        ok, frame = speaker_ok, speaker_frame
        if broll_segment is not None:
            broll_video = Path(str(broll_segment.get("video", "")))
            broll_capture = cv2.VideoCapture(str(broll_video))
            broll_start = float(broll_segment["start"])
            broll_end = broll_start + float(broll_segment["duration"])
            overlap_start = max(float(entry["start"]), broll_start)
            overlap_end = min(float(entry["end"]), broll_end)
            overlap_time = (overlap_start + overlap_end) / 2
            source_time = float(broll_segment.get("source_start", 0)) + max(0.0, overlap_time - broll_start)
            broll_capture.set(cv2.CAP_PROP_POS_MSEC, source_time * 1000)
            broll_ok, broll_frame = broll_capture.read()
            broll_capture.release()
            if not broll_ok:
                broll_frame = ffmpeg_frame(broll_video, source_time)
            frame = canvas_frame(broll_frame, width, height)
            pip = broll_segment.get("speaker_pip", {})
            if isinstance(pip, dict) and pip.get("enabled"):
                head = pip.get("head_envelope")
                anchor = PIP_ANCHORS.get(str(pip.get("position", "")))
                if not speaker_ok or not isinstance(head, dict) or anchor is None:
                    raise RuntimeError("enabled SpeakerPiP lacks a renderable speaker frame, head envelope, or approved position")
                add_circle_preview(frame, speaker_frame, head, anchor[0], anchor[1])
            ok = True
        face = None
        if speaker_ok and context == "talking_head":
            gray = cv2.cvtColor(speaker_frame, cv2.COLOR_BGR2GRAY)
            boxes = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=6, minSize=(max(72, width // 18), max(72, height // 18)))
            face = choose_face([tuple(map(int, box)) for box in boxes], width, height)
        if ok:
            frame_path = frame_dir / f"cue_{index:04d}.jpg"
            write_jpeg(frame_path, frame)
        else:
            frame_path = None
        if context == "talking_head":
            zone, transform_y = talking_head_zone(entry, face, height)
        layouts.append({
            "index": index - 1,
            "start": round(entry["start"], 3),
            "end": round(entry["end"], 3),
            "text": entry["text"],
            "visual_context": context,
            "caption_zone": zone,
            "transform_y": transform_y,
            "face_detected": face is not None,
            "face_bbox": {"x": face[0], "y": face[1], "width": face[2], "height": face[3]} if face else None,
            "representative_frame": str(frame_path.resolve()) if frame_path else None,
            "machine_observation": "Final-timeline representative pixels include active B-roll and approved SpeakerPiP placement when present; machine composition is still not AI visual approval.",
        })
    capture.release()
    return {
        "version": 1,
        "timestamp_basis": "rendered_rough_cut_output",
        "visual": str(video.resolve()),
        "captions": str(captions.resolve()),
        "representative_frame_dir": str(frame_dir.resolve()),
        "layouts": layouts,
        "status": "succeeded",
        "errors": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visual", type=Path, required=True)
    parser.add_argument("--captions", type=Path, required=True)
    parser.add_argument("--broll-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.visual.is_file() or not args.captions.is_file() or not args.broll_plan.is_file():
            raise FileNotFoundError("visual, captions, and broll plan must exist")
        result = review(
            args.visual.resolve(), args.captions.resolve(), args.broll_plan.resolve(),
            args.output.resolve().parent / "caption-layout-frames",
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        result = {"status": "failed", "errors": [str(error)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
