"""All league calculations.

Pure functions over the plain season dicts produced by normalize.py: no
network, no file access. A "unit" is one manager as the site shows them:
everyone who has ever co-owned a team together counts as one unit, keyed by
a stable owner ID, so history follows people rather than team names.

Game kinds:
  regular      tier NONE, week <= regular_season_weeks
  playoff      championship bracket (WINNERS_BRACKET)
  consolation  everything else after the regular season
Standings use regular games only. Records and head-to-head use regular +
playoff. Weekly awards use every game played that week. Byes are never games.
"""
import re
from collections import defaultdict
from functools import lru_cache

REGULAR, PLAYOFF, CONSOLATION = "regular", "playoff", "consolation"
ALL_KINDS = (REGULAR, PLAYOFF, CONSOLATION)
RECORD_KINDS = (REGULAR, PLAYOFF)


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def win_pct(wins: int, losses: int, ties: int) -> float:
    played = wins + losses + ties
    return (wins + ties / 2) / played if played else 0.0


# --------------------------------------------------------------------------
# Managers
# --------------------------------------------------------------------------

def build_managers(seasons: list, overrides: dict | None = None) -> dict:
    """Groups owner IDs into units.

    Returns {"units": {key: unit}, "owner_unit": {owner_id: key}} where unit =
    {key, slug, display_name, owner_ids, seasons, former}.
    """
    overrides = overrides or {}
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    seasons = sorted(seasons, key=lambda s: s["season"])
    seasons_of = defaultdict(set)
    name_in = {}  # (season, owner_id) -> name
    for s in seasons:
        for m in s.get("managers", []):
            name_in[(s["season"], m["owner_id"])] = m["display_name"]
        for t in s["teams"]:
            ids = _team_owner_ids(s, t)
            for oid in ids:
                find(oid)
                seasons_of[oid].add(s["season"])
            for oid in ids[1:]:
                union(ids[0], oid)

    targets = set()
    for oid, ov in overrides.items():
        target = (ov or {}).get("merge_into")
        if target:
            union(oid, target)
            targets.add(target)

    groups = defaultdict(list)
    for oid in list(parent):
        groups[find(oid)].append(oid)

    latest = seasons[-1]["season"] if seasons else None
    units, owner_unit = {}, {}
    for members in groups.values():
        years = sorted(set().union(*(seasons_of[m] for m in members)))
        if not years:
            continue
        key = max(members, key=lambda m: (m in targets, len(seasons_of[m]),
                                          -min(seasons_of[m], default=9999), m))
        members = sorted(members, key=lambda m: (m != key, m))
        display = next((overrides[m]["display_name"] for m in members
                        if (overrides.get(m) or {}).get("display_name")), None)
        if not display:
            display = _latest_names(seasons, members, name_in)
        former = any((overrides.get(m) or {}).get("former") for m in members) or latest not in years
        units[key] = {
            "key": key,
            "slug": _slug(key),
            "display_name": display,
            "owner_ids": members,
            "seasons": years,
            "former": bool(former),
        }
        for m in members:
            owner_unit[m] = key
    return {"units": units, "owner_unit": owner_unit}


def _team_owner_ids(season: dict, team: dict) -> list:
    return team["owner_ids"] or [f"team:{season['season']}:{team['team_id']}"]


def _latest_names(seasons: list, members: list, name_in: dict) -> str:
    member_set = set(members)
    for s in reversed(seasons):
        for t in s["teams"]:
            ids = [o for o in _team_owner_ids(s, t) if o in member_set]
            if ids:
                names = [name_in.get((s["season"], o)) or _any_name(o, name_in) or t["team_name"] for o in ids]
                return " & ".join(dict.fromkeys(names))
    return members[0]


def _any_name(oid: str, name_in: dict) -> str | None:
    found = [(y, n) for (y, o), n in name_in.items() if o == oid]
    return max(found)[1] if found else None


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", key.lower()) or "manager"


