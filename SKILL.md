---
name: jianying-video-workflow
description: Install and run a portable JianYing talking-head production workflow. Use to install its bundled talking-head-rough-cut and jianying-asset-director Skills, fetch verified video-understand and jianying-editor dependencies, remove retakes and excessive pauses before editing, match JianYing effects and sound effects, build a draft, and validate the result.
---

# JianYing Video Workflow

Use this repository as one portable workflow package. It bundles the Skills we
own and installs verified external Skills as siblings in the Codex Skills
directory.

```text
video-understand (external)
-> transcript and visual analysis
-> talking-head-rough-cut (bundled) + FFmpeg
-> approved speech-first rough cut
-> jianying-asset-director (bundled)
-> effect and sound-effect plan
-> jianying-editor (external)
-> JianYing draft and delivery validation
```

## Repository Layout

```text
skills/
  talking-head-rough-cut/    # bundled
  jianying-asset-director/   # bundled
scripts/                     # installer, environment checker, workflow helper
manifest.json                # internal and external dependency policy
```

Do not add user videos, drafts, cloud caches, credentials, or generated media
to this repository.

## Install

From a clone of this repository, run:

```powershell
python scripts/install.py
python scripts/check_environment.py
```

The installer copies the two bundled Skills and this workflow into the selected
Codex Skills directory. It downloads these verified external repositories at
the pinned commits below unless they already exist in the target directory:

- `https://github.com/MomoFadaly/video-understand.git` at
  `4848131e123fb868a6ae6a4f7fef33a82a0119df`
- `https://github.com/luoluoluo22/jianying-editor-skill.git` at
  `f421c8a036f4fda888a83b38fc90bb9c00d6faa9`

Use `--upgrade-external` to re-install the pinned external dependencies. Use
`--skills-dir` to select a non-default Codex Skills directory, or
`--skip-external` when those Skills are already managed elsewhere.

## Run

Create analysis, a rough-cut plan, and a JianYing asset catalog:

```powershell
python scripts/run_workflow.py `
  --video "C:\path\to\video.mp4" `
  --output-dir "work\video-job"
```

The first pass only shortens excessive silence automatically. It produces
`rough_cut_plan.json`, including duplicate/restart candidates that must be
reviewed rather than silently removed. To render a reviewed rough cut, supply
approved semantic exclusions and request a preview:

```powershell
python scripts/run_workflow.py `
  --video "C:\path\to\video.mp4" `
  --semantic-exclusions "work\approved_semantic_cuts.json" `
  --render-rough-cut `
  --output-dir "work\video-job"
```

Then inspect the rough-cut preview, prepare timestamped visual beats for the
accepted timeline, and pass them with `--beats` to create an asset plan. Only
after the user accepts the cut and asset plan should `jianying-editor` create a
new draft.

## Acceptance Gate

Before delivery, verify:

- each semantic removal has a timestamp and reason;
- cut boundaries do not remove words, safety language, or intentional pauses;
- audio fades prevent clicks and narration remains intelligible;
- the rendered output has video, audio, compatible codecs, and no black frames;
- JianYing effects and sound effects use validated local-library IDs;
- captions, PiP, titles, and effects do not collide.

Read `skills/talking-head-rough-cut/references/cut-policy.md` before changing
pause thresholds. Use `jianying-asset-director` and `jianying-editor` only
after the rough-cut plan has passed review.
