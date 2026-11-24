"""Streamlit dashboard for testing locked first-contact areas on Split."""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st
import zstandard

from fetch_demo_data import FEEDS, FETCH_COMMAND, G2_FEEDS, G2_FETCH_COMMAND
from scout import (
    DEFAULT_RADIUS_METERS,
    DEFAULT_WINDOW_SECONDS,
    MAP_UNITS_PER_METER,
    Circle,
    Contact,
    RoundRecord,
    best_circle,
    load_rounds,
    score_round,
    select_reference_rounds,
    select_sentinels_rounds,
)


ELIGIBLE_RESULTS = {"hit", "miss", "no observed contact", "contact has no position"}
SPLIT_MAP_IMAGE = Path(__file__).with_name("split_minimap.png")
MAP_U_OFFSET = 0.842188
MAP_V_OFFSET = 0.697578
MAP_COORDINATE_SCALE = 0.000078
G2_MATCHES = dict(
    zip(
        (feed.filename for feed in G2_FEEDS),
        (
            "Apr 26 � Sentinels",
            "Apr 28 � 100 Thieves",
            "May 3 � Evil Geniuses",
            "May 3 � MIBR",
            "May 4 � Sentinels",
        ),
        strict=True,
    )
)


@dataclass(frozen=True)
class ScoredRound:
    record: RoundRecord
    result: str
    distance_units: float | None


@dataclass(frozen=True)
class DashboardData:
    reference_rounds: list[RoundRecord]
    reference_points: list[tuple[float, float]]
    circle: Circle | None
    sentinels_rounds: list[ScoredRound]
    revealed_rounds: list[ScoredRound]
    eligible_rounds: list[ScoredRound]
    revealed_eligible_rounds: list[ScoredRound]


@dataclass(frozen=True)
class MatchStudy:
    reference_rounds: list[RoundRecord]
    circle: Circle | None
    reference_counts: dict[str, int]
    scored_matches: dict[str, list[ScoredRound]]


def _round_sort_key(record: RoundRecord) -> tuple[str, str, int, str]:
    started_at = record.started_at.isoformat() if record.started_at is not None else ""
    return record.source, started_at, record.number, record.game_id


def build_dashboard_data(
    rounds: list[RoundRecord],
    radius_meters: float = DEFAULT_RADIUS_METERS,
) -> DashboardData:
    reference_rounds = sorted(select_reference_rounds(rounds), key=_round_sort_key)
    reference_points = [
        record.contact.position
        for record in reference_rounds
        if record.contact is not None and record.contact.position is not None
    ]
    radius_units = radius_meters * MAP_UNITS_PER_METER
    circle = best_circle(reference_points, radius_units)

    if circle is None:
        return DashboardData(reference_rounds, reference_points, None, [], [], [], [])

    sentinels_rounds = [
        ScoredRound(record, *score_round(record, circle, radius_units))
        for record in sorted(select_sentinels_rounds(rounds), key=_round_sort_key)
    ]
    eligible_rounds = [row for row in sentinels_rounds if row.result in ELIGIBLE_RESULTS]
    revealed_rounds = [row for row in sentinels_rounds if 5 <= row.record.number <= 10]
    revealed_eligible_rounds = [row for row in revealed_rounds if row.result in ELIGIBLE_RESULTS]
    return DashboardData(
        reference_rounds,
        reference_points,
        circle,
        sentinels_rounds,
        revealed_rounds,
        eligible_rounds,
        revealed_eligible_rounds,
    )


@st.cache_data(show_spinner=False)
def _load_cached(
    path: str, modified_ns: int, size: int, window_seconds: float, player_name: str = "DGZ"
) -> list[RoundRecord]:
    del modified_ns, size
    return load_rounds(Path(path), window_seconds, player_name=player_name)


def _load_rounds(paths: list[Path], player_name: str = "DGZ") -> list[RoundRecord]:
    rounds: list[RoundRecord] = []
    for path in paths:
        stat = path.stat()
        rounds.extend(
            _load_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size, DEFAULT_WINDOW_SECONDS, player_name)
        )
    return rounds


