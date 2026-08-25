---
name: jianying-video-workflow
description: Install and run a portable multi-source JianYing production workflow. Use to classify source video roles and audio policies, rough-cut retained narration, generate a final-timeline SRT, match JianYing effects and sound effects, then create and validate a JianYing draft-library project. This workflow never launches JianYing or exports video from JianYing.
---

# JianYing Video Workflow

Use this repository as one portable workflow package. It bundles the Skills we
own and installs verified external Skills as siblings in the Codex Skills
directory.

```text
video-understand (external)
-> transcript and visual analysis
-> media-role-director (bundled)
-> reviewed source roles and audio policies
-> talking-head-rough-cut (bundled) + FFmpeg
-> validated silent visual + independent narration + review MP4
-> global captions.srt + speech_timeline.json
-> jianying-asset-director (bundled)
-> effect and sound-effect plan
-> jianying-editor (external)
-> JianYing draft-library creation and structural validation
```

## Repository Layout

```text
skills/
  media-role-director/      # bundled
  talking-head-rough-cut/    # bundled
  jianying-asset-director/   # bundled
scripts/                     # installer, environment checker, workflow helper
manifest.json                # internal and external dependency policy
```

Do not add user videos, drafts, cloud caches, credentials, or generated media
to this repository.

## JianYing Draft-Library Mode

`jianying-editor` is deliberately restricted to direct operations on the local
JianYing draft library. It may create, read, repair, and structurally inspect
draft files such as `draft_info.json`; JianYing itself does not need to be
running for these operations.

Do not launch JianYing Pro, drive its UI, invoke `JianyingController`, call
`auto_exporter.py` or `export_draft`, or ask the user to open an export page.
Do not treat a draft as an exported deliverable. The workflow ends with a
validated draft name and absolute draft path. Opening that draft and exporting
an MP4 are manual user actions outside this workflow.

For connection checks, use `jianying-editor`'s draft inspector only. Do not
run its API validator: it creates a diagnostic draft and can report a false
failure on Windows when the console uses GBK instead of UTF-8.

## Install

From a clone of this repository, run:

```powershell
python scripts/install.py
python scripts/check_environment.py
```

The installer copies the three bundled Skills and this workflow into the selected
Codex Skills directory. It downloads these verified external repositories at
the pinned commits below unless they already exist in the target directory:

- `https://github.com/MomoFadaly/video-understand.git` at
  `4848131e123fb868a6ae6a4f7fef33a82a0119df`
- `https://github.com/luoluoluo22/jianying-editor-skill.git` at
  `f421c8a036f4fda888a83b38fc90bb9c00d6faa9`

Use `--upgrade-external` to re-install the pinned external dependencies. Use
`--skills-dir` to select a non-default Codex Skills directory, or
`--skip-external` when those Skills are already managed elsewhere.

During installation, the workflow adds its draft-library-only policy to the
installed `jianying-editor` Skill. This makes the no-launch/no-export boundary
available even when that external Skill is invoked directly as part of this
workflow.

## Run

First create a multi-source intake. Repeat `--video` for every available
source. This pass analyzes each file and stops with `media_intake.json`; it
does not silently decide which original audio to mute.

```powershell
python scripts/run_workflow.py `
  --video "C:\path\to\doctor.mp4" `
  --video "C:\path\to\illustration.mp4" `
  --output-dir "work\video-job"
```

Inspect the source transcripts and frames, then use `media-role-director` to
write reviewed decisions. Each source must state a role, an audio policy,
reason, confidence, and a narration order when its original speech is kept.
The role director mutes only reviewed `broll_visual` sources; it preserves or
routes useful ambient audio explicitly.

To render reviewed narration rough cuts and generate the final-timeline
`captions.srt` plus `speech_timeline.json`, supply the decisions file. Every
accepted cut produces a silent visual MP4, a narration-only M4A, a review MP4,
and a QC report. The workflow stops before captions when audio duration,
continuity, or audible-content checks fail. For more than one narration source,
semantic exclusions are keyed by source ID.

```powershell
python scripts/run_workflow.py `
  --video "C:\path\to\doctor.mp4" `
  --video "C:\path\to\illustration.mp4" `
  --media-decisions "work\media_decisions.json" `
  --semantic-exclusions "work\approved_semantic_cuts.json" `
  --render-rough-cut `
  --output-dir "work\video-job"
```

Then inspect the rough-cut previews, global SRT, and source mapping. Prepare
timestamped visual beats on that final timeline and pass them with `--beats` to
create an asset candidate plan. The asset director searches the whole local
library but exposes only a constrained shortlist per beat to the AI. The AI
must select a shortlist ID or explicitly choose no effect; code validates the
real resource ID, repetition limits, and cooldown before the selected plan can
reach `jianying-editor`. Only after the user accepts the cut and selected asset
plan should `jianying-editor` create a new draft in the local draft library.

When the speaker is following a supplied script, pass that script to the rough
cut stage and review the generated script-alignment report. The rough cut must
remove unrelated setup, false starts, repeated explanations, and on-camera
corrections before captions or B-roll are timed.

## Draft Handoff Gate

Before handing off the draft, verify:

- each semantic removal has a timestamp and reason;
- every source has a reviewed role and audio policy; visual-only B-roll is
  muted only when its original audio is editorially irrelevant;
- cut boundaries do not remove words, safety language, or intentional pauses;
- audio fades prevent clicks and narration remains intelligible;
- the narration M4A, silent visual MP4, and review MP4 all match the approved
  EDL within audio-frame tolerance; narration has no unplanned long silence;
- `captions.srt` and `speech_timeline.json` map surviving speech to the final
  timeline and retain each source-video reference;
- the approved rough-cut review has video, audio, compatible codecs, and no black frames;
- the JianYing draft imports the silent visual on a video track and the
  validated narration on a named audio track; visual-only B-roll is explicitly muted;
- JianYing effects and sound effects use validated local-library IDs;
- every AI selection is constrained to its generated shortlist or an explicit
  no-effect decision, and the plan passes configured repetition limits;
- every effect-track segment references a real `materials.video_effects` entry;
- captions, PiP, titles, and effects do not collide.

Do not use `JyProject.add_effect_simple` to create a JianYing effect track: it
can leave timeline placeholders without a `video_effect` material. Resolve the
approved asset name through `pyJianYingDraft.VideoSceneEffectType` and call
`project.script.add_effect(...)` with a `Timerange` on an effect track. Then
validate the saved draft before handoff:

```powershell
python scripts/validate_draft_effects.py --draft-name "DraftName" --expected-count 10
```

For speech-led drafts, import the silent visual rough cut and the validated
narration M4A on separate tracks. Validate narration coverage before handoff:

```powershell
python scripts/validate_draft_narration.py --draft-name "DraftName" --expected-duration 340.847
```

Report the draft name and absolute draft-library path. A rough-cut preview is
an intermediate review artifact, never the final deliverable. Do not create a
full-timeline MP4 from the draft, export, or launch JianYing as part of this
gate. MP4 export is a manual action by the user after opening the draft in
JianYing.

Read `skills/talking-head-rough-cut/references/cut-policy.md` before changing
pause thresholds. Read `skills/media-role-director/references/contracts.md`
before changing media decisions. Use `jianying-asset-director` and
`jianying-editor` only after the rough-cut plan and caption mapping have passed
review.
