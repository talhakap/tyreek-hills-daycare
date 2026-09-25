"""Stats tests against the fake seasons in tests/fixtures (no network).

2024: 3 teams (a bye every week), A beats B in the final.
2025: 4 teams; A plays on a new account {A2}; B and D co-own team 2;
      week 1 has a 90-90 tie; B/D win the title; the consolation game
      (week 3, 1 v 4) must not count in records or head-to-head.
"""
import json
from pathlib import Path

import pytest

from src import stats

FIXTURES = Path(__file__).parent / "fixtures"
OVERRIDES = {"{A2}": {"merge_into": "{A}"}}
WEIGHTS = {"all_play_weight": 0.40, "points_for_weight": 0.35, "recent_form_weight": 0.25, "recent_form_weeks": 3}


@pytest.fixture
def s24():
    return json.loads((FIXTURES / "2024.json").read_text(encoding="utf-8"))


@pytest.fixture
def s25():
    return json.loads((FIXTURES / "2025.json").read_text(encoding="utf-8"))


@pytest.fixture
def managers(s24, s25):
    return stats.build_managers([s24, s25], OVERRIDES)


# ---- standings / streaks ---------------------------------------------------

def test_standings_with_tie_and_ranks(s25):
    rows = stats.standings(s25)
    assert [r["team_id"] for r in rows] == [3, 2, 1, 4]
    top = rows[0]
    assert (top["wins"], top["losses"], top["ties"]) == (1, 0, 1)
    assert top["pct"] == pytest.approx(0.75)
    assert top["pf"] == 220 and top["pa"] == 170
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]


def test_standings_ignore_byes_and_playoffs(s24):
    by_team = {r["team_id"]: r for r in stats.standings(s24)}
    # Team 1: one regular game (week 1); the week-2 bye and week-3 final don't count.
    assert (by_team[1]["wins"], by_team[1]["losses"], by_team[1]["pf"]) == (1, 0, 100)
    assert by_team[2]["games"] == 2


def test_standings_through_week(s25):
    by_team = {r["team_id"]: r for r in stats.standings(s25, through_week=1)}
    assert by_team[1]["wins"] == 1 and by_team[1]["games"] == 1


def test_espn_seeds_order(s25):
    assert [r["team_id"] for r in stats.standings(s25, use_espn_seeds=True)] == [3, 2, 1, 4]


@pytest.mark.parametrize("results, expected", [
    ([], ""), (["W"], "W1"), (["L", "W", "W", "W"], "W3"), (["W", "L", "L"], "L2"), (["W", "T"], "T1"),
])
def test_streak(results, expected):
    assert stats.streak(results) == expected


# ---- all-play / luck -------------------------------------------------------

def test_all_play_includes_bye_scores(s24):
    ap = stats.all_play(s24)
    assert (ap[1]["wins"], ap[1]["losses"]) == (2, 2)
    assert (ap[2]["wins"], ap[2]["losses"]) == (3, 1)
    assert (ap[3]["wins"], ap[3]["losses"]) == (1, 3)


def test_all_play_ties(s25):
    ap = stats.all_play(s25)
    assert (ap[3]["wins"], ap[3]["losses"], ap[3]["ties"]) == (2, 3, 1)
    assert ap[4]["pct"] == pytest.approx(0.5 / 6)


def test_luck(s24):
    luck = stats.luck(s24)
    assert luck[1] == pytest.approx(0.5)     # 1-0 actual, .500 all-play
    assert luck[2] == pytest.approx(-0.25)


# ---- optimal lineup ----------------------------------------------------------

def test_optimal_lineup_uses_flex_and_skips_ir(s25):
    eff = stats.lineup_efficiency(s25)
    assert eff[(1, 1)] == {"actual": 35, "optimal": 45, "bench_left": 10}
    assert eff[(1, 2)]["bench_left"] == 0


def test_optimal_lineup_superflex():
    slots = {"QB": 1, "RB": 1, "OP": 1}
    players = [
        {"slot": "QB", "points": 20, "eligible_slots": ["QB", "OP"]},
        {"slot": "BE", "points": 18, "eligible_slots": ["QB", "OP"]},
        {"slot": "OP", "points": 12, "eligible_slots": ["RB", "RB/WR/TE", "OP"]},
        {"slot": "RB", "points": 6, "eligible_slots": ["RB", "RB/WR/TE", "OP"]},
    ]
    # Best: QB 20, OP = second QB 18, RB = 12.
    assert stats.optimal_points(players, slots) == 50


def test_optimal_lineup_needs_exact_assignment():
    # Greedy (best player into the first slot he fits) gets this wrong.
    slots = {"RB/WR": 1, "WR": 1}
    players = [
        {"slot": "BE", "points": 10, "eligible_slots": ["WR", "RB/WR"]},
        {"slot": "BE", "points": 9, "eligible_slots": ["RB", "RB/WR"]},
    ]
    assert stats.optimal_points(players, slots) == 19


