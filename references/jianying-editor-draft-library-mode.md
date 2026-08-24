# JianYing Editor: Draft-Library-Only Mode

When `jianying-editor` is installed by `jianying-video-workflow`, restrict it
to manipulating the local JianYing draft library.

Allowed operations:

- create, read, repair, and inspect draft files;
- import media and write timeline, caption, effect, and audio metadata into a
  draft;
- return the draft name and absolute project-directory path;
- use the draft inspector for structural validation.

Forbidden operations:

- launch JianYing Pro or require it to be running;
- automate or drive the JianYing UI;
- invoke `JianyingController`, `auto_exporter.py`, `export_draft`, or any
  native MP4 export path;
- describe the draft as an exported video deliverable.

The workflow endpoint is a validated draft-library project. The user opens it
and exports it manually outside this workflow. Do not run `api_validator.py`
for a connection check: it creates a diagnostic draft and can report a false
failure under Windows GBK output.
