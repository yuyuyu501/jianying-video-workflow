#!/usr/bin/env python3
"""Install the bundled Skills and verified external dependencies."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable


EXTERNAL_SOURCES = {
    "video-understand": {
        "repository": "https://github.com/MomoFadaly/video-understand.git",
        "revision": "4848131e123fb868a6ae6a4f7fef33a82a0119df",
    },
    "jianying-editor": {
        "repository": "https://github.com/luoluoluo22/jianying-editor-skill.git",
        "revision": "f421c8a036f4fda888a83b38fc90bb9c00d6faa9",
    },
}

JY_POLICY_MARKER = "<!-- jianying-video-workflow: draft-library-only -->"


def skill_roots(explicit: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if explicit:
        roots.append(explicit.expanduser())
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        roots.append(Path(codex_home).expanduser() / "skills")
    roots.extend([Path.home() / ".codex" / "skills", Path.home() / ".agents" / "skills"])
    return list(dict.fromkeys(roots))


def copy_skill(source: Path, target: Path) -> None:
    if target.exists() and not (target / "SKILL.md").exists():
        raise RuntimeError(f"Refusing to overwrite non-Skill directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))


def discover_skill(root: Path, name: str) -> Path | None:
    if (root / "SKILL.md").exists():
        return root
    direct = root / name
    if (direct / "SKILL.md").exists():
        return direct
    candidates = list(root.glob("*/SKILL.md"))
    for candidate in candidates:
        if candidate.parent.name == name:
            return candidate.parent
    if len(candidates) == 1:
        return candidates[0].parent
    return None


def archive_url(repository: str, revision: str) -> str:
    base = repository.removesuffix(".git").rstrip("/")
    if not base.startswith("https://github.com/"):
        raise ValueError("External repositories must use an HTTPS GitHub URL")
    return f"{base}/archive/{revision}.zip"


def extract_archive(archive: Path, destination: Path) -> None:
    destination_root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination_root / member.filename).resolve()
            try:
                target.relative_to(destination_root)
            except ValueError as error:
                raise RuntimeError(f"Archive contains an unsafe path: {member.filename}") from error
        bundle.extractall(destination_root)


def install_from_archive(name: str, repository: str, revision: str, target: Path, timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix=f"{name}-") as temp:
        temporary = Path(temp)
        archive = temporary / "source.zip"
        request = urllib.request.Request(archive_url(repository, revision), headers={"User-Agent": "jianying-video-workflow"})
        with urllib.request.urlopen(request, timeout=timeout) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        checkout = temporary / "checkout"
        checkout.mkdir()
        extract_archive(archive, checkout)
        source = discover_skill(checkout, name)
        if source is None:
            raise RuntimeError(f"Repository does not contain a discoverable {name} Skill")
        copy_skill(source, target)
    return str(target)


def apply_jianying_editor_policy(target: Path, repo_root: Path) -> None:
    """Make the workflow-specific operating boundary visible inside the external Skill."""
    source = repo_root / "references" / "jianying-editor-draft-library-mode.md"
    if not source.is_file():
        raise RuntimeError(f"Missing JianYing editor policy: {source}")
    policy = target / "references" / "jianying-video-workflow-draft-library-mode.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, policy)

    skill_file = target / "SKILL.md"
    if not skill_file.is_file():
        raise RuntimeError(f"Installed jianying-editor has no SKILL.md: {target}")
    content = skill_file.read_text(encoding="utf-8")
    if JY_POLICY_MARKER not in content:
        content = (
            content.rstrip()
            + "\n\n"
            + JY_POLICY_MARKER
            + "\n\n## JianYing Video Workflow Policy\n\n"
            + "When this Skill is installed by `jianying-video-workflow`, read "
            + "[the draft-library-only policy]"
            + "(references/jianying-video-workflow-draft-library-mode.md) before "
            + "performing any JianYing operation. The policy is mandatory for this "
            + "workflow and overrides export or UI-automation examples in this Skill.\n"
        )
        skill_file.write_text(content, encoding="utf-8")


def existing_skill(name: str, roots: Iterable[Path]) -> str | None:
    for root in roots:
        candidate = root / name
        if (candidate / "SKILL.md").exists():
            return str(candidate)
    return None


def bundled_skills(repo_root: Path) -> list[Path]:
    skills_root = repo_root / "skills"
    return sorted(path.parent for path in skills_root.glob("*/SKILL.md"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Install JianYing video workflow Skills")
    parser.add_argument("--skills-dir", type=Path, help="Target Codex skills directory")
    parser.add_argument("--video-understand-repo", default=EXTERNAL_SOURCES["video-understand"]["repository"], help="Verified Git URL")
    parser.add_argument("--jianying-editor-repo", default=EXTERNAL_SOURCES["jianying-editor"]["repository"], help="Verified Git URL")
    parser.add_argument("--video-understand-revision", default=EXTERNAL_SOURCES["video-understand"]["revision"], help="Pinned Git revision")
    parser.add_argument("--jianying-editor-revision", default=EXTERNAL_SOURCES["jianying-editor"]["revision"], help="Pinned Git revision")
    parser.add_argument("--external-timeout", type=int, default=600, help="Per-dependency download timeout in seconds")
    parser.add_argument("--skip-external", action="store_true", help="Do not install external Skills")
    parser.add_argument("--upgrade-external", action="store_true", help="Re-download external Skills even when installed in target")
    parser.add_argument("--skip-workflow", action="store_true", help="Do not install the top-level workflow Skill")
    args = parser.parse_args()

    roots = skill_roots(args.skills_dir)
    target_root = roots[0]
    target_root.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]
    bundled = bundled_skills(repo_root)
    if not bundled:
        raise RuntimeError(f"No bundled Skills found under {repo_root / 'skills'}")

    installed: dict[str, str | None] = {}
    workflow_target = target_root / "jianying-video-workflow"
    if not args.skip_workflow:
        if repo_root != workflow_target:
            copy_skill(repo_root, workflow_target)
        installed["jianying-video-workflow"] = str(workflow_target)

    for skill in bundled:
        target = target_root / skill.name
        copy_skill(skill, target)
        installed[skill.name] = str(target)

    for name, repository, revision in [
        ("video-understand", args.video_understand_repo, args.video_understand_revision),
        ("jianying-editor", args.jianying_editor_repo, args.jianying_editor_revision),
    ]:
        target = target_root / name
        if args.skip_external:
            installed[name] = existing_skill(name, roots)
        elif (target / "SKILL.md").exists() and not args.upgrade_external:
            installed[name] = str(target)
        else:
            installed[name] = install_from_archive(name, repository, revision, target, args.external_timeout)
        if name == "jianying-editor" and installed[name]:
            apply_jianying_editor_policy(Path(installed[name]), repo_root)

    missing = [name for name, path in installed.items() if not path]
    result = {
        "status": "succeeded" if not missing else "incomplete",
        "installed": installed,
        "missing": missing,
        "skills_root": str(target_root),
    }
    print("RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
