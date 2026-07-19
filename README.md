# Deterministic parts matcher (AVTOZAP)

A small, **LLM-free** Python program that replaces neural-network "guessing" of
car parts with an algorithmic lookup against the dictionary (`SLOVAR_FINAL.txt`).

Lookup is **exact-first, then a near-match (≥ 90 %) fallback** — so a couple of
extra or mistyped letters still resolve, without ever loosening into a guess.

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
python cli.py "stupitsa podsipniki"          # typo -> near-match (>=90%)
python cli.py "traves"
python cli.py "map sensoru" --threshold 1.0  # 1.0 = exact-only (disable near-match)
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
  "candidates": [],
  "match_score": 1.0
}
```

* `candidates` — filled **only** when `status == "ambiguous"`, as
  `{part_id, name_ru, name_az}` for every real candidate.
* `needs_clarification` — subset of `["side", "position"]`, only flags that are
  `true` for the part and were not found in the text.
* `match_score` — similarity of the match in `[0, 1]`: `1.0` for an exact match,
  `< 1.0` for a near-match (e.g. `0.947` for a one-letter typo). Present for
  `single_match` / `ambiguous`; absent on `no_match`. Lets the caller flag
  low-confidence hits for a quick "вы имели в виду …?" confirmation.

## Algorithm

**Step 1 — find a match** (in priority order):

1. **Exact name** — the normalized phrase equals a canonical name variant of a
   part (`name_ru` or `name_az`). Highest priority; typically a single part.
   *Collision guard:* if that same word is also a synonym of a **different** leaf
   group, the name is not an unambiguous identifier — the result is `ambiguous`
   between the named part(s) and that other group, never a silent name win.
2. **Exact synonym** — the phrase equals one of a leaf group's shared synonyms.
   Since synonyms are shared across the whole group, **all** part_ids of that
   group are returned as candidates:
   * group of 1 part → automatic `single_match`;
   * group of 2+ parts → honest `ambiguous` (semantic ambiguity).
3. **Near-match on names** (only if no exact hit) — the highest-scoring name
   variants with `similarity ≥ threshold` (default `0.90`). A clear typo maps to
   one part → `single_match`; a genuine tie between distinct parts → `ambiguous`.
4. **Near-match on synonyms** — same, resolving to the group(s), same as step 2.
5. Otherwise → `no_match`.

Similarity is `1 − levenshtein(a, b) / max(len)`. Because it's length-normalized,
a couple of extra letters on a long phrase stay ≥ 0.90, while a short word needs
a near-exact match to clear the bar — typos are tolerated, loose guessing isn't.
`--threshold 1.0` turns off the near-match steps entirely (exact-only).

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

### Two-layer funnel (category → detail → attributes)

A thin **Layer 1** (`slovar_matcher/category.py`) runs *before* the detail
matcher and narrows to one of the 15 top categories, using
`data/category_index.json` (word → category, generated from the library):

* one category → `category_resolved`;
* two or more → `category_ambiguous` (ask, with the candidate categories);
* nothing (and no near-match ≥ threshold) → `category_unknown`.

Layer 1 never guesses — it only reports in how many categories the word
physically appears. If nothing matches at the threshold (no exact key, no
near-match ≥ 0.90), instead of dead-ending at `category_unknown` it falls back to
a **containment** search — every category where the word appears as a substring
of any entry — and asks across all of them (`matched_by: "containment"`). Only a
word present *nowhere* stays `category_unknown`. Every result also carries **`raw_text_passthrough`**: the
whole normalized request text, forwarded to Layer 2 **verbatim** (Layer 1 never
parses or references it). Once the category is known, the detail matcher is
called with `restrict_category=<cat>` (a `match()` parameter) so it only
considers parts in that category. The funnel therefore narrows **category →
detail → attributes**, and at each step it is either sure or asks exactly one
question.

```json
{ "status": "category_resolved", "category": "Система охлаждения",
  "raw_text_passthrough": "<normalized request text, unchanged>" }
{ "status": "category_ambiguous", "candidates": ["…", "…"], "question": "…",
  "raw_text_passthrough": "…" }
{ "status": "category_unknown", "raw_text_passthrough": "…" }
```

```python
from slovar_matcher import CategoryMatcher, Matcher

