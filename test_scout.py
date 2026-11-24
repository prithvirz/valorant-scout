import unittest
from datetime import datetime, timedelta, timezone

from scout import (
    Circle,
    Contact,
    RoundRecord,
    analyze_envelopes,
    best_circle,
    score_round,
    select_reference_rounds,
)


BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


def timestamp(seconds=0):
    return (BASE_TIME + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _inventory(items):
    return {"items": [{"id": item, "name": item.title()} for item in items]}


def _game_started_round(inventory=("classic", "melee"), side="defender", opponent="Evil Geniuses"):
    return {
        "type": "game-started-round",
        "actor": {
            "type": "game",
            "id": "game-1",
            "state": {
                "id": "game-1",
                "map": "Split",
                "teams": [
                    {
                        "id": "loud",
                        "name": "LOUD",
                        "side": side,
                        "players": [
                            {
                                "id": "dgz-id",
                                "name": "DGZ",
                                "inventory": _inventory(inventory),
                            }
                        ],
                    },
                    {"id": "opponent", "name": opponent, "side": "attacker", "players": []},
                ],
            },
        },
        "target": {"type": "round", "id": "round-1", "state": {"sequenceNumber": 1}},
    }


def _barrier(side="defender", games=None):
    event = {
        "type": "round-ended-freezetime",
        "actor": {
            "type": "round",
            "id": "round-1",
            "state": {
                "teams": [
                    {
                        "id": "loud",
                        "name": "LOUD",
                        "side": side,
                        "players": [{"id": "dgz-id", "name": "DGZ"}],
                    }
                ]
            },
        },
        "target": {"type": "freezetime", "id": "freezetime-1"},
    }
    if games is not None:
        event["seriesState"] = {"games": games}
    return event


def _game_snapshot(game_id="game-1", inventory=None):
    player = {"id": "dgz-id", "name": "DGZ"}
    if inventory is not None:
        player["inventory"] = _inventory(inventory)
    return {
        "id": game_id,
        "teams": [{"id": "loud", "name": "LOUD", "players": [player]}],
    }


def _player(player_id, name, team_id, position):
    return {
        "type": "player",
        "id": player_id,
        "state": {
            "id": player_id,
            "name": name,
            "teamId": team_id,
            "game": {"position": {"x": position[0], "y": position[1]}},
        },
    }


def _damage(actor, target):
    return {"type": "player-damaged-player", "actor": actor, "target": target}


def _envelope(seconds, *events):
    return {"occurredAt": timestamp(seconds), "events": list(events)}


def _analyze(damage_event=None, side="defender", inventory=("classic", "melee")):
    events = [
        _envelope(0, _game_started_round(inventory, side)),
        _envelope(1, _barrier(side)),
    ]
    if damage_event is not None:
        seconds, event = damage_event
        events.append(_envelope(seconds, event))
    events.append(
        _envelope(
            30,
            {
                "type": "team-won-round",
                "actor": {"type": "team", "id": "eg", "state": {"name": "Evil Geniuses"}},
                "target": {"type": "round", "id": "round-1"},
            },
        )
    )
    return analyze_envelopes(events)[0]


class AnalyzeFeedTests(unittest.TestCase):
    def test_barrier_uses_latest_game_inventory_and_defensive_side(self):
        picked_up = {
            "type": "player-pickedUp-item",
            "actor": {
                "type": "player",
                "id": "dgz-id",
                "state": {"id": "dgz-id", "name": "DGZ", "game": {"inventory": _inventory(("operator", "melee"))}},
            },
        }
        record = analyze_envelopes(
            [
                _envelope(0, _game_started_round()),
                _envelope(0.5, picked_up),
                _envelope(1, _barrier()),
            ]
        )[0]

        self.assertTrue(record.barrier_seen)
        self.assertEqual("defender", record.side)
        self.assertTrue(record.operator_at_drop)

    def test_barrier_uses_matching_game_inventory_after_freeze_purchase(self):
        record = analyze_envelopes(
            [
                _envelope(0, _game_started_round()),
                _envelope(
                    1,
                    _barrier(
                        games=[
                            _game_snapshot("another-game", ("classic", "melee")),
                            _game_snapshot(inventory=("operator", "melee")),
                        ]
                    ),
                ),
            ]
        )[0]

        self.assertTrue(record.operator_at_drop)

    def test_barrier_snapshot_can_remove_an_operator(self):
        record = analyze_envelopes(
            [
                _envelope(0, _game_started_round(inventory=("operator", "melee"))),
                _envelope(
                    1,
                    _barrier(
                        games=[
                            _game_snapshot(inventory=("classic", "melee")),
                            _game_snapshot("another-game", ("operator", "melee")),
                        ]
                    ),
                ),
            ]
        )[0]

        self.assertFalse(record.operator_at_drop)

    def test_barrier_snapshot_with_unknown_inventory_does_not_reuse_stale_inventory(self):
        record = analyze_envelopes(
            [
                _envelope(0, _game_started_round(inventory=("operator", "melee"))),
                _envelope(1, _barrier(games=[_game_snapshot()])),
            ]
        )[0]

        self.assertIsNone(record.operator_at_drop)

    def test_operator_in_game_start_snapshot_is_detected(self):
        record = _analyze(inventory=("operator", "melee"))

        self.assertTrue(record.operator_at_drop)

    def test_damage_contact_uses_dgz_position_when_he_deals_or_receives_damage(self):
        dgz_position = (123.0, -456.0)
        enemy_position = (9000.0, 9000.0)
        dealt = _analyze(
            (2, _damage(_player("dgz-id", "DGZ", "loud", dgz_position), _player("enemy", "enemy", "eg", enemy_position)))
        )
        received = _analyze(
            (2, _damage(_player("enemy", "enemy", "eg", enemy_position), _player("dgz-id", "DGZ", "loud", dgz_position)))
        )

        self.assertEqual(dgz_position, dealt.contact.position)
        self.assertEqual(dgz_position, received.contact.position)

    def test_later_damage_does_not_replace_first_contact(self):
        first_position = (123.0, -456.0)
        later_position = (9000.0, 9000.0)
        record = analyze_envelopes(
            [
                _envelope(0, _game_started_round(inventory=("operator",))),
                _envelope(1, _barrier()),
                _envelope(
                    2,
                    _damage(
                        _player("dgz-id", "DGZ", "loud", first_position),
                        _player("enemy-1", "enemy-1", "eg", (1, 2)),
                    ),
                ),
                _envelope(
                    3,
                    _damage(
                        _player("dgz-id", "DGZ", "loud", later_position),
                        _player("enemy-2", "enemy-2", "eg", (3, 4)),
                    ),
                ),
            ]
        )[0]

        self.assertEqual(timestamp(2), record.contact.occurred_at.isoformat().replace("+00:00", "Z"))
        self.assertEqual("enemy-1", record.contact.opponent)
        self.assertEqual(first_position, record.contact.position)

    def test_out_of_order_damage_still_keeps_earliest_contact(self):
        first_position = (123.0, -456.0)
        later_position = (9000.0, 9000.0)
        record = analyze_envelopes(
            [
                _envelope(0, _game_started_round(inventory=("operator",))),
                _envelope(1, _barrier()),
                _envelope(
                    3,
                    _damage(
                        _player("dgz-id", "DGZ", "loud", later_position),
                        _player("enemy-2", "enemy-2", "eg", (3, 4)),
                    ),
                ),
                _envelope(
                    2,
                    _damage(
                        _player("dgz-id", "DGZ", "loud", first_position),
                        _player("enemy-1", "enemy-1", "eg", (1, 2)),
                    ),
                ),
            ]
        )[0]

        self.assertEqual(timestamp(2), record.contact.occurred_at.isoformat().replace("+00:00", "Z"))
        self.assertEqual(first_position, record.contact.position)

    def test_later_located_damage_does_not_replace_first_contact_without_position(self):
        first = _player("dgz-id", "DGZ", "loud", (1, 2))
        first["state"]["game"].pop("position")
        later_position = (9000.0, 9000.0)
        record = analyze_envelopes(
            [
                _envelope(0, _game_started_round(inventory=("operator",))),
                _envelope(1, _barrier()),
                _envelope(2, _damage(first, _player("enemy-1", "enemy-1", "eg", (1, 2)))),
                _envelope(
                    3,
                    _damage(
                        _player("dgz-id", "DGZ", "loud", later_position),
                        _player("enemy-2", "enemy-2", "eg", (3, 4)),
                    ),
                ),
            ]
        )[0]

        self.assertEqual(timestamp(2), record.contact.occurred_at.isoformat().replace("+00:00", "Z"))
        self.assertIsNone(record.contact.position)

    def test_twenty_second_boundary_is_inclusive(self):
        event = _damage(_player("dgz-id", "DGZ", "loud", (1, 2)), _player("enemy", "enemy", "eg", (3, 4)))

        at_boundary = _analyze((21, event))
        after_boundary = _analyze((21.001, event))

        self.assertIsNotNone(at_boundary.contact)
        self.assertIsNone(after_boundary.contact)

    def test_damage_before_barrier_drop_is_ignored(self):
        event = _damage(_player("dgz-id", "DGZ", "loud", (1, 2)), _player("enemy", "enemy", "eg", (3, 4)))
        record = analyze_envelopes(
            [
                _envelope(0, _game_started_round()),
                _envelope(0.5, event),
                _envelope(1, _barrier()),
            ]
        )[0]

        self.assertIsNone(record.contact)

    def test_contact_without_position_is_not_scored(self):
        dgz = _player("dgz-id", "DGZ", "loud", (1, 2))
        dgz["state"]["game"].pop("position")
        record = _analyze((2, _damage(dgz, _player("enemy", "enemy", "eg", (3, 4)))), inventory=("operator",))

        self.assertIsNone(record.contact.position)
        self.assertEqual("contact has no position", score_round(record, Circle((0, 0), 1500, 1), 1500)[0])

    def test_no_contact_is_not_inferred(self):
        record = _analyze()

        self.assertIsNone(record.contact)
        self.assertEqual("Evil Geniuses", record.winner)

    def test_non_defender_round_is_not_operator_eligible(self):
        record = _analyze(side="attacker", inventory=("operator",))

        self.assertEqual("attacker", record.side)
        self.assertTrue(record.operator_at_drop)
        self.assertEqual([], select_reference_rounds([record]))


class CircleAndSelectionTests(unittest.TestCase):
    def test_circle_covers_maximum_points(self):
        circle = best_circle([(0, 0), (1000, 0), (2000, 0)], 1000)

        self.assertIsNotNone(circle)
        self.assertEqual(3, circle.coverage_count)
        self.assertAlmostEqual(1000, circle.center[0])
        self.assertAlmostEqual(0, circle.center[1])

    def test_tied_circle_selection_is_deterministic(self):
        points = [(-10, 0), (10, 0), (100, 0), (120, 0)]

        first = best_circle(points, 11)
        second = best_circle(points, 11)

        self.assertEqual(first, second)
        self.assertEqual(2, first.coverage_count)
        self.assertLess(first.center[0], 50)

    def test_reference_selection_excludes_sentinels_evaluation_data(self):
        def record(opponent, round_number, position):
            return RoundRecord(
                source="feed",
                game_id=f"game-{opponent}",
                round_id=f"round-{round_number}",
                number=round_number,
                map_name="Split",
                dgz_id="dgz-id",
                dgz_team_id="loud",
                dgz_team="LOUD",
                opponent=opponent,
                side="defender",
                started_at=BASE_TIME,
                inventory={"operator"},
                barrier_seen=True,
                barrier_drop=BASE_TIME,
                operator_at_drop=True,
                contact=Contact(BASE_TIME + timedelta(seconds=2), "enemy", position),
            )

        reference = [
            record("Evil Geniuses", 1, (0, 0)),
            record("KRÜ", 2, (100, 0)),
            record("Sentinels", 3, (10000, 0)),
        ]
        selected = select_reference_rounds(reference)
        circle = best_circle([round_record.contact.position for round_record in selected], 1500)

        self.assertEqual(["Evil Geniuses", "KRÜ"], [round_record.opponent for round_record in selected])
        self.assertEqual(2, circle.coverage_count)
        self.assertGreater(
            ((reference[2].contact.position[0] - circle.center[0]) ** 2 + (reference[2].contact.position[1] - circle.center[1]) ** 2) ** 0.5,
            1500,
        )

    def test_score_distinguishes_hit_miss_and_unobserved(self):
        circle = Circle((0, 0), 1500, 1)
        hit = _analyze((2, _damage(_player("dgz-id", "DGZ", "loud", (100, 0)), _player("enemy", "enemy", "eg", (200, 0)))), inventory=("operator",))
        miss = _analyze((2, _damage(_player("dgz-id", "DGZ", "loud", (2000, 0)), _player("enemy", "enemy", "eg", (3000, 0)))), inventory=("operator",))
        unobserved = _analyze(inventory=("operator",))

        self.assertEqual("hit", score_round(hit, circle, 1500)[0])
        self.assertEqual("miss", score_round(miss, circle, 1500)[0])
        self.assertEqual("no observed contact", score_round(unobserved, circle, 1500)[0])


if __name__ == "__main__":
    unittest.main()