# Media Role Contracts

## Decisions

Pass `apply-decisions` a JSON object with a `decisions` array, or an array.
Every source from the intake must have exactly one decision.

```json
{
  "decisions": [
    {
      "source_id": "source_01",
      "role": "primary_narration",
      "audio_policy": "keep_original",
      "timeline_order": 1,
      "reason": "Doctor explains the three emergency steps.",
      "confidence": 0.98,
      "review_required": false
    },
    {
      "source_id": "source_02",
      "role": "broll_visual",
      "audio_policy": "mute",
      "reason": "Illustrates chest pain but contains unrelated source audio.",
      "confidence": 0.94,
      "review_required": false
    }
  ]
}
```

`timeline_order` is required for every `keep_original` narration source and
defines the order used by the global SRT. It need not be supplied for silent
B-roll. `confidence` is a number from 0 to 1. Low confidence or an ambiguous
audio role must set `review_required` to `true`.

## Manifest And Captions

`media_manifest.json` preserves the input video and `video-understand` output
for each source, together with the reviewed decision. A narration source is
ready for captions only after its rough-cut plan is accompanied by four
validated artifacts: `visual` (silent MP4), `narration` (M4A), `review`
(muxed MP4), and `qc` (successful JSON report). `captions` then uses the kept
source ranges to map transcript segments onto the compressed global speech
timeline.

The visual and narration files are separate on purpose. Draft assembly imports
the visual on a video track and narration on a named audio track; B-roll must
not provide narration audio. A QC report with an error, a missing narration
artifact, or an unplanned long silence blocks caption generation.

It writes two files:

- `captions.srt`: text plus final timeline start/end times.
- `speech_timeline.json`: the same final times plus `source_id`, source video,
  original source start/end, role, and audio policy.

It also writes `captions.qc.json`. This report must have `status: succeeded`
before asset selection or draft construction. When a reference script is
available, pass it to `captions --reference-script`; caption QC rejects a
short subtitle tail that is skipped before the next subtitle begins. Longer
script differences remain explicit editorial-review findings because they may
be approved paraphrases or semantic removals.

The mapping expects cuts to occur between spoken phrases. If a proposed cut
splits a Whisper segment, review the result before using it for final captions.

Silent B-roll is an overlay on this speech-first timeline. An inserted visual
section that adds duration must be reflected by rebuilding the speech timeline
and SRT before it reaches `jianying-asset-director`.