cat = CategoryMatcher.from_file().resolve("emblema")   # -> category_ambiguous
# after the user picks a category:
part = Matcher.from_file().match("emblema", restrict_category="Кузов и оптика")
```

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

A name may also list slash-separated alternatives that share a word, e.g.
`Güzgü korpusu/qapağı` or `Корпус/крышка зеркала`. Those are expanded into their
shared-context variants (`güzgü korpusu` **and** `güzgü qapağı`; `корпус зеркала`
**and** `крышка зеркала`), so a client who types just one gets an exact match
instead of a ~65 % near-match. A name that is *entirely* `A/B` with no shared
word (e.g. `Emblema/logo`) is deliberately **not** expanded — bare generic words
stay on the synonym/ambiguity path rather than being promoted to a single match.

## Guarantees / non-goals

* **Near-match is bounded, never a guess.** Below the threshold there is no
  match at all; at or above it, only the *best-scoring* keys count, and any tie
  between distinct parts stays `ambiguous`. `match_score` always reports how
  close the hit was, so low-confidence matches can be confirmed with the client.
  Set `--threshold 1.0` for strict exact-only behaviour.
* **Punctuation-insensitive.** `normalize()` keeps only word characters (letters
  and digits), so a query arriving with brackets, dots or slashes — `"radiator."`,
  `"(fara)"`, `"Turbo (nadduv) datçiki"` — matches the same as its clean form. The
  same normalisation is applied to every dictionary key, so both sides stay aligned.
* **Never collapse `ambiguous` to one.** A synonym shared by several parts always
  returns every candidate; a name colliding with another group's synonym does too.
* One phrase is matched per call; splitting a request into multiple parts is the
  caller's (Seller's) responsibility.

## Dictionary integrity (single source of truth)

`data/SLOVAR_FINAL.txt` is the only hand-maintained file. Two guards keep it
honest and its derivatives in sync — both run in the test suite:

* **Collision check** — `python scripts/check_collisions.py` fails if any exact
  name is also a synonym of a *different* group (the class of bug where a common
  word like `karopka` / `mühərrik` / `fara` silently matched the wrong part).
  The matcher's collision guard makes such words `ambiguous` at runtime; this
  script makes them visible so the wording can be fixed at the source.
* **Canonical names** — `python scripts/generate_canonical_names.py` regenerates
  `data/canonical_names.md` (the trimmed `ID + AZ name` list the normalizer LLM
  needs). It is generated from the library, so the two can never drift; run with
  `--check` in CI. This replaces the hand-kept docx (which was missing `YA-025`).

Architecturally: the LLM passes the algorithm **both** its normalized-name guess
*and* the raw client text; the final single/ambiguous/no-match decision is always
the algorithm's, searching the full synonym set — it never blindly trusts the
LLM's pick.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

* `tests/test_matcher.py` — the four spec-mandated cases (Traves, Radiator ekran,
  Yan güzgü, Turbo (nadduv) datçiki), name/synonym/attribute coverage, near-match
  behaviour (typo → single, tie → ambiguous, below-threshold → no_match,
  `--threshold 1.0` → exact-only) and the bug regressions (numeral "on" ≠ front,
  "fara" ≠ silent bulb, cross-group collision → ambiguous).
* `tests/test_category.py` — Layer 1 (radiator → resolved, fara → resolved,
  emblema → ambiguous, typo near-match, unknown) and the `restrict_category`
  funnel.
* `tests/test_integrity.py` — no name/synonym collisions, and both generated
  derivatives (`canonical_names.md`, `category_index.json`) stay in sync with
  `SLOVAR_FINAL.txt`.

### A note on the reference test files

The spec references `AVTOZAP_Reference_TestCases.md` and
`..._Part2.md` as regression fixtures. Those files were **not** included with
this task, so the four explicitly-described cases were encoded directly as
tests. If the full reference files are provided, drop them into `tests/` and a
small harness can replay every `input → expected JSON` pair against
`Matcher.match`.

## Offline browser tester (`matcher_tool.html`)

For hand-checking without a Python environment, `matcher_tool.html` is a single
self-contained file — the whole dictionary, the category index, and a JavaScript
port of both layers are inlined, so it runs offline in any browser (just open it).

It is a fully **interactive funnel**: type a request, then answer with **clickable
chip buttons** — category (when ambiguous), which detail (when several match), and
side/position (`Sol / Sağ / Hər ikisi`, `Ön / Arxa`). There is always a
`Başqa / Другое` fallback, and the free-text field stays available. The dialog
runs entirely client-side and ends on a final screen showing the assembled part
(category, subcategory, RU/AZ name, filled attributes) plus its JSON.

Rebuild it after editing the dictionary:

```bash
python build_html.py
```

Its logic is verified byte-identical to `slovar_matcher` (both layers) across the
test cases.

## Layout

```
slovar_matcher/
  normalize.py    Azerbaijani/Russian-aware lowercasing, tokenizing, similarity
  parser.py       SLOVAR_FINAL.txt -> parts, groups, name/synonym indices
  attributes.py   side / position keyword extraction
  category.py     Layer 1 — category resolution (category_index.json)
  matcher.py      Matcher + MatchResult (3-state algorithm, restrict_category)
data/
  SLOVAR_FINAL.txt    source of truth (hand-maintained)
  canonical_names.md  generated ID + AZ-name list for the LLM
  category_index.json generated word -> category index (Layer 1)
scripts/
  check_collisions.py           name/other-group-synonym collision guard
  generate_canonical_names.py   regenerates canonical_names.md
  generate_category_index.py    regenerates category_index.json
cli.py            command-line entry point
build_html.py     regenerates the offline interactive matcher_tool.html
matcher_tool.html self-contained offline interactive funnel demo
tests/            pytest regression suite
```
