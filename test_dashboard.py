import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scout import Contact, RoundRecord
import dashboard
from dashboard import _circle_figure, _project_grid_position, build_dashboard_data


BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _round(number, opponent, *, side="defender", operator=True, position=None, contact_seconds=3):
    contact = None
    if position is not None:
        contact = Contact(
            occurred_at=BASE_TIME + timedelta(seconds=contact_seconds),
            opponent="enemy",
            position=position,
        )
    return RoundRecord(
        source="feed.jsonl.zst",
        game_id=f"game-{opponent}-{number}",
        round_id=f"round-{number}",
        number=number,
        map_name="Split",
        dgz_id="dgz",
        dgz_team_id="loud",
        dgz_team="LOUD",
        opponent=opponent,
        side=side,
        started_at=BASE_TIME,
        inventory={"operator"} if operator else {"classic"},
        barrier_seen=True,
        barrier_drop=BASE_TIME,
        operator_at_drop=operator,
        contact=contact,
        winner="LOUD",
    )


class DashboardDataTests(unittest.TestCase):
    def test_multi_match_area_uses_only_the_first_two_matches(self):
        rounds, matches = dashboard._multi_match_sample()
        study = dashboard.build_match_study(rounds, matches)
        reference_sources = list(matches)[:2]
        references = [row for row in rounds if row.source in reference_sources]
        reference_only = dashboard.build_match_study(references, matches)

        self.assertEqual(reference_only.circle, study.circle)
        self.assertEqual(list(matches.values())[2:], list(study.scored_matches))
        self.assertEqual(6, sum(len(rows) for rows in study.scored_matches.values()))
        self.assertEqual(2, sum(row.result == "hit" for rows in study.scored_matches.values() for row in rows))

    def test_g2_reveal_uses_real_feeds_when_available(self):
        from streamlit.testing.v1 import AppTest

        if any(not (Path.cwd() / feed.filename).is_file() for feed in dashboard.G2_FEEDS):
            self.skipTest("G2 GRID feeds are required for this integration test")
        app = AppTest.from_file(str(Path(__file__).with_name("dashboard.py")), default_timeout=120).run()
        app.radio[0].set_value("jawgemo / G2").run()
        self.assertFalse(app.exception)
        self.assertFalse(app.metric)
        app.checkbox[0].check().run()
        self.assertEqual("0 of 12", app.metric[0].value)
        self.assertFalse(app.exception)

    def test_grid_positions_project_to_split_minimap_coordinates(self):
        projected = _project_grid_position((-3142.589743589744, -6354.974358974359))

        self.assertAlmostEqual(0.3465, projected[0], places=7)
        self.assertAlmostEqual(0.9427, projected[1], places=7)

    def test_sentinels_contacts_never_change_reference_area_fit(self):
        references = [
            _round(1, "Evil Geniuses", position=(0.0, 0.0)),
            _round(2, "KR�", position=(100.0, 0.0)),
        ]
        sentinel = _round(9, "Sentinels", position=(100000.0, 100000.0))

        data = build_dashboard_data(references + [sentinel])

        self.assertEqual([(0.0, 0.0), (100.0, 0.0)], data.reference_points)
        self.assertEqual(2, data.circle.coverage_count)
        self.assertEqual((0.0, 0.0), data.circle.center)

    def test_rounds_five_to_ten_keep_no_contact_in_eligible_headline(self):
        references = [_round(1, "Evil Geniuses", position=(1000.0, 2000.0))]
        sentinels = [
            _round(number, "Sentinels", position=(1000.0, 2000.0) if number == 9 else None)
            for number in range(5, 11)
        ]

        data = build_dashboard_data(references + sentinels)

        self.assertEqual(list(range(5, 11)), [row.record.number for row in data.revealed_rounds])
        self.assertEqual(6, len(data.revealed_eligible_rounds))
        self.assertEqual(1, sum(row.result == "hit" for row in data.revealed_eligible_rounds))
        self.assertEqual(5, sum(row.result == "no observed contact" for row in data.revealed_eligible_rounds))
        round_nine = next(row for row in data.revealed_rounds if row.record.number == 9)
        self.assertEqual((1000.0, 2000.0), round_nine.record.contact.position)
        self.assertEqual(BASE_TIME + timedelta(seconds=3), round_nine.record.contact.occurred_at)

    def test_invented_sample_rounds_demonstrate_hit_miss_and_unobserved_contact(self):
        data = build_dashboard_data(dashboard._sample_rounds())

        self.assertEqual(["Sample Evil Geniuses", "Sample KR�"], [row.opponent for row in data.reference_rounds])
        self.assertEqual(2, data.circle.coverage_count)
        self.assertEqual(list(range(5, 11)), [row.record.number for row in data.revealed_rounds])
        self.assertEqual(2, sum(row.result == "hit" for row in data.revealed_eligible_rounds))
        self.assertEqual(2, sum(row.result == "miss" for row in data.revealed_eligible_rounds))
        self.assertEqual(1, sum(row.result == "no observed contact" for row in data.revealed_eligible_rounds))
        self.assertEqual(1, sum(row.result == "contact has no position" for row in data.revealed_eligible_rounds))
        round_nine = next(row for row in data.revealed_rounds if row.record.number == 9)
        self.assertEqual((200.0, 0.0), round_nine.record.contact.position)
        self.assertEqual(17, (round_nine.record.contact.occurred_at - round_nine.record.barrier_drop).total_seconds())

    def test_selected_contact_is_highlighted_on_reference_plot(self):
        references = [_round(1, "Evil Geniuses", position=(1000.0, 2000.0))]
        sentinel = _round(9, "Sentinels", position=(1300.0, 2400.0))
        data = build_dashboard_data(references + [sentinel])
        selected = next(row for row in data.revealed_rounds if row.record.number == 9)

        figure = _circle_figure(data, selected)

        highlight = next(trace for trace in figure.data if trace.name == "Selected Sentinels R9 contact")
        projected = _project_grid_position((1300.0, 2400.0))
        self.assertEqual((projected[0],), tuple(highlight.x))
        self.assertEqual((projected[1],), tuple(highlight.y))
        self.assertEqual((1300.0, 2400.0), tuple(highlight.customdata[0]))

    def test_minimap_background_and_full_circle_extent_are_preserved(self):
        reference = _round(1, "Evil Geniuses", position=(0.0, 2000.0))
        data = build_dashboard_data([reference])

        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "split_minimap.png"
            image_path.write_bytes(b"test image")
            with patch.object(dashboard, "SPLIT_MAP_IMAGE", image_path):
                figure = _circle_figure(data)

        image = figure.layout.images[0]
        self.assertTrue(image.source.startswith("data:image/png;base64,"))
        self.assertEqual((0, 0), (image.x, image.y))
        self.assertEqual((1, 1), (image.sizex, image.sizey))
        self.assertEqual("below", image.layer)
        shape = figure.layout.shapes[0]
        self.assertGreater(shape.x1, 1.0)
        self.assertLess(figure.layout.xaxis.range[0], shape.x0)
        self.assertGreater(figure.layout.xaxis.range[1], shape.x1)
        self.assertGreater(figure.layout.yaxis.range[0], figure.layout.yaxis.range[1])
        self.assertEqual("x", figure.layout.yaxis.scaleanchor)
        self.assertEqual(1, figure.layout.yaxis.scaleratio)

    def test_missing_minimap_keeps_circle_and_selected_contact_on_coordinate_plot(self):
        reference = _round(1, "Evil Geniuses", position=(0.0, 2000.0))
        sentinel = _round(9, "Sentinels", position=(1300.0, 2400.0))
        data = build_dashboard_data([reference, sentinel])
        selected = next(row for row in data.revealed_rounds if row.record.number == 9)

        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_image = Path(temporary_directory) / "missing.png"
            with patch.object(dashboard, "SPLIT_MAP_IMAGE", missing_image):
                figure = _circle_figure(data, selected)

        self.assertEqual(0, len(figure.layout.images))
        self.assertEqual(1, len(figure.layout.shapes))
        self.assertEqual("Locked center", figure.data[1].name)
        self.assertEqual("Projected map U", figure.layout.xaxis.title.text)
        self.assertEqual("Projected map V", figure.layout.yaxis.title.text)
        highlight = next(trace for trace in figure.data if trace.name == "Selected Sentinels R9 contact")
        projected = _project_grid_position((1300.0, 2400.0))
        self.assertEqual((projected[0],), tuple(highlight.x))
        self.assertEqual((projected[1],), tuple(highlight.y))
        self.assertGreater(figure.layout.xaxis.range[1], figure.layout.shapes[0].x1)
        self.assertLess(figure.layout.xaxis.range[0], figure.layout.shapes[0].x0)

    def test_selected_round_without_contact_does_not_add_plot_marker(self):
        references = [_round(1, "Evil Geniuses", position=(1000.0, 2000.0))]
        sentinel = _round(9, "Sentinels")
        data = build_dashboard_data(references + [sentinel])
        selected = next(row for row in data.revealed_rounds if row.record.number == 9)

        figure = _circle_figure(data, selected)

        self.assertFalse(any(trace.name == "Selected Sentinels R9 contact" for trace in figure.data))

    def test_selected_contact_without_position_does_not_add_plot_marker(self):
        references = [_round(1, "Evil Geniuses", position=(1000.0, 2000.0))]
        sentinel = _round(9, "Sentinels")
        sentinel.contact = Contact(
            occurred_at=BASE_TIME + timedelta(seconds=3),
            opponent="enemy",
            position=None,
        )
        data = build_dashboard_data(references + [sentinel])
        selected = next(row for row in data.revealed_rounds if row.record.number == 9)

        figure = _circle_figure(data, selected)

        self.assertFalse(any(trace.name == "Selected Sentinels R9 contact" for trace in figure.data))


