"""Traded draft picks: loading/validating draft_picks.toml, ownership, and draft slots."""
import pytest

from src import picks
from src.normalize import normalize_draft

TEAMS = {1, 2, 3, 4}


def load(tmp_path, text, max_round=16):
    f = tmp_path / "draft_picks.toml"
    f.write_text(text, encoding="utf-8")
    return picks.load_trades(f, TEAMS, max_round)


def trade(season, a, a_picks, b, b_picks):
    def side(team, ps):
        body = ", ".join(f"{{ year = {y}, round = {r}, from = {f} }}" for y, r, f in ps)
        return f"[[trade.side]]\nteam = {team}\n" + (f"picks = [{body}]\n" if ps else 'players = ["X"]\n')
    return f"[[trade]]\nseason = {season}\n" + side(a, a_picks) + side(b, b_picks)


def test_missing_file_means_no_trades(tmp_path):
    assert picks.load_trades(tmp_path / "nope.toml", TEAMS) == []


def test_pick_can_be_flipped_and_owner_follows(tmp_path):
    trades = load(tmp_path, trade(2025, 1, [], 2, [(2026, 1, 1)]) + trade(2025, 2, [], 3, [(2026, 1, 1)]))
    owners = picks.resolve_owners(trades)
    assert owners == {(2026, 1, 1): 3}
    assert trades[0]["sides"][1]["picks"][0]["given_by"] == 1
    assert trades[1]["sides"][1]["picks"][0]["given_by"] == 2


def test_cannot_trade_a_pick_you_dont_own(tmp_path):
    # Team 2 trades away team 1's pick, but team 1 still owns it and isn't in the trade.
    trades = load(tmp_path, trade(2025, 2, [], 3, [(2026, 1, 1)]))
    with pytest.raises(picks.PickError, match="belongs to team 1"):
        picks.resolve_owners(trades)


def test_cannot_receive_your_own_pick(tmp_path):
    trades = load(tmp_path, trade(2025, 1, [], 2, [(2026, 1, 2)]))
    with pytest.raises(picks.PickError, match="already owns"):
        picks.resolve_owners(trades)


@pytest.mark.parametrize("text, message", [
    (trade(2025, 1, [], 9, [(2026, 1, 1)]), "no team number 9"),
    (trade(2025, 1, [], 2, [(2026, 17, 1)]), "round 17 doesn't exist"),
    ("[[trade]]\nseason = 2025\n[[trade.side]]\nteam = 1\n", "at least two"),
    (trade(2025, 1, [], 2, []), "no draft picks changed hands"),
    ('[[trade]]\nseason = 2025\ndate = "Oct 3"\n', "date should look like"),
    ("[[trade]\n", "formatting mistake"),
])
def test_mistakes_get_clear_messages(tmp_path, text, message):
    with pytest.raises(picks.PickError, match=message):
        load(tmp_path, text)


def test_snake_slots():
    draft = {"type": "SNAKE", "pick_order": [11, 6, 5, 8, 9, 10, 4, 12, 1, 7]}
    assert picks.slot_number(draft, 1, 4) == 7       # Billieve picks 7th in odd rounds
    assert picks.slot_number(draft, 4, 4) == 4       # and 4th in even rounds
    assert picks.slot_number(draft, 2, 9) == 6
    assert picks.slot_number({**draft, "type": "LINEAR"}, 2, 9) == 5
    assert picks.slot_number(draft, 1, 99) is None


def test_draft_result_needs_a_finished_draft():
    board = {"type": "SNAKE", "pick_order": [1, 2], "complete": True,
             "picks": [{"round": 2, "pick": 1, "team_id": 2, "player": "P", "keeper": False}]}
    assert picks.draft_result(board, 2, 2)["slot"] == "2.01"
    assert picks.draft_result(board, 2, 2)["pick"]["player"] == "P"
    assert picks.draft_result({**board, "complete": False}, 2, 2) is None
    assert picks.draft_result(None, 2, 2) is None


def test_ordinals():
    assert [picks.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 16, 21, 22)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "16th", "21st", "22nd"]


