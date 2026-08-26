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
-> transcribe rendered rough-cut review + review simplified-Chinese caption template
-> global captions.srt + speech_timeline.json
-> caption QC: rough-cut speech coverage, source mapping, text completeness, and reference-script coverage
-> video-understand (final-timeline representative frames)
-> optional SpeakerPiP analysis/review (only when source mix requests it)
-> new JianYing draft skeleton + named-track QC, including an empty SpeakerPiP track
-> jianying-asset-director (bundled)
-> scene-effect/SFX shortlist and AI selection
-> character-effect shortlist and AI selection
-> jianying-editor (external)
-> materialize silent visual, narration, B-roll, optional PiP, captions, and effects on the named tracks
-> per-stage and post-build composition QC
-> optional user-exported MP4 pixel QC (black frames, audio stream, subtitle timing, PiP face/circle render, OCR native-text review)
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

After the user exports an MP4 manually, run the post-export gate against the
same final-timeline artifacts. This is the first check that can evaluate the
flattened pixels; draft JSON and candidate previews cannot prove JianYing's
actual mask/transform render.

```powershell
python scripts/verify_export.py `
  --video "C:\path\to\jianying-export.mp4" `
  --captions "work\captions\captions.srt" `
  --broll-plan "work\broll_plan.resolved.json" `
  --output "work\export_visual_qc.json"
```

`run_workflow.py` accepts the same export as `--exported-video` and writes
`export_visual_qc.json` after the draft stages. A failed exported-frame PiP
check, subtitle timing check, black-frame check, or missing audio stream fails
the gate. Native B-roll text is OCR-reviewed when Tesseract and its language
packs are available; otherwise the report records the check as skipped and
requires human inspection.

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

To render reviewed narration rough cuts, supply the decisions file. Every
accepted cut produces a silent visual MP4, a narration-only M4A, a review MP4,
and a QC report. The workflow then transcribes the rendered review MP4 and
stops with a caption-review template. Correct that template against the review
video and approved copy in simplified Chinese, preserving its rough-cut
timestamps; pass it as `--caption-review` to generate the final-timeline
`captions.srt` and `speech_timeline.json`. The workflow stops before captions
when audio duration, continuity, audible-content, or subtitle speech-coverage
checks fail. For more than one narration source, semantic exclusions are keyed
by source ID.

```powershell
python scripts/run_workflow.py `
  --video "C:\path\to\doctor.mp4" `
  --video "C:\path\to\illustration.mp4" `
  --media-decisions "work\media_decisions.json" `
  --semantic-exclusions "work\approved_semantic_cuts.json" `
  --reference-script "C:\path\to\approved-script.txt" `
  --caption-review "work\captions\caption_review.json" `
  --beats "work\final-timeline-beats.json" `
  --draft-name "Heart_Emergency_2026-08-25" `
  --render-rough-cut `
  --output-dir "work\video-job"
```

Then inspect the rough-cut previews, global SRT, and source mapping. Prepare
timestamped visual beats on that final timeline and pass them with `--beats`
and a new `--draft-name`. The workflow first creates a draft skeleton and
stops unless its empty, named tracks pass QC: `MainVisual`, muted `B_Roll`,
`SpeakerPiP`, `Narration`, `SFX`, `Effects`, `CharacterEffects`, and `Subtitles`. It refuses
to reuse a draft name unless `--overwrite` is explicitly passed to the skeleton
script. This separates track structure from creative edits.

Pass `--jianying-python "C:\path\to\python.exe"` or set `JY_PYTHON` when the
interpreter running the workflow lacks `jianying-editor` dependencies. The
selected interpreter is used only for draft-library construction; it never
launches JianYing or exports a video.

The asset director then searches the whole local library but exposes only
constrained shortlists per beat to the AI. Scene effects, sound effects, and
person/face effects are separate decisions. Person effects are considered only
for beats with a visible face and no full-height B-roll. The AI must select a
shortlist ID or explicitly choose no effect; code validates the real resource
ID, repetition limits, cooldown, effect type, and eligibility before it can be
materialized on the existing draft tracks.

Final SRT entries are generated only from the rendered rough-cut timebase. The
caption review keeps natural transcript ranges; the caption stage then splits
long reviewed text at commas, pauses, and sentence punctuation, removes those
display-only punctuation marks (default 18 non-punctuation characters), and
apportions each existing time range without gaps. This keeps readable short
captions without remapping raw-camera time.

