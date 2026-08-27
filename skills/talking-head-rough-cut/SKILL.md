---
name: talking-head-rough-cut
description: Plan and render speech-first rough cuts for talking-head videos. Use to remove retakes, false starts, filler speech, irrelevant dialogue, repeated phrases, and excessive pauses while preserving complete words, sentence meaning, natural breathing, and emphasis. Produces an auditable cut plan and uses FFmpeg to render the accepted edit.
---

# Talking Head Rough Cut

Use this Skill after `video-understand` and before effects, captions, or a
JianYing draft. Keep the source video untouched. Write all artifacts under the
current job directory.

## Required Process

1. Obtain video duration, transcript, and representative frames with
   `video-understand`. Inspect the transcript and the source around every
   potential semantic cut.
2. If a script, brief, or approved copy exists, pass it with
   `--reference-script`. Do not claim script-led editing without this input.
3. Identify four kinds of cuts: unrelated setup or off-topic dialogue, short
   and long empty pauses or excess air, retakes/self-corrections/filler speech, and
   repeated content or over-emphasis that has already been stated clearly.
4. Create a candidate plan. Use `scripts/rough_cut.py plan` to detect pauses
   from 0.15 seconds and shorten removable air to a default 0.18-second total
   gap. Supply reviewed semantic removals with `--semantic-exclusions`.
5. Review `pace_analysis` after cuts. It must state measured characters per
   minute, whether speed is needed, the recommended and applied synchronized
   speed, and the resulting duration. A template `video-understand` JSON may
   supply the target pace with `--pace-reference-analysis`.
6. Show the plan before rendering unless the user has explicitly requested the
   edit to proceed. State removed duration and reasons for every semantic cut.
7. Render the accepted plan with `scripts/rough_cut.py render`. It must produce
   a silent visual MP4, a narration-only M4A, and a muxed review MP4 from the
   same EDL. Run `validate` before any captions, effects, or draft creation.
   Inspect the opening, every cut boundary, and the end of the review MP4.
   Validation must also compare the independent visual and narration durations
   directly; a difference above 0.1 seconds fails even when both are near the EDL.
   After caption generation, require caption QC against the final SRT, speech
   timeline, and approved reference script before effects.

## Cut Rules

- Never cut inside a spoken word, sentence, medical instruction, quoted claim,
  laugh, or intentional reaction.
- Do not delete every silence. Preserve about 0.18 seconds total at a normal
  phrase boundary. Keep longer pauses that create emphasis or let an important
  instruction land.
- Treat a detected silence as a candidate, not proof that it should disappear.
  The planner only removes the middle of gaps above its threshold.
- Remove a retake, repeated sentence, filler, or unrelated exchange only when
  the remaining adjacent speech is grammatically and semantically coherent.
- For a script-led edit, compare the surviving speech against the reference
  script. The opening must begin with the first on-topic line; production
  chatter before it is a semantic cut candidate. Repeated explanations,
  repeated emphasis, and on-camera corrections must be listed with timestamps.
- The plan must distinguish `script_alignment`, `repeat`, `filler`, `retake`,
  and `off_topic` findings. A finding is not rendered until it is approved in
  `--semantic-exclusions`.
- Tighten excessive gasps and dead air only at safe acoustic boundaries. Keep
  about 0.18 seconds of natural air across normal phrase boundaries and never
  cut a consonant, syllable, medication name, dosage, warning, or emergency
  instruction.
- Record a reason and source timestamp for every semantic removal. Mark
  ambiguous material as `review`, not `remove`.
- Apply 30 ms audio fades at every retained-range boundary and normalize the
  narration. Do not use segment-file copy concatenation for speech audio: use
  one EDL-driven filter graph so every retained range has continuous timestamps.
- Treat visual and narration as separate deliverables. The visual file must
  contain no audio stream; the narration file must cover the final EDL without
  unplanned long silences. Do not overwrite the source or an existing output
  without an explicit `--overwrite`.
- Decide speed after pause and semantic cuts, not from raw-camera duration.
  Measure non-punctuation transcript characters per minute. Apply the same
  factor to visual PTS and narration `atempo`; never accelerate only one track.
  If the required speed exceeds the configured cap, revisit semantic repetition
  and mark the pace decision for review instead of hiding the problem with an
  extreme speed.

## Commands

```powershell
# Candidate plan: only excessive silence is removed automatically.
python scripts/rough_cut.py plan `
  --video "C:\path\source.mp4" `
  --analysis "work\understanding.json" `
  --reference-script "C:\path\approved_script.txt" `
  --pace-mode auto `
  --target-cpm 285 `
  --minimum-cpm 260 `
  --output "work\rough_cut_plan.json"

# Add approved semantic removals. The JSON may be an array or use an
# `exclusions` key; each item needs start, end, and reason.
python scripts/rough_cut.py plan `
  --video "C:\path\source.mp4" `
  --analysis "work\understanding.json" `
  --semantic-exclusions "work\approved_semantic_cuts.json" `
  --output "work\rough_cut_plan.json"

python scripts/rough_cut.py render `
  --plan "work\rough_cut_plan.json" `
  --output "work\rough_cut_review.mp4" `
  --visual-output "work\rough_cut.visual.mp4" `
  --narration-output "work\rough_cut.narration.m4a"

python scripts/rough_cut.py validate `
  --plan "work\rough_cut_plan.json" `
  --output "work\rough_cut_review.mp4" `
  --visual "work\rough_cut.visual.mp4" `
  --narration "work\rough_cut.narration.m4a" `
  --max-residual-pause 0.35 `
  --qc-output "work\rough_cut.qc.json"
```

When `pace_analysis.decision` is `review_required`, `speed_review_required`, or
`speed_up_and_review_content`, rendering stops. Inspect the semantic cuts and
audition the proposed speed, then pass `--approve-pace-review` only after the
decision is accepted.

Read [references/cut-policy.md](references/cut-policy.md) before changing the
default pause policy or accepting ambiguous semantic cuts.
