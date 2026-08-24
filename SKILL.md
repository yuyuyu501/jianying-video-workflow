---
name: jianying-video-workflow
description: Install and run a portable JianYing video-production workflow that checks the local environment, installs the public jianying-asset-director skill, detects or accepts sources for video-understand and jianying-editor, analyzes video content before editing, matches JianYing assets, builds drafts, and validates previews and resources. Use for setting up this workflow on a new computer or running the complete analysis-to-draft pipeline.
---

# JianYing Video Workflow

Use this Skill as the top-level orchestrator for the three-stage workflow:

```text
video-understand
-> content, transcript, emotion, action analysis
-> jianying-asset-director
-> asset catalog, semantic matching, exclusions, scoring, previews
-> jianying-editor
-> draft creation and resource/layout validation
```

This repository contains the workflow and installer. It does not bundle user
videos, JianYing drafts, cloud caches, credentials, or the source code of
third-party Skills whose upstream license/source is not known.

## Install On A New Computer

Run from a clone of this repository:

```powershell
python scripts/install.py
python scripts/check_environment.py
```

The installer:

- installs this workflow into the Codex Skills directory;
- downloads `jianying-asset-director` from its pinned public Git repository;
- detects existing `video-understand` and `jianying-editor` Skills;
- can install either detected Skill when the user supplies an explicit Git URL;
- reports missing Python, Git, FFmpeg, ffprobe, or JianYing instead of silently
  changing the system.

Repeated installs reuse an existing `jianying-asset-director`. To explicitly
refresh it from the configured repository, pass `--upgrade-asset-director`.

Use explicit sources when those Skills are not already installed:

```powershell
python scripts/install.py `
  --video-understand-repo <git-url> `
  --jianying-editor-repo <git-url>
```

Do not replace a local Skill with an unrelated similarly named repository.
Confirm the source and license first.

## Run The Workflow

For a new video, first prepare the analysis artifacts:

```powershell
python scripts/run_workflow.py `
  --video "C:\path\to\video.mp4" `
  --srt "C:\path\to\video.srt" `
  --output-dir "work\video-job"
```

The model must inspect the generated analysis and representative frames, then
write a beat plan containing timestamps, spoken text, visual subject, action,
emotion, purpose, caption zone, and PiP zone. Include beats that intentionally
receive no effect.

Use `jianying-asset-director` to catalog and score real JianYing effect IDs,
sound-effect IDs, durations, intensity, exclusions, and style compatibility.
Show the timestamped plan, selected assets, rejected assets, and unresolved
decisions before modifying a draft. A request that explicitly says to proceed
after analysis satisfies this gate; otherwise wait for confirmation.

After the plan is accepted, use `jianying-editor` to create a new draft rather
than overwrite the source draft. Keep separate audio role tracks, preserve
subtitle and PiP layout, and use only validated library assets when the plan
requires library assets.

For a prepared beat plan, the helper can generate the asset catalog and scored
handoff plan:

```powershell
python scripts/run_workflow.py `
  --video "C:\path\to\video.mp4" `
  --beats "C:\path\to\beats.json" `
  --output-dir "work\video-job"
```

The helper prepares artifacts; it does not guess missing semantic decisions or
silently apply a draft.

## Validation Gate

Before delivery, verify:

- all audio and video asset paths exist;
- effect and sound-effect IDs came from the local JianYing index;
- no generated fallback sound is present when a library asset was selected;
- audio is below narration and does not mask speech;
- effect timing follows the phrase/action onset;
- subtitles, title, face PiP, and effect overlays do not collide;
- opening frames are clean unless the plan explicitly requests an effect;
- a rendered draft preview has been cut into review clips and inspected.

Use `check_environment.py` for machine diagnostics and the
`jianying-asset-director` validator plus `jianying-editor` draft inspector for
artifact checks.

## Dependency Policy

`jianying-asset-director` is published at:

`git@github.com:yuyuyu501/jianying-asset-director.git`

The current local `video-understand` and `jianying-editor` Skills do not carry
verified public repository URLs in this environment. They are therefore
treated as Codex-local dependencies unless the installer receives explicit
repository URLs. This avoids publishing or downloading an unrelated project
under the same name.
