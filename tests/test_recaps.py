"""Recap tests: determinism, no repeats, templates valid, manual overrides."""
import json
import string
from pathlib import Path

import pytest

from src import recaps, stats

FIXTURES = Path(__file__).parent / "fixtures"
WEIGHTS = {"all_play_weight": 0.40, "points_for_weight": 0.35, "recent_form_weight": 0.25, "recent_form_weeks": 3}


@pytest.fixture
def ctx():
    seasons = [json.loads((FIXTURES / f).read_text(encoding="utf-8")) for f in ("2024.json", "2025.json")]
    s25 = seasons[1]
    managers = stats.build_managers(seasons, {"{A2}": {"merge_into": "{A}"}})
    return s25, managers, stats.power_rankings_by_week(s25, WEIGHTS), stats.lineup_efficiency(s25)


def test_every_situation_has_at_least_8_templates():
    required = {"blowout", "close", "upset", "high", "low", "bench", "win_streak",
                "lose_streak", "tie", "playoff", "championship"}
    assert required <= set(recaps.TEMPLATES)
    for kind in required:
        assert len(recaps.TEMPLATES[kind]) >= 8, kind
        assert len(set(recaps.TEMPLATES[kind])) == len(recaps.TEMPLATES[kind]), kind


def test_templates_only_use_known_placeholders():
    known = {"winner", "loser", "w_score", "l_score", "margin", "winner_mgr", "loser_mgr", "winner_rank",
             "loser_rank", "team", "mgr", "score", "bench", "actual", "optimal", "n", "a", "b", "season"}
    for kind, pool in recaps.TEMPLATES.items():
        for t in pool:
            fields = {f for _, f, _, _ in string.Formatter().parse(t) if f}
            assert fields <= known, (kind, t)


def test_same_input_same_recap(ctx):
    s25, managers, pr, eff = ctx
    first = recaps.generate_recap(s25, 1, managers, pr, eff)
    again = recaps.generate_recap(json.loads(json.dumps(s25)), 1, managers, pr, eff)
    assert first and first == again


def test_picker_never_repeats_until_pool_exhausted():
    pick = recaps._Picker(2025, 1)
    ctx = {k: "x" for k in ("team", "mgr", "score")}
    lines = [pick.say("high", **ctx) for _ in range(len(recaps.TEMPLATES["high"]))]
    assert len(set(lines)) == len(lines)


def test_recap_mentions_the_week(ctx):
    s25, managers, pr, eff = ctx
    text = " ".join(recaps.generate_recap(s25, 1, managers, pr, eff))
    assert "Alpha 25" in text                     # high scorer
    assert "Charlie 25" in text and "Echo 25" in text and "90.00" in text   # the tie


def test_championship_week(ctx):
    s25, managers, pr, eff = ctx
    assert recaps.championship_game(s25)["week"] == 3
    paragraphs = recaps.generate_recap(s25, 3, managers, pr, eff)
    first_sentence = paragraphs[0].split(". ")[0]
    champ_lines = [t.split(". ")[0].format(winner="Bravo 25", loser="Charlie 25", w_score="105.00", l_score="95.00",
                                           margin="10.00", winner_mgr="Bob Brown & Dan Diaz", season=2025)
                   for t in recaps.TEMPLATES["championship"]]
    assert first_sentence in champ_lines          # the title game leads the recap
    assert "Bravo 25" in paragraphs[0]


def test_unfinished_week_has_no_recap(ctx):
    s25, managers, pr, eff = ctx
    assert recaps.generate_recap(s25, 9, managers, pr, eff) == []


def test_manual_recap_replace_and_above(ctx, tmp_path):
    s25, managers, pr, eff = ctx
    (tmp_path / "2025-week1.md").write_text("# Big week\n\nWhat a **game** by *Alpha*.", encoding="utf-8")
    replaced = recaps.recap_for_week(s25, 1, managers, pr, eff, tmp_path, "replace")
    assert "<strong>game</strong>" in replaced["manual_html"] and "<h3>Big week</h3>" in replaced["manual_html"]
    assert replaced["paragraphs"] == []
    assert replaced["excerpt"] == "What a game by Alpha."
    above = recaps.recap_for_week(s25, 1, managers, pr, eff, tmp_path, "above")
    assert above["manual_html"] and above["paragraphs"]
    none = recaps.recap_for_week(s25, 2, managers, pr, eff, tmp_path, "replace")
    assert none["manual_html"] is None and none["excerpt"] == none["paragraphs"][0]


def test_markdown_is_escaped():
    out = recaps.markdown_to_html('<script>alert(1)</script>\n\n- one\n- [link](https://espn.com)\n\n[bad](javascript:x)')
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert '<li><a href="https://espn.com">link</a></li>' in out
    assert 'href="javascript' not in out
