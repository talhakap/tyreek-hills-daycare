"""normalize.py on hand-written raw ESPN shapes (no network)."""
from types import SimpleNamespace

from src import normalize


def raw_game(week, home, away, winner="HOME", tier="NONE", hs=100.0, as_=90.0):
    g = {"id": week * 10 + home, "matchupPeriodId": week, "winner": winner, "playoffTierType": tier,
         "home": {"teamId": home, "totalPoints": hs}}
    if away is not None:
        g["away"] = {"teamId": away, "totalPoints": as_}
    return g


def test_matchups_tie_bye_playoff_undecided():
    ms = normalize.normalize_matchups([
        raw_game(1, 1, 2, "TIE", hs=90, as_=90),
        raw_game(1, 3, None, "UNDECIDED"),
        raw_game(15, 1, 3, "AWAY", "WINNERS_BRACKET"),
        raw_game(15, 2, 4, "HOME", "LOSERS_CONSOLATION_LADDER"),
        raw_game(3, 1, 2, "UNDECIDED", hs=0, as_=0),
    ])
    tie, bye, final, conso, live = ms
    assert tie["winner"] == "tie" and tie["final"]
    assert bye["is_bye"] and bye["away_team_id"] is None and bye["winner"] is None
    assert final["is_playoff"] and final["winner"] == "away"
    assert conso["is_consolation"] and not conso["is_playoff"]
    assert not live["final"] and live["winner"] is None


def test_bye_listed_as_away_only_is_normalised_to_home():
    [m] = normalize.normalize_matchups([{"matchupPeriodId": 2, "winner": "UNDECIDED",
                                         "away": {"teamId": 7, "totalPoints": 88.0}}])
    assert m["home_team_id"] == 7 and m["is_bye"]


def test_owner_formats():
    assert normalize._owner_id("{X}") == "{X}"
    assert normalize._owner_id({"id": "{Y}", "displayName": "y"}) == "{Y}"
    assert normalize._owner_id(None) is None


def test_managers_from_either_owner_shape():
    members = [{"id": "{X}", "firstName": "Xavi", "lastName": "Xu", "displayName": "xx"},
               {"id": "{Y}", "displayName": "yolo"}]
    teams = [SimpleNamespace(team_name="Team X", owners=[{"id": "{X}"}]),
             SimpleNamespace(team_name="Team Y", owners=["{Y}"]),
             SimpleNamespace(team_name="Team Z", owners=["{Z}"])]
    ms = {m["owner_id"]: m["display_name"] for m in normalize.normalize_managers(members, teams)}
    a = normalize.anon_id
    assert ms == {a("{X}"): "Xavi Xu", a("{Y}"): "yolo", a("{Z}"): "Owner of Team Z"}


def test_status():
    settings = {"regular_season_weeks": 1, "playoff_teams": 2}
    pre = normalize.normalize_matchups([raw_game(1, 1, 2, "UNDECIDED", hs=0, as_=0)])
    assert normalize.compute_status(pre, [], settings, 1)["state"] == "preseason"
    mid = normalize.normalize_matchups([raw_game(1, 1, 2), raw_game(2, 1, 2, "UNDECIDED", "WINNERS_BRACKET", 0, 0)])
    assert normalize.compute_status(mid, [{"final_standing": None}], settings, 2) == {"state": "in_progress", "current_week": 2}
    done = normalize.normalize_matchups([raw_game(1, 1, 2), raw_game(2, 1, 2, "HOME", "WINNERS_BRACKET")])
    assert normalize.compute_status(done, [{"final_standing": None}], settings, 2)["state"] == "complete"


def test_clean_name_repairs_mis_encoded_text():
    assert normalize.clean_name("Team Comeback ?Â¿â\x80½") == "Team Comeback ?¿‽"
    assert normalize.clean_name("Shooter McPherson 🔫") == "Shooter McPherson 🔫"   # already fine
    assert normalize.clean_name("Crème Brûlée") == "Crème Brûlée"                  # real accents untouched


SWID = "{11111111-2222-3333-4444-555555555555}"   # fake, but shaped like a real ESPN member ID


def test_member_ids_are_never_stored_raw():
    code = normalize.anon_id(SWID)
    assert code.startswith("m-") and len(code) == 14 and "11111111" not in code
    assert normalize.anon_id(SWID.lower()) == code              # case doesn't matter
    assert normalize.anon_id(code) == code                      # already scrambled: unchanged
    assert normalize.anon_id(None) is None
    members = [{"id": SWID, "firstName": "Pat", "lastName": "Doe", "displayName": "patd"}]
    [m] = normalize.normalize_managers(members, [SimpleNamespace(team_name="T", owners=[SWID])])
    assert m["owner_id"] == code and "espn_name" not in m and SWID not in str(m)


