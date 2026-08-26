#!/usr/bin/env python3
"""Detect and review face-driven circular speaker PiP framing from final-timeline video."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median


HEAD_FILL_RATIO = 0.72
VISIBLE_DIAMETER_RATIO = 0.22
PIP_ANCHORS = {
    "upper_left": (-0.56, 0.53),
    "upper_right": (0.56, 0.53),
}


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def sampled_times(start: float, duration: float) -> list[float]:
    # Avoid cut boundaries where motion blur and frame seeking are least stable.
    offsets = (0.24, 0.50, 0.76)
    return [start + duration * offset for offset in offsets]


def choose_face(boxes: list[tuple[int, int, int, int]], width: int, height: int) -> tuple[int, int, int, int] | None:
    """Prefer a large central face over small faces in posters or background art."""
    candidates = []
    for x, y, w, h in boxes:
        area = w * h
        center_x, center_y = x + w / 2, y + h / 2
        distance = math.hypot((center_x / width - 0.5) / 0.5, (center_y / height - 0.5) / 0.5)
        score = area * max(0.20, 1.35 - 0.35 * distance)
        candidates.append((score, (x, y, w, h)))
    return max(candidates, default=(0.0, None), key=lambda item: item[0])[1]


def consistent_detections(detections: list[dict]) -> list[dict]:
    """Discard a torso-sized cascade false positive before calculating a head crop."""
    if len(detections) < 2:
        return []
    boxes = [item["bbox"] for item in detections]
    median_x = median(item["x"] + item["width"] / 2 for item in boxes)
    median_y = median(item["y"] + item["height"] / 2 for item in boxes)
    median_size = median((item["width"] + item["height"]) / 2 for item in boxes)
    return [
        item for item in detections
        if abs(item["bbox"]["x"] + item["bbox"]["width"] / 2 - median_x) <= median_size * 0.45
        and abs(item["bbox"]["y"] + item["bbox"]["height"] / 2 - median_y) <= median_size * 0.45
        and 0.70 <= ((item["bbox"]["width"] + item["bbox"]["height"]) / 2) / median_size <= 1.30
    ]


def detect_review(video: Path, broll_plan: Path, preview_dir: Path | None = None) -> dict:
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("OpenCV is required for PiP face framing; install opencv-python") from error

    # alt2 is materially more stable than the default cascade on the source
    # footage: it avoids promoting a torso-sized false positive over the face.
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
    if cascade.empty():
        raise RuntimeError("OpenCV frontal-face cascade is unavailable")
    plan = load_json(broll_plan)
    segments = plan.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("B-roll plan requires a segments array")
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open visual: {video}")
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width <= 0 or height <= 0:
        raise RuntimeError("visual has invalid dimensions")
    if preview_dir:
        preview_dir.mkdir(parents=True, exist_ok=True)

    reviews, errors = [], []
    for index, segment in enumerate(segments, start=1):
        pip = segment.get("speaker_pip", {}) if isinstance(segment, dict) else {}
        if not isinstance(pip, dict) or not pip.get("enabled"):
            continue
        start, duration = float(segment.get("start", -1)), float(segment.get("duration", 0))
        if start < 0 or duration <= 0:
            errors.append(f"PiP segment {index} has invalid final-timeline range")
            continue
        detections = []
        for sample_index, timestamp in enumerate(sampled_times(start, duration), start=1):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            boxes = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=6, minSize=(max(72, width // 18), max(72, height // 18)))
            chosen = choose_face([tuple(map(int, box)) for box in boxes], width, height)
            if chosen is None:
                continue
            x, y, face_w, face_h = chosen
            detections.append({"timestamp": round(timestamp, 3), "bbox": {"x": x, "y": y, "width": face_w, "height": face_h}})
            if preview_dir:
                annotated = frame.copy()
                cv2.rectangle(annotated, (x, y), (x + face_w, y + face_h), (0, 255, 0), 5)
                encoded, buffer = cv2.imencode(".jpg", annotated)
                if encoded:
                    buffer.tofile(str(preview_dir / f"pip_{index:02d}_sample_{sample_index}.jpg"))
        detections = consistent_detections(detections)
        if len(detections) < 2:
            errors.append(f"PiP segment {index} has only {len(detections)} reliable face detections")
            continue
        boxes = [item["bbox"] for item in detections]
        face_x = median(item["x"] for item in boxes)
        face_y = median(item["y"] for item in boxes)
        face_w = median(item["width"] for item in boxes)
        face_h = median(item["height"] for item in boxes)
        center_x = (face_x + face_w / 2) / width * 2 - 1
        center_y = (face_y + face_h / 2) / height * 2 - 1
        # A head-focused window keeps facial context while excluding the torso.
        # The final circle remains compact even though the source stays sharp.
        mask_size = clamp((face_h / height) / HEAD_FILL_RATIO, 0.18, 0.34)
        visible_diameter = VISIBLE_DIAMETER_RATIO
        scale = clamp(visible_diameter / mask_size, 0.45, 1.15)
        position = str(pip.get("position", "upper_right"))
        if position not in PIP_ANCHORS:
            errors.append(f"PiP segment {index} has unsupported position: {position}")
            continue
        anchor_x, anchor_y = PIP_ANCHORS[position]
        # A mask center is local to the source. Compensate for its local face
        # offset so the *detected face*, not the source center, reaches the
        # intended upper-corner anchor after JianYing applies clip scaling.
        placement_x = clamp(anchor_x - center_x * scale, -0.90, 0.90)
        placement_y = clamp(anchor_y - center_y * scale, -0.90, 0.90)
        reviews.append({
            "segment_index": index,
            "final_start": round(start, 3),
            "final_duration": round(duration, 3),
            "source_visual": str(video.resolve()),
            "frame_size": {"width": width, "height": height},
            "detections": detections,
            "face_bbox": {"x": round(face_x, 2), "y": round(face_y, 2), "width": round(face_w, 2), "height": round(face_h, 2)},
            "face_center_x": round(center_x, 4),
            "face_center_y": round(center_y, 4),
            "face_fill_ratio": round((face_h / height) / mask_size, 3),
            "mask_size": round(mask_size, 4),
            "scale": round(scale, 4),
            "visible_diameter_ratio": round(mask_size * scale, 4),
            "position": position,
            "face_anchor_x": anchor_x,
            "face_anchor_y": anchor_y,
            "placement_transform_x": round(placement_x, 4),
            "placement_transform_y": round(placement_y, 4),
            "status": "approved",
        })
    capture.release()
    return {
        "version": 1,
        "timestamp_basis": "rendered_rough_cut_output",
        "visual": str(video.resolve()),
        "pip_reviews": reviews,
        "errors": errors,
        "status": "succeeded" if not errors else "failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visual", type=Path, required=True)
    parser.add_argument("--broll-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()
    try:
        if not args.visual.is_file() or not args.broll_plan.is_file():
            raise FileNotFoundError("visual and broll plan must exist")
        report = detect_review(args.visual.resolve(), args.broll_plan.resolve(), args.preview_dir.resolve() if args.preview_dir else None)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        report = {"status": "failed", "errors": [str(error)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
