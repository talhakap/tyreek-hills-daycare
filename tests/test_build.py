"""The offline build must produce a complete site from fixtures, with no network."""
import re

import pytest

from src import build
from src.config import load_config


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    cfg = load_config()
    cfg["site"]["custom_domain"] = "fantasy.example.com"
    cfg["site"]["hide_from_search_engines"] = True
    build.build_site(cfg, offline=True, out_dir=out)
    return out


def test_every_page_exists(site):
    for page in ["index.html", "power.html", "history.html", "h2h.html", "records.html", "transactions.html",
                 "404.html", "weeks/index.html", "weeks/season-2024.html", "weeks/2025-1.html",
                 "weeks/2025-3.html", "teams/index.html", "static/style.css", "static/app.js", "static/favicon.svg"]:
        assert (site / page).exists(), page
    assert len(list((site / "teams").glob("*.html"))) == 5     # index + 4 managers (A2 merged into A)


def test_noindex_robots_and_cname(site):
    assert "Disallow: /" in (site / "robots.txt").read_text()
    assert (site / "CNAME").read_text().strip() == "fantasy.example.com"
    assert '<meta name="robots" content="noindex' in (site / "index.html").read_text(encoding="utf-8")


def test_links_are_relative(site):
    week = (site / "weeks" / "2025-1.html").read_text(encoding="utf-8")
    assert 'href="../static/style.css?v=' in week and 'href="../index.html"' in week
    assert '<base href="/">' in (site / "404.html").read_text(encoding="utf-8")


def test_offseason_home_shows_champion(site):
    home = (site / "index.html").read_text(encoding="utf-8")
    assert "2025 champion" in home and "Bravo 25" in home


def test_week_page_has_recap_awards_and_winner_marks(site):
    page = (site / "weeks" / "2025-1.html").read_text(encoding="utf-8")
    assert 'class="recap"' in page and "High score" in page and "Worst manager" in page
    assert "✓" in page


def test_hidden_when_no_data(site):
    # 2024 has no lineups: no best/worst manager tiles.
    page = (site / "weeks" / "2024-1.html").read_text(encoding="utf-8")
    assert "Worst manager" not in page and "None" not in re.sub(r"<[^>]+>", "", page)


def test_team_page_has_roster_for_each_season(site):
    # Manager A: 2025 roster from lineups (2024 fixture has no lineups, so no 2024 roster).
    # Fixture manager "{A}" has slug "a". (Don't search pages by text: other managers' pages
    # mention "Alpha 25" as an opponent, and glob order differs between Windows and Linux.)
    page = (site / "teams" / "a.html").read_text(encoding="utf-8")
    assert "data-roster-select" in page
    assert 'data-roster-year="2025"' in page and 'data-roster-year="2024"' not in page
    assert "Quinn QB" in page and "Ray RB" in page


def test_records_page_sections(site):
    page = (site / "records.html").read_text(encoding="utf-8")
    for section in ('id="single-game"', 'id="players"', 'id="season"', 'id="careers"', 'id="shame"'):
        assert section in page
    assert "Quade QB" in page                                  # best single-week performance
    assert 'href="weeks/2024-3.html"' in page                  # records link to their game


def test_no_python_leaks_in_html(site):
    for f in site.rglob("*.html"):
        text = re.sub(r'<script type="application/json".*?</script>', "", f.read_text(encoding="utf-8"), flags=re.S)
        assert "Undefined" not in text and "{'" not in text, f.name