When the speaker is following a supplied script, pass that script to the rough
cut stage and review the generated script-alignment report. The rough cut must
remove unrelated setup, false starts, repeated explanations, and on-camera
corrections before captions or B-roll are timed.

For rendered speech-led jobs, `--reference-script` is required for caption
completeness QC. Use `--allow-no-reference-script` only when no approved copy
exists; the report then records that script-level completeness was unavailable.

After the empty skeleton exists, run SpeakerPiP as its own optional review
stage. It does not write the draft: it creates `pip_visual_review.json`, one
actual B-roll/speaker composite per candidate in `pip-face-review`, and
`broll_plan.resolved.json`. The resolved plan retains B-roll but disables PiP
when it has no stable complete-head crop or collision-free placement.

```powershell
python scripts/run_workflow.py `
  --video "C:\path\to\doctor.mp4" `
  --video "C:\path\to\illustration.mp4" `
  --media-decisions "work\media_decisions.json" `
  --semantic-exclusions "work\approved_semantic_cuts.json" `
  --reference-script "C:\path\to\approved-script.txt" `
  --caption-review "work\captions\caption_review.json" `
  --beats "work\final-timeline-beats.json" `
  --broll-plan "work\broll_plan.json" `
  --draft-name "Heart_Emergency_2026-08-25" `
  --render-rough-cut `
  --prepare-speaker-pip `
  --speaker-pip-mode auto `
  --output-dir "work\video-job"
