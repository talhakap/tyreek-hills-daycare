"""Template-based weekly recaps. No AI, no network.

Templates are picked with a random generator seeded by season + week, so
rebuilding the site never changes an old recap. A template is never used
twice in one recap.

A commissioner can write recaps/{season}-week{N}.md; config
[recaps] manual_override_mode decides whether it replaces the generated recap
("replace") or sits above it ("above").
"""
import html
import random
import re
from pathlib import Path

from . import stats
from .normalize import clean_name

BLOWOUT_MARGIN = 30      # points; smaller "biggest margins" aren't worth a sentence
CLOSE_MARGIN = 10
BENCH_DISASTER = 15
STREAK_WORTH_MENTIONING = 3
UPSET_RANK_GAP = 3       # No. 9 beating No. 8 isn't a story

# Placeholders: {winner} {loser} {w_score} {l_score} {margin} {winner_mgr} {loser_mgr}
# {winner_rank} {loser_rank} {team} {mgr} {score} {bench} {actual} {optimal} {n}
# {a} {b} {season}. Team names are the subjects; manager names (which can be
# "X & Y") are only used as possessives or objects.
TEMPLATES = {
    "blowout": [
        "{winner} ran {loser} off the field, {w_score} to {l_score}. A {margin}-point beatdown that should come with a mercy rule.",
        "It was over by Sunday lunch: {winner} {w_score}, {loser} {l_score}.",
        "{loser} showed up to a gunfight with a pool noodle and fell {w_score}–{l_score} to {winner}.",
        "{winner_mgr}'s {winner} put up {w_score} and left {loser} in the dust by {margin}. Somebody check on {loser_mgr}.",
        "Biggest beatdown of the week: {winner} over {loser} by {margin}. The final was {w_score}–{l_score}, and it felt worse.",
        "{winner} treated {loser} like a bye week, winning by {margin} ({w_score}–{l_score}).",
        "No drama in {winner} vs. {loser}: {w_score} to {l_score}, a {margin}-point clinic.",
        "{loser} may want to file a missing persons report for its lineup after a {margin}-point loss to {winner}.",
        "The {margin}-point spanking {winner} handed {loser} ({w_score}–{l_score}) will be replayed in {loser_mgr}'s nightmares.",
    ],
    "close": [
        "{winner} survived {loser} by just {margin}, {w_score} to {l_score}. Somebody's Monday night was very stressful.",
        "Photo finish: {winner} {w_score}, {loser} {l_score}. A {margin}-point margin is one bad decision at kicker.",
        "{loser} came up {margin} points short against {winner}. That's one start/sit call that'll haunt {loser_mgr} all week.",
        "Nail-biter of the week: {winner} edged {loser}, {w_score}–{l_score}.",
        "{winner} squeaked past {loser} by {margin}. Ugly wins still count.",
        "Heartbreak for {loser_mgr}: {loser} lost to {winner} by only {margin} ({l_score}–{w_score}).",
        "{winner} and {loser} went down to the wire before {winner} escaped with a {margin}-point win.",
        "If you like cardiac finishes, {winner} ({w_score}) over {loser} ({l_score}) was your game.",
    ],
    "upset": [
        "Upset alert! No. {winner_rank} {winner} knocked off No. {loser_rank} {loser}, {w_score}–{l_score}. The power rankings would like a word.",
        "{loser} came in ranked No. {loser_rank} and left with a loss to No. {winner_rank} {winner}. Any given Sunday.",
        "Nobody had {winner} over {loser} on their bingo card, but here we are: {w_score} to {l_score}.",
        "The No. {winner_rank} team beat the No. {loser_rank} team. {winner} over {loser}, and the algorithm is sweating.",
        "The week's biggest stunner belongs to {winner_mgr}: {winner} took down No. {loser_rank} {loser}, {w_score}–{l_score}.",
        "David, meet Goliath: No. {winner_rank} {winner} took down No. {loser_rank} {loser}.",
        "{loser} got caught looking ahead, and No. {winner_rank} {winner} made it pay, {w_score}–{l_score}.",
        "So much for the favourites: No. {loser_rank} {loser} fell to No. {winner_rank} {winner}.",
    ],
    "high": [
        "{team} led the league with {score} points. Chef's kiss.",
        "Top score of the week belonged to {team}, who dropped {score}.",
        "Lineup of the week goes to {mgr}: {team} racked up {score}.",
        "{team} went nuclear for {score}, the best score in the league this week.",
        "Nobody scored more than {team}'s {score}. Take a bow.",
        "{team} put up {score} and made everyone else's lineup look like a practice squad.",
        "The weekly high score goes to {team}: {score} points of pure dominance.",
        "{score} points for {team}. The rest of the league just watched.",
    ],
    "low": [
        "At the other end, {team} limped to a league-low {score}.",
        "{team} brought up the rear with {score}. Maybe next week, champ.",
        "{score} points for {team}, the lowest of the week. The waiver wire is open 24/7.",
        "Somebody had to score the fewest points, and this week it was {team} with {score}.",
        "{team}'s {score} was the week's low. Thoughts and prayers.",
        "The basement belonged to {team} this week at {score} points.",
        "{team} managed just {score}. We've seen better from teams on a bye.",
        "Lowest score of the week: {team}, {score}. Let's never speak of it again.",
    ],
    "bench": [
        "{team} left {bench} points on the bench: the best possible lineup scored {optimal}, the one that actually started scored {actual}.",
        "Bench disaster: {team} had {bench} points sitting on the pine.",
        "{mgr}'s start/sit calls backfired: {team} left {bench} points on the bench.",
        "{team} scored {actual} but could have had {optimal}. That's {bench} points of 'what if'.",
        "Start/sit is hard, and {team} proved it by benching {bench} points.",
        "Worst lineup call of the week goes to {team}, with {bench} points left on the bench.",
        "{team}'s bench would have outscored a few starting lineups, with {bench} points left unused.",
        "With the optimal lineup, {team} would have scored {optimal} instead of {actual}.",
    ],
    "win_streak": [
        "{team} is rolling: that's {n} straight wins.",
        "Stay hot: {team} has won {n} in a row.",
        "{n}-game win streak for {team}. Someone stop them.",
        "{mgr}'s {team} can't lose right now, riding a {n}-game heater.",
        "Make it {n} straight for {team}.",
        "{team} extended its win streak to {n}. The rest of the league is on notice.",
        "Nobody has figured out {team} lately: {n} wins in a row.",
        "{team}'s winning streak is up to {n}, and the group chat is going to hear about it.",
    ],
    "lose_streak": [
        "{team} has now lost {n} straight. Rock bottom has a basement.",
        "Make it {n} losses in a row for {team}. The trade block is calling.",
        "{mgr}'s {team} is stuck in a {n}-game skid.",
        "{n} straight losses for {team}. Hang in there.",
        "{team} still can't find a win: {n} in a row now.",
        "The losing streak lives on for {team}, now at {n}.",
        "{team} has dropped {n} straight. The good news: it can't rain forever.",
        "{n}-game slide for {team}. Time to shake something up.",
    ],
    "tie": [
        "{a} and {b} tied at {score}. Nobody wins, nobody learns anything.",
        "A rare tie: {a} and {b} both finished with {score}.",
        "{a} {score}, {b} {score}. Kiss your sister.",
        "Somehow {a} and {b} scored exactly the same, {score} each. What are the odds?",
        "Deadlock! {a} and {b} both landed on {score}.",
        "{a} vs. {b} ended all square at {score}. Half a win for everybody.",
        "The scoreboard couldn't pick a winner between {a} and {b}, tied at {score}.",
        "{score}–{score}. {a} and {b} will be arguing about this one for a while.",
    ],
    "playoff": [
        "Playoff football: {winner} beat {loser} {w_score}–{l_score} and moves on.",
        "{winner} survives and advances after taking down {loser}, {w_score}–{l_score}.",
        "Season over for {loser}: {winner} sent it home {w_score}–{l_score}.",
        "Win or go home, and {winner} won: {w_score} to {l_score} over {loser}.",
        "{loser}'s title hopes ended at the hands of {winner}, {l_score}–{w_score}.",
        "{winner} punched a ticket to the next round by beating {loser} by {margin}.",
        "One step closer: {winner} {w_score}, {loser} {l_score}.",
        "The dream lives on for {winner_mgr}: {winner} beat {loser} by {margin}.",
    ],
    "championship": [
        "🏆 {winner} is your {season} champion! {winner_mgr}'s squad beat {loser} {w_score}–{l_score} in the title game.",
        "Bow down: {winner} won the {season} title, beating {loser} by {margin}.",
        "The {season} crown belongs to {winner_mgr}. {winner} took the final {w_score}–{l_score}.",
        "Championship week delivered: {winner} {w_score}, {loser} {l_score}. Engrave the trophy.",
        "{winner} finished the job, beating {loser} for the {season} championship.",
        "{loser} came up one win short. {winner} is the {season} champ after a {w_score}–{l_score} final.",
        "Confetti for {winner}. Bragging rights go to {winner_mgr} for the next 12 months after a {margin}-point win over {loser}.",
        "The {season} season is in the books, and {winner} stands on top after beating {loser} {w_score}–{l_score}.",
    ],
}


