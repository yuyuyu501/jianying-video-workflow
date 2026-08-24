# Dependency Notes

## Public dependency

`jianying-asset-director` is downloaded from:

`git@github.com:yuyuyu501/jianying-asset-director.git`

## Codex-local dependencies

The current `video-understand` and `jianying-editor` installations do not
contain verified upstream repository URLs. The installer checks these paths:

- `$CODEX_HOME/skills/<name>` when `CODEX_HOME` is set
- `~/.codex/skills/<name>`
- `~/.agents/skills/<name>`

Pass `--video-understand-repo` or `--jianying-editor-repo` only after verifying
the repository is the intended Skill and its license permits redistribution.

## Machine dependencies

- Python 3.10+
- Git
- FFmpeg and ffprobe on `PATH`
- JianYing Pro installed and available to the user
- A local JianYing asset index for effect and sound-effect lookup

The installer reports missing system dependencies. It does not silently alter
system packages or log into JianYing.
