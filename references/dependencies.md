# Dependency Policy

## Bundled Skills

- `skills/talking-head-rough-cut`: plans conservative talking-head cuts and
  renders accepted plans with FFmpeg.
- `skills/jianying-asset-director`: catalogs and matches JianYing effects and
  sound effects after the rough cut is accepted.

These are maintained in this repository and installed as sibling Codex Skills.
The former standalone `jianying-asset-director` repository is not a runtime
dependency of this workflow.

## Verified External Skills

- `video-understand`: `https://github.com/MomoFadaly/video-understand.git`
  at `4848131e123fb868a6ae6a4f7fef33a82a0119df` (Apache-2.0)
- `jianying-editor`:
  `https://github.com/luoluoluo22/jianying-editor-skill.git` at
  `f421c8a036f4fda888a83b38fc90bb9c00d6faa9` (MIT)

The installer downloads source archives for these exact Git revisions. It does
not infer repositories from Skill names. Pass `--skip-external` when another
version is managed locally, or `--upgrade-external` to re-install the pinned
target version.

## Machine Dependencies

- Python 3.10+
- FFmpeg and ffprobe on `PATH`
- JianYing Pro for draft creation
- A local JianYing asset index for visual-effect and sound-effect lookup

Set `PYTHONUTF8=1` on Windows when running `video-understand`; this prevents
GBK decoding failures from FFmpeg or ffprobe output.
