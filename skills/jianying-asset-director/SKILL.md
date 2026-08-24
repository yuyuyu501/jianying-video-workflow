---
name: jianying-asset-director
description: Analyze video content and transcripts, select JianYing visual effects and sound effects by semantic fit, generate auditable plans and local previews, enforce style and layout rules, then hand validated assets to jianying-editor for draft creation. Use for automated JianYing/CapCut editing where effects, sound effects, captions, picture-in-picture, timing, or human-style audiovisual judgment matter.
---

# JianYing Asset Director

Use this skill as the orchestration layer between `video-understand` and
`jianying-editor`. It decides whether an effect belongs at a beat, which
library asset is appropriate, how long it should run, where it may appear, and
whether it passes preview and safety checks. It does not replace either skill.

## Portable Installation

Install this folder under the Codex skills directory on the target machine.
Install `video-understand` and `jianying-editor` as separate sibling Skills.
The catalog command discovers `jianying-editor/data` automatically. Override
discovery with `JY_ASSET_DIRECTOR_DATA_DIR` or pass `--data-dir` explicitly;
`JY_SKILL_ROOT` is also accepted when it points to the JianYing Skill root.

The target machine must provide Python, FFmpeg/ffprobe on `PATH`, and a local
JianYing data index. Cloud sound-effect IDs are resolved by `jianying-editor`
when a draft is built; this repository does not contain user media or caches.

## Required Workflow

Run the stages in order. Do not modify a JianYing draft before the analysis
plan has been shown to the user.

1. **Understand**: use `video-understand` to obtain duration, transcript/SRT,
   scene frames, and representative frames for every cutaway or visual beat.
2. **Plan**: normalize each beat into `start`, `end`, `spoken_text`,
   `visual_subject`, `action`, `emotion`, `purpose`, `caption_zone`, and
   `pip_zone`. Include beats that intentionally receive no effect.
3. **Catalog**: run `scripts/asset_director.py catalog` against the local
   JianYing data directory. Use real IDs from `video_scene_effects.csv` and
   `cloud_sound_effects.csv`; never invent IDs from names.
4. **Match**: run `scripts/asset_director.py plan` with the beat JSON and
   catalog. Treat the output as candidates, not truth. Reject candidates that
   violate `references/asset_taxonomy.json` or user preferences.
5. **Preview gate**: create short previews for every non-trivial candidate,
   including captions, PiP, and the candidate audio. Inspect the actual visual
   result and waveform/loudness. Do not use names alone to judge an asset.
   After exporting a scratch or full draft preview, extract review clips with:
   `python scripts/asset_director.py preview --video rendered_preview.mp4 --plan plan.json --output-dir preview/`.
6. **User gate**: output the timestamped plan, selected assets, rejected
   candidates, and unresolved decisions. If the user has asked to proceed,
   continue; otherwise wait for confirmation.
7. **Build**: call `jianying-editor` to create a new draft. Use separate
   tracks for effects and each audio role. Keep source asset IDs and local
   cache paths in the build report.
8. **Validate**: run `scripts/asset_director.py validate` and the
   `jianying-editor` draft inspector. Check asset existence, effect/audio
   timing, volume, overlap with captions/PiP, opening cleanliness, and that
   the draft contains no generated fallback sound when a library asset was
   selected.

## Matching Rules

- Match the **purpose** of a beat, not just a noun in the transcript.
- Use restrained effects for medical, educational, and safety content.
- A warning beat may use a short impact, restrained glitch, or low-frequency
  cue. Do not use cartoon, comedy, fireworks, or celebratory assets for a
  medical warning.
- A correct-action beat may use a soft scan, highlight, or confirmation cue.
- A chapter beat may use a short whoosh or scan transition.
- Keep one audiovisual accent per beat unless the plan explicitly explains a
  layered combination.
- Put visual effects on the effect track and audio on named role tracks such
  as `SFX_Danger`, `SFX_Chapter`, `SFX_Confirm`, or `SFX_Timer`.
- Start an effect at the spoken phrase or action onset, not at an arbitrary
  cutaway start. End it before the next phrase unless it is an intentional
  ambient bed.
- Keep short SFX below narration. Start with approximately -12 to -18 dB
  relative to the spoken voice and verify by listening, not only by numbers.
- Do not place visual effects over the subtitle zone, title zone, or PiP face
  unless the effect is specifically designed as a full-screen overlay and the
  preview shows no loss of legibility.
- Prefer no effect over a semantically wrong effect. Empty beats are valid.

## Knowledge Representation

Use `references/asset_taxonomy.json` as a lightweight knowledge graph:

- `beat_purposes` map content purposes to preferred and forbidden tags.
- `asset_tags` map library names to semantic tags and intensity.
- `relations` encode positive and negative compatibility.
- `style_profiles` encode domain-specific constraints.

Extend this file when the user gives feedback such as “too cartoonish”,
“covers the subtitle”, or “too loud”. Keep the feedback as a rule with a
reason and scope; do not silently change a one-off edit into a global rule.

## Handoff Contract

The plan passed to `jianying-editor` must include:

```json
{
  "video": "absolute path",
  "beats": [{"start": 10.66, "end": 15.78, "purpose": "warning", "assets": []}],
  "visual_effects": [{"asset_id": "...", "name": "...", "start": 10.66, "duration": 0.8, "zone": "full_frame", "score": 0.0}],
  "sound_effects": [{"asset_id": "...", "name": "...", "start": 10.66, "duration": 0.7, "track": "SFX_Danger", "volume": 0.12, "score": 0.0}],
  "rejected": [{"asset_id": "...", "reason": "cartoon asset forbidden for medical warning"}],
  "preview_required": true,
  "validation": {"status": "pending"}
}
```

Read `references/asset_taxonomy.json` before matching and use
`scripts/asset_director.py --help` for the deterministic catalog, plan, and
validation commands.
