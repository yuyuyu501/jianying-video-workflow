#!/usr/bin/env python3
"""Install the workflow and its explicit, verified Skill dependencies."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List


ASSET_DIRECTOR_REPO = "git@github.com:yuyuyu501/jianying-asset-director.git"


def skill_roots(explicit: Path | None = None) -> List[Path]:
    roots: List[Path] = []
    if explicit:
        roots.append(explicit.expanduser())
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        roots.append(Path(codex_home).expanduser() / "skills")
    roots.extend([Path.home() / ".codex" / "skills", Path.home() / ".agents" / "skills"])
    return list(dict.fromkeys(roots))


def run(command: List[str]) -> None:
    subprocess.run(command, check=True)


def copy_skill(source: Path, target: Path) -> None:
    if target.exists() and not (target / "SKILL.md").exists():
        raise RuntimeError(f"Refusing to overwrite non-Skill directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))


def discover_skill(root: Path, name: str) -> Path | None:
    direct = root / "SKILL.md"
    if direct.exists():
        return root
    nested = root / name / "SKILL.md"
    if nested.exists():
        return nested.parent
    for candidate in root.glob("*/SKILL.md"):
        if candidate.parent.name == name:
            return candidate.parent
    return None


def install_from_git(name: str, repository: str, target: Path) -> str:
    with tempfile.TemporaryDirectory(prefix=f"{name}-") as temp:
        checkout = Path(temp) / "checkout"
        run(["git", "clone", "--depth", "1", repository, str(checkout)])
        source = discover_skill(checkout, name)
        if source is None:
            raise RuntimeError(f"Repository does not contain a discoverable {name} Skill")
        copy_skill(source, target)
    return str(target)


def existing_skill(name: str, roots: Iterable[Path]) -> str | None:
    for root in roots:
        candidate = root / name
        if (candidate / "SKILL.md").exists():
            return str(candidate)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Install JianYing video workflow Skills")
    parser.add_argument("--skills-dir", type=Path, help="Target Codex skills directory")
    parser.add_argument("--asset-director-repo", default=ASSET_DIRECTOR_REPO)
    parser.add_argument(
        "--upgrade-asset-director",
        action="store_true",
        help="Re-download jianying-asset-director even when it is already installed",
    )
    parser.add_argument("--video-understand-repo", help="Explicit verified Git URL")
    parser.add_argument("--jianying-editor-repo", help="Explicit verified Git URL")
    parser.add_argument("--skip-workflow", action="store_true")
    args = parser.parse_args()

    roots = skill_roots(args.skills_dir)
    target_root = roots[0]
    target_root.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]
    installed = {}
    if not args.skip_workflow and repo_root != target_root / "jianying-video-workflow":
        copy_skill(repo_root, target_root / "jianying-video-workflow")
        installed["jianying-video-workflow"] = str(target_root / "jianying-video-workflow")

    existing_asset = existing_skill("jianying-asset-director", roots)
    if existing_asset and not args.upgrade_asset_director:
        installed["jianying-asset-director"] = existing_asset
    else:
        installed["jianying-asset-director"] = install_from_git(
            "jianying-asset-director", args.asset_director_repo, target_root / "jianying-asset-director"
        )

    for name, repository in [
        ("video-understand", args.video_understand_repo),
        ("jianying-editor", args.jianying_editor_repo),
    ]:
        if repository:
            installed[name] = install_from_git(name, repository, target_root / name)
        else:
            installed[name] = existing_skill(name, roots)

    missing = [name for name, path in installed.items() if not path]
    result = {"status": "succeeded" if not missing else "incomplete", "installed": installed, "missing": missing, "skills_root": str(target_root)}
    print("RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