class DashboardInteractionTests(unittest.TestCase):
    def test_missing_feeds_load_sample_and_show_fetch_command(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("dashboard.Path.cwd", return_value=Path(temporary_directory)):
                app = AppTest.from_file(str(Path(__file__).with_name("dashboard.py")), default_timeout=120).run()

                self.assertFalse(app.exception)
                self.assertTrue(any("Sample match" in item.value for item in app.title))
                self.assertTrue(any("fictional sample match" in item.value for item in app.info))
                self.assertTrue(any("SAMPLE MATCH" in item.value for item in app.warning))
                self.assertTrue(any("python fetch_demo_data.py" in item.value for item in app.code))
                self.assertFalse(any("3 of 6" in str(metric.value) for metric in app.metric))
                self.assertTrue(any(button.label == "Reveal Sample match" for button in app.button))
                initial_figure = json.loads(app.get("plotly_chart")[0].proto.spec)
                self.assertFalse(any("Selected Sample Sentinels" in trace.get("name", "") for trace in initial_figure["data"]))

                next(button for button in app.button if button.key == "reveal_sentinels").click().run()
                self.assertFalse(app.exception)
                self.assertTrue(any("2 of 6" in str(metric.value) for metric in app.metric))
                self.assertTrue(any("Sample Sentinels � Rounds 5-10 (invented)" in item.value for item in app.markdown))
                next(button for button in app.button if button.key == "select_round_9").click().run()

                selected_figure = json.loads(app.get("plotly_chart")[0].proto.spec)
                round_nine = next(
                    trace for trace in selected_figure["data"] if trace.get("name") == "Selected Sample Sentinels R9 contact"
                )
                projected = _project_grid_position((200.0, 0.0))
                self.assertAlmostEqual(projected[0], round_nine["x"][0])
                self.assertAlmostEqual(projected[1], round_nine["y"][0])
                self.assertTrue(any("Round 9 detail" in item.value for item in app.markdown))
                self.assertTrue(any("2025-01-01T20:09:17Z" in item.value for item in app.markdown))
                self.assertTrue(any("17 seconds after barrier drop" in item.value for item in app.markdown))
                self.assertTrue(any("(200.0, 0.0) map units" in item.value for item in app.markdown))

    def test_partial_feeds_use_sample_without_mixing_and_show_download_guidance(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / dashboard.FEEDS[0].filename).write_bytes(b"not parsed in sample mode")
            with patch("dashboard.Path.cwd", return_value=directory), patch("dashboard.st") as streamlit:
                streamlit.session_state = {}
                with patch.object(dashboard, "_render_dashboard") as render:
                    dashboard.main()

            data, = render.call_args.args
            sample_mode = render.call_args.kwargs["sample_mode"]
            self.assertTrue(sample_mode)
            self.assertTrue(all(record.source == "invented sample data" for record in data.reference_rounds + [row.record for row in data.sentinels_rounds]))
            streamlit.info.assert_called_once()
            streamlit.code.assert_called_once_with("python fetch_demo_data.py", language="powershell")

    def test_all_feeds_keep_real_mode_and_reset_sample_interaction_state(self):
        real_rounds = [_round(1, "Evil Geniuses", position=(0.0, 0.0)), _round(9, "Sentinels", position=(100.0, 100.0))]
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for feed in dashboard.FEEDS:
                (directory / feed.filename).write_bytes(b"test input")
            with patch("dashboard.Path.cwd", return_value=directory), patch("dashboard.st") as streamlit:
                streamlit.session_state = {
                    "dashboard_mode": "sample",
                    "sentinels_revealed": True,
                    "selected_round_number": 9,
                }
                with patch.object(dashboard, "_load_rounds", return_value=real_rounds), patch.object(
                    dashboard, "_render_dashboard"
                ) as render:
                    dashboard.main()

            self.assertEqual("grid", streamlit.session_state["dashboard_mode"])
            self.assertFalse(streamlit.session_state["sentinels_revealed"])
            self.assertNotIn("selected_round_number", streamlit.session_state)
            self.assertFalse(render.call_args.kwargs.get("sample_mode", False))
            data = render.call_args.args[0]
            self.assertEqual([real_rounds[0]], data.reference_rounds)
            self.assertEqual([real_rounds[1]], [row.record for row in data.sentinels_rounds])

    def test_unreadable_complete_grid_set_shows_repair_guidance_without_sample_fallback(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for feed in dashboard.FEEDS:
                (directory / feed.filename).write_bytes(b"corrupt feed")
            with patch("dashboard.Path.cwd", return_value=directory), patch("dashboard.st") as streamlit:
                streamlit.session_state = {}
                with patch.object(dashboard, "_load_rounds", side_effect=ValueError("invalid feed")), patch.object(
                    dashboard, "_render_dashboard"
                ) as render:
                    dashboard.main()

            self.assertEqual("grid", streamlit.session_state["dashboard_mode"])
            streamlit.error.assert_called_once()
            streamlit.code.assert_called_once_with("python fetch_demo_data.py", language="powershell")
            render.assert_not_called()

    def test_reveal_and_round_nine_detail_with_local_feeds(self):
        from streamlit.testing.v1 import AppTest

        missing_feeds = [feed.filename for feed in dashboard.FEEDS if not (Path.cwd() / feed.filename).is_file()]
        if missing_feeds:
            self.skipTest(f"Local GRID feeds are unavailable: {', '.join(missing_feeds)}")
        app_path = Path(__file__).with_name("dashboard.py")
        app = AppTest.from_file(str(app_path), default_timeout=120).run()
        self.assertFalse(app.exception)
        self.assertFalse(any("Sample match" in item.value for item in app.title))
        self.assertFalse(any("1 of 6" in str(metric.value) for metric in app.metric))
        initial_figure = json.loads(app.get("plotly_chart")[0].proto.spec)
        self.assertFalse(any("Selected Sentinels" in trace.get("name", "") for trace in initial_figure["data"]))

        next(button for button in app.button if button.key == "reveal_sentinels").click().run()
        self.assertTrue(any("1 of 6" in str(metric.value) for metric in app.metric))
        self.assertTrue(any("Sentinels � Rounds 5-10" in item.value for item in app.markdown))
        revealed_figure = json.loads(app.get("plotly_chart")[0].proto.spec)
        self.assertFalse(any("Selected Sentinels" in trace.get("name", "") for trace in revealed_figure["data"]))
        next(button for button in app.button if button.key == "select_round_9").click().run()

        selected_figure = json.loads(app.get("plotly_chart")[0].proto.spec)
        round_nine = next(
            trace for trace in selected_figure["data"] if trace.get("name") == "Selected Sentinels R9 contact"
        )
        self.assertAlmostEqual(0.3465, round_nine["x"][0], delta=0.0001)
        self.assertAlmostEqual(0.9427, round_nine["y"][0], delta=0.0001)

        markdown = [item.value for item in app.markdown]
        self.assertTrue(any("Round 9 detail" in item for item in markdown))
        timestamp = next(item for item in markdown if "First damage timestamp" in item)
        elapsed = next(item for item in markdown if "Time after barrier drop" in item)
        position = next(item for item in markdown if "DGZ position" in item)
        self.assertRegex(timestamp, r"2025-\d\d-\d\dT")
        self.assertIn("17 seconds after barrier drop", elapsed)
        self.assertRegex(position, r"\(-?\d+(?:\.\d+)?, -?\d+(?:\.\d+)?\) map units")

    def test_missing_minimap_still_reveals_one_hit_and_round_nine_marker(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as temporary_directory:
            app_path = Path(temporary_directory) / "dashboard.py"
            shutil.copyfile(Path(__file__).with_name("dashboard.py"), app_path)
            app = AppTest.from_file(str(app_path), default_timeout=120).run()
            self.assertFalse(app.exception)
            self.assertFalse(any("1 of 6" in str(metric.value) for metric in app.metric))
            initial_figure = json.loads(app.get("plotly_chart")[0].proto.spec)
            self.assertFalse(initial_figure["layout"].get("images"))
            self.assertFalse(any("Selected Sentinels" in trace.get("name", "") for trace in initial_figure["data"]))

            next(button for button in app.button if button.key == "reveal_sentinels").click().run()
            self.assertTrue(any("1 of 6" in str(metric.value) for metric in app.metric))
            next(button for button in app.button if button.key == "select_round_9").click().run()

            selected_figure = json.loads(app.get("plotly_chart")[0].proto.spec)
            self.assertFalse(selected_figure["layout"].get("images"))
            round_nine = next(
                trace for trace in selected_figure["data"] if trace.get("name") == "Selected Sentinels R9 contact"
            )
            self.assertAlmostEqual(0.3465, round_nine["x"][0], delta=0.0001)
            self.assertAlmostEqual(0.9427, round_nine["y"][0], delta=0.0001)


if __name__ == "__main__":
    unittest.main()