def team_units(season: dict, managers: dict) -> dict:
    """{team_id: unit key} for one season."""
    out = {}
    for t in season["teams"]:
        ids = _team_owner_ids(season, t)
        out[t["team_id"]] = managers["owner_unit"].get(ids[0], ids[0])
    return out


# --------------------------------------------------------------------------
# Games and standings
# --------------------------------------------------------------------------

def game_kind(matchup: dict, regular_weeks: int) -> str:
    if matchup["is_playoff"]:
        return PLAYOFF
    if matchup["is_consolation"] or matchup["week"] > regular_weeks:
        return CONSOLATION
    return REGULAR


def games(season: dict, kinds=RECORD_KINDS, through_week: int | None = None) -> list:
    """Final, non-bye games of the given kinds, each with a "kind" field added."""
    reg = season["settings"]["regular_season_weeks"]
    out = []
    for m in season["matchups"]:
        if m["is_bye"] or not m["final"]:
            continue
        if through_week is not None and m["week"] > through_week:
            continue
        kind = game_kind(m, reg)
        if kind in kinds:
            out.append({**m, "kind": kind})
    return out


def _sides(game: dict):
    """Both perspectives of a game: (team, opp, pf, pa, result)."""
    h, a = game["home_team_id"], game["away_team_id"]
    hs, as_ = game["home_score"], game["away_score"]
    if game["winner"] == "tie":
        hr, ar = "T", "T"
    else:
        hr, ar = ("W", "L") if game["winner"] == "home" else ("L", "W")
    yield h, a, hs, as_, hr
    yield a, h, as_, hs, ar


def team_games(season: dict, kinds=(REGULAR,), through_week: int | None = None) -> dict:
    out = {t["team_id"]: [] for t in season["teams"]}
    for g in games(season, kinds, through_week):
        for team, opp, pf, pa, res in _sides(g):
            out.setdefault(team, []).append({
                "week": g["week"], "opp": opp, "pf": pf, "pa": pa, "result": res, "kind": g["kind"],
            })
    for lst in out.values():
        lst.sort(key=lambda x: x["week"])
    return out


def streak(results: list) -> str:
    """Current run from the end of a W/L/T list, e.g. "W3". Empty if no games."""
    if not results:
        return ""
    last, n = results[-1], 0
    for r in reversed(results):
        if r != last:
            break
        n += 1
    return f"{last}{n}"


