"""Loads config.toml and credentials. Everything league-specific comes from here."""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SEASONS_DIR = DATA_DIR / "seasons"
CURRENT_FILE = DATA_DIR / "current.json"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
RECAPS_DIR = ROOT / "recaps"
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
SITE_DIR = ROOT / "site"


def load_config(path: Path = ROOT / "config.toml") -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("site", {})
    cfg.setdefault("colors", {})
    cfg.setdefault("power_rankings", {})
    cfg.setdefault("recaps", {})
    cfg.setdefault("manager_overrides", {})
    return cfg


def load_credentials() -> tuple[str | None, str | None]:
    """Reads ESPN_S2 / ESPN_SWID from the environment (and .env locally).

    Never log or print the returned values.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    s2 = os.environ.get("ESPN_S2", "").strip() or None
    swid = os.environ.get("ESPN_SWID", "").strip() or None
    return s2, swid
