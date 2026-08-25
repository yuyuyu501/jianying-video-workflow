---
name: media-role-director
description: Classify multiple video assets into narration, visual-only B-roll, ambient sound, music, or excluded material before rough cutting. Use when a video workflow must decide which source audio to retain, mute, duck, or extract, and when it needs a reviewed media manifest plus a global timestamped SRT after rough cutting.
---

# Media Role Director

Use this Skill after `video-understand` has analyzed every input asset and
before `talking-head-rough-cut`. It turns a collection of files into an
auditable media manifest. It does not silently mute audio merely because a
clip is not the primary talking head.

## Required Process

1. Run `video-understand` for every source. Inspect each transcript and its
   representative frames in the context of the approved video message.
2. Run `scripts/media_role_director.py intake` to record source metadata and
   conservative recommendations. A recommendation is not a final decision.
3. Judge every source and write a decisions JSON. Include a reason,
   confidence, and `review_required` when the intent is ambiguous. Then run
   `apply-decisions` to produce `media_manifest.json`.
4. Send only sources whose role is `primary_narration` or `secondary_speech`
   and whose `audio_policy` is `keep_original` to `talking-head-rough-cut`.
   Keep B-roll visually available, but set `broll_visual` clips to `mute`.
5. Render every approved narration plan into a silent visual MP4, narration
   M4A, review MP4, and successful QC report. Attach that full artifact set to
   the manifest, then run `captions`. A plan alone never authorizes caption
   generation. `captions` writes a final-timeline `captions.srt`, a
   `speech_timeline.json` source mapping, and `captions.qc.json`. Caption QC
   must pass before effects or draft creation; when an approved script exists,
   pass `--reference-script` so unacknowledged sentence-tail truncation blocks
   the workflow.

## Roles And Audio Policies

Use the narrowest accurate role:

- `primary_narration`: carries the main explanation; normally `keep_original`.
- `secondary_speech`: an interview, quotation, or hand-off that remains in the
  final narrative; normally `keep_original`.
- `broll_visual`: illustrative footage; use `mute` unless its sound has an
  explicit editorial purpose.
- `ambient_broll`: visual footage with useful real-world sound; use
  `duck_under_primary` or `extract_as_sfx` only when it supports the message.
- `music` or `sfx`: audio-led source; do not send it to speech rough cutting.
- `exclude`: do not place it on the final timeline.

Never choose `keep_original` for `broll_visual`, or `mute` for a narration
source, without an explicit reason and review. If the transcript is missing or
the audio's editorial meaning cannot be established, set `review_required`.

## Caption Contract

`captions.srt` represents the final narration timeline, not the timestamp of
any raw camera file. `speech_timeline.json` retains the source video, source
timestamps, final timestamps, and source role for every caption. Pass both to
`jianying-asset-director`; use them to align effects, SFX, PiP, and subtitle
safe zones with the final edit.

This contract assumes that silent B-roll overlays the narration timeline. Do
not insert a standalone visual-only duration between narration sources after
captions are generated: it would shift every later subtitle. If an edit needs
such inserted material, rebuild the speech timeline and SRT after the final
timeline is known.

Caption QC rejects SRT/timeline count, text, or timing disagreement, overlap,
and a short reference-script sentence tail skipped before the next subtitle.
Longer script differences are reported for editorial review because they may be
approved paraphrases or semantic removals. An externally supplied SRT requires
its speech timeline and the same `caption-qc` gate.

Read [references/contracts.md](references/contracts.md) before creating or
modifying decision files. Use `scripts/media_role_director.py --help` for the
deterministic intake, validation, rough-cut attachment, and caption commands.
