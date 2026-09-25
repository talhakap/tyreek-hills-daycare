"""Talks to ESPN (through espn-api) and writes normalized JSON into data/.

Past seasons are fetched once and cached in data/seasons/{year}.json.
The current season is refetched every run into data/current.json.

Request budget per season: 2 for league/teams/schedule/bracket, 1 per played week for
lineups, and (current season only) 1 for player names + 1 per 100 transactions.
"""
import json
import logging
import time
from pathlib import Path

import requests
from espn_api.base_league import BaseLeague
from espn_api.football import League
from espn_api.football.box_score import BoxScore
from espn_api.football.settings import Settings
from espn_api.football.team import Team
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague, ESPNUnknownError

from . import normalize
from .config import CURRENT_FILE, SEASONS_DIR, load_credentials

log = logging.getLogger("league")

UPGRADE_HINT = (
    "ESPN may have changed its data format. Try upgrading espn-api:\n"
    "    pip install --upgrade espn-api\n"
    "then put the new version number in requirements.txt."
)
COOKIE_HELP = (
    "ESPN refused access to the league. Your ESPN_S2 / ESPN_SWID cookies are\n"
    "probably wrong or expired. Log into espn.com, copy fresh values (README:\n"
    "'Getting espn_s2 and SWID') into .env on your computer, or into the\n"
    "ESPN_S2 / ESPN_SWID secrets on GitHub (Settings > Secrets and variables > Actions)."
)
MISSING_CREDS_HELP = (
    "This league is private, so ESPN_S2 and ESPN_SWID are required.\n"
    "  - On your computer: copy .env.example to .env and paste both values in.\n"
    "  - On GitHub: Settings > Secrets and variables > Actions > New repository secret,\n"
    "    once for ESPN_S2 and once for ESPN_SWID.\n"
    "See README 'Getting espn_s2 and SWID' for where to find them."
)
TRANSACTION_PAGE = 100


class FetchError(Exception):
    """A problem worth stopping for, with a message written for humans."""


def _with_retries(what: str, fn, attempts: int = 3):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ESPNAccessDenied:
            raise FetchError(COOKIE_HELP) from None
        except ESPNInvalidLeague:
            raise FetchError(f"ESPN says this league/season doesn't exist ({what}). "
                             "Check [league] id in config.toml.") from None
        except ESPNUnknownError as e:
            if "403" in str(e):
                raise FetchError(COOKIE_HELP) from None
            last = e
        except requests.RequestException as e:
            last = e
        if attempt < attempts:
            wait = 2 ** attempt
            log.info("  ESPN request failed (%s). Retrying in %ss…", type(last).__name__, wait)
            time.sleep(wait)
    raise FetchError(f"Couldn't {what} after {attempts} tries ({last}).\n{UPGRADE_HINT}")


def _played_weeks(matchups: list) -> list[int]:
    return sorted({m["week"] for m in matchups if m["final"] or m["home_score"] or m["away_score"]})


def _fetch_lineups(league, raw: dict, matchups: list, year: int, in_progress: bool) -> list | None:
    """One request per scoring period. Returns None if ESPN has no lineup data."""
    periods = raw.get("settings", {}).get("scheduleSettings", {}).get("matchupPeriods", {})
    latest = league.scoringPeriodId
    lineups = []
    for week in _played_weeks(matchups):
        for sp in periods.get(str(week), [week]):
            if in_progress and sp > latest:
                continue
            params = {"view": ["mMatchupScore", "mScoreboard"], "scoringPeriodId": sp}
            filters = {"schedule": {"filterMatchupPeriodIds": {"value": [week]}}}
            headers = {"x-fantasy-filter": json.dumps(filters)}
            data = _with_retries(f"load {year} week {week} lineups",
                                 lambda: league.espn_request.league_get(params=params, headers=headers))
            try:
                boxes = [BoxScore(m, {}, {}, sp, year) for m in data.get("schedule", [])]
                week_lineups = normalize.normalize_lineups(boxes, week, sp)
            except (KeyError, TypeError, ValueError) as e:
                if not lineups:
                    log.info("  No lineup data available for %s (%s: %s).", year, type(e).__name__, e)
                    return None
                log.warning("  Warning: couldn't read %s week %s lineups (%s); skipping that week.",
                            year, week, type(e).__name__)
                continue
            lineups.extend(week_lineups)
    return lineups or None


