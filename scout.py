"""Analyze DGZ's early defensive damage contacts on Split."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, TextIO

import zstandard


MAP_UNITS_PER_METER = 100.0
DEFAULT_RADIUS_METERS = 15.0
DEFAULT_WINDOW_SECONDS = 20.0
DEFAULT_GLOB = "events_*_grid.jsonl.zst"


@dataclass(frozen=True)
class Contact:
    occurred_at: datetime
    opponent: str
    position: tuple[float, float] | None


@dataclass
class RoundRecord:
    source: str
    game_id: str
    round_id: str
    number: int
    map_name: str
    dgz_id: str
    dgz_team_id: str
    dgz_team: str
    opponent: str
    side: str
    started_at: datetime | None
    inventory: set[str] | None
    barrier_seen: bool = False
    barrier_drop: datetime | None = None
    operator_at_drop: bool | None = None
    contact: Contact | None = None
    winner: str | None = None


@dataclass(frozen=True)
class Circle:
    center: tuple[float, float]
    radius_units: float
    coverage_count: int


def _key(value: object) -> str:
    return str(value).strip().casefold()


def _label(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("id") or ""
    return str(value or "").strip()


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").casefold()


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _map_name(value: object) -> str:
    return _label(value)


def _round_number(round_id: str, target: dict) -> int:
    target_state = target.get("state") or {}
    sequence = target_state.get("sequenceNumber")
    if isinstance(sequence, int):
        return sequence
    match = re.search(r"(\d+)$", round_id)
    return int(match.group(1)) if match else 0


def _inventory_from_player(player: dict) -> set[str] | None:
    state = player.get("state") or {}
    delta = player.get("stateDelta") or {}
    game = state.get("game") or delta.get("game") or {}
    inventory = game.get("inventory")
    if inventory is None:
        inventory = state.get("inventory") or player.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("items"), list):
        return None
    items: set[str] = set()
    for item in inventory["items"]:
        if not isinstance(item, dict):
            continue
        for field in ("id", "name"):
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                items.add(_key(value))
    return items


def _has_operator(inventory: set[str] | None) -> bool | None:
    if inventory is None:
        return None
    return any(item == "operator" or item.endswith("/operator") for item in inventory)


def _player_details(player: dict) -> tuple[str, str, str, dict, dict]:
    state = player.get("state") or {}
    delta = player.get("stateDelta") or {}
    player_id = str(player.get("id") or state.get("id") or delta.get("id") or "")
    name = _label(state.get("name") or delta.get("name") or player.get("name"))
    team_id = str(state.get("teamId") or delta.get("teamId") or "")
    return player_id, name, team_id, state, delta


def _player_position(player: dict) -> tuple[float, float] | None:
    _, _, _, state, delta = _player_details(player)
    game = state.get("game") or delta.get("game") or {}
    position = game.get("position") or state.get("position") or delta.get("position")
    if not isinstance(position, dict):
        return None
    try:
        return float(position["x"]), float(position["y"])
    except (KeyError, TypeError, ValueError):
        return None


def _round_from_start(event: dict, occurred_at: datetime | None, source: str) -> RoundRecord | None:
    actor = event.get("actor") or {}
    game_state = actor.get("state") or {}
    target = event.get("target") or {}
    round_id = str(target.get("id") or "")
    if not round_id:
        return None

    teams = game_state.get("teams") or []
    dgz_team = None
    dgz_player = None
    for team in teams:
        for player in team.get("players") or []:
            if _normalized_text(_label(player.get("name"))) == "dgz":
                dgz_team = team
                dgz_player = player
                break
        if dgz_player is not None:
            break
    if dgz_team is None or dgz_player is None:
        return None

    team_id = str(dgz_team.get("id") or "")
    opponents = [
        _label(team.get("name"))
        for team in teams
        if str(team.get("id") or "") != team_id and _label(team.get("name"))
    ]
    inventory = _inventory_from_player(dgz_player)
    return RoundRecord(
        source=source,
        game_id=str(actor.get("id") or game_state.get("id") or ""),
        round_id=round_id,
        number=_round_number(round_id, target),
        map_name=_map_name(game_state.get("map")),
        dgz_id=str(dgz_player.get("id") or ""),
        dgz_team_id=team_id,
        dgz_team=_label(dgz_team.get("name")),
        opponent=" / ".join(opponents),
        side=_key(dgz_team.get("side")),
        started_at=occurred_at,
        inventory=inventory,
    )


def _find_round(rounds: list[RoundRecord], current: RoundRecord | None, round_id: str) -> RoundRecord | None:
    if current is not None and current.round_id == round_id:
        return current
    for record in reversed(rounds):
        if record.round_id == round_id:
            return record
    return None


def _update_side_from_barrier(record: RoundRecord, event: dict) -> None:
    actor_state = (event.get("actor") or {}).get("state") or {}
    barrier_inventory = None
    for team in actor_state.get("teams") or []:
        for player in team.get("players") or []:
            player_id, name, _, _, _ = _player_details(player)
            if player_id == record.dgz_id or _normalized_text(name) == "dgz":
                record.side = _key(team.get("side") or record.side)
                record.dgz_team = _label(team.get("name") or record.dgz_team)
                barrier_inventory = _inventory_from_player(player)
                break

    series_state = event.get("seriesState") or {}
    games = series_state.get("games") if isinstance(series_state, dict) else None
    if not isinstance(games, list) or not games:
        if barrier_inventory is not None:
            record.inventory = barrier_inventory
        return

    matching_game = next(
        (game for game in games if isinstance(game, dict) and str(game.get("id") or "") == record.game_id),
        None,
    )
    if matching_game is None:
        record.inventory = None
        return

    for team in matching_game.get("teams") or []:
        for player in team.get("players") or []:
            player_id, name, _, _, _ = _player_details(player)
            if player_id == record.dgz_id or _normalized_text(name) == "dgz":
                record.inventory = _inventory_from_player(player)
                return

    record.inventory = None


def _update_inventory(record: RoundRecord, event: dict) -> None:
    actor = event.get("actor") or {}
    player_id, name, _, _, _ = _player_details(actor)
    if player_id != record.dgz_id and _normalized_text(name) != "dgz":
        return
    inventory = _inventory_from_player(actor)
    if inventory is not None:
        record.inventory = inventory


def _set_first_contact(record: RoundRecord, event: dict, occurred_at: datetime, window_seconds: float) -> None:
    if record.barrier_drop is None or occurred_at < record.barrier_drop:
        return
    if (occurred_at - record.barrier_drop).total_seconds() > window_seconds:
        return

    actor = event.get("actor") or {}
    target = event.get("target") or {}
    actor_id, actor_name, actor_team, _, _ = _player_details(actor)
    target_id, target_name, target_team, _, _ = _player_details(target)
    if actor_id == record.dgz_id:
        dgz_player, other_player, other_name, other_team = actor, target, target_name, target_team
    elif target_id == record.dgz_id:
        dgz_player, other_player, other_name, other_team = target, actor, actor_name, actor_team
    else:
        return
    if not other_team or other_team == record.dgz_team_id:
        return

    contact = Contact(
        occurred_at=occurred_at,
        opponent=other_name or other_team,
        position=_player_position(dgz_player),
    )
    if record.contact is None or occurred_at < record.contact.occurred_at:
        record.contact = contact


def analyze_envelopes(envelopes: Iterable[dict], source: str = "<input>", window_seconds: float = DEFAULT_WINDOW_SECONDS) -> list[RoundRecord]:
    rounds: list[RoundRecord] = []
    current: RoundRecord | None = None

    for envelope in envelopes:
        if not isinstance(envelope, dict):
            continue
        occurred_at = _parse_timestamp(envelope.get("occurredAt"))
        for event in envelope.get("events") or []:
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")

            if event_type == "game-started-round":
                record = _round_from_start(event, occurred_at, source)
                if record is not None:
                    rounds.append(record)
                    current = record
                continue

            if event_type == "round-ended-freezetime":
                round_id = str((event.get("actor") or {}).get("id") or "")
                record = _find_round(rounds, current, round_id)
                if record is not None:
                    record.barrier_seen = True
                    record.barrier_drop = occurred_at
                    _update_side_from_barrier(record, event)
                    record.operator_at_drop = _has_operator(record.inventory)
                continue

            if event_type in ("player-pickedUp-item", "player-dropped-item"):
                if current is not None and not current.barrier_seen:
                    _update_inventory(current, event)
                continue

            if event_type == "player-damaged-player" and current is not None and occurred_at is not None:
                _set_first_contact(current, event, occurred_at, window_seconds)
                continue

            if event_type == "team-won-round":
                target = event.get("target") or {}
                round_id = str(target.get("id") or (current.round_id if current else ""))
                record = _find_round(rounds, current, round_id)
                if record is not None:
                    actor_state = (event.get("actor") or {}).get("state") or {}
                    actor_delta = (event.get("actor") or {}).get("stateDelta") or {}
                    record.winner = _label(actor_state.get("name") or actor_delta.get("name")) or str(
                        (event.get("actor") or {}).get("id") or "unknown"
                    )

    return rounds


def _iter_envelopes(path: Path) -> Iterable[dict]:
    with zstandard.open(str(path), "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(envelope, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            yield envelope


def load_rounds(path: Path, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> list[RoundRecord]:
    return analyze_envelopes(_iter_envelopes(path), path.name, window_seconds)


def _is_split(record: RoundRecord) -> bool:
    return _normalized_text(record.map_name) == "split"


def _is_reference_opponent(opponent: str) -> bool:
    normalized = _normalized_text(opponent)
    return "evil geniuses" in normalized or re.search(r"\bkru\b", normalized) is not None


def _is_sentinels(opponent: str) -> bool:
    return "sentinels" in _normalized_text(opponent)


def select_reference_rounds(rounds: Iterable[RoundRecord]) -> list[RoundRecord]:
    return [
        record
        for record in rounds
        if _is_split(record)
        and _is_reference_opponent(record.opponent)
        and record.side == "defender"
        and record.operator_at_drop is True
        and record.barrier_drop is not None
    ]


def select_sentinels_rounds(rounds: Iterable[RoundRecord]) -> list[RoundRecord]:
    return [record for record in rounds if _is_split(record) and _is_sentinels(record.opponent)]


def best_circle(points: Iterable[tuple[float, float]], radius_units: float) -> Circle | None:
    points = list(points)
    if not points or radius_units < 0:
        return None

    candidates: set[tuple[float, float]] = set()

    def add_candidate(x: float, y: float) -> None:
        candidates.add((round(x, 9), round(y, 9)))

    for point in points:
        add_candidate(point[0], point[1])

    diameter = radius_units * 2.0
    for index, first in enumerate(points):
        for second in points[index + 1 :]:
            dx = second[0] - first[0]
            dy = second[1] - first[1]
            distance = math.hypot(dx, dy)
            if distance == 0 or distance > diameter + 1e-9:
                continue
            midpoint_x = (first[0] + second[0]) / 2.0
            midpoint_y = (first[1] + second[1]) / 2.0
            offset = math.sqrt(max(0.0, radius_units * radius_units - distance * distance / 4.0))
            perpendicular_x = -dy / distance
            perpendicular_y = dx / distance
            add_candidate(midpoint_x + perpendicular_x * offset, midpoint_y + perpendicular_y * offset)
            add_candidate(midpoint_x - perpendicular_x * offset, midpoint_y - perpendicular_y * offset)

    def coverage(center: tuple[float, float]) -> int:
        return sum(math.hypot(point[0] - center[0], point[1] - center[1]) <= radius_units + 1e-7 for point in points)

    best_center = min(candidates, key=lambda center: (-coverage(center), center[0], center[1]))
    return Circle(best_center, radius_units, coverage(best_center))


def score_round(record: RoundRecord, circle: Circle, radius_units: float) -> tuple[str, float | None]:
    if record.side != "defender":
        return "not a DGZ defense round", None
    if record.barrier_drop is None:
        return "not eligible: no barrier-drop timestamp", None
    if record.operator_at_drop is not True:
        if record.operator_at_drop is None:
            return "not eligible: starting inventory unknown", None
        return "not eligible: no Operator at barrier drop", None
    if record.contact is None:
        return "no observed contact", None
    if record.contact.position is None:
        return "contact has no position", None

    distance = math.hypot(
        record.contact.position[0] - circle.center[0],
        record.contact.position[1] - circle.center[1],
    )
    return ("hit" if distance <= radius_units + 1e-7 else "miss"), distance


def _elapsed(record: RoundRecord, contact: Contact) -> float | None:
    if record.barrier_drop is None:
        return None
    return (contact.occurred_at - record.barrier_drop).total_seconds()


def _round_sort_key(record: RoundRecord) -> tuple[str, datetime, int, str]:
    return record.source, record.started_at or datetime.min, record.number, record.game_id


def _format_contact(record: RoundRecord) -> str:
    if record.contact is None:
        return "no observed contact"
    elapsed = _elapsed(record, record.contact)
    time_label = f"{elapsed:.2f}s" if elapsed is not None else "time unknown"
    if record.contact.position is None:
        return f"{time_label}, {record.contact.opponent}, position unavailable"
    x, y = record.contact.position
    return f"{time_label}, {record.contact.opponent}, ({x:.1f}, {y:.1f})"


def print_report(
    rounds: Iterable[RoundRecord],
    radius_meters: float = DEFAULT_RADIUS_METERS,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    output: TextIO = sys.stdout,
) -> bool:
    all_rounds = list(rounds)
    references = sorted(select_reference_rounds(all_rounds), key=_round_sort_key)
    reference_points = [
        record.contact.position
        for record in references
        if record.contact is not None and record.contact.position is not None
    ]
    radius_units = radius_meters * MAP_UNITS_PER_METER

    print(
        f"Reference evidence (Evil Geniuses and KRÜ; Split; DGZ defending with Operator at barrier drop; "
        f"damage contact within {window_seconds:g}s):",
        file=output,
    )
    for record in references:
        evidence = _format_contact(record)
        used = "used for fit" if record.contact is not None and record.contact.position is not None else "not fitted"
        print(f"  {record.opponent} R{record.number}: {evidence} [{used}]", file=output)

    circle = best_circle(reference_points, radius_units)
    if circle is None:
        print("Cannot lock an area: no reference round has an observed DGZ position.", file=output)
        return False

    print(
        f"Locked area: center=({circle.center[0]:.1f}, {circle.center[1]:.1f}) map units; "
        f"radius={radius_meters:g}m ({radius_units:.0f} units); "
        f"covers {circle.coverage_count}/{len(reference_points)} reference contacts.",
        file=output,
    )

    evaluation = sorted(select_sentinels_rounds(all_rounds), key=_round_sort_key)
    print("\nSentinels match, Split (fixed reference area):", file=output)
    print("  Round | DGZ side / Operator | First damage contact | Distance | Result | Winner", file=output)
    outcomes: list[str] = []
    for record in evaluation:
        result, distance = score_round(record, circle, radius_units)
        side_and_loadout = f"{record.side or 'unknown'} / "
        side_and_loadout += "Operator" if record.operator_at_drop is True else (
            "inventory unknown" if record.operator_at_drop is None else "no Operator"
        )
        distance_label = f"{distance / MAP_UNITS_PER_METER:.2f}m" if distance is not None else "—"
        print(
            f"  {record.number:>5} | {side_and_loadout:<24} | {_format_contact(record)} | "
            f"{distance_label:>8} | {result} | {record.winner or 'unknown'}",
            file=output,
        )
        outcomes.append(result)

    eligible = [
        result
        for result in outcomes
        if result in ("hit", "miss", "no observed contact", "contact has no position")
    ]
    hits = outcomes.count("hit")
    misses = outcomes.count("miss")
    no_contact = outcomes.count("no observed contact")
    unlocated = outcomes.count("contact has no position")
    denominator = hits + misses
    hit_rate = f"{hits / denominator:.1%}" if denominator else "n/a"
    print(
        f"Summary: {len(evaluation)} Sentinels Split rounds; {len(eligible)} eligible DGZ defense rounds "
        f"({hits} hit, {misses} miss, {no_contact} no observed contact, {unlocated} contact without position). "
        f"Hit rate among located early contacts: {hit_rate} ({hits}/{denominator}).",
        file=output,
    )
    print(
        "Interpretation: damage events are the feed's observable proxy for contact; no observed damage is not a miss.",
        file=output,
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path, help="GRID .jsonl.zst feeds (defaults to the current folder)")
    parser.add_argument("--radius-m", type=float, default=DEFAULT_RADIUS_METERS, help="locked circle radius in meters")
    parser.add_argument("--window-s", type=float, default=DEFAULT_WINDOW_SECONDS, help="post-barrier damage window")
    args = parser.parse_args(argv)

    if args.radius_m <= 0 or args.window_s < 0:
        parser.error("--radius-m must be positive and --window-s must not be negative")
    paths = args.files or sorted(Path.cwd().glob(DEFAULT_GLOB))
    if not paths:
        print(f"No feeds found matching {DEFAULT_GLOB!r} in the current folder.", file=sys.stderr)
        return 2

    rounds: list[RoundRecord] = []
    try:
        for path in paths:
            rounds.extend(load_rounds(path, args.window_s))
    except (OSError, UnicodeError, ValueError, zstandard.ZstdError) as exc:
        print(f"Unable to read GRID feed: {exc}", file=sys.stderr)
        return 2

    if not select_reference_rounds(rounds):
        print("No Split DGZ defense rounds with an Operator were found against Evil Geniuses or KRÜ.", file=sys.stderr)
        return 2
    if not select_sentinels_rounds(rounds):
        print("No Split rounds against Sentinels were found.", file=sys.stderr)
        return 2
    return 0 if print_report(rounds, args.radius_m, args.window_s) else 2


if __name__ == "__main__":
    raise SystemExit(main())