```

Run this stage only when full-frame B-roll needs the speaker to remain visibly
identifiable. No B-roll, one talking-head source, or short assets that do not
benefit from an identifiable speaker leave `SpeakerPiP` empty. `auto` skips
unsafe requests, `off` skips the stage, and `require` needs a visual decision
file that chooses one generated safe candidate.

## Draft Handoff Gate

Before handing off the draft, verify:

- each semantic removal has a timestamp and reason;
- every source has a reviewed role and audio policy; visual-only B-roll is
  muted only when its original audio is editorially irrelevant;
- cut boundaries do not remove words, safety language, or intentional pauses;
- audio fades prevent clicks and narration remains intelligible;
- the narration M4A, silent visual MP4, and review MP4 all match the approved
  EDL within audio-frame tolerance; narration has no unplanned long silence;
- `captions.srt` and `speech_timeline.json` use the rendered rough-cut output
  as their timestamp basis, map its surviving speech to the final timeline,
  and retain each source-video reference;
- `captions.qc.json` passed: SRT and speech timeline agree in count, text, and
  timestamps; detected opening, middle, and final rough-cut speech all have
  subtitle coverage; there is no unaccounted long subtitle gap; a
  reference-script sentence is not silently truncated;
- `draft_skeleton.qc.json` passed before any effect, subtitle, B-roll, or PiP
  segment is written; all required tracks exist once with the correct type and
  `B_Roll` begins muted;
- the approved rough-cut review has video, audio, compatible codecs, and no black frames;
- when an exported MP4 is supplied, `export_visual_qc.json` passed. This is a
  separate flattened-pixel gate and inspects actual export frames for PiP head
  completeness/circle geometry, subtitle timing/readability risks, black
  frames, audio presence, and B-roll native text;
- the JianYing draft imports the silent visual on `MainVisual` and the
  validated narration on `Narration`; visual-only B-roll remains explicitly
  muted on `B_Roll`; optional `SpeakerPiP` segments use the approved silent visual,
  final-timeline source time, zero segment volume, and a circular mask;
  `pip_visual_review.json` must detect the speaker face from final-timeline
  frames and supplies a complete-head envelope, protected information zones,
  candidate placement, crop size, source scale, and face-anchored transform.
  It also renders each candidate as a composite over its actual B-roll frame.
  Fixed face center, crop, or placement constants are not permitted. In
  `require` mode, an approved visual-decision file must select one safe
  candidate. PiP is allowed to be skipped when no safe candidate exists;
- JianYing effects and sound effects use validated local-library IDs;
- every AI selection is constrained to its generated shortlist or an explicit
  no-effect decision, and the plan passes configured repetition limits;
- every effect-track segment references a real `materials.video_effects` entry;
- `Effects` contains only `video_effect` materials and `CharacterEffects`
  contains only `face_effect` materials;
- character-effect segments use `face_target` and never overlap a full-height
  B-roll beat;
- `caption_layout_review.json` must inspect representative rough-cut frames for
  every SRT cue before draft assembly; its safe zone is materialized as the
  subtitle transform and then compared back against the saved draft;
- captions, PiP, titles, and effects do not collide; captions exactly match
  the SRT, use high-frequency expressive variants (large outlined text, bubble,
  and local flower text when available), and have multiple safe positions.

Do not use `JyProject.add_effect_simple` to create a JianYing effect track: it
can leave timeline placeholders without a `video_effect` material. Resolve the
approved scene-effect name through `pyJianYingDraft.VideoSceneEffectType` and
person-effect name through `VideoCharacterEffectType`, then call
`project.script.add_effect(...)` with a `Timerange` on the already-created
named track. Run post-build composition QC and validate the saved draft before
handoff:

```powershell
python scripts/validate_draft_effects.py --draft-name "DraftName" --expected-count 10
```

The skeleton itself can be checked independently:

```powershell
python scripts/validate_draft_skeleton.py --draft-name "DraftName"
```

For speech-led drafts, import the silent visual rough cut and the validated
narration M4A on separate tracks. Validate narration coverage before handoff:

```powershell
python scripts/validate_draft_narration.py --draft-name "DraftName" --expected-duration 340.847
```

Then verify that the `Subtitles` track exactly reproduces the approved SRT:

```powershell
python scripts/validate_draft_captions.py --draft-name "DraftName" --srt "work\captions\captions.srt"
```

Use `scripts/assemble_draft.py` only after the empty skeleton, caption QC, and
AI effect selection are approved. It refuses a populated draft, rebuilds the
verified empty skeleton in one editable session, and writes silent visual,
narration, B-roll, optional circular speaker PiP, caption presentation, and
approved effects. PiP is a separate optional stage after the empty skeleton and
before subtitle layout/assembly. The asset director requests it only when a
full-frame B-roll segment needs the visible speaker identity; no B-roll, a
single talking-head source, or short assets that do not benefit from a visible
speaker all leave `SpeakerPiP` empty. Use `--speaker-pip-mode off` to skip it,
`auto` to disable unsafe/unavailable segments, or `require` to fail when a
requested segment has no approved visual decision. The stage writes candidate
composites to `pip-face-review`; an AI or reviewer may return the selected
safe position in `--speaker-pip-visual-decisions`. Omit `--broll-plan` when
there is no B-roll. A B-roll plan can request PiP per segment:

```json
{"segments": [{"video": "C:\\media\\demonstration.mp4", "start": 10.84, "duration": 3.0, "source_start": 0.0, "speaker_pip": {"enabled": true, "position": "upper_right"}}]}
```

```powershell
python scripts/assemble_draft.py --draft-name "DraftName" `
  --visual "work\rough-cuts\source.visual.mp4" `
  --narration "work\rough-cuts\source.narration.m4a" `
  --captions "work\captions\captions.srt" `
  --broll-plan "work\broll_plan.json" `
  --pip-visual-review "work\pip_visual_review.json" `
  --caption-layout-review "work\caption_layout_review.json" `
  --asset-plan "work\selected_plan.json" `
  --output "work\draft_assembly.qc.json" `
  --rebuild-empty-skeleton
```

For a strict visual gate, generate a decision file after inspecting the
candidate composites. Only a generated candidate marked `safe` may be chosen:

```json
{"status":"approved","decisions":[{"segment_index":1,"status":"approved","position":"middle_right","reason":"The head is complete and no title, diagram, or subtitle is obscured."}]}
```

It rotates basic lower captions, middle warning captions, rounded-background
"bubble" captions, and sparse locally cached flower text. It uses only flower
IDs found in `artistEffect`; unavailable flower resources fall back to the
basic presentation. The main runner exposes the same step only with explicit
`--materialize-draft --broll-plan ... --approved-asset-plan ...`; it currently
requires one narration source so the PiP can reference that source's validated
silent visual. Verify PiP explicitly:

```powershell
python scripts/validate_draft_pip.py --draft-name "DraftName" --require-pip
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
