#!/usr/bin/env python3
"""Prepare analysis and asset-plan artifacts for the Codex workflow."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def locate_skill(name: str) -> Path | None:
    roots = []
    skill_root = os.environ.get("JY_SKILL_ROOT", "").strip()
    if skill_root:
        roots.append(Path(skill_root).expanduser())
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        roots.append(Path(codex_home).expanduser() / "skills")
    roots.extend([Path.home() / ".codex" / "skills", Path.home() / ".agents" / "skills"])
    for root in roots:
        if root.name == name and (root / "SKILL.md").exists():
            return root
        path = root / name
        if (path / "SKILL.md").exists():
            return path
    return None


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the JianYing content-to-draft workflow")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--srt", type=Path)
    parser.add_argument("--beats", type=Path)
    parser.add_argument("--style", default="medical_education")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    if not args.video.exists():
        raise FileNotFoundError(args.video)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    understanding = locate_skill("video-understand")
    director = locate_skill("jianying-asset-director")
    editor = locate_skill("jianying-editor")
    missing = [name for name, value in [("video-understand", understanding), ("jianying-asset-director", director), ("jianying-editor", editor)] if value is None]
    if missing:
        result = {"status": "incomplete", "missing_skills": missing, "output_dir": str(args.output_dir)}
        print("RESULT: " + json.dumps(result, ensure_ascii=False))
        return 2

    understanding_json = args.output_dir / "understanding.json"
    understand_script = understanding / "scripts" / "understand_video.py"
    run([sys.executable, str(understand_script), str(args.video), "-o", str(understanding_json), "-q"])

    catalog_json = args.output_dir / "asset_catalog.json"
    director_script = director / "scripts" / "asset_director.py"
    run([sys.executable, str(director_script), "catalog", "--output", str(catalog_json)])

    plan_json = None
    if args.beats:
        plan_json = args.output_dir / "asset_plan.json"
        run([sys.executable, str(director_script), "plan", "--beats", str(args.beats), "--catalog", str(catalog_json), "--style", args.style, "--output", str(plan_json)])

    result = {
        "status": "succeeded",
        "video": str(args.video),
        "srt": str(args.srt) if args.srt else None,
        "understanding": str(understanding_json),
        "asset_catalog": str(catalog_json),
        "asset_plan": str(plan_json) if plan_json else None,
        "next_step": "Review the analysis and asset plan, then hand the accepted plan to jianying-editor.",
    }
    print("RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