def test_config_can_use_raw_ids_or_codes():
    from src.build import load_overrides
    code = normalize.anon_id(SWID)
    cfg = {"manager_overrides": {SWID: {"display_name": "Pat"}, "m-000000000000": {"merge_into": SWID}}}
    ov = load_overrides(cfg, offline=False)
    assert ov[code] == {"display_name": "Pat"}
    assert ov["m-000000000000"]["merge_into"] == code


def test_live_scores_fill_unfinished_games_only():
    from src.fetch import _apply_live_scores
    raw = {"schedule": [
        {"id": 1, "matchupPeriodId": 3, "winner": "UNDECIDED",
         "home": {"teamId": 1, "totalPoints": 0.0}, "away": {"teamId": 2, "totalPoints": 0.0}},
        {"id": 2, "matchupPeriodId": 2, "winner": "HOME",
         "home": {"teamId": 3, "totalPoints": 110.0}, "away": {"teamId": 4, "totalPoints": 90.0}},
    ]}
    _apply_live_scores(raw, {1: {"home": 38.9, "away": None}, 2: {"home": 1.0, "away": 1.0}})
    live, final = normalize.normalize_matchups(raw["schedule"])
    assert (live["home_score"], live["away_score"], live["final"]) == (38.9, 0.0, False)
    assert (final["home_score"], final["away_score"]) == (110.0, 90.0)     # final games keep ESPN's totals


def test_past_season_transactions_from_mtransactions2():
    from src.normalize import normalize_transactions2
    players = {1: "Adder", 2: "Dropper", 3: "Claimed", 4: "Traded A", 5: "Traded B"}
    raw = [
        {"id": "fa", "type": "FREEAGENT", "status": "EXECUTED", "processDate": 1_700_000_000_000, "memberId": "{SECRET}",
         "items": [{"type": "ADD", "playerId": 1, "toTeamId": 3, "fromTeamId": 0},
                   {"type": "DROP", "playerId": 2, "fromTeamId": 3, "toTeamId": 0}]},
        {"id": "wv", "type": "WAIVER", "status": "EXECUTED", "bidAmount": 12, "processDate": 1_700_000_100_000,
         "items": [{"type": "ADD", "playerId": 3, "toTeamId": 4}]},
        {"id": "wv2", "type": "WAIVER", "status": "FAILED_INVALIDPLAYERSOURCE", "items": [{"type": "ADD", "playerId": 3}]},
        {"id": "lu", "type": "ROSTER", "status": "EXECUTED", "items": [{"type": "LINEUP", "playerId": 1}]},
        # A finished trade: the accept points at the proposal that holds the players.
        {"id": "prop", "type": "TRADE_PROPOSAL", "status": "PENDING", "proposedDate": 1_700_000_200_000,
         "items": [{"type": "TRADE", "playerId": 4, "fromTeamId": 3, "toTeamId": 4},
                   {"type": "TRADE", "playerId": 5, "fromTeamId": 4, "toTeamId": 3}]},
        {"id": "acc", "type": "TRADE_ACCEPT", "status": "EXECUTED", "relatedTransactionId": "prop",
         "processDate": 1_700_000_300_000},
        # Votes and unaccepted proposals aren't trades.
        {"id": "vote", "type": "TRADE_UPHOLD", "status": "EXECUTED", "relatedTransactionId": "prop"},
        {"id": "open", "type": "TRADE_PROPOSAL", "status": "PENDING",
         "items": [{"type": "TRADE", "playerId": 1, "fromTeamId": 3, "toTeamId": 4}]},
    ]
    out = normalize_transactions2(raw, players)
    assert [t["type"] for t in out] == ["trade", "add", "add"]          # newest first
    trade = out[0]
    assert ({"team_id": 4, "action": "TRADE_RECEIVED", "player": "Traded A", "bid": None} in trade["items"]
            and {"team_id": 3, "action": "TRADE_RECEIVED", "player": "Traded B", "bid": None} in trade["items"])
    assert out[1]["items"] == [{"team_id": 4, "action": "WAIVER ADDED", "player": "Claimed", "player_id": 3, "bid": 12}]
    assert out[1]["period"] is None and out[2]["items"][0]["player_id"] == 1
    assert [i["action"] for i in out[2]["items"]] == ["FA ADDED", "DROPPED"]
    assert "SECRET" not in repr(out)