def _timestamp_label(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sample_rounds() -> list[RoundRecord]:
    barrier_drop = datetime(2025, 1, 1, 20, 0, tzinfo=timezone.utc)

    def make_round(
        number: int,
        opponent: str,
        position: tuple[float, float] | None,
        contact_observed: bool = True,
    ) -> RoundRecord:
        round_barrier = barrier_drop + timedelta(minutes=number)
        contact = None
        if contact_observed:
            contact = Contact(
                occurred_at=round_barrier + timedelta(seconds=17),
                opponent=f"Fictional {opponent}",
                position=position,
            )
        return RoundRecord(
            source="invented sample data",
            game_id=f"sample-{opponent.casefold().replace(' ', '-')}",
            round_id=f"sample-round-{number}",
            number=number,
            map_name="Split",
            dgz_id="sample-dgz",
            dgz_team_id="sample-loud",
            dgz_team="Sample LOUD",
            opponent=opponent,
            side="defender",
            started_at=round_barrier - timedelta(seconds=30),
            inventory={"operator"},
            barrier_seen=True,
            barrier_drop=round_barrier,
            operator_at_drop=True,
            contact=contact,
            winner="Sample LOUD" if number % 2 else "Fictional opponent",
        )

    return [
        make_round(1, "Sample Evil Geniuses", (0.0, 0.0)),
        make_round(2, "Sample KR�", (400.0, 0.0)),
        make_round(5, "Sample Sentinels", (100.0, 0.0)),
        make_round(6, "Sample Sentinels", (4000.0, 4000.0)),
        make_round(7, "Sample Sentinels", None, contact_observed=False),
        make_round(8, "Sample Sentinels", None),
        make_round(9, "Sample Sentinels", (200.0, 0.0)),
        make_round(10, "Sample Sentinels", (-4000.0, 4000.0)),
    ]


def _project_grid_position(position: tuple[float, float]) -> tuple[float, float]:
    x, y = position
    return MAP_U_OFFSET + y * MAP_COORDINATE_SCALE, MAP_V_OFFSET - x * MAP_COORDINATE_SCALE


def _contact_label(record: RoundRecord) -> str:
    if record.contact is None:
        return "No observed contact"
    if record.contact.position is None:
        return f"{_timestamp_label(record.contact.occurred_at)} � position unavailable"
    x, y = record.contact.position
    return f"{_timestamp_label(record.contact.occurred_at)} � ({x:.1f}, {y:.1f})"


def _elapsed_after_barrier_label(record: RoundRecord) -> str:
    if record.contact is None or record.barrier_drop is None:
        return "Unavailable"
    elapsed = (record.contact.occurred_at - record.barrier_drop).total_seconds()
    if elapsed < 0:
        return "Unavailable (contact predates barrier drop)"
    nearest_second = round(elapsed)
    if abs(elapsed - nearest_second) < 0.05:
        elapsed_label = str(nearest_second)
    else:
        elapsed_label = f"{elapsed:.2f}".rstrip("0").rstrip(".")
    unit = "second" if elapsed == 1 else "seconds"
    if nearest_second == 1 and abs(elapsed - nearest_second) < 0.05:
        unit = "second"
    return f"{elapsed_label} {unit} after barrier drop"


def _circle_figure(data: DashboardData | MatchStudy, selected_round: ScoredRound | None = None) -> go.Figure:
    circle = data.circle
    assert circle is not None
    center = _project_grid_position(circle.center)
    radius = circle.radius_units * MAP_COORDINATE_SCALE
    figure = go.Figure()
    opponents = sorted({record.opponent for record in data.reference_rounds})
    colors = {"evil geniuses": "#60a5fa", "kru": "#fbbf24"}
    extent_values = [0.0, 1.0, center[0] - radius, center[0] + radius, center[1] - radius, center[1] + radius]

    for opponent in opponents:
        records = [
            record
            for record in data.reference_rounds
            if record.opponent == opponent and record.contact is not None and record.contact.position is not None
        ]
        if not records:
            continue
        positions = [record.contact.position for record in records]
        projected_positions = [_project_grid_position(position) for position in positions]
        extent_values.extend(coordinate for position in projected_positions for coordinate in position)
        figure.add_trace(
            go.Scatter(
                x=[position[0] for position in projected_positions],
                y=[position[1] for position in projected_positions],
                mode="markers",
                name=opponent,
                marker={
                    "color": colors.get(opponent.casefold(), "#a78bfa"),
                    "size": 12,
                    "symbol": [
                        "circle"
                        if math.hypot(position[0] - circle.center[0], position[1] - circle.center[1]) <= radius + 1e-7
                        else "x"
                        for position in positions
                    ],
                    "line": {"width": 1, "color": "#0f172a"},
                },
                customdata=[
                    [
                        record.number,
                        record.contact.opponent,
                        _timestamp_label(record.contact.occurred_at),
                        record.contact.position[0],
                        record.contact.position[1],
                    ]
                    for record in records
                ],
                hovertemplate=(
                    "%{customdata[1]} � Round %{customdata[0]}<br>"
                    "GRID (%{customdata[3]:.1f}, %{customdata[4]:.1f}) map units<br>"
                    "%{customdata[2]}<extra></extra>"
                ),
            )
        )

    figure.add_shape(
        type="circle",
        xref="x",
        yref="y",
        x0=center[0] - radius,
        y0=center[1] - radius,
        x1=center[0] + radius,
        y1=center[1] + radius,
        line={"color": "#f8fafc", "width": 2},
        fillcolor="rgba(248, 250, 252, 0.08)",
    )
    figure.add_trace(
        go.Scatter(
            x=[center[0]],
            y=[center[1]],
            mode="markers",
            name="Locked center",
            marker={"color": "#f8fafc", "size": 9, "symbol": "cross"},
            customdata=[[circle.center[0], circle.center[1]]],
            hovertemplate="Locked center<br>GRID (%{customdata[0]:.1f}, %{customdata[1]:.1f}) map units<extra></extra>",
        )
    )
    if (
        selected_round is not None
        and selected_round.record.contact is not None
        and selected_round.record.contact.position is not None
    ):
        grid_position = selected_round.record.contact.position
        x, y = _project_grid_position(grid_position)
        extent_values.extend((x, y))
        figure.add_trace(
            go.Scatter(
                x=[x],
                y=[y],
                mode="markers",
                name=f"Selected {selected_round.record.opponent} R{selected_round.record.number} contact",
                marker={
                    "color": "#fb7185",
                    "size": 18,
                    "symbol": "star",
                    "line": {"color": "#f8fafc", "width": 2},
                },
                customdata=[[grid_position[0], grid_position[1]]],
                hovertemplate="Selected contact<br>GRID (%{customdata[0]:.1f}, %{customdata[1]:.1f}) map units<extra></extra>",
            )
        )
    axis_min = min(extent_values)
    axis_max = max(extent_values)
    axis_padding = (axis_max - axis_min) * 0.02
    axis_min -= axis_padding
    axis_max += axis_padding
    images = []
    if SPLIT_MAP_IMAGE.is_file():
        image_source = "data:image/png;base64," + base64.b64encode(SPLIT_MAP_IMAGE.read_bytes()).decode("ascii")
        images.append(
            {
                "source": image_source,
                "xref": "x",
                "yref": "y",
                "x": 0,
                "y": 0,
                "sizex": 1,
                "sizey": 1,
                "xanchor": "left",
                "yanchor": "top",
                "sizing": "stretch",
                "layer": "below",
            }
        )
    figure.update_layout(
        images=images,
        height=470,
        margin={"l": 12, "r": 12, "t": 18, "b": 12},
        legend={"orientation": "h", "y": 1.08, "x": 0},
        xaxis={
            "title": "Projected map U",
            "range": [axis_min, axis_max],
            "gridcolor": "rgba(51, 65, 85, 0.25)",
            "zerolinecolor": "rgba(71, 85, 105, 0.4)",
        },
        yaxis={
            "title": "Projected map V",
            "range": [axis_max, axis_min],
            "gridcolor": "rgba(51, 65, 85, 0.25)",
            "zerolinecolor": "rgba(71, 85, 105, 0.4)",
            "scaleanchor": "x",
            "scaleratio": 1,
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.35)",
        font={"color": "#e2e8f0"},
    )
    return figure


def _round_row(row: ScoredRound) -> dict[str, object]:
    record = row.record
    loadout = "Operator" if record.operator_at_drop is True else (
        "Inventory unknown" if record.operator_at_drop is None else "No Operator"
    )
    distance = f"{row.distance_units / MAP_UNITS_PER_METER:.2f} m" if row.distance_units is not None else "-"
    return {
        "Round": record.number,
        "Side / loadout": f"{record.side.title() or 'Unknown'} � {loadout}",
        "First contact": _contact_label(record),
        "Distance": distance,
        "Result": row.result.title(),
        "Winner": record.winner or "Unknown",
    }


def _render_round_detail(row: ScoredRound, player_label: str = "DGZ") -> None:
    record = row.record
    st.markdown(f"#### Round {record.number} detail")
    if record.contact is None:
        st.markdown("**First damage timestamp:** No observed contact")
        st.markdown(f"**{player_label} position:** Unavailable")
    else:
        st.markdown(f"**First damage timestamp:** {_timestamp_label(record.contact.occurred_at)}")
        st.markdown(f"**Time after barrier drop:** {_elapsed_after_barrier_label(record)}")
        if record.contact.position is None:
            position = "Unavailable in the feed"
        else:
            x, y = record.contact.position
            position = f"({x:.1f}, {y:.1f}) map units"
        st.markdown(f"**{player_label} position:** {position}")
        st.markdown(f"**Opponent:** {record.contact.opponent}")
    st.markdown(f"**Result:** {row.result.title()} � **Round winner:** {record.winner or 'Unknown'}")


def _render_dashboard(data: DashboardData, sample_mode: bool = False) -> None:
    st.title("DGZ on Split � Sample match" if sample_mode else "DGZ on Split")
    if sample_mode:
        st.warning("SAMPLE MATCH - all teams, rounds, timestamps, and positions below are invented for demonstration.")
        st.caption("Fictional EG / KR� reference rounds lock the area; a fictional Sentinels match is scored without refitting.")
    else:
        st.caption("EG / KR� establish the locked area; Sentinels are scored against it without refitting.")

    if data.circle is None:
        st.error("No located EG / KR� reference contacts are available to lock the area.")
        return

    hits = sum(row.result == "hit" for row in data.revealed_eligible_rounds)
    misses = sum(row.result == "miss" for row in data.revealed_eligible_rounds)
    no_contact = sum(row.result == "no observed contact" for row in data.revealed_eligible_rounds)
    unlocated = sum(row.result == "contact has no position" for row in data.revealed_eligible_rounds)

    st.markdown(
        f"**Locked center:** ({data.circle.center[0]:.1f}, {data.circle.center[1]:.1f}) map units"
    )

    selected_number = st.session_state.get("selected_round_number")
    selected_round = next(
        (row for row in data.revealed_rounds if row.record.number == selected_number),
        None,
    ) if st.session_state.get("sentinels_revealed", False) else None

    plot_column, evidence_column = st.columns([1.8, 1])
    with plot_column:
        st.plotly_chart(
            _circle_figure(data, selected_round),
            use_container_width=True,
            config={"displayModeBar": False},
        )
    with evidence_column:
        st.markdown("#### Invented EG / KR� reference evidence" if sample_mode else "#### EG / KR� evidence")
        evidence = []
        for record in data.reference_rounds:
            evidence.append(
                {
                    "Opponent": record.opponent,
                    "Round": record.number,
                    "Position": (
                        f"({record.contact.position[0]:.1f}, {record.contact.position[1]:.1f})"
                        if record.contact is not None and record.contact.position is not None
                        else "Unavailable"
                    ),
                    "Fit": (
                        "Included"
                        if record.contact is not None and record.contact.position is not None
                        else "No position"
                    ),
                }
            )
        if evidence:
            st.dataframe(evidence, hide_index=True, use_container_width=True, height=300)
        else:
            st.info("No qualifying reference rounds found.")
        st.caption("? Inside locked area � x Outside locked area")

    st.divider()
    reveal_label = "Reveal Sample match" if sample_mode else "Reveal Sentinels match"
    if st.button(reveal_label, type="primary", key="reveal_sentinels"):
        st.session_state["sentinels_revealed"] = True

    if not st.session_state.get("sentinels_revealed", False):
        hidden_label = "The invented sample rounds stay hidden until you reveal the sample match." if sample_mode else (
            "The Sentinels rounds stay hidden until you reveal the match."
        )
        st.caption(hidden_label)
        return

    st.markdown("### Sample Sentinels � Rounds 5-10 (invented)" if sample_mode else "### Sentinels � Rounds 5-10")
    if not data.revealed_rounds:
        st.info("No Sentinels rounds 5-10 were found in the supplied feeds." if not sample_mode else "No invented sample rounds are available.")
        return

    metric_columns = st.columns(3)
    metric_columns[0].metric("Locked radius", "15 m", "1,500 map units")
    metric_columns[1].metric(
        "Reference coverage",
        f"{data.circle.coverage_count} / {len(data.reference_points)}",
        "located EG / KR� contacts",
    )
    metric_columns[2].metric(
        "Eligible rounds hit",
        f"{hits} of {len(data.revealed_eligible_rounds)}",
        f"{misses} misses � {no_contact} no contact � {unlocated} unlocated",
    )

    st.dataframe([_round_row(row) for row in data.revealed_rounds], hide_index=True, use_container_width=True)
    round_columns = st.columns(len(data.revealed_rounds))
    for column, row in zip(round_columns, data.revealed_rounds):
        number = row.record.number
        column.button(
            f"Round {number}",
            key=f"select_round_{number}",
            on_click=_select_round,
            args=(number,),
        )

    selected_number = st.session_state.get("selected_round_number")
    selected = next(
        (row for row in data.revealed_rounds if row.record.number == selected_number),
        None,
    )
    if selected is not None:
        _render_round_detail(selected)
    st.caption(
        "Invented sample contacts are illustrative only; they do not represent match data."
        if sample_mode
        else "Damage events are the feed's observable contact proxy; no observed damage is not a miss."
    )


def _select_round(number: int) -> None:
    st.session_state["selected_round_number"] = number


def _show_fetch_command() -> None:
    st.markdown("Download or repair the required local inputs with:")
    st.code(FETCH_COMMAND, language="powershell")


def _set_dashboard_mode(mode: str) -> None:
    if st.session_state.get("dashboard_mode") != mode:
        st.session_state["dashboard_mode"] = mode
        st.session_state["sentinels_revealed"] = False
        st.session_state.pop("selected_round_number", None)


def build_match_study(rounds: list[RoundRecord], matches: dict[str, str]) -> MatchStudy:
    eligible = {source: [] for source in matches}
    for record in sorted(rounds, key=_round_sort_key):
        if (
            record.source in eligible
            and record.map_name.casefold() == "split"
            and record.side == "defender"
            and record.operator_at_drop is True
            and record.barrier_drop is not None
        ):
            eligible[record.source].append(record)

    reference_ids = list(matches)[:2]
    reference_rounds = [record for source in reference_ids for record in eligible[source]]
    radius = DEFAULT_RADIUS_METERS * MAP_UNITS_PER_METER
    circle = best_circle(
        [record.contact.position for record in reference_rounds if record.contact and record.contact.position],
        radius,
    )
    reference_counts = {matches[source]: len(eligible[source]) for source in reference_ids}
    scored = {
        matches[source]: [ScoredRound(record, *score_round(record, circle, radius)) for record in eligible[source]]
        for source in list(matches)[2:]
    } if circle is not None else {}
    return MatchStudy(reference_rounds, circle, reference_counts, scored)


def _multi_match_sample() -> tuple[list[RoundRecord], dict[str, str]]:
    originals = _sample_rounds()
    groups = (originals[:1], originals[1:2], originals[2:4], originals[4:6], originals[6:])
    labels = ("Reference A", "Reference B", "Evaluation A", "Evaluation B", "Evaluation C")
    rounds = [
        replace(record, source=label, opponent=label)
        for label, records in zip(labels, groups, strict=True)
        for record in records
    ]
    return rounds, dict(zip(labels, labels, strict=True))


def _render_g2_study(directory: Path) -> None:
    paths = [directory / feed.filename for feed in G2_FEEDS]
    sample = any(not path.is_file() for path in paths)
    st.title("G2 on Split � Sample matches" if sample else "G2 jawgemo on Split")
    if sample:
        st.warning("FICTIONAL SAMPLE � The rounds below are invented.")
        st.caption("Load the five official feeds to see the real patch 10.07 study.")
        st.code(G2_FETCH_COMMAND, language="bash")
        rounds, matches = _multi_match_sample()
    else:
        st.caption("2025 VCT Americas Stage 1 playoffs � Patch 10.07 � Match dates in UTC")
        try:
            rounds = _load_rounds(paths, "jawgemo")
        except (OSError, UnicodeError, ValueError, zstandard.ZstdError) as exc:
            st.error(f"Unable to read the G2 feeds. {exc}")
            st.code(G2_FETCH_COMMAND, language="bash")
            return
        matches = G2_MATCHES

    study = build_match_study(rounds, matches)
    if study.circle is None:
        st.info("The reference matches contain no located eligible contacts.")
        return

    reference = [
        {"Reference match": label, "Eligible rounds": count}
        for label, count in study.reference_counts.items()
    ]
    st.dataframe(reference, hide_index=True)
    st.caption(f"The locked 15 m area covers {study.circle.coverage_count} of {len(study.reference_rounds)} reference rounds.")
    revealed = st.checkbox("Reveal evaluation matches", key=f"g2_reveal_{sample}")
    selected = None
    if revealed:
        summary = []
        for label, rows in study.scored_matches.items():
            summary.append(
                {
                    "Evaluation match": label,
                    "Eligible": len(rows),
                    "In area": sum(row.result == "hit" for row in rows),
                    "Outside area": sum(row.result == "miss" for row in rows),
                    "No early damage": sum(row.result == "no observed contact" for row in rows),
                    "Position unavailable": sum(row.result == "contact has no position" for row in rows),
                }
            )
        eligible = sum(row["Eligible"] for row in summary)
        hits = sum(row["In area"] for row in summary)
        misses = sum(row["Outside area"] for row in summary)
        st.metric("Evaluation rounds in the locked area", f"{hits} of {eligible}")
        st.dataframe(summary, hide_index=True)
        if eligible and not hits and misses:
            st.info(f"Review the {misses} first contacts outside the area before planning around the reference spot.")
        match = st.selectbox("Inspect a match", list(study.scored_matches), key=f"g2_match_{sample}")
        match_rounds = study.scored_matches[match]
        if match_rounds:
            st.dataframe([_round_row(row) for row in match_rounds], hide_index=True)
            selected = st.selectbox(
                "Inspect a round",
                match_rounds,
                format_func=lambda row: f"Round {row.record.number} � {row.result}",
                index=None,
                key=f"g2_round_{sample}_{match}",
            )
        else:
            st.caption("No eligible defense rounds in this match.")

    st.plotly_chart(_circle_figure(study, selected), config={"displayModeBar": False})
    if selected is not None:
        _render_round_detail(selected, "Sample player" if sample else "jawgemo")
    st.caption("Enemy damage is the observable contact signal. The time window ends 20 seconds after barrier drop.")


def main() -> None:
    st.set_page_config(page_title="Operator Scout on Split", page_icon="??", layout="wide")
    directory = Path.cwd()
    if st.radio("Scouting case", ("DGZ / LOUD", "jawgemo / G2"), horizontal=True) == "jawgemo / G2":
        _render_g2_study(directory)
        return
    missing = [feed.filename for feed in FEEDS if not (directory / feed.filename).is_file()]
    if missing:
        _set_dashboard_mode("sample")
        st.info(f"Missing GRID feeds: {', '.join(missing)}. Showing a fictional sample match instead; no available feeds are mixed into it.")
        _show_fetch_command()
        _render_dashboard(build_dashboard_data(_sample_rounds()), sample_mode=True)
        return
    _set_dashboard_mode("grid")
    paths = [directory / feed.filename for feed in FEEDS]
    try:
        rounds = _load_rounds(paths)
    except (OSError, UnicodeError, ValueError, zstandard.ZstdError) as exc:
        st.error(f"Unable to read a GRID feed: {exc}")
        _show_fetch_command()
        return

    data = build_dashboard_data(rounds)
    _render_dashboard(data)


if __name__ == "__main__":
    main()
