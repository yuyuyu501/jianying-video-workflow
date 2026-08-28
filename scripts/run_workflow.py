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
    parser.add_argument("--broll-plan", type=Path, help="Approved B-roll JSON; every segment is automatically evaluated for optional circular speaker_pip")
    parser.add_argument("--speaker-pip-mode", choices=("auto", "off", "require"), default="auto", help="Speaker PiP review: auto accepts machine-safe candidates and skips unsafe/unavailable segments, off disables the whole stage, require requires visual approval for every evaluated segment")
    parser.add_argument("--speaker-pip-visual-decisions", type=Path, help="Optional approved model/human decisions for generated SpeakerPiP candidate previews; required by --speaker-pip-mode require")
    parser.add_argument("--exported-video", type=Path, help="Optional MP4 manually exported from JianYing for post-export pixel QC")
    parser.add_argument("--export-audit-output", type=Path, help="Optional post-export QC JSON path; defaults to output-dir/export_visual_qc.json")
    parser.add_argument("--prepare-speaker-pip", action="store_true", help="Run only the optional SpeakerPiP analysis/review stage after creating the draft skeleton; it writes review artifacts but does not modify the draft")
    parser.add_argument("--prepare-caption-design", action="store_true", help="Extract representative caption frames and write a semantic caption-plan template for AI review without modifying the draft")
    parser.add_argument("--prepare-sfx-timing", action="store_true", help="Analyze final-timeline beats and write a reviewed SFX opportunity template without modifying the draft")
    parser.add_argument("--av-events", type=Path, help="Prebuilt unified audiovisual event timeline; otherwise the workflow builds one after caption/visual plans are available")
    parser.add_argument("--approved-asset-plan", type=Path, help="AI-approved selected asset plan used only with --materialize-draft")
    parser.add_argument("--approved-visual-treatment-plan", type=Path, help="Approved one-treatment-per-beat editorial plan; gates effect candidate generation and draft materialization")
    parser.add_argument("--approved-sfx-timing-plan", type=Path, help="Approved SFX opportunity plan; gates sound-effect candidate generation and draft materialization")
    parser.add_argument("--approved-effect-visual-review", type=Path, help="Approved per-effect pixel observations from a real JianYing render")
    parser.add_argument("--effect-review-video", type=Path, help="Real JianYing-rendered preview/export inspected by --approved-effect-visual-review")
    parser.add_argument("--approved-filter-plan", type=Path, help="AI-approved portrait filter plan used only with --materialize-draft")
    parser.add_argument("--approved-sticker-plan", type=Path, help="AI-approved content-aware sticker plan used only with --materialize-draft")
    parser.add_argument("--approved-caption-plan", type=Path, help="AI-approved semantic and frame-reviewed caption design plan used only with --materialize-draft")
    parser.add_argument("--materialize-draft", action="store_true", help="Write the approved single-speaker plan into the new validated JianYing draft")
    parser.add_argument("--draft-name", help="New JianYing draft name; required with --beats to create the validated track skeleton")
    parser.add_argument("--draft-width", type=int, default=1080)
    parser.add_argument("--draft-height", type=int, default=1920)
    parser.add_argument("--drafts-root", type=Path, help="Optional JianYing draft-library root")
    parser.add_argument("--jianying-python", type=Path, help="Python environment with jianying-editor dependencies; defaults to JY_PYTHON or this interpreter")
    parser.add_argument("--style", default="medical_education")
    parser.add_argument("--media-decisions", type=Path, help="Reviewed media-role-director decisions JSON")
    parser.add_argument("--semantic-exclusions", type=Path, help="Reviewed cuts; use a per-source mapping when multiple narration sources exist")
    parser.add_argument("--reference-script", type=Path, help="Approved script/copy used to audit off-topic and repeated speech")
    parser.add_argument("--caption-review", type=Path, help="Reviewed simplified-Chinese captions timed against rough-cut output; required to create generated captions")
    parser.add_argument("--caption-max-chars", type=int, default=18, help="Maximum non-punctuation characters per final subtitle chunk; commas always form a break")
    parser.add_argument("--allow-no-reference-script", action="store_true", help="Explicitly allow caption QC without script-completeness checks")
    parser.add_argument("--render-rough-cut", action="store_true", help="Render every approved narration rough cut and then generate global captions")
    parser.add_argument("--skip-captions", action="store_true", help="Do not generate captions after rendering rough cuts")
    parser.add_argument("--rough-cut-quality", choices=("preview", "final"), default="preview")
    parser.add_argument("--rough-cut-pace-mode", choices=("auto", "review", "off"), default="auto", help="Judge synchronized rough-cut speed automatically, report it for review, or disable it")
    parser.add_argument("--rough-cut-target-cpm", type=float, default=285.0, help="Target non-punctuation Chinese transcript characters per minute")
    parser.add_argument("--rough-cut-minimum-cpm", type=float, default=260.0, help="Do not speed up rough cuts already at or above this density")
    parser.add_argument("--rough-cut-max-speed", type=float, default=1.35, help="Maximum synchronized audio/video speed chosen by automatic pace analysis")
    parser.add_argument("--approve-rough-cut-pace-review", action="store_true", help="Allow rendering after inspecting a pace decision marked review_required")
    parser.add_argument("--pace-reference-analysis", type=Path, help="Optional video-understand JSON for a template video; its measured speech density becomes the pace target")
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
    if bool(args.beats) != bool(args.draft_name):
        raise ValueError("--beats and --draft-name must be supplied together so analysis starts from a validated draft skeleton")
    if args.materialize_draft and (not args.beats or not args.approved_asset_plan or not args.approved_visual_treatment_plan):
        raise ValueError("--materialize-draft requires --beats, --approved-visual-treatment-plan, and --approved-asset-plan")
    if bool(args.approved_effect_visual_review) != bool(args.effect_review_video):
        raise ValueError("--approved-effect-visual-review and --effect-review-video must be supplied together")
    if args.materialize_draft and (not args.approved_filter_plan or not args.approved_sticker_plan):
        raise ValueError("--materialize-draft requires reviewed --approved-filter-plan and --approved-sticker-plan; an empty approved plan must include a specific skip_reason")
    if args.materialize_draft and not args.approved_caption_plan:
        raise ValueError("--materialize-draft requires --approved-caption-plan after representative-frame semantic/collision review")
    if args.materialize_draft and not args.approved_sfx_timing_plan:
        raise ValueError("--materialize-draft requires --approved-sfx-timing-plan; an empty approved plan must include a specific skip_reason")
    if args.approved_visual_treatment_plan and not args.approved_sfx_timing_plan and not args.prepare_sfx_timing:
        raise ValueError("--approved-visual-treatment-plan requires --approved-sfx-timing-plan unless --prepare-sfx-timing is building the event-linked timing review first")
    if args.prepare_sfx_timing and not args.beats:
        raise ValueError("--prepare-sfx-timing requires --beats")
    if args.prepare_sfx_timing and not (args.av_events or args.approved_caption_plan):
        raise ValueError("--prepare-sfx-timing requires --approved-caption-plan or a prebuilt --av-events timeline so caption/card events are not omitted")
    if args.materialize_draft and not args.render_rough_cut:
        raise ValueError("--materialize-draft requires --render-rough-cut so it can import validated silent visual and narration artifacts")
    if args.prepare_speaker_pip and (not args.render_rough_cut or not args.beats):
        raise ValueError("--prepare-speaker-pip requires --render-rough-cut and --beats so it uses the validated rough-cut timeline and draft skeleton")
    if args.prepare_caption_design and (not args.render_rough_cut or not args.beats):
        raise ValueError("--prepare-caption-design requires --render-rough-cut and --beats so it uses final-timeline frames and captions")
    jianying_python = args.jianying_python or Path(os.environ.get("JY_PYTHON", "").strip() or sys.executable)
    if not jianying_python.is_file():
        raise FileNotFoundError(f"JianYing Python executable not found: {jianying_python}")
    for path in (args.srt, args.speech_timeline, args.caption_review, args.broll_plan, args.av_events, args.approved_asset_plan, args.approved_visual_treatment_plan, args.approved_sfx_timing_plan, args.approved_effect_visual_review, args.effect_review_video, args.approved_filter_plan, args.approved_sticker_plan, args.approved_caption_plan, args.speaker_pip_visual_decisions, args.pace_reference_analysis):
        if path and not path.is_file():
            raise FileNotFoundError(path)
    if args.exported_video and not args.exported_video.is_file():
        raise FileNotFoundError(args.exported_video)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    skills = {name: locate_skill(name) for name in ["video-understand", "media-role-director", "talking-head-rough-cut", "jianying-asset-director", "jianying-editor"]}
    missing = [name for name, skill in skills.items() if skill is None]
    if missing:
        print("RESULT: " + json.dumps({"status": "incomplete", "missing_skills": missing, "output_dir": str(args.output_dir)}, ensure_ascii=False))
        return 2

    analysis_dir = args.output_dir / "analysis"
    # The external video-understand CLI writes directly to its output path.
    # Create the child directory before the first analysis so a clean run is
    # equivalent to a rerun with an existing workspace.
    analysis_dir.mkdir(parents=True, exist_ok=True)
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
    rough_cut_transcripts: dict[str, Path] = {}
    for source in speech_sources:
        source_id = str(source["id"])
        plan = rough_cut_dir / f"{source_id}.plan.json"
        plan_command = [
            sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "plan",
            "--video", str(source["video"]), "--analysis", str(source["analysis"]), "--output", str(plan),
            "--pace-mode", args.rough_cut_pace_mode,
            "--target-cpm", str(args.rough_cut_target_cpm),
            "--minimum-cpm", str(args.rough_cut_minimum_cpm),
            "--max-speed", str(args.rough_cut_max_speed),
        ]
        if args.pace_reference_analysis:
            plan_command.extend(["--pace-reference-analysis", str(args.pace_reference_analysis.resolve())])
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
            render_command = [
                sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "render",
                "--plan", str(plan), "--output", str(preview), "--visual-output", str(visual),
                "--narration-output", str(narration), "--quality", args.rough_cut_quality, "--overwrite",
            ]
            if args.approve_rough_cut_pace_review:
                render_command.append("--approve-pace-review")
            run(render_command)
            run([
                sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "validate",
                "--plan", str(plan), "--output", str(preview), "--visual", str(visual),
                "--narration", str(narration), "--qc-output", str(qc_report),
            ])
            previews[source_id] = preview
            visuals[source_id] = visual
            narrations[source_id] = narration
            qc_reports[source_id] = qc_report
            transcript = args.output_dir / "captions" / "rough-cut-transcripts" / f"{source_id}.json"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            run([
                sys.executable, str(skills["video-understand"] / "scripts" / "understand_video.py"),
                str(preview), "-o", str(transcript), "-q", "--max-frames", "1",
            ])
            rough_cut_transcripts[source_id] = transcript

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
                "--rough-cut-transcript", f"{source_id}={rough_cut_transcripts[source_id]}",
            ])
    run(attach_command)

    generated_srt = None
    speech_timeline = None
    caption_qc = None
    captions_dir = args.output_dir / "captions"
    caption_template = captions_dir / "caption_review.template.json"
    run([
        sys.executable, str(skills["media-role-director"] / "scripts" / "media_role_director.py"), "caption-template",
        "--manifest", str(manifest_path), "--output", str(caption_template),
    ])
    if not args.srt and not args.skip_captions and not args.caption_review:
        print("RESULT: " + json.dumps({
            "status": "review_required",
            "media_manifest": str(manifest_path),
            "rough_cut_transcripts": {source_id: str(path) for source_id, path in rough_cut_transcripts.items()},
            "caption_review_template": str(caption_template),
            "next_step": "Review the actual rough-cut transcript against the rough-cut video and reference script. Fill text in the caption template with simplified Chinese while preserving each rough_cut_start/end, then rerun with --caption-review.",
        }, ensure_ascii=False))
        return 0
    if args.srt:
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
        captions_command = [
            sys.executable, str(skills["media-role-director"] / "scripts" / "media_role_director.py"), "captions",
            "--manifest", str(manifest_path), "--output-dir", str(captions_dir),
        ]
        captions_command.extend(["--caption-review", str(args.caption_review.resolve()), "--max-chars", str(args.caption_max_chars)])
        if args.reference_script:
            captions_command.extend(["--reference-script", str(args.reference_script.resolve())])
        run(captions_command)
        generated_srt = captions_dir / "captions.srt"
        speech_timeline = captions_dir / "speech_timeline.json"
        caption_qc = captions_dir / "captions.qc.json"

    skeleton_draft = None
    skeleton_qc = None
    if args.beats:
        if generated_srt is None or speech_timeline is None or caption_qc is None:
            raise RuntimeError("draft skeleton creation requires captions and a passed caption QC report")
        skeleton_qc = args.output_dir / "draft_skeleton.qc.json"
        skeleton_command = [
            str(jianying_python.resolve()), str(Path(__file__).resolve().with_name("create_draft_skeleton.py")),
            "--draft-name", args.draft_name,
            "--width", str(args.draft_width), "--height", str(args.draft_height),
            "--output", str(skeleton_qc),
        ]
        if args.drafts_root:
            skeleton_command.extend(["--drafts-root", str(args.drafts_root.resolve())])
        run(skeleton_command)
        skeleton_report = read_json(skeleton_qc)
        if not isinstance(skeleton_report, dict) or skeleton_report.get("status") != "passed":
            raise RuntimeError("draft skeleton validation did not pass")
        skeleton_draft = skeleton_report.get("draft_path")

    catalog_json = args.output_dir / "asset_catalog.json"
    run([sys.executable, str(skills["jianying-asset-director"] / "scripts" / "asset_director.py"), "catalog", "--output", str(catalog_json)])
    asset_plan = None
    visual_treatment_template = None
    visual_treatment_validation = None
    sfx_timing_template = None
    sfx_timing_validation = None
    av_event_timeline = args.av_events.resolve() if args.av_events else None
    if args.beats:
        visual_treatment_template = args.output_dir / "visual_treatment_plan.template.json"
        treatment_template_command = [
            sys.executable, str(skills["jianying-asset-director"] / "scripts" / "visual_treatment_director.py"), "template",
            "--beats", str(args.beats.resolve()), "--output", str(visual_treatment_template),
        ]
        if args.broll_plan:
            treatment_template_command.extend(["--broll-plan", str(args.broll_plan.resolve())])
        run(treatment_template_command)
        if av_event_timeline is None and args.approved_caption_plan:
            av_event_timeline = args.output_dir / "av_event_timeline.json"
            av_command = [
                sys.executable, str(skills["jianying-asset-director"] / "scripts" / "av_event_timeline.py"),
                "--beats", str(args.beats.resolve()),
                "--caption-plan", str(args.approved_caption_plan.resolve()),
                "--output", str(av_event_timeline),
            ]
            if args.broll_plan:
                av_command.extend(["--broll-plan", str(args.broll_plan.resolve())])
            if args.approved_visual_treatment_plan:
                av_command.extend(["--visual-treatment-plan", str(args.approved_visual_treatment_plan.resolve())])
            if args.approved_asset_plan:
                av_command.extend(["--asset-plan", str(args.approved_asset_plan.resolve())])
            if args.approved_sticker_plan:
                av_command.extend(["--sticker-plan", str(args.approved_sticker_plan.resolve())])
            run(av_command)
        sfx_timing_template = args.output_dir / "sfx_timing_plan.template.json"
        sfx_timing_command = [
            sys.executable, str(skills["jianying-asset-director"] / "scripts" / "sfx_timing_director.py"), "template",
            "--beats", str(args.beats.resolve()), "--style", args.style, "--output", str(sfx_timing_template),
        ]
        if av_event_timeline:
            sfx_timing_command.extend(["--av-events", str(av_event_timeline)])
        if args.broll_plan:
            sfx_timing_command.extend(["--broll-plan", str(args.broll_plan.resolve())])
        run(sfx_timing_command)
        if args.approved_sfx_timing_plan:
            sfx_timing_validation = args.output_dir / "sfx_timing_plan.qc.json"
            sfx_validate_command = [
                sys.executable, str(skills["jianying-asset-director"] / "scripts" / "sfx_timing_director.py"), "validate",
                "--plan", str(args.approved_sfx_timing_plan.resolve()), "--beats", str(args.beats.resolve()),
                "--output", str(sfx_timing_validation),
            ]
            if av_event_timeline:
                sfx_validate_command.extend(["--av-events", str(av_event_timeline)])
            run(sfx_validate_command)
            if read_json(sfx_timing_validation).get("valid") is not True:
                raise RuntimeError("SFX timing plan validation did not pass")
        if args.approved_visual_treatment_plan:
            visual_treatment_validation = args.output_dir / "visual_treatment_plan.qc.json"
            run([
                sys.executable, str(skills["jianying-asset-director"] / "scripts" / "visual_treatment_director.py"), "validate",
                "--plan", str(args.approved_visual_treatment_plan.resolve()), "--beats", str(args.beats.resolve()),
                "--output", str(visual_treatment_validation),
            ])
            asset_plan = args.output_dir / "asset_plan.json"
            asset_plan_command = [
                sys.executable, str(skills["jianying-asset-director"] / "scripts" / "asset_director.py"), "plan",
                "--beats", str(args.beats), "--catalog", str(catalog_json), "--style", args.style,
                "--visual-treatments", str(args.approved_visual_treatment_plan.resolve()), "--output", str(asset_plan),
            ]
            if args.approved_sfx_timing_plan:
                asset_plan_command.extend(["--sfx-opportunities", str(args.approved_sfx_timing_plan.resolve())])
            if av_event_timeline:
                asset_plan_command.extend(["--av-events", str(av_event_timeline)])
            run(asset_plan_command)

    finish_catalog = None
    filter_plan_template = None
    sticker_plan_template = None
    if args.beats:
        finish_catalog = args.output_dir / "visual_finish_catalog.json"
        finish_catalog_command = [
            sys.executable, str(Path(__file__).resolve().with_name("visual_finish_director.py")), "catalog",
            "--output", str(finish_catalog),
        ]
        if args.drafts_root:
            finish_catalog_command.extend(["--drafts-root", str(args.drafts_root.resolve())])
        run(finish_catalog_command)
        run([
            sys.executable, str(Path(__file__).resolve().with_name("visual_finish_director.py")), "plan",
            "--beats", str(args.beats.resolve()), "--catalog", str(finish_catalog),
            "--output-dir", str(args.output_dir),
        ])
        filter_plan_template = args.output_dir / "filter_plan.template.json"
        sticker_plan_template = args.output_dir / "sticker_plan.template.json"

    draft_assembly = None
    draft_assembly_qc = None
    pip_visual_review = None
    resolved_broll_plan = None
    caption_layout_review = None
    caption_plan_template = None
    caption_plan_validation = None
    asset_post_assembly_validation = None
    if args.prepare_speaker_pip or args.prepare_caption_design or args.prepare_sfx_timing or args.materialize_draft:
        if len(speech_sources) != 1:
            raise RuntimeError("SpeakerPiP and frame-reviewed caption design currently require exactly one narration source")
        if generated_srt is None or speech_timeline is None or skeleton_draft is None:
            raise RuntimeError("SpeakerPiP/caption design preparation requires generated captions and a validated skeleton")
        source_id = str(speech_sources[0]["id"])
        pip_visual_review = args.output_dir / "pip_visual_review.json"
        resolved_broll_plan = args.output_dir / "broll_plan.resolved.json"
        if args.broll_plan:
            broll_input = args.broll_plan.resolve()
        else:
            broll_input = args.output_dir / "broll_plan.empty.json"
            write_json(broll_input, {"version": 1, "segments": []})
        run([
            sys.executable, str(Path(__file__).resolve().with_name("analyze_pip_faces.py")),
            "--visual", str(visuals[source_id]), "--broll-plan", str(broll_input),
            "--mode", args.speaker_pip_mode, "--output", str(pip_visual_review),
            "--resolved-broll-plan", str(resolved_broll_plan), "--preview-dir", str(args.output_dir / "pip-face-review"),
        ] + (["--visual-decisions", str(args.speaker_pip_visual_decisions.resolve())] if args.speaker_pip_visual_decisions else []))
    if args.prepare_caption_design or args.materialize_draft:
        source_id = str(speech_sources[0]["id"])
        caption_layout_review = args.output_dir / "caption_layout_review.json"
        caption_plan_template = args.output_dir / "caption_plan.template.json"
        run([
            sys.executable, str(Path(__file__).resolve().with_name("analyze_caption_layout.py")),
            "--visual", str(visuals[source_id]), "--captions", str(generated_srt),
            "--broll-plan", str(resolved_broll_plan), "--output", str(caption_layout_review),
        ])
        run([
            sys.executable, str(Path(__file__).resolve().with_name("caption_design_director.py")), "template",
            "--captions", str(generated_srt), "--layout-review", str(caption_layout_review),
            "--output", str(caption_plan_template),
        ])
    if args.materialize_draft:
        source_id = str(speech_sources[0]["id"])
        asset_validation = args.output_dir / "asset_plan.qc.json"
        run([
            sys.executable, str(skills["jianying-asset-director"] / "scripts" / "asset_director.py"), "validate",
            "--plan", str(args.approved_asset_plan.resolve()), "--catalog", str(catalog_json),
            "--output", str(asset_validation),
        ])
        if read_json(asset_validation).get("valid") is not True:
            raise RuntimeError("asset-plan creative coverage or AI-review QC did not pass")
        filter_validation = args.output_dir / "filter_plan.qc.json"
        sticker_validation = args.output_dir / "sticker_plan.qc.json"
        for kind, plan_path, output_path in (
            ("filters", args.approved_filter_plan, filter_validation),
            ("stickers", args.approved_sticker_plan, sticker_validation),
        ):
            run([
                sys.executable, str(Path(__file__).resolve().with_name("visual_finish_director.py")), "validate",
                "--kind", kind, "--plan", str(plan_path.resolve()), "--catalog", str(finish_catalog),
                "--output", str(output_path),
            ])
        draft_assembly_qc = args.output_dir / "draft_assembly.qc.json"
        caption_plan_validation = args.output_dir / "caption_plan.qc.json"
        run([
            sys.executable, str(Path(__file__).resolve().with_name("caption_design_director.py")), "validate",
            "--captions", str(generated_srt), "--plan", str(args.approved_caption_plan.resolve()),
            "--output", str(caption_plan_validation),
        ])
        command = [
            str(jianying_python.resolve()), str(Path(__file__).resolve().with_name("assemble_draft.py")),
            "--draft-name", args.draft_name,
            "--visual", str(visuals[source_id]), "--narration", str(narrations[source_id]),
            "--captions", str(generated_srt), "--broll-plan", str(resolved_broll_plan),
            "--pip-visual-review", str(pip_visual_review),
            "--caption-layout-review", str(caption_layout_review),
            "--caption-plan", str(args.approved_caption_plan.resolve()),
            "--asset-plan", str(args.approved_asset_plan.resolve()), "--output", str(draft_assembly_qc),
            "--filter-plan", str(args.approved_filter_plan.resolve()),
            "--sticker-plan", str(args.approved_sticker_plan.resolve()),
            "--finish-catalog", str(finish_catalog),
            "--rebuild-empty-skeleton",
        ]
        if args.drafts_root:
            command.extend(["--drafts-root", str(args.drafts_root.resolve())])
        run(command)
        assembly_report = read_json(draft_assembly_qc)
        if not isinstance(assembly_report, dict) or assembly_report.get("status") != "succeeded":
            raise RuntimeError("draft assembly QC did not pass")
        draft_assembly = assembly_report.get("draft_path")
        asset_post_assembly_validation = args.output_dir / "asset_plan.post_assembly.qc.json"
        post_asset_command = [
            sys.executable, str(skills["jianying-asset-director"] / "scripts" / "asset_director.py"), "validate",
            "--plan", str(args.approved_asset_plan.resolve()), "--catalog", str(catalog_json),
            "--draft", str(Path(draft_assembly).resolve()), "--output", str(asset_post_assembly_validation),
        ]
        if args.approved_effect_visual_review:
            post_asset_command.extend([
                "--visual-review", str(args.approved_effect_visual_review.resolve()),
                "--review-video", str(args.effect_review_video.resolve()),
            ])
        run(post_asset_command)
        post_asset_report = read_json(asset_post_assembly_validation)
        if post_asset_report.get("valid") is not True:
            raise RuntimeError("post-assembly effect structure QC did not pass")

    export_audit = None
    if args.exported_video:
        audit_captions = generated_srt or (args.srt.resolve() if args.srt else None)
        audit_broll = resolved_broll_plan or (args.broll_plan.resolve() if args.broll_plan else None)
        if audit_captions is None:
            raise RuntimeError("--exported-video requires generated or supplied captions for post-export QC")
        export_audit = args.export_audit_output or (args.output_dir / "export_visual_qc.json")
        audit_command = [
            sys.executable, str(Path(__file__).resolve().with_name("verify_export.py")),
            "--video", str(args.exported_video.resolve()), "--captions", str(audit_captions), "--output", str(export_audit),
        ]
        if audit_broll:
            audit_command.extend(["--broll-plan", str(audit_broll)])
        run(audit_command)
        export_report = read_json(export_audit)
        if export_report.get("status") != "succeeded":
            raise RuntimeError("post-export pixel QC did not pass; inspect export_visual_qc.json")

    workflow_status = "succeeded"
    if args.beats and not args.approved_visual_treatment_plan:
        workflow_status = "review_required"
    if args.materialize_draft and read_json(asset_post_assembly_validation).get("workflow_status") == "effect_visual_review_required":
        workflow_status = "review_required"
    result = {
        "status": workflow_status,
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
        "rough_cut_transcripts": {source_id: str(path) for source_id, path in rough_cut_transcripts.items()},
        "caption_review_template": str(caption_template),
        "captions_srt": str(generated_srt) if generated_srt else None,
        "speech_timeline": str(speech_timeline) if speech_timeline else None,
        "captions_qc": str(caption_qc) if caption_qc else None,
        "draft_skeleton": skeleton_draft,
        "draft_skeleton_qc": str(skeleton_qc) if skeleton_qc else None,
        "asset_catalog": str(catalog_json),
        "asset_plan": str(asset_plan) if asset_plan else None,
        "asset_plan_qc": str(asset_validation) if args.materialize_draft else None,
        "asset_plan_post_assembly_qc": str(asset_post_assembly_validation) if asset_post_assembly_validation else None,
        "visual_treatment_plan_template": str(visual_treatment_template) if visual_treatment_template else None,
        "approved_visual_treatment_plan": str(args.approved_visual_treatment_plan.resolve()) if args.approved_visual_treatment_plan else None,
        "visual_treatment_plan_qc": str(visual_treatment_validation) if visual_treatment_validation else None,
        "sfx_timing_plan_template": str(sfx_timing_template) if sfx_timing_template else None,
        "av_event_timeline": str(av_event_timeline) if av_event_timeline else None,
        "approved_sfx_timing_plan": str(args.approved_sfx_timing_plan.resolve()) if args.approved_sfx_timing_plan else None,
        "sfx_timing_plan_qc": str(sfx_timing_validation) if sfx_timing_validation else None,
        "effect_visual_review": str(args.approved_effect_visual_review.resolve()) if args.approved_effect_visual_review else None,
        "effect_review_video": str(args.effect_review_video.resolve()) if args.effect_review_video else None,
        "visual_finish_catalog": str(finish_catalog) if finish_catalog else None,
        "filter_plan_template": str(filter_plan_template) if filter_plan_template else None,
        "sticker_plan_template": str(sticker_plan_template) if sticker_plan_template else None,
        "filter_plan_qc": str(filter_validation) if args.materialize_draft else None,
        "sticker_plan_qc": str(sticker_validation) if args.materialize_draft else None,
        "draft_assembly": draft_assembly,
        "draft_assembly_qc": str(draft_assembly_qc) if draft_assembly_qc else None,
        "pip_visual_review": str(pip_visual_review) if pip_visual_review else None,
        "resolved_broll_plan": str(resolved_broll_plan) if resolved_broll_plan else None,
        "caption_layout_review": str(caption_layout_review) if caption_layout_review else None,
        "caption_plan_template": str(caption_plan_template) if caption_plan_template else None,
        "approved_caption_plan": str(args.approved_caption_plan.resolve()) if args.approved_caption_plan else None,
        "caption_plan_qc": str(caption_plan_validation) if caption_plan_validation else None,
        "speaker_pip_mode": args.speaker_pip_mode,
        "speaker_pip_visual_decisions": str(args.speaker_pip_visual_decisions.resolve()) if args.speaker_pip_visual_decisions else None,
        "exported_video": str(args.exported_video.resolve()) if args.exported_video else None,
        "export_audit": str(export_audit) if export_audit else None,
        "next_step": "Approve the one-treatment-per-beat visual treatment plan first. Then review only the requested effect-family shortlists plus filter, sticker, SpeakerPiP, and caption artifacts. After draft materialization, provide a real JianYing-rendered preview/export and approved per-effect pixel review before final handoff.",
    }
    print("RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
