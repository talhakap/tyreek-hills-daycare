"""Converts espn-api objects (and the raw ESPN payload they were built from)
into our own plain-dict schema. Nothing outside fetch.py/normalize.py should
ever touch espn-api.

Verified against espn-api 0.46.0:
  - football.team.Team: team_id, team_abbrev, team_name, division_id, wins,
    losses, ties, points_for, points_against, standing (playoffSeed),
    final_standing (rankCalculatedFinal, 0 until the season ends), logo_url,
    roster (list of Player), owners (list of member dicts).
  - football.box_player.BoxPlayer: name, playerId, position, slot_position,
    eligibleSlots, points, projected_points, proTeam, injuryStatus.
  - football.activity.Activity: date (epoch ms), actions = [(Team, action,
    Player|int, bid)], action in FA ADDED / WAIVER ADDED / DROPPED /
    TRADE_SENT / TRADE_RECEIVED.
  - Matchup period and playoff tier are not kept on the library's Team or
    Matchup objects, so matchups are read from the raw league `schedule`
    (fields: id, matchupPeriodId, home/away.teamId, home/away.totalPoints,
    winner). playoffTierType is only in the mMatchupScore view; fetch.py
    copies it onto each schedule entry by matchup id.
"""
import hashlib
from datetime import datetime, timezone

from espn_api.football.constant import POSITION_MAP

SCHEMA_VERSION = 1
NON_STARTER_SLOTS = {"BE", "IR"}
CONSOLATION_TIERS = {"LOSERS_CONSOLATION_LADDER", "WINNERS_CONSOLATION_LADDER"}


def anon_id(owner_id: str | None) -> str | None:
    """One-way public code for an ESPN member ID.

    ESPN member IDs are the same value as each person's SWID cookie, and the
    saved data ends up in a public repo, so they're never stored raw. The same
    ID always gives the same code, so history still links up across seasons.
    """
    if not owner_id or owner_id.startswith("m-"):
        return owner_id
    digest = hashlib.sha256(("espn-owner:" + owner_id.strip().upper()).encode("utf-8")).hexdigest()
    return "m-" + digest[:12]


def _owner_id(owner) -> str | None:
    """Owners have been both plain ID strings and dicts across versions."""
    if isinstance(owner, str):
        return owner
    if isinstance(owner, dict):
        return owner.get("id")
    return None


def clean_name(name: str) -> str:
    """Repairs names ESPN stored with the wrong text encoding ("?Â¿â\x80½" -> "?¿‽")."""
    if not any(c in name for c in "ÃÂâ"):
        return name
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _round(x) -> float:
    return round(float(x or 0), 2)


def normalize_settings(settings, raw_settings: dict) -> dict:
    slot_counts = raw_settings.get("rosterSettings", {}).get("lineupSlotCounts", {})
    lineup_slots = {}
    for slot_id, count in slot_counts.items():
        name = POSITION_MAP.get(int(slot_id))
        if count and name and name not in NON_STARTER_SLOTS:
            lineup_slots[name] = count
    return {
        "regular_season_weeks": settings.reg_season_count,
        "playoff_teams": settings.playoff_team_count,
        "team_count": settings.team_count,
        "scoring_type": settings.scoring_type,
        "median_scoring": bool(settings.median_scoring),
        "divisions": {str(k): v for k, v in settings.division_map.items()},
        "matchup_periods": {str(k): list(v) for k, v in settings.matchup_periods.items()},
        "playoff_matchup_period_length": settings.playoff_matchup_period_length,
        "lineup_slots": lineup_slots,
    }


def normalize_managers(members: list, teams: list) -> list:
    by_id = {m.get("id"): m for m in members if m.get("id")}
    managers, seen = [], set()
    for team in teams:
        for owner in team.owners or []:
            oid = _owner_id(owner)
            if not oid or oid in seen:
                continue
            seen.add(oid)
            m = by_id.get(oid) or (owner if isinstance(owner, dict) else {})
            first = (m.get("firstName") or "").strip()
            last = (m.get("lastName") or "").strip()
            full = f"{first} {last}".strip()
            managers.append({
                "owner_id": anon_id(oid),
                "display_name": full or m.get("displayName") or f"Owner of {team.team_name}",
                "first_name": first or None,
                "last_name": last or None,
            })
    return managers


def normalize_team(team, raw_team: dict | None = None) -> dict:
    owner_ids = [oid for oid in (_owner_id(o) for o in team.owners or []) if oid]
    if not owner_ids and raw_team:
        owner_ids = [oid for oid in (_owner_id(o) for o in raw_team.get("owners", [])) if oid]
    owner_ids = [anon_id(oid) for oid in owner_ids]
    return {
        "team_id": team.team_id,
        "team_name": clean_name(team.team_name.strip()),
        "abbrev": team.team_abbrev,
        "owner_ids": owner_ids,
        "logo_url": team.logo_url or None,
        "division_id": team.division_id,
        "playoff_seed": team.standing or None,
        "final_standing": team.final_standing or None,
        "wins": team.wins,
        "losses": team.losses,
        "ties": team.ties,
        "points_for": _round(team.points_for),
        "points_against": _round(team.points_against),
    }


