import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MEDIA_ROLE_SCRIPT = ROOT / "skills" / "media-role-director" / "scripts" / "media_role_director.py"
DRAFT_CAPTION_SCRIPT = ROOT / "scripts" / "validate_draft_captions.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


media_role = load_module("media_role_director", MEDIA_ROLE_SCRIPT)
draft_captions = load_module("validate_draft_captions", DRAFT_CAPTION_SCRIPT)


class CaptionQcTests(unittest.TestCase):
    def write(self, directory, name, value):
        path = Path(directory) / name
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def test_caption_qc_rejects_reference_tail_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            srt = self.write(directory, "captions.srt", "1\n00:00:00,000 --> 00:00:02,000\n心脏骤停，家属只能眼睁睁看着，一点办\n\n2\n00:00:02,000 --> 00:00:04,000\n正确做法是立刻拨打120\n")
            timeline = self.write(directory, "speech_timeline.json", {"duration": 4.0, "captions": [
                {"timeline_start": 0.0, "timeline_end": 2.0, "text": "心脏骤停，家属只能眼睁睁看着，一点办"},
                {"timeline_start": 2.0, "timeline_end": 4.0, "text": "正确做法是立刻拨打120"},
            ]})
            reference = self.write(directory, "reference.txt", "心脏骤停，家属只能眼睁睁地看着，一点办法都没有。正确做法是立刻拨打120。")
            report = media_role.caption_qc(srt, timeline, reference)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["truncations"][0]["missing_prefix"], "法都没有")

    def test_caption_qc_accepts_matching_srt_and_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            srt = self.write(directory, "captions.srt", "1\n00:00:00,000 --> 00:00:02,000\n完整字幕\n")
            timeline = self.write(directory, "speech_timeline.json", {"duration": 2.0, "captions": [
                {"timeline_start": 0.0, "timeline_end": 2.0, "text": "完整字幕"},
            ]})
            report = media_role.caption_qc(srt, timeline)
            self.assertEqual(report["status"], "succeeded")

    def test_draft_caption_validation_rejects_text_mismatch(self):
        document = {
            "materials": {"texts": [{"id": "caption", "content": json.dumps({"text": "错误字幕"}, ensure_ascii=False)}]},
            "tracks": [{"type": "text", "name": "Subtitles", "segments": [{
                "material_id": "caption", "target_timerange": {"start": 0, "duration": 2_000_000},
            }]}],
        }
        report = draft_captions.validate(document, [{"start": 0.0, "end": 2.0, "text": "正确字幕"}], "Subtitles")
        self.assertEqual(report["status"], "failed")
        self.assertIn("subtitle text differs at entry 1", report["errors"])

    def test_draft_caption_validation_accepts_exact_materialization(self):
        document = {
            "materials": {"texts": [{"id": "caption", "content": json.dumps({"text": "正确字幕"}, ensure_ascii=False)}]},
            "tracks": [{"type": "text", "name": "Subtitles", "segments": [{
                "material_id": "caption", "target_timerange": {"start": 0, "duration": 2_000_000},
            }]}],
        }
        report = draft_captions.validate(document, [{"start": 0.0, "end": 2.0, "text": "正确字幕"}], "Subtitles")
        self.assertEqual(report["status"], "passed")


if __name__ == "__main__":
    unittest.main()
