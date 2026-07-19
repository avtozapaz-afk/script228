# Deterministic parts matcher (AVTOZAP)

A small, **LLM-free** Python program that replaces neural-network "guessing" of
car parts with an exact algorithmic lookup against the dictionary
(`SLOVAR_FINAL.txt`).

It takes a normalized phrase (already cleaned up by the Seller model) and
returns **one of three honest outcomes** — never a silent guess:

| status | meaning |
|---|---|
| `single_match` | exactly one concrete `part_id` |
| `ambiguous` | 2+ real candidates (returned in full, never narrowed by guessing) |
| `no_match` | nothing found in names or synonyms |

For a `single_match` it additionally extracts `side` / `position` from the raw
client text when the part declares those flags, or reports which of them still
`needs_clarification`.

## Usage

```bash
# normalized phrase in, JSON out
python cli.py "Turbo (nadduv) datçiki"
python cli.py "yan güzgü" --raw "sol güzgü"
python cli.py "traves"
python cli.py "radator ekranı" --fuzzy      # advisory suggestions on no_match
```

Programmatic:

```python
from slovar_matcher import Matcher

m = Matcher.from_file()                       # loads data/SLOVAR_FINAL.txt
result = m.match("Stupitsa podşipniki", raw_text="sol tərəf")
print(result.to_dict())
```

Output shape:

```json
{
  "status": "single_match",
  "part_id": "AS-007",
  "name_ru": "Подшипник ступицы",
  "name_az": "Stupitsa podşipniki",
  "category": "Подвеска",
  "subcategory": "Stupisa (toplar) / podşipniklər",
  "attributes": { "side": "sol", "position": null },
  "needs_clarification": ["position"],
  "candidates": []
}
```

* `candidates` — filled **only** when `status == "ambiguous"`, as
  `{part_id, name_ru, name_az}` for every real candidate.
* `needs_clarification` — subset of `["side", "position"]`, only flags that are
  `true` for the part and were not found in the text.
* `fuzzy_suggestions` — present only with `--fuzzy` on a `no_match`; these are
  advisory and **never** auto-selected (status stays `no_match`).

## Algorithm

**Step 1 — find a match** (in priority order):

1. **Exact name** — the normalized phrase equals a canonical name variant of a
   part (`name_ru` or `name_az`). Highest priority; typically a single part.
2. **Synonym** — the phrase equals one of a leaf group's shared synonyms. Since
   synonyms are shared across the whole group, **all** part_ids of that group
   are returned as candidates:
   * group of 1 part → automatic `single_match`;
   * group of 2+ parts → honest `ambiguous` (semantic ambiguity).
3. Otherwise → `no_match`.

**Step 2 — attributes** (only for `single_match`): each part carries a `side`
(left/right) and a `position` (front/rear) flag. **Only flags that are `true`
are ever considered** — if a flag is `false` we neither search for it nor ask
about it. For a `true` flag, search the raw text for side (`sol/sağ/left/лево…`)
or position (`ön/arxa/qabaq/перед/зад…`) tokens; found → fill; not found → add
to `needs_clarification` (the caller asks the client that one question).

Example: a side mirror (`KZ-020`, `side:true position:false`) is asked only "which
side?" — never "front or rear?" — while a wheel bearing (`AS-007`,
`side:true position:true`) can be asked both.

**Step 3 — category / subcategory** come straight from the dictionary structure
(the `########## Category ##########` section header and the `▸ leaf [code]`
line), so no external mapping is required.

### Why "traves" is ambiguous but "yan güzgü" / "nadduv" are single

Names may carry a parenthetical alternative, e.g. `Turbo (nadduv) datçiki`,
`Güzgü (yan güzgü)`, `Traves (podramnik)`. Exact-match name variants are built
from **the full name plus each parenthetical alternative** — but *not* the
de-parenthesised stem. Consequently:

* `"yan güzgü"` matches KZ-020's parenthetical → `single_match`;
* `"nadduv"` matches MU-075's parenthetical → `single_match`;
* `"traves"` is **not** a name variant (the stem `Traves` is not indexed), so it
  falls through to the group-level synonym `traves`, which is shared by all 5
  parts of the *Balka / körpü* leaf → `ambiguous` with 5 candidates.

This is the behaviour required by the spec's flagship cases.

## Guarantees / non-goals

* **No fuzzy auto-selection.** Only exact/explicit matches are ever selected.
  Levenshtein-style fuzzy search exists solely behind the optional `--fuzzy`
  flag, and only to *suggest* candidates on `no_match`.
* **Never collapse `ambiguous` to one.** A synonym shared by several parts always
  returns every candidate.
* One phrase is matched per call; splitting a request into multiple parts is the
  caller's (Seller's) responsibility.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

`tests/test_matcher.py` covers the four spec-mandated cases (Traves, Radiator
ekran, Yan güzgü, Turbo (nadduv) datçiki) plus name/synonym/attribute/fuzzy
coverage and structural sanity of the parse (15 categories, 86 leaves, 438
parts).

### A note on the reference test files

The spec references `AVTOZAP_Reference_TestCases.md` and
`..._Part2.md` as regression fixtures. Those files were **not** included with
this task, so the four explicitly-described cases were encoded directly as
tests. If the full reference files are provided, drop them into `tests/` and a
small harness can replay every `input → expected JSON` pair against
`Matcher.match`.

## Offline browser tester (`matcher_tool.html`)

For hand-checking without a Python environment, `matcher_tool.html` is a single
self-contained file — the whole dictionary and a JavaScript port of the matcher
are inlined, so it runs offline in any browser (just open it). It shows the JSON
result plus the explicit clarifying question when a part needs `side`/`position`.

Rebuild it after editing the dictionary:

```bash
python build_html.py
```

Its output is verified byte-identical to `slovar_matcher` across the test cases.

## Layout

```
slovar_matcher/
  normalize.py    Azerbaijani/Russian-aware lowercasing + tokenizing
  parser.py       SLOVAR_FINAL.txt -> parts, groups, name/synonym indices
  attributes.py   side / position keyword extraction
  matcher.py      Matcher + MatchResult (the 3-state algorithm)
data/
  SLOVAR_FINAL.txt   source of truth
cli.py            command-line entry point
build_html.py     regenerates the offline matcher_tool.html
matcher_tool.html self-contained offline browser tester
tests/            pytest regression suite
```