def test_no_lineups_means_none(s24):
    assert stats.lineup_efficiency(s24) is None


# ---- power rankings / awards -------------------------------------------------

def test_power_rankings(s25):
    pr = stats.power_rankings_by_week(s25, WEIGHTS)
    assert sorted(pr) == [1, 2]
    wk1 = pr[1]
    assert wk1[0]["team_id"] == 1 and wk1[0]["score"] == 100.0
    assert wk1[0]["move"] is None
    wk2 = {r["team_id"]: r for r in pr[2]}
    assert wk2[2]["rank"] == 1
    assert all(r["prev_rank"] for r in pr[2])
    assert wk2[2]["move"] == wk2[2]["prev_rank"] - 1


def test_power_weights_come_from_config(s25):
    only_points = {"all_play_weight": 0, "points_for_weight": 1, "recent_form_weight": 0}
    wk2 = stats.power_rankings_by_week(s25, only_points)[2]
    assert [r["team_id"] for r in wk2][:2] == [2, 3]   # PF 240, 220


def test_weekly_awards(s25):
    pr = stats.power_rankings_by_week(s25, WEIGHTS)
    eff = stats.lineup_efficiency(s25)
    wk1 = stats.weekly_awards(s25, 1, pr, eff)
    assert wk1["high_score"]["team_id"] == 1
    assert wk1["closest"]["tie"] is True
    assert wk1["unlucky"]["team_id"] == 2       # 2nd-highest score, lost
    assert wk1["best_manager"]["team_id"] == 2 and wk1["worst_manager"]["team_id"] == 1
    assert wk1["upset"] is None                  # no rankings before week 1

    wk2 = stats.weekly_awards(s25, 2, pr, eff)
    assert wk2["blowout"]["winner_id"] == 2 and wk2["blowout"]["margin"] == 80
    assert wk2["upset"]["winner_id"] == 3 and wk2["upset"]["loser_id"] == 1
    assert wk2["unlucky"] is None
    assert wk2["best_manager"] is None           # no lineup data that week


# ---- managers ----------------------------------------------------------------

def test_manager_merging(managers):
    units = managers["units"]
    assert set(units) == {"{A}", "{B}", "{C}", "{E}"}
    assert managers["owner_unit"]["{A2}"] == "{A}"      # override merge
    assert managers["owner_unit"]["{D}"] == "{B}"       # co-owners are one unit
    assert units["{B}"]["display_name"] == "Bob Brown & Dan Diaz"
    assert units["{A}"]["seasons"] == [2024, 2025]
    assert units["{E}"]["seasons"] == [2025]


def test_manager_overrides_name_and_former(s24, s25):
    m = stats.build_managers([s24, s25], {**OVERRIDES, "{C}": {"display_name": "The Commish", "former": True}})
    assert m["units"]["{C}"]["display_name"] == "The Commish"
    assert m["units"]["{C}"]["former"] is True
    assert m["units"]["{E}"]["former"] is False


def test_manager_without_override_stays_separate(s24, s25):
    m = stats.build_managers([s24, s25])
    assert m["owner_unit"]["{A2}"] != m["owner_unit"]["{A}"]


def test_former_is_automatic(s24, s25):
    s25["teams"] = [t for t in s25["teams"] if t["team_id"] != 3]   # C didn't come back
    m = stats.build_managers([s24, s25], OVERRIDES)
    assert m["units"]["{C}"]["former"] is True
    assert m["units"]["{A}"]["former"] is False


# ---- head-to-head / records / profiles -----------------------------------------

def test_head_to_head(s24, s25, managers):
    h2h = stats.head_to_head([s24, s25], managers)
    ab = h2h[("{A}", "{B}")]
    assert (ab["wins"], ab["losses"], ab["ties"]) == (3, 0, 0)
    assert (ab["playoff_wins"], ab["playoff_losses"]) == (1, 0)
    assert ab["pf"] == 340.5 and ab["pa"] == 305
    ba = h2h[("{B}", "{A}")]
    assert (ba["wins"], ba["losses"]) == (0, 3)
    assert h2h[("{C}", "{E}")]["ties"] == 1
    # The week-3 consolation game (A2 v E) is not head-to-head.
    assert ("{A}", "{E}") not in h2h


def test_records(s24, s25, managers):
    rec = stats.records([s24, s25], managers)
    assert rec["highest_week"][0]["score"] == 140 and rec["highest_week"][0]["unit"] == "{B}"
    assert rec["lowest_week"][0]["score"] == 60        # consolation 70 and bye 70 excluded
    assert rec["biggest_margin"][0]["margin"] == 80
    assert rec["longest_win_streak"][0] == {"unit": "{A}", "length": 3, "start": (2024, 1), "end": (2025, 1)}
    assert rec["longest_losing_streak"][0]["unit"] == "{B}"
    assert rec["longest_losing_streak"][0]["length"] == 2
    assert [(c["unit"], c["seasons"]) for c in rec["championships"]] == [("{A}", [2024]), ("{B}", [2025])]
    assert {c["unit"]: c["seasons"] for c in rec["last_places"]} == {"{C}": [2024], "{A}": [2025]}
    assert rec["most_pf_season"][0]["pf"] == 240
    assert [e["season"] for e in rec["lowest_by_season"]] == [2024, 2025]