def _pts(x: float) -> str:
    return f"{x:.2f}"


class _Picker:
    """Deterministic template chooser that never repeats within one recap."""

    def __init__(self, season: int, week: int):
        self.rng = random.Random(season * 100 + week)
        self.used = set()

    def say(self, kind: str, **ctx) -> str:
        pool = TEMPLATES[kind]
        fresh = [i for i in range(len(pool)) if (kind, i) not in self.used]
        i = self.rng.choice(fresh or list(range(len(pool))))
        self.used.add((kind, i))
        return pool[i].format(**ctx)


def championship_game(season: dict) -> dict | None:
    """The title game: the only bracket game in the last bracket week."""
    bracket = [m for m in season["matchups"] if m["is_playoff"] and not m["is_bye"]]
    if not bracket:
        return None
    last = max(m["week"] for m in bracket)
    finals = [m for m in bracket if m["week"] == last]
    return finals[0] if len(finals) == 1 else None


def generate_recap(season: dict, week: int, managers: dict, power_by_week: dict,
                   efficiency: dict | None) -> list[str]:
    """1-3 paragraphs (plain text) for a finished week, or [] if nothing to say."""
    awards = stats.weekly_awards(season, week, power_by_week, efficiency)
    if not awards:
        return []
    names = {t["team_id"]: clean_name(t["team_name"]) for t in season["teams"]}
    units = stats.team_units(season, managers)
    mgr = lambda tid: managers["units"][units[tid]]["display_name"] if units.get(tid) in managers["units"] else names[tid]
    pick = _Picker(season["season"], week)
    described = set()

    def game_ctx(a):
        return {"winner": names[a["winner_id"]], "loser": names[a["loser_id"]],
                "w_score": _pts(a["winner_score"]), "l_score": _pts(a["loser_score"]),
                "margin": _pts(a["margin"]), "winner_mgr": mgr(a["winner_id"]),
                "loser_mgr": mgr(a["loser_id"]), "season": season["season"]}

    def key(a):
        return frozenset((a["winner_id"], a["loser_id"]))

    def team_ctx(tid, **extra):
        return {"team": names[tid], "mgr": mgr(tid), **extra}

    week_games = [g for g in stats.games(season, stats.ALL_KINDS) if g["week"] == week]
    postseason = week > season["settings"]["regular_season_weeks"]
    lead, middle, tail = [], [], []

    # Headline: title game / playoff games, or the week's top storylines.
    if postseason:
        title = championship_game(season)
        for g in week_games:
            if g["kind"] != stats.PLAYOFF or g["winner"] == "tie":
                continue
            a = _as_award(g)
            is_title = title is not None and g["week"] == title["week"] and \
                {g["home_team_id"], g["away_team_id"]} == {title["home_team_id"], title["away_team_id"]}
            lead.append(pick.say("championship" if is_title else "playoff", **game_ctx(a)))
            described.add(key(a))
    hs = awards["high_score"]
    if not any(hs["team_id"] in k for k in described):
        lead.append(pick.say("high", **team_ctx(hs["team_id"], score=_pts(hs["score"]))))
    if not postseason:
        b = awards["blowout"]
        if b and b["margin"] >= BLOWOUT_MARGIN:
            lead.append(pick.say("blowout", **game_ctx(b)))
            described.add(key(b))
        u = awards["upset"]
        if u and u["winner_rank"] - u["loser_rank"] >= UPSET_RANK_GAP and key(u) not in described:
            lead.append(pick.say("upset", **game_ctx(u), winner_rank=u["winner_rank"], loser_rank=u["loser_rank"]))
            described.add(key(u))

    # Middle: ties, the nail-biter, the low score, the bench disaster.
    for g in week_games:
        if g["winner"] == "tie":
            middle.append(pick.say("tie", a=names[g["home_team_id"]], b=names[g["away_team_id"]],
                                   score=_pts(g["home_score"])))
            described.add(frozenset((g["home_team_id"], g["away_team_id"])))
    c = awards["closest"]
    if c and not c["tie"] and c["margin"] < CLOSE_MARGIN and key(c) not in described:
        middle.append(pick.say("close", **game_ctx(c)))
        described.add(key(c))
    ls = awards["low_score"]
    if ls["team_id"] != hs["team_id"]:
        middle.append(pick.say("low", **team_ctx(ls["team_id"], score=_pts(ls["score"]))))
    wm = awards["worst_manager"]
    if wm and wm["bench_left"] >= BENCH_DISASTER:
        line = pick.say("bench", **team_ctx(wm["team_id"], bench=_pts(wm["bench_left"]),
                                            actual=_pts(wm["actual"]), optimal=_pts(wm["optimal"])))
        opp = next((g for g in week_games if wm["team_id"] in (g["home_team_id"], g["away_team_id"])), None)
        if opp:
            opp_score = opp["away_score"] if opp["home_team_id"] == wm["team_id"] else opp["home_score"]
            if wm["actual"] < opp_score < wm["optimal"]:
                line += " The right lineup would have won that game."
        middle.append(line)

    # Tail: streaks (regular season only).
    if not postseason:
        played = {tid for g in week_games for tid in (g["home_team_id"], g["away_team_id"])}
        rows = [r for r in stats.standings(season, through_week=week) if r["team_id"] in played]
        for letter, kind in (("W", "win_streak"), ("L", "lose_streak")):
            hot = [(int(r["streak"][1:]), r["team_id"]) for r in rows
                   if r["streak"].startswith(letter) and int(r["streak"][1:]) >= STREAK_WORTH_MENTIONING]
            if hot:
                n, tid = max(hot, key=lambda x: (x[0], -x[1]))
                tail.append(pick.say(kind, **team_ctx(tid, n=n)))

    return [" ".join(p) for p in (lead, middle, tail) if p]