def normalize_matchups(schedule: list) -> list:
    """One entry per game. A bye is an entry with away_team_id = None."""
    out = []
    for m in schedule:
        home, away = m.get("home"), m.get("away")
        if not home and not away:
            continue
        if not home:  # normalise so the lone team is always "home"
            home, away = away, None
        tier = m.get("playoffTierType") or "NONE"
        winner = {"HOME": "home", "AWAY": "away", "TIE": "tie"}.get(m.get("winner"))
        if away is None:
            winner = None
        out.append({
            "week": m["matchupPeriodId"],
            "home_team_id": home["teamId"],
            "away_team_id": away["teamId"] if away else None,
            "home_score": _round(home.get("totalPoints")),
            "away_score": _round(away.get("totalPoints")) if away else None,
            "winner": winner,
            "final": m.get("winner") not in (None, "UNDECIDED"),
            "playoff_tier": tier,
            "is_playoff": tier == "WINNERS_BRACKET",
            "is_consolation": tier in CONSOLATION_TIERS,
            "is_bye": away is None,
        })
    return out


def normalize_box_player(p) -> dict:
    return {
        "player_id": p.playerId,
        "name": p.name,
        "position": p.position,
        "slot": p.slot_position,
        "starter": p.slot_position not in NON_STARTER_SLOTS,
        "eligible_slots": [s for s in p.eligibleSlots if s not in NON_STARTER_SLOTS],
        "points": _round(p.points),
        "projected_points": _round(p.projected_points),
        "pro_team": p.proTeam,
    }


def normalize_lineups(box_scores: list, week: int, scoring_period: int) -> list:
    out = []
    for box in box_scores:
        for side in ("home", "away"):
            team = getattr(box, f"{side}_team")
            lineup = getattr(box, f"{side}_lineup")
            if team is None or not lineup:
                continue
            team_id = team if isinstance(team, int) else team.team_id
            out.append({
                "week": week,
                "scoring_period": scoring_period,
                "team_id": team_id,
                "players": [normalize_box_player(p) for p in lineup],
            })
    return out


def normalize_roster(team) -> list:
    return [{
        "player_id": p.playerId,
        "name": p.name,
        "position": p.position,
        "pro_team": p.proTeam,
        "slot": p.lineupSlot,
        "injury_status": p.injuryStatus if isinstance(p.injuryStatus, str) else None,
    } for p in team.roster]


def normalize_activity(activity, player_map: dict) -> dict | None:
    items, team_ids, kinds = [], [], set()
    for team, action, player, bid in activity.actions:
        team_id = getattr(team, "team_id", None)
        if isinstance(player, int):
            name = player_map.get(player, "Unknown player")
        else:
            name = getattr(player, "name", None) or "Unknown player"
        if "TRADE" in action:
            kinds.add("trade")
        elif "ADDED" in action:
            kinds.add("add")
        elif "DROPPED" in action:
            kinds.add("drop")
        items.append({
            "team_id": team_id,
            "action": action,
            "player": name,
            "bid": bid or None,
        })
        if team_id is not None and team_id not in team_ids:
            team_ids.append(team_id)
    if not items:
        return None
    kind = "trade" if "trade" in kinds else "add" if "add" in kinds else "drop"
    return {
        "date": datetime.fromtimestamp(activity.date / 1000, tz=timezone.utc).isoformat(),
        "type": kind,
        "team_ids": team_ids,
        "items": items,
    }


def compute_status(matchups: list, teams: list, settings: dict, current_matchup_period: int) -> dict:
    played = [m for m in matchups if m["final"] or m["home_score"] or m["away_score"]]
    if not played:
        return {"state": "preseason", "current_week": 0}
    last_week = max(m["week"] for m in matchups)
    everything_final = all(m["final"] or m["is_bye"] for m in matchups)
    reached_playoffs = last_week > settings["regular_season_weeks"] or not settings["playoff_teams"]
    has_final_ranks = any(t["final_standing"] for t in teams)
    if has_final_ranks or (everything_final and reached_playoffs):
        return {"state": "complete", "current_week": last_week}
    week = min(max(current_matchup_period, max(m["week"] for m in played)), last_week)
    return {"state": "in_progress", "current_week": week}


def build_season(*, year, settings_obj, raw, teams, lineups, transactions, rosters) -> dict:
    settings = normalize_settings(settings_obj, raw.get("settings", {}))
    raw_teams = {t["id"]: t for t in raw.get("teams", [])}
    norm_teams = [normalize_team(t, raw_teams.get(t.team_id)) for t in teams]
    matchups = normalize_matchups(raw.get("schedule", []))
    status = compute_status(matchups, norm_teams, settings,
                            raw.get("status", {}).get("currentMatchupPeriod", 0))
    return {
        "schema_version": SCHEMA_VERSION,
        "season": year,
        "league_name": settings_obj.name,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": settings,
        "status": status,
        "managers": normalize_managers(raw.get("members", []), teams),
        "teams": norm_teams,
        "matchups": matchups,
        "lineups": lineups,
        "rosters": rosters,
        "transactions": transactions,
    }
