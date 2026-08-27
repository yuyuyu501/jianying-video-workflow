# Talking-Head Cut Policy

## Pause Bands

| Gap duration | Default treatment |
| --- | --- |
| Under 0.15 s | Keep. It may be an acoustic dip inside a word. |
| 0.15-0.35 s | Detect and tighten when there is at least 0.06 s of removable air. Retain about 0.18 s total, centered on the original gap. |
| 0.35-0.85 s | Tighten by default to about 0.18 s total unless transcript and picture show intentional emphasis. |
| Over 0.85 s | Tighten and inspect the cut boundary; do not assume a long gap is editorially intentional. |
| Over 2.0 s | Review picture and audio. It may be a deliberate visual beat. |

Detection starts at 0.15 seconds, but detection is not permission to clip speech.
Inspect low-energy consonants, breaths attached to words, medication names,
dosages, and emergency instructions. QC analyzes the rendered narration again
and fails when unexplained pauses above the configured residual-pause limit
remain.
Duration comparison allows only frame-level rounding accumulated across the
retained ranges; it must not hide a real visual/narration mismatch.

## Pace Decision

Measure pace only after semantic cuts and pause tightening. Use non-punctuation
transcript characters per minute so silence and speaking rate are not confused.

- Below 260 characters/minute: recommend synchronized audio/video speed-up.
- Target 285 characters/minute by default for short-form educational speech.
- At or above 260 characters/minute: do not speed up merely to reach the target.
- A supplied template-analysis JSON replaces the default target with the
  template's measured transcript density.
- Apply the same speed to video PTS and narration `atempo`. Never speed only the
  detached narration track.
- Cap automatic speed at 1.35x. If the uncapped recommendation is higher, apply
  no more than the cap and review repeated or off-topic content again.
- Generate captions and every downstream timestamp only from the rendered,
  speed-adjusted rough cut.

## Semantic Removal

Approve a semantic exclusion only when one of these applies:

- the speaker restarts the same sentence or thought;
- the content is a clear self-correction or verbal dead end;
- it is unrelated setup, production chatter, or off-topic dialogue;
- a later, cleaner take preserves the intended message.

Do not remove qualification, uncertainty, safety language, audience questions,
or emotional reaction merely to make the video shorter.

## Script Comparison

When a reference script is available, it is part of the editorial contract, not
just a caption source. The first surviving spoken line must be the first
on-topic line from the script. Before rendering, review every candidate in these
categories:

- `off_topic`: production chatter, greetings, setup, or dialogue unrelated to
  the approved subject;
- `retake`: a false start, self-correction, or abandoned sentence followed by a
  clean take;
- `repeat`: the same fact, instruction, or transition repeated later;
- `filler`: standalone filler words or verbal dead ends that can be removed
  without changing meaning;
- `script_alignment`: speech that cannot be matched to the approved copy.

The candidate report must show the source range, transcript text, matching copy
when available, category, and decision. Only `remove` decisions supplied as
semantic exclusions may affect the render. Safety language and the first clean
version of an instruction take precedence over brevity.

## Semantic Exclusion Format

```json
{
  "exclusions": [
    {
      "start": 50.64,
      "end": 64.24,
      "reason": "Repeated introduction replaced by the clean take"
    }
  ]
}
```

The plan keeps `review_candidates` separate from removals. A candidate never
changes the rendered output until it is copied into `exclusions`.
