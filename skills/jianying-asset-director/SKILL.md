---
name: jianying-asset-director
description: Analyze video content and transcripts, select JianYing visual effects and sound effects by semantic fit, generate auditable plans and local previews, enforce style and layout rules, then hand validated assets to jianying-editor for draft creation. Use for automated JianYing/CapCut editing where effects, sound effects, captions, picture-in-picture, timing, or human-style audiovisual judgment matter.
---

# JianYing Asset Director

Use this skill as the orchestration layer between the reviewed
`media-role-director` handoff and `jianying-editor`. It decides whether an effect belongs at a beat, which
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

1. **Read final timeline**: when multiple source assets are used, read
   `media_manifest.json`, `captions.srt`, and `speech_timeline.json` from
   `media-role-director`. Treat their timestamps as the final narration
   timeline. Do not use a raw camera timestamp for an effect, subtitle, or
   sound-effect placement.
2. **Understand**: use `video-understand` to obtain duration, transcript/SRT,
   scene frames, and representative frames for every cutaway or visual beat.
3. **Plan**: normalize each beat into `start`, `end`, `spoken_text`,
   `visual_subject`, `action`, `emotion`, `purpose`, `caption_zone`, and
   `pip_zone`. Include beats that intentionally receive no effect.
4. **Catalog**: run `scripts/asset_director.py catalog` against the local
   JianYing data directory. Use real IDs from `video_scene_effects.csv` and
   `cloud_sound_effects.csv`; never invent IDs from names.
5. **Shortlist**: run `scripts/asset_director.py plan` with the beat JSON and
   catalog. It ranks the full local library deterministically, then emits only
   the configured per-beat shortlists. It never auto-selects the first ranked
   effect. Reject candidates that violate `references/asset_taxonomy.json` or
   user preferences.
6. **AI selection**: give the AI the beat's representative frame(s),
   `spoken_text`, purpose, domain style, and its `visual_candidates` /
   `sound_candidates`. It must return one candidate ID from each shortlist or
   `null` for no effect. Apply this response using the `select` command; the
   command rejects invented IDs, duplicate beat decisions, excessive reuse,
   and effects within the configured cooldown window.
7. **Preview gate**: create short previews for every non-trivial candidate,
   including captions, PiP, and the candidate audio. Inspect the actual visual
   result and waveform/loudness. Do not use names alone to judge an asset.
   A preview video may be rendered from the source assets before draft creation
   or supplied by the user. Never export a JianYing draft automatically. For a
   supplied preview, extract review clips with:
   `python scripts/asset_director.py preview --video rendered_preview.mp4 --plan plan.json --output-dir preview/`.
8. **User gate**: output the timestamped plan, selected assets, rejected
   candidates, and unresolved decisions. If the user has asked to proceed,
   continue; otherwise wait for confirmation.
9. **Build**: call `jianying-editor` in draft-library-only mode to create a
   new draft. Do not launch JianYing, automate its UI, invoke
   `JianyingController`, or call an exporter. Use separate tracks for effects
   and each audio role. Keep source asset IDs, local cache paths, and the
   absolute draft path in the build report.
10. **Validate**: run `scripts/asset_director.py validate` and the
   `jianying-editor` draft inspector. Check asset existence, effect/audio
   timing, volume, overlap with captions/PiP, opening cleanliness, and that
   the draft contains no generated fallback sound when a library asset was
   selected. This structural validation is the workflow's endpoint; the user
   manually opens and exports the draft outside the workflow.

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
- Do not send the entire effect library to the AI for every beat. The local
  shortlist is the token boundary; the AI receives only candidate metadata and
  relevant video frames.
- Do not choose a candidate merely because it is ranked first. The model must
  consider the actual frame, spoken meaning, subtitle/PiP zones, and the
  recent selection history. The `select` command is the enforcement point.

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
  "media_manifest": "absolute path to media_manifest.json",
  "captions_srt": "absolute path to captions.srt",
  "speech_timeline": "absolute path to speech_timeline.json",
  "beats": [{"start": 10.66, "end": 15.78, "purpose": "warning", "assets": []}],
  "visual_effects": [{"asset_id": "real_resource_id", "resource_id": "real_resource_id", "source_identifier": "剪映特效名称", "name": "...", "start": 10.66, "duration": 0.8, "zone": "full_frame", "score": 0.0}],
  "sound_effects": [{"asset_id": "...", "name": "...", "start": 10.66, "duration": 0.7, "track": "SFX_Danger", "volume": 0.12, "score": 0.0}],
  "rejected": [{"asset_id": "...", "reason": "cartoon asset forbidden for medical warning"}],
  "preview_required": true,
  "draft_library_only": true,
  "export": {"mode": "manual_user_action_required"},
  "validation": {"status": "pending"}
}
```

The initial `plan` output contains only candidates. Apply a structured AI
decision before draft creation:

```json
{
  "selections": [
    {
      "beat_id": "warning-01",
      "visual_asset_id": "real_resource_id_from_visual_candidates",
      "sound_asset_id": null,
      "reason": "The frame and phrase need a brief restrained warning accent."
    }
  ]
}
```

```powershell
python scripts/asset_director.py select `
  --plan work/candidate_plan.json `
  --selections work/ai_selections.json `
  --output work/selected_plan.json

python scripts/asset_director.py validate `
  --plan work/selected_plan.json `
  --catalog work/asset_catalog.json
```

For a selected visual effect, `asset_id` and `resource_id` are the true
JianYing resource ID used for auditing. Draft builders must resolve
`source_identifier` through `VideoSceneEffectType.from_name()` before calling
`script.add_effect`; they must not pass `asset_id` to `from_name()`.

Read `references/asset_taxonomy.json` before matching and use
`scripts/asset_director.py --help` for the deterministic catalog, plan, and
validation commands.
