"""Traded draft picks.

ESPN doesn't record traded picks, so they're kept by hand in data/draft_picks.toml.
This module checks that file, works out who owns every traded pick, and matches
each pick to its slot on ESPN's draft board once that draft has happened.
No network, no rendering: build.py turns the result into pages.
"""
import datetime as dt
import re
import tomllib
from pathlib import Path


class PickError(Exception):
    """A mistake in draft_picks.toml, with a message written for humans."""


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _int(value, what: str, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PickError(f"{where}: {what} should be a whole number, got {value!r}.")
    return value


def load_trades(path: Path, team_ids: set[int], max_round: int | None = None) -> list[dict]:
    """Reads and checks the trades file. Returns [] if it doesn't exist."""
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise PickError(f"{path.name} has a formatting mistake: {e}") from None

    trades = []
    for n, t in enumerate(data.get("trade", []), 1):
        where = f"{path.name}, trade #{n}"
        season = _int(t.get("season"), "season", where)
        date = t.get("date")
        if date is not None and not isinstance(date, dt.date):
            raise PickError(f"{where}: date should look like 2026-10-21 (no quotes), got {date!r}.")
        sides = []
        for s in t.get("side", []):
            team = _int(s.get("team"), "team", where)
            if team not in team_ids:
                raise PickError(f"{where}: there's no team number {team}. "
                                f"Valid numbers: {', '.join(map(str, sorted(team_ids)))}.")
            players = s.get("players", [])
            if not isinstance(players, list) or not all(isinstance(p, str) for p in players):
                raise PickError(f'{where}: players should look like ["Player One", "Player Two"].')
            picks = []
            for p in s.get("picks", []):
                if not isinstance(p, dict):
                    raise PickError(f"{where}: each pick should look like {{ year = 2027, round = 1, from = 4 }}.")
                year = _int(p.get("year"), "a pick's year", where)
                rnd = _int(p.get("round"), "a pick's round", where)
                frm = _int(p.get("from"), "a pick's from (original team)", where)
                if frm not in team_ids:
                    raise PickError(f"{where}: a pick says from = {frm}, but there's no team number {frm}.")
                if rnd < 1 or (max_round and rnd > max_round):
                    raise PickError(f"{where}: round {rnd} doesn't exist"
                                    + (f" (the draft has {max_round} rounds)." if max_round else "."))
                picks.append({"year": year, "round": rnd, "from": frm})
            sides.append({"team": team, "players": [p.strip() for p in players if p.strip()], "picks": picks})
        if len(sides) < 2:
            raise PickError(f"{where}: a trade needs at least two [[trade.side]] sections.")
        if len({s["team"] for s in sides}) != len(sides):
            raise PickError(f"{where}: the same team is listed twice.")
        if not any(s["picks"] for s in sides):
            raise PickError(f"{where}: no draft picks changed hands, so it belongs on ESPN, not here.")
        trades.append({"n": n, "season": season, "date": date, "note": str(t.get("note") or "").strip(),
                       "sides": sides})
    return trades


def _team_label(tid: int) -> str:
    return f"team {tid}"


def _apply(t: dict, owners: dict, name=_team_label) -> list:
    """Checks one trade against current ownership; returns its pick moves. Sets "given_by"."""
    where = t.get("where") or f"draft_picks.toml, trade #{t['n']}"
    in_trade = {s["team"] for s in t["sides"]}
    moves = []
    for s in t["sides"]:
        for p in s["picks"]:
            key = (p["year"], p["round"], p["from"])
            holder = owners.get(key, p["from"])
            label = f"the {p['year']} {ordinal(p['round'])} originally from {name(p['from'])}"
            if holder == s["team"]:
                raise PickError(f"{where}: {name(s['team'])} receives {label}, but it already owns that pick.")
            if holder not in in_trade:
                raise PickError(f"{where}: {label} belongs to {name(holder)} at that point, "
                                "and that team isn't part of this trade.")
            if any(m[0] == key for m in moves):
                raise PickError(f"{where}: {label} is listed twice.")
            p["given_by"] = holder
            moves.append((key, s["team"]))
    return moves


def resolve_owners(trades: list[dict]) -> dict:
    """{(year, round, original team): current owner} for every pick that was ever traded.

    Trades are applied in order. Each pick must be owned by one of the other teams in the
    trade at that moment, which catches typos like a wrong "from" or round. Raises on the
    first bad trade. Adds "given_by" (the team that gave the pick up) to every pick.
    """
    owners = {}
    for t in trades:
        owners.update(_apply(t, owners))
    return owners


def resolve_owners_lenient(trades: list[dict], name=_team_label) -> tuple[dict, list, list]:
    """Like resolve_owners, but skips a bad trade instead of stopping (for the shared sheet,
    so one typo doesn't hold back the whole site). Returns (owners, good trades, problems)."""
    owners, kept, problems = {}, [], []
    for t in trades:
        try:
            owners.update(_apply(t, owners, name))
            kept.append(t)
        except PickError as e:
            problems.append({"row": t.get("row"), "message": str(e)})
    return owners, kept, problems


# --------------------------------------------------------------------------
# The shared Google Sheet: one row per trade
# --------------------------------------------------------------------------

SHEET_HEADER = ["Season", "Date",
                "Team 1", "Team 1 gets: players", "Team 1 gets: picks",
                "Team 2", "Team 2 gets: players", "Team 2 gets: picks",
                "Team 3 (optional)", "Team 3 gets: players", "Team 3 gets: picks",
                "Note"]
_PICK = re.compile(r"^(\d{4})\s+(\d{1,2})\s*(?:st|nd|rd|th)?(?:\s*(?:round|rd|rnd))?\.?"
                   r"(?:\s*(?:\((.+)\)|(?:from\s+|-\s*|–\s*)?(.+)))?$", re.I)
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y/%m/%d")


def name_key(name: str) -> str:
    """Team names compared loosely: case, spaces, apostrophes and emoji don't matter."""
    return "".join(c for c in name.lower() if c.isalnum())


_SUFFIX = re.compile(r"\s+(jr|sr|ii|iii|iv|v)\.?$", re.I)


def player_key(name: str) -> str:
    """Player names compared loosely: "Travis Etienne" matches "Travis Etienne Jr."."""
    return name_key(_SUFFIX.sub("", name.strip()))


def _split(cell: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\n,;]", cell or "") if x.strip()]


def _parse_date(text: str) -> dt.date | None:
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_sheet(rows: list[list[str]], team_names: dict, max_round: int | None = None) -> tuple[list, list]:
    """Rows from the trade sheet (CSV) -> (trades in the same shape as load_trades, problems).

    team_names maps name_key(any name a team has ever had) -> team id. A row that can't be
    read is reported in `problems` and skipped. Picks are written like "2027 4th"; they're
    the other team's own pick unless a team is named, e.g. "2027 1st (Billieve)".
    """
    trades, problems = [], []
    for i, raw in enumerate(rows, 1):
        cells = [(c or "").strip() for c in raw] + [""] * len(SHEET_HEADER)
        if i == 1 and not re.match(r"\d{4}", cells[0]):
            continue                                    # header row
        if not any(cells[:len(SHEET_HEADER)]):
            continue                                    # blank row
        where = f"Sheet row {i}"
        try:
            season_m = re.match(r"(\d{4})", cells[0])
            if not season_m:
                raise PickError(f"{where}: Season should look like 2026-27, got {cells[0]!r}.")
            date = None
            if cells[1]:
                date = _parse_date(cells[1])
                if date is None:
                    raise PickError(f"{where}: couldn't read the date {cells[1]!r}. Use a date like 2026-10-21.")
            sides = []
            for c in (2, 5, 8):
                team_text, players, picks_text = cells[c], cells[c + 1], cells[c + 2]
                if not team_text:
                    if players or picks_text:
                        raise PickError(f"{where}: Team {c // 3 + 1} gets something but has no team name.")
                    continue
                tid = team_names.get(name_key(team_text))
                if tid is None:
                    raise PickError(f"{where}: no team called {team_text!r}.")
                sides.append({"team": tid, "players": _split(players), "pick_texts": _split(picks_text)})
            if len(sides) < 2:
                raise PickError(f"{where}: a trade needs at least two teams.")
            if len({s["team"] for s in sides}) != len(sides):
                raise PickError(f"{where}: the same team is listed twice.")
            for s in sides:
                s["picks"] = []
                for text in s.pop("pick_texts"):
                    m = _PICK.match(text)
                    if not m:
                        raise PickError(f"{where}: couldn't read the pick {text!r}. "
                                        "Write picks like \"2027 4th\" or \"2027 1st (Billieve)\".")
                    year, rnd, whose = int(m.group(1)), int(m.group(2)), (m.group(3) or m.group(4) or "").strip()
                    if rnd < 1 or (max_round and rnd > max_round):
                        raise PickError(f"{where}: round {rnd} doesn't exist"
                                        + (f" (the draft has {max_round} rounds)." if max_round else "."))
                    if whose:
                        frm = team_names.get(name_key(whose))
                        if frm is None:
                            raise PickError(f"{where}: in {text!r}, there's no team called {whose!r}.")
                    else:
                        others = [o["team"] for o in sides if o is not s]
                        if len(others) != 1:
                            raise PickError(f"{where}: in a 3-team trade, say whose pick {text!r} is, "
                                            "e.g. \"2027 1st (Billieve)\".")
                        frm = others[0]
                    s["picks"].append({"year": year, "round": rnd, "from": frm})
            for s in sides:
                if not s["players"] and not s["picks"]:
                    raise PickError(f"{where}: one team doesn't receive anything.")
        except PickError as e:
            problems.append({"row": i, "message": str(e)})
            continue
        trades.append({"n": len(trades) + 1, "row": i, "where": where, "season": int(season_m.group(1)),
                       "date": date, "note": cells[11], "sides": sides})
    return trades, problems


def slot_number(draft: dict, rnd: int, original: int) -> int | None:
    """Which pick in round `rnd` belonged to `original`, from ESPN's draft order."""
    order = draft.get("pick_order") or []
    if original not in order:
        return None
    i = order.index(original)
    if draft.get("type", "SNAKE") == "SNAKE" and rnd % 2 == 0:
        i = len(order) - 1 - i
    return i + 1


def draft_result(draft: dict | None, rnd: int, original: int) -> dict | None:
    """The draft board entry for a pick, or None if that draft hasn't happened (or has no data)."""
    if not draft or not draft.get("complete"):
        return None
    slot = slot_number(draft, rnd, original)
    if slot is None:
        return None
    pick = next((p for p in draft.get("picks", []) if p["round"] == rnd and p["pick"] == slot), None)
    return {"slot": f"{rnd}.{slot:02d}", "pick": pick}
