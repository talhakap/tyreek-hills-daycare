"""Rebuilds a season's trades from weekly rosters and draft boards.

ESPN keeps few finished trades (none at all for past seasons' activity feed), but it does keep
every team's full roster each week. A player who is on one team in one week and another team
the next, without that team picking him up as a free agent or on waivers, was traded. Moves
between the same two teams in the same week are one trade.

Draft picks don't show on rosters. For a finished draft, any pick made by a team that didn't
own that slot originally was traded; those are attached to the trade between the same two
teams (or listed as a pick-only trade). The trade sheet's pick details win where it has them.
No network: works on the saved season data.
"""
from collections import defaultdict

from . import picks


def roster_timeline(season: dict) -> tuple[dict, dict]:
    """({player_id: {period: team_id}}, {player_id: name}). Period 0 is the draft board;
    the current season's live rosters count as the period after the latest lineups."""
    timeline, names = defaultdict(dict), {}
    draft = season.get("draft") or {}
    if draft.get("complete"):
        for p in draft.get("picks", []):
            if p.get("player_id") and p.get("team_id"):
                timeline[p["player_id"]][0] = p["team_id"]
                if p.get("player"):
                    names[p["player_id"]] = p["player"]
    latest = 0
    for lu in season.get("lineups") or []:
        latest = max(latest, lu["scoring_period"])
        for p in lu["players"]:
            timeline[p["player_id"]][lu["scoring_period"]] = lu["team_id"]
            names[p["player_id"]] = p["name"]
    for tid, roster in (season.get("rosters") or {}).items():
        for p in roster:
            timeline[p["player_id"]][latest + 1] = int(tid)
            if p.get("name"):
                names[p["player_id"]] = p["name"]
    return timeline, names


def player_moves(season: dict) -> tuple[list, dict]:
    """Every time a player changed teams without being picked up: [{pid, from, to, window}]."""
    timeline, names = roster_timeline(season)
    adds = defaultdict(list)       # (player_id, team) -> periods it was picked up
    for t in season.get("transactions") or []:
        for i in t["items"]:
            if i["action"] in ("FA ADDED", "WAIVER ADDED") and i.get("player_id") is not None:
                adds[(i["player_id"], i["team_id"])].append(t.get("period") or 0)
    moves = []
    for pid, tl in timeline.items():
        seq = sorted(tl.items())
        for (pa, a), (pb, b) in zip(seq, seq[1:]):
            if a != b and not any(pa <= sp <= pb for sp in adds.get((pid, b), [])):
                moves.append({"pid": pid, "from": a, "to": b, "window": (pa, pb)})
    return moves, names


def rebuild_trades(season: dict) -> list[dict]:
    """[{window, teams: [a, b], got: {team: [player names]}, picks: {team: [...]}}], oldest first."""
    moves, names = player_moves(season)
    groups = defaultdict(list)
    for m in moves:
        groups[(m["window"], tuple(sorted((m["from"], m["to"]))))].append(m)
    trades = []
    for (window, pair), ms in sorted(groups.items()):
        got = {tid: [] for tid in pair}
        for m in ms:
            got[m["to"]].append(names.get(m["pid"], "Unknown player"))
        trades.append({"window": window, "teams": list(pair), "got": got, "picks": {tid: [] for tid in pair},
                       "pick_only": False})
    return trades


def board_traded_picks(draft: dict | None) -> list[dict]:
    """Picks on a finished draft board made by a team that didn't own the slot originally."""
    if not draft or not draft.get("complete") or not draft.get("pick_order"):
        return []
    order, out = draft["pick_order"], []
    for p in draft.get("picks", []):
        original = next((t for t in order if picks.slot_number(draft, p["round"], t) == p["pick"]), None)
        if original is not None and p.get("team_id") and p["team_id"] != original:
            out.append({"round": p["round"], "slot": f"{p['round']}.{p['pick']:02d}", "from": original,
                        "to": p["team_id"], "player": p.get("player"), "keeper": bool(p.get("keeper"))})
    return out


def _sheet_trade_window(st: dict, timeline: dict, names: dict) -> tuple | None:
    """When a sheet trade's players moved on ESPN, however the move was made (a real trade, or a
    commissioner dropping and adding them). None if no received player is on his new team yet."""
    by_key = defaultdict(list)
    for pid, name in names.items():
        by_key[picks.player_key(name)].append(pid)
    teams = {s["team"] for s in st["sides"]}
    windows = []
    for s in st["sides"]:
        for player in s["players"]:
            for pid in by_key.get(picks.player_key(player), []):
                seq = sorted(timeline.get(pid, {}).items())
                for (pa, a), (pb, b) in zip(seq, seq[1:]):
                    if b == s["team"] and a in teams and a != b:
                        windows.append((pa, pb))
    return max(windows) if windows else None


def attach_picks(trades: list, season: int, next_draft: dict | None, sheet_trades: list,
                 season_data: dict | None = None) -> list:
    """Adds the draft picks that went with each rebuilt trade.

    1. Sheet trades made in `season`: matched to the rebuilt trade with the same players and
       teams; their players and picks (exact) are used. If the rosters show the players moved but
       not as a trade (a commissioner's manual move), the sheet trade still counts as done on
       ESPN. Otherwise it's marked sheet_only (pick-only, or not processed on ESPN yet).
    2. Picks on the next finished draft board that changed hands and aren't explained by the
       sheet: attached to the latest rebuilt trade between those two teams, else a pick-only trade.
    """
    timeline, names = roster_timeline(season_data) if season_data else ({}, {})
    explained = set()
    for st in (t for t in sheet_trades if t["season"] == season):
        teams = {s["team"] for s in st["sides"]}
        players = {picks.player_key(p) for s in st["sides"] for p in s["players"]}
        match = None
        for t in trades:
            rebuilt_players = {picks.player_key(p) for ps in t["got"].values() for p in ps}
            if set(t["teams"]) <= teams and players & rebuilt_players:
                match = t
        if match is None:
            window = _sheet_trade_window(st, timeline, names) if players else None
            match = {"window": window, "teams": sorted(teams), "got": {tid: [] for tid in teams},
                     "picks": {tid: [] for tid in teams}, "pick_only": not players,
                     "sheet_only": window is None}
            trades.append(match)
        match["from_sheet"] = True
        match["note"] = st.get("note") or ""
        for s in st["sides"]:
            match["got"].setdefault(s["team"], [])
            match["picks"].setdefault(s["team"], [])
            have = {picks.player_key(p) for p in match["got"][s["team"]]}
            match["got"][s["team"]] += [p for p in s["players"] if picks.player_key(p) not in have]
            for p in s["picks"]:
                match["picks"][s["team"]].append({"year": p["year"], "round": p["round"], "from": p["from"]})
                explained.add((p["year"], p["round"], p["from"]))

    year = season + 1
    for bp in board_traded_picks(next_draft):
        if (year, bp["round"], bp["from"]) in explained:
            continue
        pair = sorted((bp["from"], bp["to"]))
        between = [t for t in trades if sorted(t["teams"]) == pair and not t.get("from_sheet")]
        if between:
            target = between[-1]
        else:
            target = next((t for t in trades if t["pick_only"] and sorted(t["teams"]) == pair), None)
            if target is None:
                target = {"window": None, "teams": pair, "got": {tid: [] for tid in pair},
                          "picks": {tid: [] for tid in pair}, "pick_only": True}
                trades.append(target)
        target["picks"][bp["to"]].append({"year": year, "round": bp["round"], "from": bp["from"],
                                          "slot": bp["slot"], "player": bp["player"]})
    return trades
