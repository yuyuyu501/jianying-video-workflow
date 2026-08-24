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

### `jianying-editor` Integration Mode

This workflow uses the external `jianying-editor` dependency in
**draft-library-only mode**. Its allowed scope is creating, reading, repairing,
and structurally inspecting local JianYing draft files. It must not launch
JianYing Pro, automate its interface, call `JianyingController`, or use
`auto_exporter.py` / `export_draft`.

The workflow returns an editable draft and its absolute path. MP4 export is a
manual user operation outside the workflow. For non-mutating diagnostics use
`draft_inspector.py`; do not run `api_validator.py`, because it creates a
diagnostic project and its Unicode status output can fail under Windows GBK.

The installer downloads source archives for these exact Git revisions. It does
not infer repositories from Skill names. Pass `--skip-external` when another
version is managed locally, or `--upgrade-external` to re-install the pinned
target version.

## Machine Dependencies

- Python 3.10+
- FFmpeg and ffprobe on `PATH`
- JianYing Pro installed locally for draft-library compatibility; it does not
  need to be running and is never launched by this workflow
- A local JianYing asset index for visual-effect and sound-effect lookup

Set `PYTHONUTF8=1` on Windows when running `video-understand`; this prevents
GBK decoding failures from FFmpeg or ffprobe output.
