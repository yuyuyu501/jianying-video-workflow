#!/usr/bin/env python3
"""Review an optional speaker PiP step for complete head framing and collisions."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median


FACE_FILL_RATIO = 0.52
VISIBLE_DIAMETER_RATIO = 0.22
PIP_ANCHORS = {
    "upper_left": (-0.58, 0.42),
    "upper_right": (0.58, 0.42),
    "middle_left": (-0.58, 0.08),
    "middle_right": (0.58, 0.08),
}


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def sampled_times(start: float, duration: float) -> list[float]:
    return [start + duration * offset for offset in (0.24, 0.50, 0.76)]


def choose_face(boxes: list[tuple[int, int, int, int]], width: int, height: int) -> tuple[int, int, int, int] | None:
    candidates = []
    for x, y, w, h in boxes:
        center_x, center_y = x + w / 2, y + h / 2
        distance = math.hypot((center_x / width - 0.5) / 0.5, (center_y / height - 0.5) / 0.5)
        candidates.append((w * h * max(0.20, 1.35 - 0.35 * distance), (x, y, w, h)))
    return max(candidates, default=(0.0, None), key=lambda item: item[0])[1]


def consistent_detections(detections: list[dict]) -> list[dict]:
    if len(detections) < 2:
        return []
    boxes = [item["bbox"] for item in detections]
    cx = median(item["x"] + item["width"] / 2 for item in boxes)
    cy = median(item["y"] + item["height"] / 2 for item in boxes)
    size = median((item["width"] + item["height"]) / 2 for item in boxes)
    return [item for item in detections if
            abs(item["bbox"]["x"] + item["bbox"]["width"] / 2 - cx) <= size * 0.45 and
            abs(item["bbox"]["y"] + item["bbox"]["height"] / 2 - cy) <= size * 0.45 and
            0.70 <= ((item["bbox"]["width"] + item["bbox"]["height"]) / 2) / size <= 1.30]


def head_envelope(face: dict, width: int, height: int) -> dict:
    """Expand the face detector box to include hair, chin, and headroom."""
    x, y, w, h = (float(face[key]) for key in ("x", "y", "width", "height"))
    left, top = max(0.0, x - w * 0.28), max(0.0, y - h * 0.42)
    right, bottom = min(float(width), x + w * 1.28), min(float(height), y + h * 1.34)
    return {"x": round(left, 2), "y": round(top, 2), "width": round(right - left, 2), "height": round(bottom - top, 2)}


def overlap(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def protected_zones(segment: dict) -> list[tuple[float, float, float, float]]:
    # Native B-roll captions/titles are not represented in the SRT. Reserve
    # the top title band and lower subtitle band unless visual analysis adds
    # more precise protected_zones to this B-roll segment.
    zones = [(-1.0, 0.44, 1.0, 1.0), (-1.0, -1.0, 1.0, -0.36)]
    pip = segment.get("speaker_pip", {})
    raw = pip.get("protected_zones", []) if isinstance(pip, dict) else []
    for zone in raw:
        if not isinstance(zone, dict):
            continue
        try:
            item = tuple(float(zone[key]) for key in ("left", "bottom", "right", "top"))
        except (KeyError, TypeError, ValueError):
            continue
        if -1 <= item[0] < item[2] <= 1 and -1 <= item[1] < item[3] <= 1:
            zones.append(item)
    return zones


def position_candidates(head: dict, width: int, height: int, scale: float, zones: list[tuple[float, float, float, float]]) -> list[dict]:
    center_x = (head["x"] + head["width"] / 2) / width * 2 - 1
    center_y = (head["y"] + head["height"] / 2) / height * 2 - 1
    radius = VISIBLE_DIAMETER_RATIO / 2
    candidates = []
    for position, (anchor_x, anchor_y) in PIP_ANCHORS.items():
        circle = (anchor_x - radius, anchor_y - radius, anchor_x + radius, anchor_y + radius)
        conflicts = [zone for zone in zones if overlap(circle, zone)]
        # JianYing's transform direction is opposite to the source-space face
        # offset; addition keeps the head at the selected canvas anchor.
        transform_x = clamp(anchor_x + center_x * scale, -0.90, 0.90)
        transform_y = clamp(anchor_y + center_y * scale, -0.90, 0.90)
        candidates.append({
            "position": position, "face_anchor_x": anchor_x, "face_anchor_y": anchor_y,
            "placement_transform_x": round(transform_x, 4), "placement_transform_y": round(transform_y, 4),
            "circle_bounds": {"left": round(circle[0], 4), "bottom": round(circle[1], 4), "right": round(circle[2], 4), "top": round(circle[3], 4)},
            "safe": not conflicts, "conflicts": len(conflicts),
        })
    return candidates


def choose_safe_candidate(candidates: list[dict], requested_position: str) -> dict | None:
    """Prefer a middle-side placement so native top titles remain visible."""
    side = "left" if "left" in requested_position else "right"
    order = (f"middle_{side}", f"middle_{'right' if side == 'left' else 'left'}", f"upper_{side}", f"upper_{'right' if side == 'left' else 'left'}")
    by_position = {item["position"]: item for item in candidates}
    return next((by_position[position] for position in order if by_position.get(position, {}).get("safe")), None)


def read_visual_decisions(path: Path | None) -> dict[int, dict]:
    """Load optional model/human review decisions keyed by B-roll segment."""
    if path is None:
        return {}
    payload = load_json(path)
    if payload.get("status") not in {"approved", "succeeded"}:
        raise ValueError("PiP visual decisions are not approved")
    decisions = payload.get("decisions", [])
    if not isinstance(decisions, list):
        raise ValueError("PiP visual decisions require a decisions array")
    result: dict[int, dict] = {}
    for item in decisions:
        if not isinstance(item, dict):
            raise ValueError("each PiP visual decision must be an object")
        index = int(item.get("segment_index", 0))
        if index <= 0 or index in result:
            raise ValueError("PiP visual decisions require unique positive segment_index values")
        if item.get("status") not in {"approved", "rejected"}:
            raise ValueError("PiP visual decision status must be approved or rejected")
        result[index] = item
    return result


def canvas_frame(frame, width: int = 1080, height: int = 1920):
    """Cover a vertical canvas to match JianYing's full-frame B-roll behavior."""
    import cv2
    source_height, source_width = frame.shape[:2]
    scale = max(width / source_width, height / source_height)
    resized = cv2.resize(frame, (round(source_width * scale), round(source_height * scale)))
    x = max(0, (resized.shape[1] - width) // 2)
    y = max(0, (resized.shape[0] - height) // 2)
    return resized[y:y + height, x:x + width].copy()


def add_circle_preview(canvas, speaker_frame, head: dict, anchor_x: float, anchor_y: float) -> None:
    """Composite a conservative complete-head circle at the candidate anchor."""
    import cv2
    source_height, source_width = speaker_frame.shape[:2]
    side = max(float(head["width"]), float(head["height"])) * 1.18
    center_x = float(head["x"]) + float(head["width"]) / 2
    center_y = float(head["y"]) + float(head["height"]) / 2
    left = int(clamp(round(center_x - side / 2), 0, source_width - 1))
    top = int(clamp(round(center_y - side / 2), 0, source_height - 1))
    right = int(clamp(round(center_x + side / 2), left + 1, source_width))
    bottom = int(clamp(round(center_y + side / 2), top + 1, source_height))
    crop = speaker_frame[top:bottom, left:right]
    diameter = round(min(canvas.shape[0], canvas.shape[1]) * VISIBLE_DIAMETER_RATIO)
    circle = cv2.resize(crop, (diameter, diameter))
    mask = cv2.circle(circle[:, :, 0] * 0, (diameter // 2, diameter // 2), diameter // 2 - 3, 255, -1)
    target_x = round((anchor_x + 1) * canvas.shape[1] / 2)
    target_y = round((1 - anchor_y) * canvas.shape[0] / 2)
    left = max(0, min(canvas.shape[1] - diameter, target_x - diameter // 2))
    top = max(0, min(canvas.shape[0] - diameter, target_y - diameter // 2))
    region = canvas[top:top + diameter, left:left + diameter]
    region[mask > 0] = circle[mask > 0]
    cv2.circle(canvas, (left + diameter // 2, top + diameter // 2), diameter // 2 - 2, (255, 255, 255), 4)


def write_candidate_previews(
    speaker_capture,
    plan: dict,
    review: dict,
    preview_dir: Path,
) -> list[dict]:
    """Render candidate PiP placements over actual B-roll frames for vision review."""
    import cv2
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_paths: list[dict] = []
    segments = plan.get("segments", [])
    for finding in review.get("pip_reviews", []):
        index = int(finding["segment_index"])
        segment = segments[index - 1]
        video = Path(str(segment.get("video", "")))
        if not video.is_file():
            finding["preview_error"] = "B-roll source file is unavailable"
            continue
        speaker_capture.set(cv2.CAP_PROP_POS_MSEC, (float(finding["final_start"]) + float(finding["final_duration"]) / 2) * 1000)
        speaker_ok, speaker_frame = speaker_capture.read()
        broll_capture = cv2.VideoCapture(str(video))
        broll_capture.set(cv2.CAP_PROP_POS_MSEC, (float(segment.get("source_start", 0)) + float(segment.get("duration", 0)) / 2) * 1000)
        broll_ok, broll_frame = broll_capture.read()
        broll_capture.release()
        if not speaker_ok or not broll_ok:
            finding["preview_error"] = "Unable to read the representative speaker or B-roll frame"
            continue
        base = canvas_frame(broll_frame)
        preview_paths_for_segment = []
        for candidate in finding.get("candidates", []):
            image = base.copy()
            for zone in protected_zones(segment):
                left, bottom, right, top = zone
                cv2.rectangle(
                    image,
                    (round((left + 1) * image.shape[1] / 2), round((1 - top) * image.shape[0] / 2)),
                    (round((right + 1) * image.shape[1] / 2), round((1 - bottom) * image.shape[0] / 2)),
                    (0, 0, 255),
                    3,
                )
            add_circle_preview(image, speaker_frame, finding["head_envelope"], float(candidate["face_anchor_x"]), float(candidate["face_anchor_y"]))
            label = f"{candidate['position']} {'SAFE' if candidate['safe'] else 'BLOCKED'}"
            cv2.putText(image, label, (36, image.shape[0] - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 3, cv2.LINE_AA)
            path = preview_dir / f"pip_{index:02d}_{candidate['position']}.jpg"
            encoded, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if encoded:
                buffer.tofile(str(path))
                candidate["preview_path"] = str(path.resolve())
                preview_paths_for_segment.append(str(path.resolve()))
        finding["preview_paths"] = preview_paths_for_segment
        preview_paths.extend({"segment_index": index, "path": path} for path in preview_paths_for_segment)
    return preview_paths


def apply_visual_decisions(review: dict, plan: dict, decisions: dict[int, dict], mode: str) -> None:
    """Let visual review approve, move, or reject only machine-safe candidates.

    Face detection and caption/title collision checks are only a candidate
    generator. They cannot determine whether a B-roll's medical diagram,
    product, or action is the information that must remain visible. Therefore
    no requested PiP may reach draft assembly until a visual decision explicitly
    approves one candidate.
    """
    for finding in review.get("pip_reviews", []):
        decision = decisions.get(int(finding["segment_index"]))
        if decision is None:
            finding["visual_review_status"] = "missing"
            finding["status"] = "rejected"
            finding["reason"] = "No visual approval was supplied for this requested PiP segment."
            continue
        finding["visual_review_status"] = decision["status"]
        finding["visual_review_reason"] = str(decision.get("reason", ""))
        if decision["status"] != "approved":
            finding["status"] = "rejected"
            finding["reason"] = "Visual review rejected this PiP placement."
            continue
        requested_position = str(decision.get("position", finding.get("selected_candidate", {}).get("position", "")))
        candidate = next((item for item in finding.get("candidates", []) if item["position"] == requested_position and item["safe"]), None)
        if candidate is None:
            finding["status"] = "rejected"
            finding["reason"] = "Visual review selected a missing or protected-zone-conflicting candidate."
            continue
        finding["selected_candidate"] = candidate
        finding["status"] = "approved"
        finding["reason"] = "A complete-head crop and collision-free placement passed visual review."


def resolve_broll_plan(plan: dict, review: dict) -> dict:
    """Disable rejected PiP requests before draft assembly; PiP is optional."""
    resolved = json.loads(json.dumps(plan))
    by_index = {int(item["segment_index"]): item for item in review.get("pip_reviews", [])}
    for index, segment in enumerate(resolved.get("segments", []), start=1):
        pip = segment.get("speaker_pip")
        if pip is True:
            pip = {"enabled": True}
            segment["speaker_pip"] = pip
        if not isinstance(pip, dict):
            continue
        finding = by_index.get(index)
        approved = finding if finding and finding.get("status") == "approved" else None
        pip["enabled"] = bool(approved)
        pip.pop("mode", None)
        if approved:
            candidate = approved["selected_candidate"]
            pip["position"] = candidate["position"]
            for key in ("scale", "face_center_x", "face_center_y", "mask_size", "head_envelope"):
                if key in approved:
                    pip[key] = approved[key]
            for key in ("placement_transform_x", "placement_transform_y"):
                if key in candidate:
                    pip[key] = candidate[key]
    return resolved


def detect_review(
    video: Path,
    broll_plan: Path,
    mode: str = "auto",
    preview_dir: Path | None = None,
    visual_decisions: Path | None = None,
) -> dict:
    if mode == "off":
        return {"version": 3, "mode": mode, "pip_reviews": [], "skipped": "disabled_by_mode", "errors": [], "status": "skipped"}
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("OpenCV is required for PiP face framing; install opencv-python") from error
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
    reviews, errors, requested = [], [], 0
    for index, segment in enumerate(segments, start=1):
        pip = segment.get("speaker_pip", {}) if isinstance(segment, dict) else {}
        requested_here = isinstance(pip, dict) and pip.get("mode", pip.get("enabled", False)) not in {False, "off", "disabled"}
        if not requested_here:
            continue
        requested += 1
        start, duration = float(segment.get("start", -1)), float(segment.get("duration", 0))
        detections = []
        for timestamp in sampled_times(start, duration):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            boxes = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=6, minSize=(max(72, width // 18), max(72, height // 18)))
            chosen = choose_face([tuple(map(int, box)) for box in boxes], width, height)
            if chosen:
                x, y, w, h = chosen
                detections.append({"timestamp": round(timestamp, 3), "bbox": {"x": x, "y": y, "width": w, "height": h}})
        detections = consistent_detections(detections)
        if len(detections) < 2:
            errors.append(f"PiP segment {index} has only {len(detections)} reliable face detections")
            continue
        boxes = [item["bbox"] for item in detections]
        face = {key: median(item[key] for item in boxes) for key in ("x", "y", "width", "height")}
        head = head_envelope(face, width, height)
        mask_size = clamp(float(face["height"]) / height / FACE_FILL_RATIO, 0.22, 0.38)
        scale = clamp(VISIBLE_DIAMETER_RATIO / mask_size, 0.42, 1.05)
        candidates = position_candidates(head, width, height, scale, protected_zones(segment))
        selected = choose_safe_candidate(candidates, str(pip.get("position", "middle_right")))
        reviews.append({
            "segment_index": index, "final_start": round(start, 3), "final_duration": round(duration, 3),
            "source_visual": str(video.resolve()), "frame_size": {"width": width, "height": height},
            "detections": detections, "face_bbox": {key: round(value, 2) for key, value in face.items()},
            "head_envelope": head,
            "face_center_x": round((head["x"] + head["width"] / 2) / width * 2 - 1, 4),
            "face_center_y": round((head["y"] + head["height"] / 2) / height * 2 - 1, 4),
            "face_fill_ratio": FACE_FILL_RATIO, "mask_size": round(mask_size, 4),
            "scale": round(scale, 4), "visible_diameter_ratio": VISIBLE_DIAMETER_RATIO,
            "candidates": candidates, "selected_candidate": selected,
            "status": "approved" if selected else "rejected",
            "reason": "Complete-head crop and a collision-free middle-side position were selected." if selected else "No candidate avoids protected zones.",
        })
    if preview_dir:
        review_stub = {"pip_reviews": reviews}
        preview_paths = write_candidate_previews(capture, plan, review_stub, preview_dir)
    else:
        preview_paths = []
    decisions = read_visual_decisions(visual_decisions)
    apply_visual_decisions({"pip_reviews": reviews}, plan, decisions, mode)
    capture.release()
    if not requested:
        return {"version": 3, "mode": mode, "pip_reviews": [], "skipped": "no_segment_requested_pip", "errors": [], "status": "skipped"}
    if mode == "require" and (errors or any(item["status"] != "approved" for item in reviews)):
        return {"version": 3, "mode": mode, "pip_reviews": reviews, "preview_paths": preview_paths, "errors": errors + ["one or more PiP segments have no safe visual-reviewed candidate"], "status": "failed"}
    return {"version": 3, "mode": mode, "pip_reviews": reviews, "preview_paths": preview_paths, "errors": errors, "status": "succeeded"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visual", type=Path, required=True)
    parser.add_argument("--broll-plan", type=Path, required=True)
    parser.add_argument("--mode", choices=("auto", "off", "require"), default="auto")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolved-broll-plan", type=Path, help="B-roll plan with unsafe optional PiP segments disabled")
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--visual-decisions", type=Path, help="Optional approved model/human visual decisions for the generated candidate previews")
    args = parser.parse_args()
    try:
        original_plan = load_json(args.broll_plan.resolve())
        report = detect_review(args.visual.resolve(), args.broll_plan.resolve(), args.mode, args.preview_dir, args.visual_decisions.resolve() if args.visual_decisions else None)
        if args.resolved_broll_plan:
            args.resolved_broll_plan.parent.mkdir(parents=True, exist_ok=True)
            args.resolved_broll_plan.write_text(json.dumps(resolve_broll_plan(original_plan, report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        report = {"status": "failed", "errors": [str(error)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
