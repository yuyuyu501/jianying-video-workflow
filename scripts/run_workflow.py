#!/usr/bin/env python3
"""Prepare analysis, rough-cut, and JianYing asset-plan handoff artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


INTERNAL_SKILLS = {"talking-head-rough-cut", "jianying-asset-director"}


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the JianYing content-to-draft workflow")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--srt", type=Path)
    parser.add_argument("--beats", type=Path)
    parser.add_argument("--style", default="medical_education")
    parser.add_argument("--semantic-exclusions", type=Path, help="Reviewed rough-cut semantic exclusions JSON")
    parser.add_argument("--render-rough-cut", action="store_true", help="Render the accepted rough-cut plan")
    parser.add_argument("--rough-cut-quality", choices=("preview", "final"), default="preview")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.video.is_file():
        raise FileNotFoundError(args.video)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    skills = {name: locate_skill(name) for name in ["video-understand", "talking-head-rough-cut", "jianying-asset-director", "jianying-editor"]}
    missing = [name for name, skill in skills.items() if skill is None]
    if missing:
        print("RESULT: " + json.dumps({"status": "incomplete", "missing_skills": missing, "output_dir": str(args.output_dir)}, ensure_ascii=False))
        return 2

    understanding_json = args.output_dir / "understanding.json"
    run([sys.executable, str(skills["video-understand"] / "scripts" / "understand_video.py"), str(args.video), "-o", str(understanding_json), "-q"])

    rough_cut_plan = args.output_dir / "rough_cut_plan.json"
    plan_command = [
        sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "plan",
        "--video", str(args.video), "--analysis", str(understanding_json), "--output", str(rough_cut_plan),
    ]
    if args.semantic_exclusions:
        plan_command.extend(["--semantic-exclusions", str(args.semantic_exclusions)])
    run(plan_command)

    working_video = args.video
    rough_cut_preview = None
    if args.render_rough_cut:
        rough_cut_preview = args.output_dir / "rough_cut_preview.mp4"
        run([
            sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "render",
            "--plan", str(rough_cut_plan), "--output", str(rough_cut_preview),
            "--quality", args.rough_cut_quality, "--overwrite",
        ])
        run([
            sys.executable, str(skills["talking-head-rough-cut"] / "scripts" / "rough_cut.py"), "validate",
            "--plan", str(rough_cut_plan), "--output", str(rough_cut_preview),
        ])
        working_video = rough_cut_preview

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
        "source_video": str(args.video),
        "working_video": str(working_video),
        "understanding": str(understanding_json),
        "rough_cut_plan": str(rough_cut_plan),
        "rough_cut_preview": str(rough_cut_preview) if rough_cut_preview else None,
        "asset_catalog": str(catalog_json),
        "asset_plan": str(asset_plan) if asset_plan else None,
        "next_step": "Review rough-cut semantic candidates and the asset plan before creating a JianYing draft.",
    }
    print("RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