def _as_award(g: dict) -> dict:
    win, lose = ("home", "away") if g["winner"] == "home" else ("away", "home")
    return {"winner_id": g[f"{win}_team_id"], "loser_id": g[f"{lose}_team_id"],
            "winner_score": g[f"{win}_score"], "loser_score": g[f"{lose}_score"],
            "margin": round(abs(g["home_score"] - g["away_score"]), 2)}


# --------------------------------------------------------------------------
# Manual recaps (a tiny, safe subset of Markdown)
# --------------------------------------------------------------------------

def manual_recap_path(recaps_dir: Path, season: int, week: int) -> Path:
    return Path(recaps_dir) / f"{season}-week{week}.md"


def markdown_to_html(text: str) -> str:
    """Paragraphs, # headings, - bullet lists, **bold**, *italic*, [links](https://...).
    Everything else is escaped, so a recap can't break the page."""
    def inline(s):
        s = html.escape(s, quote=True)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', s)
        return s

    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.rstrip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        if all(re.match(r"\s*[-*]\s+", l) for l in lines):
            bullets = [inline(re.sub(r"^\s*[-*]\s+", "", l)) for l in lines]
            items = "".join(f"<li>{b}</li>" for b in bullets)
            out.append(f"<ul>{items}</ul>")
        elif len(lines) == 1 and (m := re.match(r"(#{1,3})\s+(.*)", lines[0])):
            out.append(f"<h3>{inline(m.group(2))}</h3>")
        else:
            out.append(f"<p>{inline(' '.join(l.strip() for l in lines))}</p>")
    return "\n".join(out)


def recap_for_week(season: dict, week: int, managers: dict, power_by_week: dict,
                   efficiency: dict | None, recaps_dir: Path, mode: str = "replace") -> dict:
    """{"manual_html": str|None, "paragraphs": [plain text], "excerpt": str}."""
    path = manual_recap_path(recaps_dir, season["season"], week)
    manual = markdown_to_html(path.read_text(encoding="utf-8")) if path.exists() else None
    generated = generate_recap(season, week, managers, power_by_week, efficiency)
    paragraphs = [] if manual and mode != "above" else generated
    if manual:
        first = re.search(r"<p>(.*?)</p>", manual, re.S)
        excerpt = html.unescape(re.sub(r"<[^>]+>", "", first.group(1) if first else manual))
    else:
        excerpt = generated[0] if generated else ""
    return {"manual_html": manual, "paragraphs": paragraphs, "excerpt": excerpt}
