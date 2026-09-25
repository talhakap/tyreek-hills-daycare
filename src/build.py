"""Renders the site from data/ (or test fixtures) into site/. No network.

build.py turns stats into small "view" dicts so the templates only display
things and never calculate. Every link is relative (via `root`), so the site
works at username.github.io/repo/ and on a custom domain alike.
"""
import hashlib
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from . import recaps, stats
from .normalize import anon_id, clean_name
from .config import (CURRENT_FILE, FIXTURES_DIR, RECAPS_DIR, SEASONS_DIR, SITE_DIR, STATIC_DIR,
                     TEMPLATES_DIR)

log = logging.getLogger("league")
POSITION_ORDER = ["QB", "RB", "WR", "TE", "D/ST", "K"]


class BuildError(Exception):
    """A problem worth stopping for, with a message written for humans."""


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_seasons(offline: bool) -> dict:
    if offline:
        files = sorted(FIXTURES_DIR.glob("[0-9]*.json"))
    else:
        files = sorted(SEASONS_DIR.glob("*.json")) + ([CURRENT_FILE] if CURRENT_FILE.exists() else [])
    seasons = {}
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        seasons[data["season"]] = data          # current.json is last, so it wins
    if not seasons:
        raise BuildError("No league data yet. Run `python -m src.main fetch` first, "
                         "or `python -m src.main build --offline` for a preview with fake data.")
    return seasons


_ESPN_ID = re.compile(r"^\{[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}$")


def _public_id(oid: str) -> str:
    """Accept either a raw ESPN ID or its m-code in config.toml."""
    return anon_id(oid) if _ESPN_ID.match(oid or "") else oid


def load_overrides(cfg: dict, offline: bool) -> dict:
    overrides = {}
    for oid, ov in cfg.get("manager_overrides", {}).items():
        ov = dict(ov or {})
        if ov.get("merge_into"):
            ov["merge_into"] = _public_id(ov["merge_into"])
        overrides[_public_id(oid)] = ov
    fixture_overrides = FIXTURES_DIR / "manager_overrides.json"
    if offline and fixture_overrides.exists():
        overrides.update(json.loads(fixture_overrides.read_text(encoding="utf-8")))
    return overrides


# --------------------------------------------------------------------------
# Formatting helpers (also registered as Jinja filters)
# --------------------------------------------------------------------------

def fmt_pts(x) -> str:
    return "–" if x is None else f"{x:,.2f}"


def fmt_pct(x) -> str:
    return "1.000" if x >= 1 else f"{x:.3f}".lstrip("0") if x >= 0 else f"{x:.3f}"


def fmt_signed(x) -> str:
    s = f"{x:+.2f}"
    return s.replace("+0.", "+.").replace("-0.", "−.").replace("-", "−")


def fmt_record(w, l, t) -> str:
    return f"{w}-{l}-{t}" if t else f"{w}-{l}"


def fmt_move(move) -> str:
    if not move:
        return "—"
    return f"▲{move}" if move > 0 else f"▼{-move}"


def to_local(iso: str, tzname: str) -> tuple[str, str]:
    """(display text, ISO) for a UTC timestamp. Falls back to UTC text if this
    computer has no timezone database; the browser then localises it."""
    dt = datetime.fromisoformat(iso)
    label = "UTC"
    try:
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo(tzname))
        label = dt.tzname() or tzname
    except Exception:
        dt = dt.astimezone(timezone.utc)
    hour = dt.hour % 12 or 12
    text = f"{dt:%b} {dt.day}, {dt.year}, {hour}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'} {label}"
    return text, dt.isoformat()


def fmt_date(iso: str, tzname: str) -> str:
    text, _ = to_local(iso, tzname)
    return text.rsplit(",", 1)[0]


def initials(name: str) -> str:
    words = [w for w in "".join(c if c.isalnum() or c.isspace() else " " for c in name).split() if w]
    return ("".join(w[0] for w in words[:2]) or name[:2] or "?").upper()


# --------------------------------------------------------------------------
# View models
# --------------------------------------------------------------------------