def test_more_records(s24, s25, managers):
    rec = stats.records([s24, s25], managers)
    top = rec["highest_combined"][0]
    assert (top["season"], top["week"], top["total"], top["unit"]) == (2024, 3, 235, "{A}")   # 120-115 final
    assert rec["closest_games"][0]["tie"] and rec["closest_games"][0]["margin"] == 0
    assert (rec["highest_losing"][0]["score"], rec["highest_losing"][0]["unit"]) == (115, "{B}")
    assert (rec["lowest_winning"][0]["score"], rec["lowest_winning"][0]["unit"]) == (100, "{A}")
    assert rec["best_record"][0]["unit"] == "{A}" and rec["best_record"][0]["pct"] == 1.0
    assert rec["worst_record"][0]["unit"] == "{C}" and rec["worst_record"][0]["season"] == 2024
    assert rec["most_pa_season"][0]["pa"] == 230
    assert rec["luckiest_season"][0]["luck"] == pytest.approx(0.5)
    assert rec["most_bench_left"][0] == {**rec["most_bench_left"][0], "unit": "{A}", "season": 2025,
                                         "week": 1, "bench_left": 10}
    assert [(c["unit"], c["count"]) for c in rec["playoff_appearances"]][0] == ("{B}", 2)
    # Every weekly entry can be linked to its game.
    for key in ("highest_week", "highest_combined", "closest_games", "highest_losing", "lowest_winning",
                "most_bench_left", "biggest_margin", "lowest_week"):
        assert all("season" in e and "week" in e for e in rec[key]), key


def test_career_records(s24, s25, managers):
    rec = stats.records([s24, s25], managers)
    highs = {r["unit"]: r["value"] for r in rec["most_weekly_highs"]}
    assert highs == {"{A}": 2, "{B}": 2}                     # regular-season weeks only
    lows = {r["unit"]: r["value"] for r in rec["most_weekly_lows"]}
    assert lows == {"{C}": 2, "{E}": 2, "{B}": 1}            # the 90-90 tie counts for both
    pts = {r["unit"]: r["value"] for r in rec["career_points"]}
    assert pts["{A}"] == 300.5                               # 100 + 120.5 + 80, final not included
    assert {r["unit"]: r["value"] for r in rec["playoff_wins"]} == {"{A}": 1, "{B}": 1}
    assert rec["career_ppg"] == []                            # nobody has 20 games in the fixtures
    assert rec["best_all_play"][0]["ap_pct"] == pytest.approx(5 / 6)


def test_player_records(s24, s25, managers):
    pr = stats.player_records([s24, s25], managers)
    best = pr["best_player_week"][0]
    assert (best["player"], best["points"], best["unit"], best["season"], best["week"]) == ("Quade QB", 25, "{B}", 2025, 1)
    assert all(e["player"] != "Ian IR" for e in pr["best_player_week"])     # IR isn't a start
    assert pr["best_bench_week"][0]["player"] == "Ray RB"                    # 15 on the bench
    assert all(e["player"] != "Ian IR" for e in pr["best_bench_week"])       # IR isn't the bench either
    assert [(e["position"], e["player"]) for e in pr["position_bests"]] == [
        ("QB", "Quade QB"), ("RB", "Rudy RB"), ("WR", "Wes WR"), ("TE", "Ted TE")]
    assert pr["best_player_season"][0]["starts"] == 1


def test_records_default_to_top_10(s24, s25, managers):
    rec = stats.records([s24, s25], managers)
    assert len(rec["highest_week"]) == 10       # 16 scores in the fixtures, capped at 10
    assert len(stats.records([s24, s25], managers, top=3)["highest_week"]) == 3


def test_profiles_and_all_time(s24, s25, managers):
    prof = stats.manager_profiles([s24, s25], managers)
    a = prof["{A}"]
    assert a["seasons_played"] == 2
    assert (a["wins"], a["losses"]) == (2, 1)
    assert a["titles"] == [2024] and a["last_places"] == [2025]
    assert a["best_finish"] == 1
    assert a["playoff_appearances"] == 1
    assert prof["{B}"]["titles"] == [2025]
    order = [p["key"] for p in stats.all_time_standings(prof)]
    assert order[0] == "{A}"


def test_in_progress_season_has_no_champion(s25):
    s25["status"]["state"] = "in_progress"
    assert stats.champion(s25) is None and stats.last_place(s25) is None


def test_unfinished_week_is_ignored(s25):
    for m in s25["matchups"]:
        if m["week"] == 2:
            m["final"] = False
    assert stats.complete_weeks(s25) == [1, 3]
    assert sorted(stats.power_rankings_by_week(s25, WEIGHTS)) == [1]
    assert stats.standings(s25)[0]["games"] == 1
