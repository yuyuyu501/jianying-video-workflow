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
3. Identify four kinds of cuts: unrelated setup or off-topic dialogue, long
   empty pauses and excess air, retakes/self-corrections/filler speech, and
   repeated content or over-emphasis that has already been stated clearly.
4. Create a candidate plan. Use `scripts/rough_cut.py plan` to shorten only
   excess silence automatically. Supply reviewed semantic removals with
   `--semantic-exclusions`.
5. Show the plan before rendering unless the user has explicitly requested the
   edit to proceed. State removed duration and reasons for every semantic cut.
6. Render the accepted plan with `scripts/rough_cut.py render`, then run
   `validate`. Inspect the opening, every cut boundary, and the end of the
   rendered video.

## Cut Rules

- Never cut inside a spoken word, sentence, medical instruction, quoted claim,
  laugh, or intentional reaction.
- Do not delete every silence. Preserve 0.20-0.35 seconds of air around a
  normal phrase boundary. Keep pauses that create emphasis or let an important
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
  0.20-0.35 seconds of natural air around normal phrase boundaries and never
  cut a consonant, syllable, medication name, dosage, warning, or emergency
  instruction.
- Record a reason and source timestamp for every semantic removal. Mark
  ambiguous material as `review`, not `remove`.
- Apply 30 ms audio fades at every retained-range boundary and normalize the
  final audio. Do not overwrite the source or an existing output without an
  explicit `--overwrite`.

## Commands

```powershell
# Candidate plan: only excessive silence is removed automatically.
python scripts/rough_cut.py plan `
  --video "C:\path\source.mp4" `
  --analysis "work\understanding.json" `
  --reference-script "C:\path\approved_script.txt" `
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
  --output "work\rough_cut_preview.mp4"

python scripts/rough_cut.py validate `
  --plan "work\rough_cut_plan.json" `
  --output "work\rough_cut_preview.mp4"
```

Read [references/cut-policy.md](references/cut-policy.md) before changing the
default pause policy or accepting ambiguous semantic cuts.
