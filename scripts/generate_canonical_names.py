#!/usr/bin/env python3
"""Generate the LLM canonical-names list from the single source of truth.

The Seller/normalizer LLM only needs ``ID + canonical Azerbaijani name`` (no
synonyms, no side/direction/location flags). Keeping that list by hand alongside the full
``SLOVAR_FINAL.txt`` drifts (e.g. YA-025 was missing from the manual docx).
Generating it from the library makes drift impossible: one source, two
derivatives (the full library for the algorithm, this trimmed list for the LLM).

    python scripts/generate_canonical_names.py            # write data/canonical_names.md
    python scripts/generate_canonical_names.py --check    # verify it is up to date
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slovar_matcher.parser import parse_file  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "data", "SLOVAR_FINAL.txt")
_OUT = os.path.join(_ROOT, "data", "canonical_names.md")


def render(path: str = _SRC) -> str:
    d = parse_file(path)
    lines = [
        "# AVTOZAP — Canonical Names",
        "",
        "> Auto-generated from `data/SLOVAR_FINAL.txt` by "
        "`scripts/generate_canonical_names.py`. Do not edit by hand.",
        f"> {len(d.parts)} parts · {len(d.groups)} leaves.",
        "",
    ]
    current_category: str | None = None
    for group in d.groups.values():
        if group.category != current_category:
            current_category = group.category
            lines.append(f"## {current_category}")
            lines.append("")
        lines.append(f"### {group.subcategory}")
        lines.append("")
        lines.append("| ID | Canonical Name (AZ) |")
        lines.append("|----|---------------------|")
        for pid in group.part_ids:
            lines.append(f"| {pid} | {d.parts[pid].name_az} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="Exit non-zero if data/canonical_names.md is stale")
    args = parser.parse_args(argv)

    content = render()
    if args.check:
        existing = ""
        if os.path.exists(_OUT):
            with open(_OUT, encoding="utf-8") as fh:
                existing = fh.read()
        if existing != content:
            print("FAIL: data/canonical_names.md is out of date — run "
                  "scripts/generate_canonical_names.py")
            return 1
        print("OK: canonical_names.md is up to date.")
        return 0

    with open(_OUT, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"wrote {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
