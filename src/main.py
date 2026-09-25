"""Command line entry point.

    python -m src.main fetch                      fetch current season + any uncached past seasons
    python -m src.main fetch --season 2021 --force  refetch one season
    python -m src.main build                      render site/ from data/ (no network)
    python -m src.main build --offline            render site/ from test fixtures
    python -m src.main all                        fetch, then build (what GitHub Actions runs)
"""
import argparse
import logging
import sys

from .build import BuildError, build_site
from .config import load_config
from .fetch import FetchError, fetch_all

log = logging.getLogger("league")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="Build the league website.")
    parser.add_argument("command", choices=["fetch", "build", "all"])
    parser.add_argument("--season", type=int, help="only fetch this season")
    parser.add_argument("--force", action="store_true", help="refetch even if already cached")
    parser.add_argument("--offline", action="store_true", help="build from test fixtures, no ESPN access")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    if args.offline and args.command != "build":
        log.error("--offline only works with 'build' (it never contacts ESPN).")
        return 2

    cfg = load_config()
    try:
        if args.command in ("fetch", "all"):
            fetch_all(cfg, only_season=args.season, force=args.force)
        if args.command in ("build", "all"):
            build_site(cfg, offline=args.offline)
    except (FetchError, BuildError) as e:
        log.error("\nERROR: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
