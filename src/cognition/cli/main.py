"""Minimal command-line entry point for Cognition."""

import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and return a successful exit status."""
    parser = argparse.ArgumentParser(
        prog="cognition",
        description="Cognition: persistent AI individuals.",
    )
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