def test_normalize_draft_drops_member_ids():
    data = {"settings": {"draftSettings": {"type": "SNAKE", "pickOrder": [2, 1]}},
            "draftDetail": {"drafted": True, "picks": [
                {"roundId": 1, "roundPickNumber": 1, "overallPickNumber": 1, "teamId": 2, "playerId": 7,
                 "keeper": True, "memberId": "{SECRET}"}]}}
    d = normalize_draft(data, {7: "Some Guy"})
    assert d["complete"] and d["rounds"] == 1 and d["pick_order"] == [2, 1]
    assert d["picks"] == [{"round": 1, "pick": 1, "overall": 1, "team_id": 2, "player_id": 7, "player": "Some Guy",
                          "keeper": True}]
    assert "SECRET" not in repr(d)


# ---- the shared Google Sheet ----
NAMES = {picks.name_key(n): i for n, i in
         [("Alpha 25", 1), ("Bravo 25", 2), ("Charlie 25", 3), ("Echo 25", 4), ("Old Alpha Name", 1)]}


def sheet(*rows):
    return [picks.SHEET_HEADER] + [list(r) + [""] * (12 - len(r)) for r in rows]


def test_sheet_row_reads_like_people_write_it():
    rows = sheet(["2026-27", "10/21/2026", "alpha 25", "Josh Allen, Puka Nacua", "",
                  "BRAVO 25", "", "2027 1st\n2027 5th round", "", "", "", "Deadline deal"])
    trades, problems = picks.parse_sheet(rows, NAMES, 16)
    assert problems == []
    t = trades[0]
    assert t["season"] == 2026 and t["date"].isoformat() == "2026-10-21" and t["note"] == "Deadline deal"
    assert t["sides"][0] == {"team": 1, "players": ["Josh Allen", "Puka Nacua"], "picks": []}
    # No team named: it's the other team's own pick.
    assert t["sides"][1]["picks"] == [{"year": 2027, "round": 1, "from": 1}, {"year": 2027, "round": 5, "from": 1}]


def test_sheet_third_team_and_named_pick():
    rows = sheet(["2026", "", "Alpha 25", "", "2027 2nd (Charlie 25)", "Bravo 25", "Player B", "",
                  "Charlie 25", "", "2027 3rd - Old Alpha Name"])
    trades, problems = picks.parse_sheet(rows, NAMES, 16)
    assert problems == []
    assert trades[0]["sides"][0]["picks"] == [{"year": 2027, "round": 2, "from": 3}]
    assert trades[0]["sides"][2]["picks"] == [{"year": 2027, "round": 3, "from": 1}]


def test_bad_sheet_rows_are_skipped_not_fatal():
    rows = sheet(["2026", "", "Alpha 25", "X", "", "Nobody FC", "", "2027 1st"],                # unknown team
                 ["2026", "", "Alpha 25", "X", "", "Bravo 25", "", "first round next year"],    # unreadable pick
                 ["2026", "", "Alpha 25", "", "2027 1st", "Bravo 25", "", "", "Charlie 25", "", "2027 2nd"],
                 [],                                                                           # blank row: ignored
                 ["2026", "", "Alpha 25", "X", "", "Bravo 25", "", "2027 4th"])                 # fine
    trades, problems = picks.parse_sheet(rows, NAMES, 16)
    assert [p["row"] for p in problems] == [2, 3, 4]
    assert "no team called 'Nobody FC'" in problems[0]["message"]
    assert "couldn't read the pick" in problems[1]["message"]
    assert "3-team trade" in problems[2]["message"]
    assert len(trades) == 1 and trades[0]["row"] == 6


def test_lenient_owners_skip_a_trade_that_gives_away_a_pick_twice():
    rows = sheet(["2026", "", "Alpha 25", "X", "", "Bravo 25", "", "2027 4th"],
                 ["2026", "", "Charlie 25", "Y", "", "Echo 25", "", "2027 4th (Alpha 25)"])  # Alpha's 4th is gone
    trades, problems = picks.parse_sheet(rows, NAMES, 16)
    owners, kept, bad = picks.resolve_owners_lenient(trades, name=lambda i: f"T{i}")
    assert owners == {(2027, 4, 1): 2} and len(kept) == 1
    assert bad[0]["row"] == 3 and "belongs to T2" in bad[0]["message"]
