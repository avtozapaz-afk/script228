#!/usr/bin/env python3
"""Guard against name-vs-other-group-synonym collisions in the dictionary.

A collision is a normalized key that is the exact name (variant) of one part
*and* a synonym of a **different** leaf group. Such a word is not an unambiguous
identifier, yet exact-name matching would let it win silently (see Bug 3). Run
this on every change to ``SLOVAR_FINAL.txt``; it exits non-zero if any new,
unhandled collision appears.

    python scripts/check_collisions.py [path/to/SLOVAR_FINAL.txt]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slovar_matcher.parser import parse_file  # noqa: E402

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "SLOVAR_FINAL.txt",
)


def find_collisions(path: str = _DEFAULT_PATH) -> list[tuple[str, list[str], list[str]]]:
    """Return ``[(key, part_ids, other_group_codes), ...]`` for every collision."""
    d = parse_file(path)
    collisions: list[tuple[str, list[str], list[str]]] = []
    for name_key, pids in d.name_index.items():
        if name_key in d.synonym_index:
            owner_groups = {d.parts[pid].leaf_code for pid in pids}
            other_groups = set(d.synonym_index[name_key]) - owner_groups
            if other_groups:
                collisions.append((name_key, sorted(pids), sorted(other_groups)))
    return sorted(collisions)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else _DEFAULT_PATH
    collisions = find_collisions(path)
    if collisions:
        print(f"FAIL: {len(collisions)} unhandled name/other-group-synonym collision(s):")
        for key, pids, others in collisions:
            print(f"  {key!r}: name of {pids} but also synonym of {others}")
        return 1
    print("OK: no name/other-group-synonym collisions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
