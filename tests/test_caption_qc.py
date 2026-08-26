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
            timeline = self.write(directory, "speech_timeline.json", {"duration": 4.0, "timestamp_basis": "rendered_rough_cut_output", "speech_ranges": [{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 4.0}], "captions": [
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
            timeline = self.write(directory, "speech_timeline.json", {"duration": 2.0, "timestamp_basis": "rendered_rough_cut_output", "speech_ranges": [{"start": 0.0, "end": 2.0}], "captions": [
                {"timeline_start": 0.0, "timeline_end": 2.0, "text": "完整字幕"},
            ]})
            report = media_role.caption_qc(srt, timeline)
            self.assertEqual(report["status"], "succeeded")

    def test_caption_qc_rejects_timeline_without_rendered_rough_cut_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            srt = self.write(directory, "captions.srt", "1\n00:00:00,000 --> 00:00:02,000\n字幕\n")
            timeline = self.write(directory, "speech_timeline.json", {"duration": 2.0, "captions": [
                {"timeline_start": 0.0, "timeline_end": 2.0, "text": "字幕"},
            ]})
            report = media_role.caption_qc(srt, timeline)
            self.assertEqual(report["status"], "failed")
            self.assertIn("speech timeline must use rendered_rough_cut_output as its timestamp basis", report["errors"])

    def test_caption_qc_rejects_uncovered_opening_rough_cut_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            srt = self.write(directory, "captions.srt", "1\n00:00:07,000 --> 00:00:10,000\n第一条字幕\n")
            timeline = self.write(directory, "speech_timeline.json", {
                "duration": 10.0,
                "timestamp_basis": "rendered_rough_cut_output",
                "speech_ranges": [{"start": 0.0, "end": 6.8}, {"start": 7.0, "end": 10.0}],
                "captions": [{"timeline_start": 7.0, "timeline_end": 10.0, "text": "第一条字幕"}],
            })
            report = media_role.caption_qc(srt, timeline)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any("opening speech is uncovered" in error for error in report["errors"]))

    def test_caption_qc_accepts_rough_cut_speech_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            srt = self.write(directory, "captions.srt", "1\n00:00:00,000 --> 00:00:02,000\n开场字幕\n\n2\n00:00:02,000 --> 00:00:04,000\n结尾字幕\n")
            timeline = self.write(directory, "speech_timeline.json", {
                "duration": 4.0,
                "timestamp_basis": "rendered_rough_cut_output",
                "speech_ranges": [{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 4.0}],
                "captions": [
                    {"timeline_start": 0.0, "timeline_end": 2.0, "text": "开场字幕"},
                    {"timeline_start": 2.0, "timeline_end": 4.0, "text": "结尾字幕"},
                ],
            })
            report = media_role.caption_qc(srt, timeline)
            self.assertEqual(report["status"], "succeeded")
            self.assertEqual(report["rough_cut_coverage"]["status"], "passed")

    def test_caption_split_breaks_at_commas_with_continuous_timestamps(self):
        entries = media_role.split_caption_entry({"start": 10.0, "end": 14.0, "text": "第一步，立刻坐下，保持半卧位。"})
        self.assertEqual([entry["text"] for entry in entries], ["第一步", "立刻坐下", "保持半卧位"])
        self.assertEqual(entries[0]["start"], 10.0)
        self.assertEqual(entries[-1]["end"], 14.0)
        self.assertTrue(all(left["end"] == right["start"] for left, right in zip(entries, entries[1:])))

    def test_caption_split_hard_wraps_long_unpunctuated_text(self):
        text = "这是一个没有标点但是需要被拆分成短字幕的长句子为了便于阅读"
        entries = media_role.split_caption_entry({"start": 0.0, "end": 6.0, "text": text}, max_chars=10)
        self.assertGreater(len(entries), 1)
        self.assertTrue(all(media_role.caption_reading_weight(entry["text"]) <= 10 for entry in entries))
        self.assertEqual("".join(entry["text"] for entry in entries), text)

    def test_caption_split_removes_all_display_punctuation(self):
        entries = media_role.split_caption_entry({"start": 0.0, "end": 3.0, "text": "赶紧坐下，立刻拨打120！"})
        self.assertEqual([entry["text"] for entry in entries], ["赶紧坐下", "立刻拨打120"])

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

    def test_draft_caption_validation_requires_real_style_variation(self):
        document = {
            "materials": {"texts": [
                {"id": "one", "content": json.dumps({"text": "普通字幕", "styles": [{}]}, ensure_ascii=False)},
                {"id": "two", "content": json.dumps({"text": "气泡字幕", "styles": [{}]}, ensure_ascii=False), "background_style": 1},
            ]},
            "tracks": [{"type": "text", "name": "Subtitles", "segments": [
                {"material_id": "one", "target_timerange": {"start": 0, "duration": 1_000_000}, "clip": {"transform": {"y": -0.72}}},
                {"material_id": "two", "target_timerange": {"start": 1_000_000, "duration": 1_000_000}, "clip": {"transform": {"y": -0.08}}},
            ]}],
        }
        entries = [{"start": 0.0, "end": 1.0, "text": "普通字幕"}, {"start": 1.0, "end": 2.0, "text": "气泡字幕"}]
        report = draft_captions.validate(document, entries, "Subtitles", require_style_variation=True)
        self.assertEqual(report["status"], "passed", report["errors"])

    def test_draft_caption_validation_recognizes_linked_flower_material(self):
        document = {
            "materials": {
                "texts": [
                    {"id": "one", "content": json.dumps({"text": "普通字幕", "styles": [{}]}, ensure_ascii=False)},
                    {"id": "two", "content": json.dumps({"text": "花字字幕", "styles": [{}]}, ensure_ascii=False)},
                ],
                "effects": [{"id": "flower-effect", "type": "text_effect"}],
            },
            "tracks": [{"type": "text", "name": "Subtitles", "segments": [
                {"material_id": "one", "target_timerange": {"start": 0, "duration": 1_000_000}, "clip": {"transform": {"y": -0.72}}},
                {"material_id": "two", "target_timerange": {"start": 1_000_000, "duration": 1_000_000}, "extra_material_refs": ["flower-effect"], "clip": {"transform": {"y": -0.08}}},
            ]}],
        }
        entries = [{"start": 0.0, "end": 1.0, "text": "普通字幕"}, {"start": 1.0, "end": 2.0, "text": "花字字幕"}]
        report = draft_captions.validate(document, entries, "Subtitles", require_style_variation=True)
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(report["style_types"], ["base", "flower"])


if __name__ == "__main__":
    unittest.main()