class League:
    """Everything the templates need, computed once."""

    def __init__(self, cfg: dict, seasons: dict, overrides: dict):
        self.cfg = cfg
        self.seasons = seasons
        self.years = sorted(seasons)
        self.weights = cfg.get("power_rankings", {})
        self.tz = cfg["site"].get("timezone", "UTC")
        self.managers = stats.build_managers(list(seasons.values()), overrides)
        self.units = self.managers["units"]
        self.season_views = {y: self._season_view(seasons[y]) for y in self.years}
        self.current = self.season_views[self.years[-1]]
        season_list = list(seasons.values())
        self.profiles = stats.manager_profiles(season_list, self.managers)
        self.records = {**stats.records(season_list, self.managers),
                        **stats.player_records(season_list, self.managers)}
        self.h2h = stats.head_to_head(season_list, self.managers)

    # ---- units / teams ----
    def unit_view(self, key: str) -> dict:
        u = self.units[key]
        return {"key": key, "slug": u["slug"], "name": u["display_name"], "former": u["former"]}

    def ordered_units(self) -> list:
        us = sorted(self.units.values(), key=lambda u: (u["former"], u["display_name"].lower()))
        return [self.unit_view(u["key"]) for u in us]

    def _team_views(self, s: dict) -> dict:
        tu = stats.team_units(s, self.managers)
        out = {}
        for t in s["teams"]:
            unit = self.unit_view(tu[t["team_id"]]) if tu[t["team_id"]] in self.units else None
            name = clean_name(t["team_name"])
            out[t["team_id"]] = {
                "id": t["team_id"], "name": name, "abbrev": t["abbrev"],
                "logo": t["logo_url"], "initials": initials(name),
                "unit": unit, "season": s["season"],
            }
        return out

    # ---- seasons ----
    def _season_view(self, s: dict) -> dict:
        teams = self._team_views(s)
        power = stats.power_rankings_by_week(s, self.weights)
        eff = stats.lineup_efficiency(s)
        complete = set(stats.complete_weeks(s))
        title = recaps.championship_game(s)
        title_week = title["week"] if title else None
        reg = s["settings"]["regular_season_weeks"]

        def has_scores(w):
            return any(m["final"] or m["home_score"] or m["away_score"] for m in s["matchups"] if m["week"] == w)

        weeks = []
        for w in sorted({m["week"] for m in s["matchups"]}):
            if not has_scores(w) and w != s["status"]["current_week"]:
                continue
            done = w in complete
            recap = recaps.recap_for_week(s, w, self.managers, power, eff, RECAPS_DIR,
                                          self.cfg["recaps"].get("manual_override_mode", "replace")) if done else None
            awards = stats.weekly_awards(s, w, power, eff) if done else None
            weeks.append({
                "season": s["season"], "week": w, "complete": done,
                "label": self._week_label(w, reg, title_week),
                "short": f"Week {w}", "postseason": w > reg,
                "matchups": self._matchups(s, w, teams, reg),
                "byes": [teams[m["home_team_id"]] for m in s["matchups"]
                         if m["week"] == w and m["is_bye"] and m["home_team_id"] in teams],
                "recap": recap,
                "awards": self._attach(awards, teams) if awards else None,
                "href": f"weeks/{s['season']}-{w}.html",
            })
        for i, wk in enumerate(weeks):
            wk["prev"] = weeks[i - 1] if i else None
            wk["next"] = weeks[i + 1] if i + 1 < len(weeks) else None

        champ, second = stats.champion(s), stats.runner_up(s)
        return {
            "year": s["season"], "raw": s, "teams": teams, "state": s["status"]["state"],
            "current_week": s["status"]["current_week"], "reg_weeks": reg,
            "playoff_teams": s["settings"]["playoff_teams"], "power": power, "efficiency": eff,
            "weeks": weeks, "weeks_by_num": {w["week"]: w for w in weeks},
            "latest_complete": next((w for w in reversed(weeks) if w["complete"]), None),
            "champion": teams.get(champ), "runner_up": teams.get(second),
            "title_week": title_week, "fetched_at": s["fetched_at"],
        }

    @staticmethod
    def _week_label(w: int, reg: int, title_week: int | None) -> str:
        if w <= reg:
            return f"Week {w}"
        if title_week and w == title_week:
            return f"Week {w} · Championship"
        return f"Week {w} · Playoffs"

    def _matchups(self, s: dict, w: int, teams: dict, reg: int) -> list:
        out = []
        order = {stats.PLAYOFF: 0, stats.REGULAR: 1, stats.CONSOLATION: 2}
        for m in s["matchups"]:
            if m["week"] != w or m["is_bye"]:
                continue
            kind = stats.game_kind(m, reg)
            out.append({
                "kind": kind,
                "badge": {"playoff": "Playoffs", "consolation": "Consolation"}.get(kind),
                "final": m["final"], "tie": m["winner"] == "tie",
                "home": {"team": teams[m["home_team_id"]], "score": m["home_score"], "won": m["winner"] == "home"},
                "away": {"team": teams[m["away_team_id"]], "score": m["away_score"], "won": m["winner"] == "away"},
            })
        return sorted(out, key=lambda x: order[x["kind"]])

    @staticmethod
    def _attach(obj, teams: dict):
        """Adds a team view next to every *_id field (team_id -> team, winner_id -> winner)."""
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                out[k] = League._attach(v, teams)
                if k.endswith("_id") and v in teams:
                    out["team" if k == "team_id" else k[:-3]] = teams[v]
            return out
        return obj

    # ---- tables ----
    def standings_rows(self, sv: dict, use_seeds: bool) -> list:
        luck = stats.luck(sv["raw"])
        rows = []
        for r in stats.standings(sv["raw"], use_espn_seeds=use_seeds):
            rows.append({**r, "team": sv["teams"][r["team_id"]], "luck": luck.get(r["team_id"])})
        return rows

    def final_standings_rows(self, sv: dict) -> list:
        """Final order for a complete season, else current standings."""
        st = {r["team_id"]: r for r in self.standings_rows(sv, use_seeds=True)}
        if sv["state"] != "complete":
            return list(st.values())
        teams = sorted(sv["raw"]["teams"], key=lambda t: (t["final_standing"] or 99, t["team_id"]))
        return [{**st[t["team_id"]], "place": t["final_standing"]} for t in teams]

    def power_view(self, sv: dict) -> dict | None:
        if not sv["power"]:
            return None
        week = max(sv["power"])
        luck = stats.luck(sv["raw"], through_week=week)
        rows = [{**r, "team": sv["teams"][r["team_id"]], "luck": luck.get(r["team_id"]),
                 "move_text": fmt_move(r["move"])} for r in sv["power"][week]]
        return {"season": sv["year"], "week": week, "rows": rows}


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(pts=fmt_pts, pct=fmt_pct, signed=fmt_signed, move=fmt_move)
    env.globals.update(record=fmt_record)
    return env


