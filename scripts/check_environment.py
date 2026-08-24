#!/usr/bin/env python3
"""Check tools, JianYing, and sibling Codex Skills for this workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Dict, Iterable, List


def skill_roots(explicit: Path | None = None) -> List[Path]:
    roots: List[Path] = []
    if explicit:
        roots.append(explicit.expanduser())
    skill_root = os.environ.get("JY_SKILL_ROOT", "").strip()
    if skill_root:
        roots.append(Path(skill_root).expanduser())
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        roots.append(Path(codex_home).expanduser() / "skills")
    roots.extend([
        Path.home() / ".codex" / "skills",
        Path.home() / ".agents" / "skills",
    ])
    result: List[Path] = []
    for root in roots:
        if root not in result:
            result.append(root)
    return result


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
    candidates.extend([
        Path(r"C:\Program Files\JianyingPro\JianyingPro.exe"),
        Path(r"C:\Program Files (x86)\JianyingPro\JianyingPro.exe"),
    ])
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Check JianYing workflow prerequisites")
    parser.add_argument("--skills-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    roots = skill_roots(args.skills_dir)
    tools: Dict[str, str | None] = {name: shutil.which(name) for name in ["python", "git", "ffmpeg", "ffprobe"]}
    skills = {name: find_skill(name, roots) for name in ["video-understand", "jianying-asset-director", "jianying-editor"]}
    result = {
        "status": "succeeded" if all(tools.values()) and all(skills.values()) and find_jianying() else "failed",
        "tools": tools,
        "skills": skills,
        "jianying": find_jianying(),
        "skills_roots": [str(root) for root in roots],
        "missing": [
            *[name for name, value in tools.items() if not value],
            *[name for name, value in skills.items() if not value],
            *([] if find_jianying() else ["JianYingPro"]),
        ],
    }
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output if args.json else "RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "succeeded" else 2


if __name__ == "__main__":
    raise SystemExit(main())