def standings(season: dict, through_week: int | None = None, use_espn_seeds: bool = False) -> list:
    """Regular season standings rows, best first, each with a 1-based "rank".

    use_espn_seeds orders by ESPN's own playoff seeds (which apply the league's
    real tiebreakers and division winners) when every team has one.
    """
    tg = team_games(season, (REGULAR,), through_week)
    rows = []
    for t in season["teams"]:
        gs = tg.get(t["team_id"], [])
        res = [g["result"] for g in gs]
        w, l, ti = res.count("W"), res.count("L"), res.count("T")
        rows.append({
            "team_id": t["team_id"],
            "wins": w, "losses": l, "ties": ti,
            "pct": win_pct(w, l, ti),
            "pf": _r(sum(g["pf"] for g in gs)),
            "pa": _r(sum(g["pa"] for g in gs)),
            "streak": streak(res),
            "games": len(gs),
            "seed": t.get("playoff_seed"),
        })
    if use_espn_seeds and through_week is None and all(r["seed"] for r in rows):
        rows.sort(key=lambda r: r["seed"])
    else:
        rows.sort(key=lambda r: (-r["pct"], -r["pf"], r["team_id"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def complete_weeks(season: dict) -> list:
    """Weeks whose every real game is final."""
    by_week = defaultdict(list)
    for m in season["matchups"]:
        if not m["is_bye"]:
            by_week[m["week"]].append(m["final"])
    return sorted(w for w, finals in by_week.items() if finals and all(finals))


def regular_complete_weeks(season: dict, through_week: int | None = None) -> list:
    reg = season["settings"]["regular_season_weeks"]
    limit = reg if through_week is None else min(reg, through_week)
    return [w for w in complete_weeks(season) if w <= limit]


def week_scores(season: dict, week: int) -> dict:
    """{team_id: points} for a week, including teams on a bye."""
    out = {}
    for m in season["matchups"]:
        if m["week"] != week:
            continue
        out[m["home_team_id"]] = m["home_score"]
        if m["away_team_id"] is not None:
            out[m["away_team_id"]] = m["away_score"]
    return out


def all_play(season: dict, through_week: int | None = None) -> dict:
    """Record each team would have had playing every other team every week."""
    rec = {t["team_id"]: [0, 0, 0] for t in season["teams"]}
    for w in regular_complete_weeks(season, through_week):
        scores = week_scores(season, w)
        for team, s in scores.items():
            r = rec.setdefault(team, [0, 0, 0])
            for other, o in scores.items():
                if other == team:
                    continue
                r[0 if s > o else 1 if s < o else 2] += 1
    return {t: {"wins": w, "losses": l, "ties": ti, "pct": win_pct(w, l, ti)}
            for t, (w, l, ti) in rec.items()}


def luck(season: dict, through_week: int | None = None) -> dict:
    """Actual win % minus all-play win %. Positive = lucky."""
    ap = all_play(season, through_week)
    return {r["team_id"]: round(r["pct"] - ap[r["team_id"]]["pct"], 3)
            for r in standings(season, through_week) if r["games"]}


# --------------------------------------------------------------------------
# Lineups
# --------------------------------------------------------------------------

def optimal_points(players: list, slots: dict) -> float:
    """Best possible starting lineup total, exactly.

    slots: {"QB": 1, "RB": 2, "RB/WR/TE": 1, ...}. A player can fill any slot
    listed in his eligible_slots (so FLEX / superflex rules come from ESPN).
    Players on IR can't start. Empty slots are allowed, as on ESPN.
    """
    names = [s for s, c in slots.items() if c > 0]
    start = tuple(slots[s] for s in names)
    pool = []
    for p in players:
        if p.get("slot") == "IR" or p["points"] <= 0:
            continue
        elig = tuple(i for i, s in enumerate(names) if s in p["eligible_slots"])
        if elig:
            pool.append((p["points"], elig))
    pool.sort(reverse=True)

    @lru_cache(maxsize=None)
    def best(i: int, left: tuple) -> float:
        if i == len(pool) or not any(left):
            return 0.0
        pts, elig = pool[i]
        top = best(i + 1, left)
        for j in elig:
            if left[j]:
                nxt = left[:j] + (left[j] - 1,) + left[j + 1:]
                top = max(top, pts + best(i + 1, nxt))
        return top

    return _r(best(0, start))


def lineup_efficiency(season: dict) -> dict | None:
    """{(week, team_id): {actual, optimal, bench_left}} for complete weeks, or
    None if this season has no lineup data."""
    lineups = season.get("lineups")
    if not lineups:
        return None
    slots = season["settings"]["lineup_slots"]
    done = set(complete_weeks(season))
    acc = defaultdict(lambda: [0.0, 0.0])
    for entry in lineups:
        if entry["week"] not in done:
            continue
        actual = sum(p["points"] for p in entry["players"] if p["starter"])
        best = max(optimal_points(entry["players"], slots), actual)
        a = acc[(entry["week"], entry["team_id"])]
        a[0] += actual
        a[1] += best
    return {k: {"actual": _r(a), "optimal": _r(o), "bench_left": _r(o - a)} for k, (a, o) in acc.items()}


# --------------------------------------------------------------------------
# Power rankings
# --------------------------------------------------------------------------

def _minmax(values: dict) -> dict:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def power_rankings_by_week(season: dict, weights: dict) -> dict:
    """{week: [rows best first]} for every complete regular-season week.

    Score = all_play_weight x all-play win % + points_for_weight x points for
    + recent_form_weight x average of the last N weeks, each min-max
    normalised to 0-1 across teams, then x100.
    """
    w_ap = weights.get("all_play_weight", 0.40)
    w_pf = weights.get("points_for_weight", 0.35)
    w_rf = weights.get("recent_form_weight", 0.25)
    n_recent = weights.get("recent_form_weeks", 3)

    out, prev_rank = {}, None
    weeks = regular_complete_weeks(season)
    scores_by_week = {w: week_scores(season, w) for w in weeks}
    for i, week in enumerate(weeks):
        upto = weeks[: i + 1]
        ap = all_play(season, week)
        teams = [t for t in ap if any(t in scores_by_week[w] for w in upto)]
        pf = {t: sum(scores_by_week[w].get(t, 0) for w in upto) for t in teams}
        recent = {}
        for t in teams:
            last = [scores_by_week[w][t] for w in upto if t in scores_by_week[w]][-n_recent:]
            recent[t] = sum(last) / len(last) if last else 0
        n_ap = _minmax({t: ap[t]["pct"] for t in teams})
        n_pf, n_rf = _minmax(pf), _minmax(recent)
        rows = [{
            "team_id": t,
            "score": round(100 * (w_ap * n_ap[t] + w_pf * n_pf[t] + w_rf * n_rf[t]), 1),
            "all_play_pct": ap[t]["pct"],
            "all_play": ap[t],
            "pf": _r(pf[t]),
            "recent_avg": _r(recent[t]),
        } for t in teams]
        rows.sort(key=lambda r: (-r["score"], -r["pf"], r["team_id"]))
        for rank, r in enumerate(rows, 1):
            r["rank"] = rank
            r["prev_rank"] = prev_rank.get(r["team_id"]) if prev_rank else None
            r["move"] = (r["prev_rank"] - rank) if r["prev_rank"] else None
        out[week] = rows
        prev_rank = {r["team_id"]: r["rank"] for r in rows}
    return out


def ranks_before(power_by_week: dict, week: int) -> dict | None:
    """Power ranks going into `week` (from the last ranked week before it)."""
    earlier = [w for w in power_by_week if w < week]
    if not earlier:
        return None
    return {r["team_id"]: r["rank"] for r in power_by_week[max(earlier)]}


# --------------------------------------------------------------------------
# Weekly awards
# --------------------------------------------------------------------------

def weekly_awards(season: dict, week: int, power_by_week: dict, efficiency: dict | None) -> dict | None:
    week_games = [g for g in games(season, ALL_KINDS) if g["week"] == week]
    if not week_games:
        return None
    sides = [dict(zip(("team_id", "opp_id", "score", "opp_score", "result"), s))
             for g in week_games for s in _sides(g)]
    by_score = sorted(sides, key=lambda s: (-s["score"], s["team_id"]))

    def game_award(g):
        home_won = g["winner"] == "home"
        win, lose = ("home", "away") if home_won or g["winner"] == "tie" else ("away", "home")
        return {
            "winner_id": g[f"{win}_team_id"], "loser_id": g[f"{lose}_team_id"],
            "winner_score": g[f"{win}_score"], "loser_score": g[f"{lose}_score"],
            "margin": _r(abs(g["home_score"] - g["away_score"])),
            "tie": g["winner"] == "tie", "kind": g["kind"],
        }

    decided = [g for g in week_games if g["winner"] != "tie"]
    awards = {
        "high_score": by_score[0],
        "low_score": by_score[-1],
        "blowout": game_award(max(decided, key=lambda g: abs(g["home_score"] - g["away_score"]))) if decided else None,
        "closest": game_award(min(week_games, key=lambda g: abs(g["home_score"] - g["away_score"]))),
        "upset": None, "best_manager": None, "worst_manager": None, "unlucky": None,
    }

    pre = ranks_before(power_by_week, week)
    if pre:
        upsets = []
        for g in decided:
            a = game_award(g)
            wr, lr = pre.get(a["winner_id"]), pre.get(a["loser_id"])
            if wr and lr and wr > lr:
                upsets.append((wr - lr, a["margin"], {**a, "winner_rank": wr, "loser_rank": lr}))
        if upsets:
            awards["upset"] = max(upsets, key=lambda u: (u[0], u[1]))[2]

    if efficiency:
        eff = [(efficiency[(week, s["team_id"])], s["team_id"]) for s in sides if (week, s["team_id"]) in efficiency]
        if len(eff) >= 2:
            best = min(eff, key=lambda e: (e[0]["bench_left"], e[1]))
            worst = max(eff, key=lambda e: (e[0]["bench_left"], -e[1]))
            awards["best_manager"] = {"team_id": best[1], **best[0]}
            awards["worst_manager"] = {"team_id": worst[1], **worst[0]}

    if len(by_score) > 1 and by_score[1]["result"] == "L":
        awards["unlucky"] = by_score[1]
    return awards


# --------------------------------------------------------------------------
# Season results
# --------------------------------------------------------------------------

def _team_with_finish(season: dict, place: int) -> int | None:
    if season["status"]["state"] != "complete":
        return None
    return next((t["team_id"] for t in season["teams"] if t["final_standing"] == place), None)


def champion(season: dict) -> int | None:
    return _team_with_finish(season, 1)


def runner_up(season: dict) -> int | None:
    return _team_with_finish(season, 2)


def last_place(season: dict) -> int | None:
    if season["status"]["state"] != "complete":
        return None
    ranked = [t for t in season["teams"] if t["final_standing"]]
    return max(ranked, key=lambda t: t["final_standing"])["team_id"] if ranked else None


def playoff_teams(season: dict) -> set:
    return {tid for m in season["matchups"] if m["is_playoff"]
            for tid in (m["home_team_id"], m["away_team_id"]) if tid is not None}


# --------------------------------------------------------------------------
# All-time: head-to-head, records, manager profiles
# --------------------------------------------------------------------------

def _blank_h2h():
    return {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0,
            "playoff_wins": 0, "playoff_losses": 0, "playoff_ties": 0, "games": []}


def head_to_head(seasons: list, managers: dict) -> dict:
    """{(unit_a, unit_b): record from a's point of view}, regular + playoff games."""
    rec = defaultdict(_blank_h2h)
    for s in sorted(seasons, key=lambda s: s["season"]):
        tu = team_units(s, managers)
        for g in games(s, RECORD_KINDS):
            for team, opp, pf, pa, res in _sides(g):
                a, b = tu.get(team), tu.get(opp)
                if a is None or b is None or a == b:
                    continue
                r = rec[(a, b)]
                r[{"W": "wins", "L": "losses", "T": "ties"}[res]] += 1
                r["pf"] += pf
                r["pa"] += pa
                if g["kind"] == PLAYOFF:
                    r[{"W": "playoff_wins", "L": "playoff_losses", "T": "playoff_ties"}[res]] += 1
                r["games"].append({"season": s["season"], "week": g["week"], "kind": g["kind"],
                                   "pf": pf, "pa": pa, "result": res})
    for r in rec.values():
        r["pf"], r["pa"] = _r(r["pf"]), _r(r["pa"])
    return dict(rec)


def _runs(results: list, want: str) -> list:
    """Maximal runs of `want` in a chronological list of (result, where) tuples."""
    runs, cur = [], []
    for res, where in results:
        if res == want:
            cur.append(where)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def records(seasons: list, managers: dict, top: int = 10) -> dict:
    """All-time leaderboards (top N each). Weekly entries carry season + week so
    the site can link to the game; season entries carry season."""
    seasons = sorted(seasons, key=lambda s: s["season"])
    weekly, margins, season_rows, game_rows, bench = [], [], [], [], []
    results_by_unit = defaultdict(list)
    titles, lasts, playoff_years = defaultdict(list), defaultdict(list), defaultdict(list)
    shame_by_season = []

    for s in seasons:
        tu = team_units(s, managers)
        season_scores = []
        played = {}                       # (week, team) -> game kind
        for g in games(s, RECORD_KINDS):
            for team, opp, pf, pa, res in _sides(g):
                entry = {"unit": tu[team], "season": s["season"], "week": g["week"], "team_id": team,
                         "opp_unit": tu[opp], "opp_id": opp, "score": pf, "opp_score": pa,
                         "result": res, "kind": g["kind"]}
                weekly.append(entry)
                season_scores.append(entry)
                played[(g["week"], team)] = g["kind"]
                if res == "W":
                    margins.append({**entry, "margin": _r(pf - pa)})
                results_by_unit[tu[team]].append((res, (s["season"], g["week"])))
            # One row per game, from the winner's side (home side for a tie).
            home_won = g["winner"] in ("home", "tie")
            w, l = ("home", "away") if home_won else ("away", "home")
            game_rows.append({
                "unit": tu[g[f"{w}_team_id"]], "opp_unit": tu[g[f"{l}_team_id"]],
                "team_id": g[f"{w}_team_id"], "opp_id": g[f"{l}_team_id"],
                "season": s["season"], "week": g["week"], "kind": g["kind"],
                "score": g[f"{w}_score"], "opp_score": g[f"{l}_score"],
                "total": _r(g["home_score"] + g["away_score"]),
                "margin": _r(abs(g["home_score"] - g["away_score"])), "tie": g["winner"] == "tie",
            })
        if season_scores:
            shame_by_season.append(min(season_scores, key=lambda e: e["score"]))

        eff = lineup_efficiency(s) or {}
        for (week, team), v in eff.items():
            if (week, team) in played:        # skip consolation weeks, where nobody's trying
                bench.append({"unit": tu[team], "season": s["season"], "week": week, "team_id": team,
                              "kind": played[(week, team)], **v})

        for tid in playoff_teams(s):
            playoff_years[tu[tid]].append(s["season"])
        if s["status"]["state"] == "complete":
            lk = luck(s)
            ap = all_play(s)
            for row in standings(s):
                a = ap.get(row["team_id"], {"wins": 0, "losses": 0, "ties": 0, "pct": 0.0})
                season_rows.append({"unit": tu[row["team_id"]], "season": s["season"], "team_id": row["team_id"],
                                    "pf": row["pf"], "pa": row["pa"], "games": row["games"],
                                    "wins": row["wins"], "losses": row["losses"], "ties": row["ties"],
                                    "pct": row["pct"], "luck": lk.get(row["team_id"], 0.0),
                                    "ap_wins": a["wins"], "ap_losses": a["losses"], "ap_ties": a["ties"],
                                    "ap_pct": a["pct"]})
            if (c := champion(s)) is not None:
                titles[tu[c]].append(s["season"])
            if (lp := last_place(s)) is not None:
                lasts[tu[lp]].append(s["season"])

    def streaks(want):
        out = []
        for unit, res in results_by_unit.items():
            res = sorted(res, key=lambda x: x[1])
            for run in _runs(res, want):
                out.append({"unit": unit, "length": len(run), "start": run[0], "end": run[-1]})
        return sorted(out, key=lambda x: (-x["length"], x["start"]))[:top]

    def counts(d):
        return sorted(({"unit": u, "count": len(ys), "seasons": ys} for u, ys in d.items()),
                      key=lambda x: (-x["count"], x["seasons"]))

    when = lambda e: (e["season"], e["week"])
    losses = [e for e in weekly if e["result"] == "L"]
    wins = [e for e in weekly if e["result"] == "W"]

    # Careers: weekly highs/lows (regular season), points, playoff wins.
    by_week = defaultdict(list)
    for e in weekly:
        if e["kind"] == REGULAR:
            by_week[when(e)].append(e)
    highs, lows = defaultdict(int), defaultdict(int)
    for entries in by_week.values():
        top_score = max(e["score"] for e in entries)
        low_score = min(e["score"] for e in entries)
        for e in entries:
            highs[e["unit"]] += e["score"] == top_score
            lows[e["unit"]] += e["score"] == low_score
    career = defaultdict(lambda: {"points": 0.0, "games": 0, "po_w": 0, "po_l": 0, "po_t": 0})
    for e in weekly:
        c = career[e["unit"]]
        if e["kind"] == REGULAR:
            c["points"] += e["score"]
            c["games"] += 1
        else:
            c[{"W": "po_w", "L": "po_l", "T": "po_t"}[e["result"]]] += 1

    def ranked(values, detail=lambda u: "", reverse=True):
        rows = [{"unit": u, "value": v, "detail": detail(u)} for u, v in values.items() if v]
        return sorted(rows, key=lambda r: (-r["value"] if reverse else r["value"], r["unit"]))[:top]

    min_games = 20
    ppg = {u: _r(c["points"] / c["games"]) for u, c in career.items() if c["games"] >= min_games}
    return {
        # Weekly
        "highest_week": sorted(weekly, key=lambda e: (-e["score"], *when(e)))[:top],
        "lowest_week": sorted(weekly, key=lambda e: (e["score"], *when(e)))[:top],
        "biggest_margin": sorted(margins, key=lambda e: (-e["margin"], *when(e)))[:top],
        "highest_combined": sorted(game_rows, key=lambda e: (-e["total"], *when(e)))[:top],
        "closest_games": sorted(game_rows, key=lambda e: (e["margin"], *when(e)))[:top],
        "highest_losing": sorted(losses, key=lambda e: (-e["score"], *when(e)))[:top],
        "lowest_winning": sorted(wins, key=lambda e: (e["score"], *when(e)))[:top],
        "most_bench_left": sorted(bench, key=lambda e: (-e["bench_left"], *when(e)))[:top],
        # Season
        "most_pf_season": sorted(season_rows, key=lambda e: (-e["pf"], e["season"]))[:top],
        "fewest_pf_season": sorted(season_rows, key=lambda e: (e["pf"], e["season"]))[:top],
        "best_record": sorted(season_rows, key=lambda e: (-e["pct"], -e["pf"], e["season"]))[:top],
        "worst_record": sorted(season_rows, key=lambda e: (e["pct"], e["pf"], e["season"]))[:top],
        "most_pa_season": sorted(season_rows, key=lambda e: (-e["pa"], e["season"]))[:top],
        "luckiest_season": sorted(season_rows, key=lambda e: (-e["luck"], e["season"]))[:top],
        "unluckiest_season": sorted(season_rows, key=lambda e: (e["luck"], e["season"]))[:top],
        # Streaks and careers
        "longest_win_streak": streaks("W"),
        "longest_losing_streak": streaks("L"),
        "championships": counts(titles),
        "last_places": counts(lasts),
        "playoff_appearances": counts(playoff_years)[:top],
        "lowest_by_season": shame_by_season,
        "best_all_play": sorted(season_rows, key=lambda e: (-e["ap_pct"], -e["pf"], e["season"]))[:top],
        "most_weekly_highs": ranked(highs, lambda u: "weeks with the league's top score"),
        "most_weekly_lows": ranked(lows, lambda u: "weeks with the league's lowest score"),
        "career_points": ranked({u: _r(c["points"]) for u, c in career.items()},
                                lambda u: f"{career[u]['games']} regular-season games"),
        "career_ppg": ranked(ppg, lambda u: f"over {career[u]['games']} games (min. {min_games})"),
        "playoff_wins": ranked({u: c["po_w"] for u, c in career.items()},
                               lambda u: f"playoff record {career[u]['po_w']}-{career[u]['po_l']}"
                                         + (f"-{career[u]['po_t']}" if career[u]["po_t"] else "")),
    }


PLAYER_POSITIONS = ["QB", "RB", "WR", "TE", "K", "D/ST"]


def player_records(seasons: list, managers: dict, top: int = 10) -> dict:
    """Leaderboards for individual players, from lineup data (regular + playoff
    games only). Seasons without lineups are simply skipped."""
    perf, benched = [], []
    season_tot = {}
    for s in sorted(seasons, key=lambda s: s["season"]):
        tu = team_units(s, managers)
        kinds = {}
        for g in games(s, RECORD_KINDS):
            kinds[(g["week"], g["home_team_id"])] = g["kind"]
            kinds[(g["week"], g["away_team_id"])] = g["kind"]
        for entry in s.get("lineups") or []:
            kind = kinds.get((entry["week"], entry["team_id"]))
            if not kind:
                continue
            for p in entry["players"]:
                base = {"unit": tu[entry["team_id"]], "team_id": entry["team_id"], "season": s["season"],
                        "week": entry["week"], "kind": kind, "player": p["name"], "position": p["position"],
                        "pro_team": p["pro_team"], "points": p["points"]}
                if p["starter"]:
                    perf.append(base)
                    key = (s["season"], entry["team_id"], p["player_id"])
                    tot = season_tot.setdefault(key, {**base, "points": 0.0, "starts": 0})
                    tot["points"] = _r(tot["points"] + p["points"])
                    tot["starts"] += 1
                elif p["slot"] == "BE":
                    benched.append(base)
    order = lambda e: (-e["points"], e["season"], e["week"])
    season_list = [{k: v for k, v in e.items() if k not in ("week", "kind")} for e in season_tot.values()]
    return {
        "best_player_week": sorted(perf, key=order)[:top],
        "best_bench_week": sorted(benched, key=order)[:top],
        "best_player_season": sorted(season_list, key=lambda e: (-e["points"], e["season"]))[:top],
        "position_bests": [min((e for e in perf if e["position"] == pos), key=order)
                           for pos in PLAYER_POSITIONS if any(e["position"] == pos for e in perf)],
    }


def manager_profiles(seasons: list, managers: dict) -> dict:
    """{unit key: {seasons: [...per-season rows], totals}}."""
    profiles = {k: {"key": k, "rows": []} for k in managers["units"]}
    for s in sorted(seasons, key=lambda s: s["season"]):
        tu = team_units(s, managers)
        st = {r["team_id"]: r for r in standings(s)}
        po_games = team_games(s, (PLAYOFF,))
        in_playoffs = playoff_teams(s)
        complete = s["status"]["state"] == "complete"
        champ, second, last = champion(s), runner_up(s), last_place(s)
        for t in s["teams"]:
            unit = tu[t["team_id"]]
            row = st[t["team_id"]]
            po = [g["result"] for g in po_games.get(t["team_id"], [])]
            profiles.setdefault(unit, {"key": unit, "rows": []})["rows"].append({
                "season": s["season"], "team_id": t["team_id"], "team_name": t["team_name"],
                "logo_url": t["logo_url"], "wins": row["wins"], "losses": row["losses"],
                "ties": row["ties"], "pf": row["pf"], "pa": row["pa"], "pct": row["pct"],
                "final_standing": t["final_standing"] if complete else None,
                "team_count": len(s["teams"]), "complete": complete,
                "made_playoffs": t["team_id"] in in_playoffs,
                "playoff_wins": po.count("W"), "playoff_losses": po.count("L"), "playoff_ties": po.count("T"),
                "champion": t["team_id"] == champ, "runner_up": t["team_id"] == second,
                "last_place": t["team_id"] == last,
            })
    for p in profiles.values():
        rows = p["rows"]
        w, l, ti = (sum(r[k] for r in rows) for k in ("wins", "losses", "ties"))
        finishes = [r["final_standing"] for r in rows if r["final_standing"]]
        p.update({
            "seasons_played": len({r["season"] for r in rows}),
            "wins": w, "losses": l, "ties": ti, "pct": win_pct(w, l, ti),
            "pf": _r(sum(r["pf"] for r in rows)), "pa": _r(sum(r["pa"] for r in rows)),
            "titles": [r["season"] for r in rows if r["champion"]],
            "runner_ups": [r["season"] for r in rows if r["runner_up"]],
            "last_places": [r["season"] for r in rows if r["last_place"]],
            "best_finish": min(finishes) if finishes else None,
            "playoff_appearances": sum(1 for r in rows if r["made_playoffs"]),
            "playoff_wins": sum(r["playoff_wins"] for r in rows),
            "playoff_losses": sum(r["playoff_losses"] for r in rows),
            "playoff_ties": sum(r["playoff_ties"] for r in rows),
        })
    return profiles


def all_time_standings(profiles: dict) -> list:
    rows = [p for p in profiles.values() if p["rows"]]
    return sorted(rows, key=lambda p: (-p["pct"], -p["wins"], p["key"]))
