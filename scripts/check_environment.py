#!/usr/bin/env python3
"""Check tools, JianYing, and Skill prerequisites for this workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Iterable


REQUIRED_SKILLS = [
    "video-understand",
    "media-role-director",
    "talking-head-rough-cut",
    "jianying-asset-director",
    "jianying-editor",
]


def skill_roots(explicit: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if explicit:
        roots.append(explicit.expanduser())
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        roots.append(Path(codex_home).expanduser() / "skills")
    roots.extend([Path.home() / ".codex" / "skills", Path.home() / ".agents" / "skills"])
    return list(dict.fromkeys(roots))


def find_skill(name: str, roots: Iterable[Path]) -> str | None:
    for root in roots:
        if root.name == name and (root / "SKILL.md").exists():
            return str(root)
        candidate = root / name / "SKILL.md"
        if candidate.exists():
            return str(candidate.parent)
    return None


def find_jianying() -> str | None:
    explicit = os.environ.get("JIANYING_EXE", "").strip()
    candidates = [Path(explicit)] if explicit else []
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(Path(local_app_data) / "JianyingPro" / "Apps" / "JianyingPro.exe")
    candidates.extend([Path(r"C:\Program Files\JianyingPro\JianYingPro.exe"), Path(r"C:\Program Files (x86)\JianyingPro\JianYingPro.exe")])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Check JianYing workflow prerequisites")
    parser.add_argument("--skills-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    roots = skill_roots(args.skills_dir)
    tools = {name: shutil.which(name) for name in ["python", "ffmpeg", "ffprobe"]}
    skills = {name: find_skill(name, roots) for name in REQUIRED_SKILLS}
    jianying = find_jianying()
    missing = [*[name for name, value in tools.items() if not value], *[name for name, value in skills.items() if not value], *([] if jianying else ["JianYingPro"])]
    result = {
        "status": "succeeded" if not missing else "failed",
        "tools": tools,
        "skills": skills,
        "jianying": jianying,
        "skills_roots": [str(root) for root in roots],
        "missing": missing,
    }
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output if args.json else "RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