def _fetch_transactions(league, year: int) -> list | None:
    if year < 2019:
        return None
    try:
        _with_retries("load player names", league._fetch_players)
        # Activity looks up unknown players one request at a time; skip that
        # and fall back to the name map instead.
        league.player_info = lambda name=None, playerId=None: None
        out, offset = [], 0
        while offset < 2000:
            page = _with_retries("load transactions",
                                 lambda: league.recent_activity(size=TRANSACTION_PAGE, offset=offset))
            for activity in page:
                item = normalize.normalize_activity(activity, league.player_map)
                if item:
                    out.append(item)
            if len(page) < TRANSACTION_PAGE:
                break
            offset += TRANSACTION_PAGE
        return out
    except FetchError:
        raise
    except Exception as e:  # optional data: never fail the build over it
        log.warning("  Warning: couldn't read transactions (%s: %s); the page will be hidden.",
                    type(e).__name__, e)
        return None


def fetch_season(cfg: dict, year: int, s2, swid, is_current: bool) -> dict:
    league = League(cfg["league"]["id"], year, espn_s2=s2, swid=swid, fetch_league=False)
    raw = _with_retries(f"load the {year} season",
                        lambda: BaseLeague._fetch_league(league, SettingsClass=Settings))
    # The league payload omits playoffTierType; only the mMatchupScore view has it.
    scores = _with_retries(f"load the {year} playoff bracket",
                           lambda: league.espn_request.league_get(params={"view": "mMatchupScore"}))
    tier_by_id = {m["id"]: m.get("playoffTierType") for m in scores.get("schedule", []) if "id" in m}
    for m in raw.get("schedule", []):
        m.setdefault("playoffTierType", tier_by_id.get(m.get("id")))
    try:
        BaseLeague._fetch_teams(league, raw, TeamClass=Team)
        matchups = normalize.normalize_matchups(raw.get("schedule", []))
    except (KeyError, TypeError, ValueError) as e:
        raise FetchError(f"Couldn't read the {year} season ({type(e).__name__}: {e}).\n{UPGRADE_HINT}") from None

    in_progress = any(not m["final"] and not m["is_bye"] for m in matchups)
    lineups = _fetch_lineups(league, raw, matchups, year, in_progress)
    rosters = transactions = None
    if is_current:
        rosters = {str(t.team_id): normalize.normalize_roster(t) for t in league.teams}
        transactions = _fetch_transactions(league, year)

    return normalize.build_season(year=year, settings_obj=league.settings, raw=raw, teams=league.teams,
                                  lineups=lineups, transactions=transactions, rosters=rosters)


def _summary(season: dict) -> str:
    finals = {m["week"] for m in season["matchups"] if m["final"]}
    parts = [f"{len(season['teams'])} teams", f"{len(finals)} weeks of results"]
    parts.append(f"lineups for {len({l['week'] for l in season['lineups']})} weeks"
                 if season["lineups"] else "no lineup data")
    if season["transactions"] is not None:
        parts.append(f"{len(season['transactions'])} transactions")
    return f"{season['status']['state'].replace('_', ' ')}: " + ", ".join(parts)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _check_not_empty(season: dict) -> None:
    state = season["status"]["state"]
    if not season["teams"]:
        raise FetchError(f"ESPN returned no teams for {season['season']}. Not deploying.\n{UPGRADE_HINT}")
    if state == "in_progress" and not season["matchups"]:
        raise FetchError(f"ESPN returned no matchups for the in-progress {season['season']} season. "
                         f"Not deploying.\n{UPGRADE_HINT}")


def fetch_all(cfg: dict, only_season: int | None = None, force: bool = False) -> None:
    s2, swid = load_credentials()
    if cfg["league"].get("private") and not (s2 and swid):
        raise FetchError(MISSING_CREDS_HELP)

    current = cfg["league"]["current_season"]
    first = cfg["league"]["first_season"]
    years = [only_season] if only_season else [current] + list(range(current - 1, first - 1, -1))

    for year in years:
        cached = SEASONS_DIR / f"{year}.json"
        is_current = year == current
        if not is_current and cached.exists() and not force:
            log.info("Season %s: cached, skipping.", year)
            continue
        log.info("Fetching %s season…", year)
        try:
            season = fetch_season(cfg, year, s2, swid, is_current)
            _check_not_empty(season)
        except FetchError as e:
            if is_current:
                raise
            log.warning("  Warning: skipping %s. %s", year, str(e).splitlines()[0])
            continue
        log.info("  %s", _summary(season))

        if is_current:
            _write_json(CURRENT_FILE, season)
            _log_managers(season)
        if not is_current or season["status"]["state"] == "complete":
            _write_json(cached, season)
            log.info("  Cached to data/seasons/%s.json", year)


def _log_managers(season: dict) -> None:
    teams_by_owner = {}
    for t in season["teams"]:
        for oid in t["owner_ids"]:
            teams_by_owner.setdefault(oid, []).append(t["team_name"])
    log.info("  Managers (use these codes for [manager_overrides] in config.toml):")
    for m in season["managers"]:
        log.info("    %-40s %s — %s", m["owner_id"], m["display_name"],
                 ", ".join(teams_by_owner.get(m["owner_id"], [])))
