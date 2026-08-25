#!/usr/bin/env python3
"""Create a new, empty JianYing draft with the workflow's named tracks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_draft_skeleton.py"


def enable_utf8_output() -> None:
    """Keep JianYing wrapper status output from failing under Windows GBK."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def locate_editor_skill() -> Path:
    configured = os.environ.get("JY_SKILL_ROOT", "").strip()
    candidates = [Path(configured)] if configured else []
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        candidates.append(Path(codex_home).expanduser() / "skills" / "jianying-editor")
    candidates.extend([Path.home() / ".codex" / "skills" / "jianying-editor", Path.home() / ".agents" / "skills" / "jianying-editor"])
    for candidate in candidates:
        if (candidate / "scripts" / "jy_wrapper.py").is_file():
            return candidate.resolve()
    raise RuntimeError("jianying-editor Skill is not installed; run scripts/install.py first")


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_draft_skeleton", VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load skeleton validator: {VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    enable_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-name", required=True)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--drafts-root", type=Path)
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing draft with this name")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        editor_skill = locate_editor_skill()
        sys.path.insert(0, str(editor_skill / "scripts"))
        sys.path.insert(0, str(editor_skill / "scripts" / "vendor"))
        import pyJianYingDraft as draft
        from jy_wrapper import JyProject, get_default_drafts_root

        drafts_root = args.drafts_root.resolve() if args.drafts_root else Path(get_default_drafts_root()).resolve()
        expected_path = drafts_root / args.draft_name
        if expected_path.exists() and not args.overwrite:
            raise FileExistsError(f"draft already exists: {expected_path}; choose a new --draft-name or pass --overwrite")

        project = JyProject(
            args.draft_name,
            width=args.width,
            height=args.height,
            drafts_root=str(drafts_root),
            overwrite=args.overwrite,
        )
        project.script.add_track(draft.TrackType.video, "MainVisual")
        project.script.add_track(draft.TrackType.video, "B_Roll", mute=True, relative_index=1)
        project.script.add_track(draft.TrackType.audio, "Narration")
        project.script.add_track(draft.TrackType.audio, "SFX")
        project.script.add_track(draft.TrackType.effect, "Effects")
        project.script.add_track(draft.TrackType.effect, "CharacterEffects")
        project.script.add_track(draft.TrackType.text, "Subtitles")
        saved = project.save()
        draft_path = Path(saved["draft_path"]).resolve()
        document = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
        report = load_validator().validate(document)
        report.update({"draft_name": args.draft_name, "draft_path": str(draft_path), "editor_skill": str(editor_skill)})
        if report["status"] != "passed":
            raise RuntimeError("draft skeleton validation failed: " + "; ".join(report["errors"]))
        result = report
    except (OSError, RuntimeError, ValueError, ImportError) as error:
        result = {"status": "failed", "errors": [str(error)]}

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RESULT: " + json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
