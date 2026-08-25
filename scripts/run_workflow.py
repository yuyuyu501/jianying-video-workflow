#!/usr/bin/env python3
"""Prepare analysis, rough-cut, and JianYing asset-plan handoff artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


INTERNAL_SKILLS = {"talking-head-rough-cut", "media-role-director", "jianying-asset-director"}


def locate_skill(name: str) -> Path | None:
    repo_root = Path(__file__).resolve().parents[1]
    internal = repo_root / "skills" / name
    if name in INTERNAL_SKILLS and (internal / "SKILL.md").exists():
        return internal
    roots: list[Path] = []
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        roots.append(Path(codex_home).expanduser() / "skills")
    roots.extend([Path.home() / ".codex" / "skills", Path.home() / ".agents" / "skills"])
    for root in dict.fromkeys(roots):
        candidate = root / name
        if (candidate / "SKILL.md").exists():
            return candidate
    return None


def run(command: list[str]) -> None:
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    subprocess.run(command, check=True, env=environment)


def read_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_exclusions(path: Path | None, source_id: str, speech_source_count: int, output_dir: Path) -> Path | None:
    if not path:
        return None
    payload = read_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("sources"), dict):
        value = payload["sources"].get(source_id, [])
    elif speech_source_count == 1:
        value = payload
    else:
        raise ValueError(
            "Multiple speech sources require semantic exclusions shaped as "
            "{\"sources\": {\"source_id\": [...]}}"
        )
    destination = output_dir / f"{source_id}.semantic-exclusions.json"
    write_json(destination, value)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a reviewed multi-source JianYing content-to-draft workflow")
    parser.add_argument("--video", type=Path, action="append", required=True, help="Input video; repeat for each source asset")
    parser.add_argument("--srt", type=Path, help="Externally reviewed global SRT; requires --speech-timeline and is always caption-QC checked")
    parser.add_argument("--speech-timeline", type=Path, help="Required companion JSON for --srt")
    parser.add_argument("--beats", type=Path)
    parser.add_argument("--style", default="medical_education")
    parser.add_argument("--media-decisions", type=Path, help="Reviewed media-role-director decisions JSON")
    parser.add_argument("--semantic-exclusions", type=Path, help="Reviewed cuts; use a per-source mapping when multiple narration sources exist")
    parser.add_argument("--reference-script", type=Path, help="Approved script/copy used to audit off-topic and repeated speech")
    parser.add_argument("--allow-no-reference-script", action="store_true", help="Explicitly allow caption QC without script-completeness checks")
    parser.add_argument("--render-rough-cut", action="store_true", help="Render every approved narration rough cut and then generate global captions")
    parser.add_argument("--skip-captions", action="store_true", help="Do not generate captions after rendering rough cuts")
    parser.add_argument("--rough-cut-quality", choices=("preview", "final"), default="preview")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    videos = [video.resolve() for video in args.video]
    missing_videos = [str(video) for video in videos if not video.is_file()]
    if missing_videos:
        raise FileNotFoundError(", ".join(missing_videos))
    if args.skip_captions and not args.render_rough_cut:
        raise ValueError("--skip-captions only applies with --render-rough-cut")
    if bool(args.srt) != bool(args.speech_timeline):
        raise ValueError("--srt and --speech-timeline must be supplied together")
    for path in (args.srt, args.speech_timeline):
        if path and not path.is_file():
            raise FileNotFoundError(path)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    skills = {name: locate_skill(name) for name in ["video-understand", "media-role-director", "talking-head-rough-cut", "jianying-asset-director", "jianying-editor"]}
    missing = [name for name, skill in skills.items() if skill is None]
    if missing:
        print("RESULT: " + json.dumps({"status": "incomplete", "missing_skills": missing, "output_dir": str(args.output_dir)}, ensure_ascii=False))
        return 2

    analysis_dir = args.output_dir / "analysis"
    analyses: list[Path] = []
    for index, video in enumerate(videos, start=1):
        analysis = analysis_dir / f"source_{index:02d}_{video.stem}.json"
        run([sys.executable, str(skills["video-understand"] / "scripts" / "understand_video.py"), str(video), "-o", str(analysis), "-q"])
        analyses.append(analysis)

    intake_path = args.output_dir / "media_intake.json"
    intake_command = [sys.executable, str(skills["media-role-director"] / "scripts" / "media_role_director.py"), "intake"]
    for video, analysis in zip(videos, analyses):
        intake_command.extend(["--video", str(video), "--analysis", str(analysis)])
    intake_command.extend(["--output", str(intake_path)])
    run(intake_command)

    if not args.media_decisions:
        print("RESULT: " + json.dumps({
            "status": "review_required",
            "media_intake": str(intake_path),
            "analysis": [str(path) for path in analyses],
            "next_step": "Inspect all source transcripts and frames, then provide --media-decisions to classify role, audio policy, and narration order.",
        }, ensure_ascii=False))
        return 0
    if not args.media_decisions.is_file():
        raise FileNotFoundError(args.media_decisions)
    if args.reference_script and not args.reference_script.is_file():
        raise FileNotFoundError(args.reference_script)
    captions_requested = bool(args.srt) or (args.render_rough_cut and not args.skip_captions)
    if captions_requested and not args.reference_script and not args.allow_no_reference_script:
        raise ValueError("--reference-script is required for caption completeness QC; pass --allow-no-reference-script only when no approved copy exists")

    manifest_path = args.output_dir / "media_manifest.json"
    run([
        sys.executable, str(skills["media-role-director"] / "scripts" / "media_role_director.py"), "apply-decisions",
        "--intake", str(intake_path), "--decisions", str(args.media_decisions.resolve()), "--output", str(manifest_path),
    ])
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("media manifest is not an object")
    unresolved = manifest.get("unresolved_sources", [])
    if unresolved:
        print("RESULT: " + json.dumps({
            "status": "review_required",
            "media_manifest": str(manifest_path),
            "unresolved_sources": unresolved,
            "next_step": "Resolve ambiguous media-role decisions before rough cutting or muting any source.",
        }, ensure_ascii=False))
        return 0

    sources = manifest.get("sources", [])
    speech_sources = [source for source in sources if source.get("decision", {}).get("requires_rough_cut")]
    if not speech_sources:
        print("RESULT: " + json.dumps({
            "status": "incomplete",
            "media_manifest": str(manifest_path),
            "reason": "No keep_original narration sources were selected; no final speech timeline can be built.",
        }, ensure_ascii=False))
        return 2

    rough_cut_dir = args.output_dir / "rough-cuts"
    plans: dict[str, Path] = {}
    previews: dict[str, Path] = {}
    visuals: dict[str, Path] = {}
    narrations: dict[str, Path] = {}
    qc_reports: dict[str, Path] = {}
    for source in speech_sources:
        source_id = str(source["id"])
        plan = rough_cut_dir / f"{source_id}.plan.json"
        plan_command = [
            sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "plan",
            "--video", str(source["video"]), "--analysis", str(source["analysis"]), "--output", str(plan),
        ]
        if args.reference_script:
            plan_command.extend(["--reference-script", str(args.reference_script.resolve())])
        exclusions = source_exclusions(args.semantic_exclusions, source_id, len(speech_sources), rough_cut_dir)
        if exclusions:
            plan_command.extend(["--semantic-exclusions", str(exclusions)])
        run(plan_command)
        plans[source_id] = plan
        if args.render_rough_cut:
            preview = rough_cut_dir / f"{source_id}.mp4"
            visual = rough_cut_dir / f"{source_id}.visual.mp4"
            narration = rough_cut_dir / f"{source_id}.narration.m4a"
            qc_report = rough_cut_dir / f"{source_id}.qc.json"
            run([
                sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "render",
                "--plan", str(plan), "--output", str(preview), "--visual-output", str(visual),
                "--narration-output", str(narration), "--quality", args.rough_cut_quality, "--overwrite",
            ])
            run([
                sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "validate",
                "--plan", str(plan), "--output", str(preview), "--visual", str(visual),
                "--narration", str(narration), "--qc-output", str(qc_report),
            ])
            previews[source_id] = preview
            visuals[source_id] = visual
            narrations[source_id] = narration
            qc_reports[source_id] = qc_report

    if not args.render_rough_cut:
        print("RESULT: " + json.dumps({
            "status": "review_required",
            "media_manifest": str(manifest_path),
            "rough_cut_plans": {source_id: str(plan) for source_id, plan in plans.items()},
            "next_step": "Render each approved rough-cut plan into silent visual, narration, and review artifacts, then pass rough-cut QC before captions or draft creation.",
        }, ensure_ascii=False))
        return 0

    attach_command = [
        sys.executable, str(skills["media-role-director"] / "scripts" / "media_role_director.py"), "attach-rough-cuts",
        "--manifest", str(manifest_path),
    ]
    for source_id, plan in plans.items():
        attach_command.extend(["--rough-cut", f"{source_id}={plan}"])
        if args.render_rough_cut:
            attach_command.extend([
                "--rough-cut-visual", f"{source_id}={visuals[source_id]}",
                "--rough-cut-narration", f"{source_id}={narrations[source_id]}",
                "--rough-cut-review", f"{source_id}={previews[source_id]}",
                "--rough-cut-qc", f"{source_id}={qc_reports[source_id]}",
            ])
    run(attach_command)

    generated_srt = None
    speech_timeline = None
    caption_qc = None
    if args.srt:
        captions_dir = args.output_dir / "captions"
        generated_srt = args.srt.resolve()
        speech_timeline = args.speech_timeline.resolve()
        caption_qc_command = [
            sys.executable, str(skills["media-role-director"] / "scripts" / "media_role_director.py"), "caption-qc",
            "--srt", str(generated_srt), "--speech-timeline", str(speech_timeline),
            "--output", str(captions_dir / "captions.qc.json"),
        ]
        if args.reference_script:
            caption_qc_command.extend(["--reference-script", str(args.reference_script.resolve())])
        run(caption_qc_command)
        caption_qc = captions_dir / "captions.qc.json"
    elif args.render_rough_cut and not args.skip_captions:
        captions_dir = args.output_dir / "captions"
        captions_command = [
            sys.executable, str(skills["media-role-director"] / "scripts" / "media_role_director.py"), "captions",
            "--manifest", str(manifest_path), "--output-dir", str(captions_dir),
        ]
        if args.reference_script:
            captions_command.extend(["--reference-script", str(args.reference_script.resolve())])
        run(captions_command)
        generated_srt = captions_dir / "captions.srt"
        speech_timeline = captions_dir / "speech_timeline.json"
        caption_qc = captions_dir / "captions.qc.json"

    catalog_json = args.output_dir / "asset_catalog.json"
    run([sys.executable, str(skills["jianying-asset-director"] / "scripts" / "asset_director.py"), "catalog", "--output", str(catalog_json)])
    asset_plan = None
    if args.beats:
        asset_plan = args.output_dir / "asset_plan.json"
        run([
            sys.executable, str(skills["jianying-asset-director"] / "scripts" / "asset_director.py"), "plan",
            "--beats", str(args.beats), "--catalog", str(catalog_json), "--style", args.style, "--output", str(asset_plan),
        ])

    result = {
        "status": "succeeded",
        "sources": [str(video) for video in videos],
        "analysis": [str(path) for path in analyses],
        "media_intake": str(intake_path),
        "media_manifest": str(manifest_path),
        "rough_cut_plans": {source_id: str(plan) for source_id, plan in plans.items()},
        "reference_script": str(args.reference_script.resolve()) if args.reference_script else None,
        "rough_cut_previews": {source_id: str(preview) for source_id, preview in previews.items()},
        "rough_cut_artifacts": {
            source_id: {
                "visual": str(visuals[source_id]),
                "narration": str(narrations[source_id]),
                "review": str(previews[source_id]),
                "qc": str(qc_reports[source_id]),
            }
            for source_id in previews
        },
        "captions_srt": str(generated_srt) if generated_srt else None,
        "speech_timeline": str(speech_timeline) if speech_timeline else None,
        "captions_qc": str(caption_qc) if caption_qc else None,
        "asset_catalog": str(catalog_json),
        "asset_plan": str(asset_plan) if asset_plan else None,
        "next_step": "Use the reviewed media manifest, captions SRT, and speech timeline to prepare timestamped visual beats before creating a JianYing draft-library project.",
    }
    print("RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
