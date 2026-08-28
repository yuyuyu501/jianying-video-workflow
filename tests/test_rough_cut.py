import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "skills" / "talking-head-rough-cut" / "scripts" / "rough_cut.py"
SPEC = importlib.util.spec_from_file_location("rough_cut", MODULE_PATH)
rough_cut = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(rough_cut)


class RoughCutPaceTests(unittest.TestCase):
    def analysis(self, folder: Path, characters: int, duration: float, name: str = "analysis.json") -> Path:
        path = folder / name
        path.write_text(json.dumps({
            "duration": duration,
            "transcript": [{"start": 0.0, "end": duration, "text": "测" * characters}],
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def test_short_pause_is_tightened_to_total_target(self):
        exclusion = rough_cut.pause_exclusion(10.0, 10.25, 0.18)
        self.assertIsNotNone(exclusion)
        start, end = exclusion
        self.assertAlmostEqual((10.25 - end) + (start - 10.0), 0.18, places=6)
        self.assertAlmostEqual(end - start, 0.07, places=6)

    def test_adjacent_silence_fragments_are_merged_before_tightening(self):
        merged = rough_cut.merge_silence_ranges([
            (31.519, 31.755),
            (31.755, 31.959),
        ])
        self.assertEqual(merged, [(31.519, 31.959)])
        exclusion = rough_cut.pause_exclusion(*merged[0], 0.18)
        self.assertIsNotNone(exclusion)
        self.assertAlmostEqual(exclusion[1] - exclusion[0], 0.26, places=6)

    def test_silence_fragments_with_real_speech_gap_stay_separate(self):
        merged = rough_cut.merge_silence_ranges([
            (10.0, 10.2),
            (10.24, 10.5),
        ])
        self.assertEqual(merged, [(10.0, 10.2), (10.24, 10.5)])

    def test_no_speed_when_density_is_in_band(self):
        with tempfile.TemporaryDirectory() as temporary:
            analysis = self.analysis(Path(temporary), 100, 20.0)
            result = rough_cut.pace_analysis(
                analysis, [{"start": 0.0, "end": 20.0}], mode="auto",
                target_cpm=285.0, minimum_cpm=260.0, maximum_speed=1.35,
            )
        self.assertEqual(result["decision"], "no_change")
        self.assertEqual(result["applied_speed"], 1.0)

    def test_auto_speed_uses_target_density(self):
        with tempfile.TemporaryDirectory() as temporary:
            analysis = self.analysis(Path(temporary), 100, 24.0)
            result = rough_cut.pace_analysis(
                analysis, [{"start": 0.0, "end": 24.0}], mode="auto",
                target_cpm=285.0, minimum_cpm=260.0, maximum_speed=1.35,
            )
        self.assertEqual(result["decision"], "speed_up")
        self.assertAlmostEqual(result["recommended_speed"], 1.14, places=3)
        self.assertAlmostEqual(result["estimated_output_duration"], 24.0 / 1.14, places=3)

    def test_speed_cap_requires_content_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            analysis = self.analysis(Path(temporary), 100, 30.0)
            result = rough_cut.pace_analysis(
                analysis, [{"start": 0.0, "end": 30.0}], mode="auto",
                target_cpm=300.0, minimum_cpm=260.0, maximum_speed=1.35,
            )
        self.assertEqual(result["decision"], "speed_up_and_review_content")
        self.assertEqual(result["applied_speed"], 1.35)
        self.assertTrue(rough_cut.pace_requires_review({"pace_analysis": result}))

    def test_template_analysis_sets_target_density(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            analysis = self.analysis(folder, 100, 25.0)
            reference = self.analysis(folder, 80, 16.0, "template.json")
            result = rough_cut.pace_analysis(
                analysis, [{"start": 0.0, "end": 25.0}], mode="auto",
                target_cpm=285.0, minimum_cpm=260.0, maximum_speed=1.35,
                reference_analysis=reference,
            )
        self.assertEqual(result["target_chars_per_minute"], 300.0)
        self.assertIsNotNone(result["reference"])

    def test_render_graph_speeds_audio_and_video_together(self):
        graph = rough_cut.render_filter_graph([{"start": 0.0, "end": 2.0}], 1.2)
        self.assertIn("setpts=PTS/1.200000", graph)
        self.assertIn("atempo=1.200000", graph)

    def test_normal_speed_decision_does_not_require_review(self):
        self.assertFalse(rough_cut.pace_requires_review({"pace_analysis": {"decision": "speed_up"}}))


if __name__ == "__main__":
    unittest.main()
