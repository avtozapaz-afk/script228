#!/usr/bin/env python3
"""Command-line entry point for the deterministic parts matcher.

Examples::

    python cli.py "Turbo (nadduv) datçiki"
    python cli.py "yan güzgü" --raw "sol güzgü"
    python cli.py "stupitsa podsipniki"          # typo -> near-match (>=90%)
    python cli.py "traves"
    python cli.py "map sensoru" --threshold 1.0  # disable near-match (exact only)
"""

from __future__ import annotations

import argparse
import json
import sys

from slovar_matcher.matcher import DEFAULT_THRESHOLD, _DEFAULT_PATH, Matcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phrase", help="Normalized phrase from the Seller model")
    parser.add_argument("--raw", default=None,
                        help="Original raw client text (for side/position search)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Near-match cutoff in [0,1] (default {DEFAULT_THRESHOLD}; "
                             "use 1.0 for exact-only)")
    parser.add_argument("--dict", default=_DEFAULT_PATH, dest="dict_path",
                        help="Path to SLOVAR_FINAL.txt")
    args = parser.parse_args(argv)

    matcher = Matcher.from_file(args.dict_path)
    result = matcher.match(args.phrase, raw_text=args.raw, threshold=args.threshold)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
