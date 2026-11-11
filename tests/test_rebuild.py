"""Trades rebuilt from weekly rosters and draft boards."""
from src import rebuild


def lineup(period, team, *pids):
    return {"week": period, "scoring_period": period, "team_id": team,
            "players": [{"player_id": p, "name": f"P{p}"} for p in pids]}


def season(**extra):
    base = {
        "lineups": [
            lineup(1, 1, 10, 11), lineup(1, 2, 20, 21), lineup(1, 3, 30),
            # Week 2: 10 and 20 swapped teams (a trade); 31 was picked up by team 3 (not a trade).
            lineup(2, 1, 20, 11), lineup(2, 2, 10, 21), lineup(2, 3, 30, 31),
            # Week 3: team 1 sent 11 to team 3 for nothing on rosters (a pick deal).
            lineup(3, 1, 20), lineup(3, 2, 10, 21), lineup(3, 3, 30, 31, 11),
        ],
        "transactions": [{"period": 2, "type": "add",
                          "items": [{"team_id": 3, "action": "FA ADDED", "player_id": 31, "player": "P31"}]}],
        "draft": None, "rosters": None,
    }
    return {**base, **extra}


def test_roster_moves_become_trades_and_pickups_dont():
    trades = rebuild.rebuild_trades(season())
    assert [(t["window"], t["teams"]) for t in trades] == [((1, 2), [1, 2]), ((2, 3), [1, 3])]
    assert trades[0]["got"] == {1: ["P20"], 2: ["P10"]}
    assert trades[1]["got"] == {1: [], 3: ["P11"]}


def test_draft_board_picks_attach_to_the_trade_between_those_teams():
    # Next draft: team 3 used team 1's 2nd-round slot (snake, order [1, 2, 3] -> round 2 slot 3 is team 1's).
    next_draft = {"complete": True, "type": "SNAKE", "pick_order": [1, 2, 3],
                  "picks": [{"round": 2, "pick": 3, "team_id": 3, "player": "Rookie", "keeper": False},
                            {"round": 1, "pick": 2, "team_id": 2, "player": "Own pick", "keeper": False}]}
    assert rebuild.board_traded_picks(next_draft) == [
        {"round": 2, "slot": "2.03", "from": 1, "to": 3, "player": "Rookie", "keeper": False}]
    trades = rebuild.attach_picks(rebuild.rebuild_trades(season()), 2025, next_draft, [])
    assert trades[1]["picks"][3] == [{"year": 2026, "round": 2, "from": 1, "slot": "2.03", "player": "Rookie"}]


def test_sheet_trades_supply_picks_and_unprocessed_trades_are_marked():
    sheet = [
        {"season": 2025, "note": "deal", "sides": [{"team": 1, "players": ["P20"], "picks": []},
                                                    {"team": 2, "players": ["P10"],
                                                     "picks": [{"year": 2026, "round": 1, "from": 1}]}]},
        {"season": 2025, "note": "", "sides": [{"team": 2, "players": ["P99"], "picks": []},
                                                {"team": 3, "players": [], "picks": [{"year": 2026, "round": 4, "from": 2}]}]},
    ]
    trades = rebuild.attach_picks(rebuild.rebuild_trades(season()), 2025, None, sheet)
    assert trades[0]["note"] == "deal" and trades[0]["picks"][2] == [{"year": 2026, "round": 1, "from": 1}]
    pending = trades[-1]
    assert pending["sheet_only"] and not pending["pick_only"] and pending["got"][2] == ["P99"]


def test_live_rosters_count_as_the_latest_week():
    s = season(rosters={"1": [{"player_id": 20}], "2": [{"player_id": 21}], "3": [{"player_id": 10}]})
    moves, _ = rebuild.player_moves(s)
    assert {"pid": 10, "from": 2, "to": 3, "window": (3, 4)} in moves
