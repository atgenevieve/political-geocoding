"""Command line entry point: `python -m geocoding build [plan ...]`."""

import argparse

from .build import build


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m geocoding")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="download sources and rebuild data/")
    b.add_argument("plans", nargs="*", help="plan ids from config/plans.toml (default: all)")
    b.add_argument("--refresh-base", action="store_true",
                   help="rebuild the cached 2020 block population / ZCTA table")
    args = parser.parse_args()
    if args.command == "build":
        build(args.plans or None, refresh_base=args.refresh_base)


if __name__ == "__main__":
    main()