def _asset_version() -> str:
    """Short hash of style.css + app.js, appended as ?v= to bust browser/CDN caches."""
    h = hashlib.sha1()
    for name in ("style.css", "app.js"):
        h.update((STATIC_DIR / name).read_bytes())
    return h.hexdigest()[:10]


def _base_path(cfg: dict) -> str:
    """Absolute path of the site root, only needed by 404.html (served at any URL)."""
    if cfg["site"].get("custom_domain"):
        return "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    name = repo.split("/")[-1] if repo else ""
    return "/" if not name or name.endswith(".github.io") else f"/{name}/"


def build_site(cfg: dict, offline: bool = False, out_dir: Path = SITE_DIR) -> None:
    seasons = load_seasons(offline)
    league = League(cfg, seasons, load_overrides(cfg, offline))
    env = _env()
    cur = league.current
    tz = league.tz

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "static").mkdir(parents=True)
    for f in STATIC_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, out_dir / "static" / f.name)

    updated_text, updated_iso = to_local(cur["fetched_at"], tz)
    transactions = cur["raw"].get("transactions") or []
    league_id = cfg["league"]["id"]
    colors = {"primary": "#202123", "secondary": "#B0A381", "secondary_text": "#7A6B44", "cream": "#EEE6C1",
              **cfg["colors"]}
    common = {
        "league_name": cfg["league"]["name"], "colors": colors,
        "noindex": cfg["site"].get("hide_from_search_engines", True),
        "updated_text": updated_text, "updated_iso": updated_iso, "timezone": tz,
        "espn_url": f"https://fantasy.espn.com/football/league?leagueId={league_id}",
        "show_transactions": bool(transactions), "offline": offline,
        # Changes whenever style.css/app.js change, so browsers never mix new pages with old CSS.
        "asset_version": _asset_version(),
    }

    def render(template: str, rel_path: str, page: str, title: str, **ctx):
        depth = rel_path.count("/")
        html_text = env.get_template(template).render(
            **common, **ctx, page=page, title=title, root="../" * depth,
            base_href=_base_path(cfg) if template == "404.html" else None)
        path = out_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_text, encoding="utf-8")

    # Home
    preseason = cur["state"] == "preseason" or not cur["weeks"]
    last_season = league.season_views[league.years[-2]] if preseason and len(league.years) > 1 else None
    shown = last_season if preseason and last_season else cur
    this_week = cur["weeks_by_num"].get(cur["current_week"]) or (cur["weeks"][-1] if cur["weeks"] else None)
    power = league.power_view(cur) or (league.power_view(last_season) if last_season else None)
    render("index.html", "index.html", "home", cfg["league"]["name"],
           cur=cur, shown=shown, preseason=preseason,
           standings=league.standings_rows(cur, use_seeds=True) if not preseason else None,
           final_rows=league.final_standings_rows(shown) if shown["state"] == "complete" else None,
           this_week=None if preseason else this_week,
           latest=cur["latest_complete"], power_top=power["rows"][:3] if power else [])

    # Weeks
    seasons_with_weeks = [league.season_views[y] for y in reversed(league.years) if league.season_views[y]["weeks"]]
    for sv in seasons_with_weeks:
        is_current = sv is cur
        render("weeks_index.html", "weeks/index.html" if is_current else f"weeks/season-{sv['year']}.html",
               "weeks", f"{sv['year']} weeks", sv=sv, seasons=seasons_with_weeks, current_year=cur["year"])
        for wk in sv["weeks"]:
            render("week.html", wk["href"], "weeks", f"{wk['label']}, {sv['year']}", sv=sv, wk=wk,
                   current_year=cur["year"])
    if not any(sv is cur for sv in seasons_with_weeks):
        render("weeks_index.html", "weeks/index.html", "weeks", "Weeks",
               sv=cur, seasons=seasons_with_weeks, current_year=cur["year"])

    # Power rankings
    render("power.html", "power.html", "power", "Power rankings", power=power, weights=league.weights,
           preseason=preseason)

    # Teams
    active = [u for u in league.ordered_units() if not u["former"]]
    former = [u for u in league.ordered_units() if u["former"]]
    cards = {u["key"]: _unit_card(league, u) for u in active + former}
    render("teams_index.html", "teams/index.html", "teams", "Teams", active=active, former=former, cards=cards)
    for u in active + former:
        render("team.html", f"teams/{u['slug']}.html", "teams", cards[u["key"]]["name"],
               **_team_page(league, u, cards[u["key"]]))

    # History
    history = []
    for y in reversed(league.years):
        sv = league.season_views[y]
        history.append({"sv": sv, "rows": league.final_standings_rows(sv)})
    all_time = [{**p, "unit": league.unit_view(p["key"])} for p in stats.all_time_standings(league.profiles)]
    render("history.html", "history.html", "history", "History", history=history, all_time=all_time)

    # Head-to-head
    units = league.ordered_units()
    grid, data = {}, {}
    for a in units:
        for b in units:
            r = league.h2h.get((a["key"], b["key"]))
            if r:
                grid[(a["key"], b["key"])] = r
                data.setdefault(a["key"], {})[b["key"]] = {
                    "w": r["wins"], "l": r["losses"], "t": r["ties"], "pf": r["pf"], "pa": r["pa"],
                    "pw": r["playoff_wins"], "pl": r["playoff_losses"], "pt": r["playoff_ties"],
                    "games": [[g["season"], g["week"], g["kind"], g["pf"], g["pa"], g["result"]]
                              for g in reversed(r["games"])],
                }
    render("h2h.html", "h2h.html", "h2h", "Head-to-head", units=units, grid=grid,
           h2h_json={"names": {u["key"]: u["name"] for u in units}, "records": data})

    # Records
    render("records.html", "records.html", "records", "Records", rec=_records_view(league),
           current_year=cur["year"])

    # Transactions
    if transactions:
        teams = cur["teams"]
        items = []
        for t in transactions:
            items.append({**t, "date_text": fmt_date(t["date"], tz),
                          "rows": [{**i, "team": teams.get(i["team_id"])} for i in t["items"]]})
        trades = [t for t in items if t["type"] == "trade"]
        moves = [t for t in items if t["type"] != "trade"]
        render("transactions.html", "transactions.html", "transactions", "Transactions",
               trades=trades, moves=moves, season=cur["year"])

    render("404.html", "404.html", "404", "Page not found")

    # Extras
    hide = cfg["site"].get("hide_from_search_engines", True)
    (out_dir / "robots.txt").write_text("User-agent: *\nDisallow: /\n" if hide else "User-agent: *\nAllow: /\n",
                                        encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    domain = (cfg["site"].get("custom_domain") or "").strip()
    if domain:
        (out_dir / "CNAME").write_text(domain + "\n", encoding="utf-8")

    pages = sum(1 for _ in out_dir.rglob("*.html"))
    log.info("Built %s pages into %s%s", pages, out_dir.name + "/", " (offline preview data)" if offline else "")


def _unit_card(league: League, u: dict) -> dict:
    p = league.profiles[u["key"]]
    latest = p["rows"][-1] if p["rows"] else None
    team = league.season_views[latest["season"]]["teams"][latest["team_id"]] if latest else None
    return {**u, "team": team, "profile": p}


def _team_page(league: League, u: dict, card: dict) -> dict:
    cur = league.current
    p = card["profile"]
    tu = stats.team_units(cur["raw"], league.managers)
    cur_team_id = next((tid for tid, key in tu.items() if key == u["key"]), None)

    roster_years = [ry for r in reversed(p["rows"]) if (ry := _roster_year(league, r))]

    results = None
    if cur_team_id is not None:
        results = []
        reg = cur["reg_weeks"]
        for wk in cur["weeks"]:
            for m in wk["matchups"]:
                side, other = (("home", "away") if m["home"]["team"]["id"] == cur_team_id else
                               ("away", "home") if m["away"]["team"]["id"] == cur_team_id else (None, None))
                if not side:
                    continue
                me, opp = m[side], m[other]
                res = "T" if m["tie"] else "W" if me["won"] else "L" if m["final"] else None
                results.append({"week": wk, "opp": opp["team"], "pf": me["score"], "pa": opp["score"],
                                "result": res, "badge": m["badge"]})
            if any(b["id"] == cur_team_id for b in wk["byes"]) and wk["week"] <= reg:
                results.append({"week": wk, "opp": None, "pf": None, "pa": None, "result": None, "badge": "Bye"})

    rows = []
    for r in reversed(p["rows"]):
        rows.append({**r, "team": league.season_views[r["season"]]["teams"][r["team_id"]]})

    h2h = []
    for other in league.ordered_units():
        rec = league.h2h.get((u["key"], other["key"]))
        if rec:
            h2h.append({"unit": other, **rec})

    return {"card": card, "unit": u, "profile": p, "roster_years": roster_years, "results": results,
            "season_rows": rows, "h2h_rows": h2h, "cur_year": cur["year"],
            "has_current_team": cur_team_id is not None}


def _roster_year(league: League, row: dict) -> dict | None:
    """One season's roster for a team: the live roster for the current season,
    otherwise the roster from the team's last lineup of that season. Each player
    carries the points he scored as a starter for this team that season."""
    s = league.seasons[row["season"]]
    tid = row["team_id"]
    lineups = [l for l in (s.get("lineups") or []) if l["team_id"] == tid]
    pts, starts = {}, {}
    for l in lineups:
        for pl in l["players"]:
            if pl["starter"]:
                pts[pl["player_id"]] = pts.get(pl["player_id"], 0.0) + pl["points"]
                starts[pl["player_id"]] = starts.get(pl["player_id"], 0) + 1

    live = (s.get("rosters") or {}).get(str(tid))
    if live and s["season"] == league.current["year"]:
        players, label = live, "Current roster"
    elif lineups:
        last = max(lineups, key=lambda l: l["scoring_period"])
        players, label = last["players"], f"Final roster (week {last['week']})"
    else:
        return None

    groups = {}
    for pl in players:
        groups.setdefault(pl["position"] or "Other", []).append({
            "name": pl["name"], "pro_team": pl["pro_team"], "slot": pl["slot"],
            "injury_status": pl.get("injury_status"),
            "points": round(pts.get(pl["player_id"], 0.0), 2), "starts": starts.get(pl["player_id"], 0),
        })
    order = POSITION_ORDER + sorted(k for k in groups if k not in POSITION_ORDER)
    return {
        "year": s["season"], "label": label, "team": league.season_views[s["season"]]["teams"][tid],
        "has_points": bool(pts),
        "groups": [(pos, sorted(groups[pos], key=lambda x: (-x["points"], x["name"]))) for pos in order if pos in groups],
    }


def _records_view(league: League) -> dict:
    def team(e):
        return league.season_views[e["season"]]["teams"].get(e["team_id"])

    def unit(key):
        return league.unit_view(key) if key in league.units else None

    def view(e):
        out = {**e, "unit_v": unit(e["unit"])}
        if "team_id" in e:
            out["team"] = team(e)
        if "opp_unit" in e:
            out["opp_v"] = unit(e["opp_unit"])
            out["opp_team"] = league.season_views[e["season"]]["teams"].get(e["opp_id"])
        return out

    rec = dict(league.records)
    rec["lowest_by_season"] = sorted(rec["lowest_by_season"], key=lambda e: -e["season"])
    return {k: [view(e) for e in lst] for k, lst in rec.items()}
