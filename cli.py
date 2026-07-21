#!/usr/bin/env python3
"""Command-line entry point for the deterministic parts matcher.

String mode (normalized phrase in, MatchResult JSON out)::

    python cli.py "Turbo (nadduv) datçiki"
    python cli.py "yan güzgü" --raw "sol güzgü"
    python cli.py "stupitsa podsipniki"          # typo -> near-match (>=90%)
    python cli.py "traves"
    python cli.py "map sensoru" --threshold 1.0  # disable near-match (exact only)

JSON mode (full reference object in, same object with filled fields out)::

    python cli.py --json '{"part_name": "Amortizator", "raw": "sol qabaq amortizator"}'
    echo '{...}' | python cli.py --json -        # read the object from stdin
"""

from __future__ import annotations

import argparse
import json
import sys

from slovar_matcher.category import CategoryMatcher
from slovar_matcher.json_api import JsonMatcher
from slovar_matcher.matcher import DEFAULT_THRESHOLD, _DEFAULT_PATH, Matcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phrase", nargs="?",
                        help="Normalized phrase from the Seller model (string mode)")
    parser.add_argument("--raw", default=None,
                        help="Original raw client text (for side/direction/location search)")
    parser.add_argument("--json", dest="json_input", default=None,
                        help="Full reference JSON object (or '-' to read it from stdin); "
                             "fills part_id/category/subcategory/side/direction/location and "
                             "echoes the object back")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Near-match cutoff in [0,1] (default {DEFAULT_THRESHOLD}; "
                             "use 1.0 for exact-only)")
    parser.add_argument("--dict", default=_DEFAULT_PATH, dest="dict_path",
                        help="Path to SLOVAR_FINAL.txt")
    args = parser.parse_args(argv)

    # ── JSON mode ────────────────────────────────────────────────────────────
    if args.json_input is not None:
        text = sys.stdin.read() if args.json_input == "-" else args.json_input
        try:
            part_json = json.loads(text)
        except json.JSONDecodeError as exc:
            parser.error(f"--json expects a JSON object: {exc}")
        if not isinstance(part_json, dict):
            parser.error("--json expects a JSON object (got a non-object)")
        funnel = JsonMatcher(Matcher.from_file(args.dict_path), CategoryMatcher.from_file())
        result = funnel.match(part_json, threshold=args.threshold)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # ── string mode ──────────────────────────────────────────────────────────
    if not args.phrase:
        parser.error("a phrase is required unless --json is given")
    matcher = Matcher.from_file(args.dict_path)
    result = matcher.match(args.phrase, raw_text=args.raw, threshold=args.threshold)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
