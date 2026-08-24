# Talking-Head Cut Policy

## Pause Bands

| Gap duration | Default treatment |
| --- | --- |
| Under 0.45 s | Keep. This is normal speech rhythm. |
| 0.45-0.85 s | Review in context. Keep unless it interrupts pace. |
| Over 0.85 s | Shorten the middle while retaining 0.20-0.35 s at both sides. |
| Over 2.0 s | Review picture and audio. It may be a deliberate visual beat. |

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
