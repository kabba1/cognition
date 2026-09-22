"""Minimal command-line entry point for Cognition."""

import argparse
from collections.abc import Sequence

from cognition.cli.commands.admin import add_admin_parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and return a successful exit status."""
    parser = argparse.ArgumentParser(
        prog="cognition",
        description="Cognition: persistent AI individuals.",
    )
    subparsers = parser.add_subparsers(dest="command")
    add_admin_parser(subparsers)
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
