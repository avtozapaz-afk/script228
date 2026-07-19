"""Parser for ``SLOVAR_FINAL.txt`` — the single source of truth.

File layout (detail section)::

    ########## Двигатель ##########                         <- top category (RU)

      ▸ Turbina / Турбина  [engine-turbo]  (4 дет)          <- leaf / subcategory
          MU-037  Турбина  |  Turbo  [side:false|position:false]   <- part
          ...
          синонимы: turbo, turbina, ...                     <- synonyms (whole leaf)

Synonyms are shared across every part of the leaf — that is the whole point of
the matcher: a bare synonym hit is a *group* hit, not a single-part hit.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from .normalize import normalize

# ── line patterns ────────────────────────────────────────────────────────────
_CATEGORY_RE = re.compile(r"^#{6,}\s*(.+?)\s*#{6,}\s*$")
_LEAF_RE = re.compile(r"^\s*▸\s*(?P<name>.+?)\s*\[(?P<code>[a-z0-9-]+)\]")
_PART_RE = re.compile(
    r"^\s*(?P<id>[A-Z]{2,3}-\d+)\s+(?P<ru>.+?)\s+\|\s+(?P<az>.+?)\s+"
    r"\[side:(?P<side>true|false)\|position:(?P<pos>true|false)\]\s*$"
)
_SYN_RE = re.compile(r"^\s*синонимы:\s*(?P<syns>.+)$")

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_PAREN_RE = re.compile(r"\(([^)]*)\)")


@dataclass
class Part:
    part_id: str
    name_ru: str
    name_az: str
    category: str            # top category, RU (e.g. "Подвеска")
    subcategory: str         # leaf name, AZ portion (e.g. "Turbina")
    leaf_code: str           # e.g. "engine-turbo"
    side_flag: bool
    position_flag: bool
    group_id: str            # == leaf_code


@dataclass
class Group:
    leaf_code: str
    category: str
    subcategory: str
    part_ids: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)  # normalized


@dataclass
class Dictionary:
    parts: dict[str, Part]
    groups: dict[str, Group]
    # normalized name variant -> ordered list of part_ids
    name_index: dict[str, list[str]]
    # normalized synonym -> ordered list of leaf_codes
    synonym_index: dict[str, list[str]]


def _subcategory_of(leaf_name: str) -> str:
    """Extract the Azerbaijani leaf name, dropping the trailing Russian gloss.

    ``"Stupisa (toplar) / podşipniklər / Ступицы/подшипники"``
        -> ``"Stupisa (toplar) / podşipniklər"``
    """
    leaf_name = leaf_name.replace("🆕", "").strip()
    segments = re.split(r"\s+/\s+", leaf_name)
    # Drop trailing segments that are the Russian gloss (contain Cyrillic).
    while len(segments) > 1 and _CYRILLIC_RE.search(segments[-1]):
        segments.pop()
    return " / ".join(segments).strip()


def name_variants(name: str) -> set[str]:
    """Exact-match variants for a canonical name.

    Includes the full name and each parenthetical alternative, but **not** the
    de-parenthesised stem.  This is deliberate: ``"Turbo (nadduv) datçiki"``
    should match ``"nadduv"`` and the full phrase, while ``"Traves (podramnik)"``
    must *not* turn a bare ``"traves"`` into a single match (``traves`` is a
    group-level synonym shared by 5 parts and must stay ``ambiguous``).
    """
    name = name.strip()
    variants = {name}
    for inner in _PAREN_RE.findall(name):
        inner = inner.strip()
        if not inner:
            continue
        variants.add(inner)
        for alt in re.split(r"\s*/\s*", inner):
            alt = alt.strip()
            if alt:
                variants.add(alt)
    variants |= _expand_slashes(name)
    return {v for v in variants if v}


def _expand_slashes(name: str) -> set[str]:
    """Expand a top-level ``A/B`` inside a name into its shared-context variants.

    ``"Güzgü korpusu/qapağı"`` -> ``{"Güzgü korpusu", "Güzgü qapağı"}`` and
    ``"Корпус/крышка зеркала"`` -> ``{"Корпус зеркала", "крышка зеркала"}`` — the
    slash lists alternative names that share the surrounding word(s), so a client
    who types just one of them still gets an exact match. Parenthetical groups
    are dropped here (their slashes are handled above); combos are capped so a
    pathological name can't explode the index.
    """
    base = re.sub(r"\s*/\s*", "/", _PAREN_RE.sub(" ", name))
    options: list[list[str]] = []
    has_slash = False
    combos = 1
    for tok in base.split():
        parts = [p for p in tok.split("/") if p]
        if "/" in tok and len(parts) > 1:
            has_slash = True
            options.append(parts)
            combos *= len(parts)
        else:
            options.append([parts[0] if parts else tok])
    # Require a shared context word (>= 2 token positions): expanding a bare
    # "A/B" name into single generic words ("emblema", "logo") would collide
    # with other groups' synonyms and over-match. Those stay on the synonym path.
    if not has_slash or combos > 16 or len(options) < 2:
        return set()
    return {" ".join(combo) for combo in itertools.product(*options)}


def parse_file(path: str) -> Dictionary:
    with open(path, encoding="utf-8") as fh:
        return parse_text(fh.read())


def parse_text(text: str) -> Dictionary:
    parts: dict[str, Part] = {}
    groups: dict[str, Group] = {}
    name_index: dict[str, list[str]] = {}
    synonym_index: dict[str, list[str]] = {}

    in_detail = False
    category: str | None = None
    current: Group | None = None

    def add_name(variant: str, part_id: str) -> None:
        key = normalize(variant)
        if not key:
            return
        bucket = name_index.setdefault(key, [])
        if part_id not in bucket:
            bucket.append(part_id)

    for raw in text.splitlines():
        # The overview tree at the top also uses "▸"-free category-looking lines;
        # only start parsing once we reach the detail section marker.
        if "ДЕТАЛИЗАЦИЯ" in raw:
            in_detail = True
            continue
        if not in_detail:
            continue

        m = _CATEGORY_RE.match(raw)
        if m:
            category = m.group(1).replace("🆕", "").strip()
            current = None
            continue

        m = _LEAF_RE.match(raw)
        if m:
            code = m.group("code")
            subcat = _subcategory_of(m.group("name"))
            current = Group(leaf_code=code, category=category or "", subcategory=subcat)
            # A leaf code could in principle repeat; keep the first, extend parts.
            groups.setdefault(code, current)
            current = groups[code]
            continue

        m = _PART_RE.match(raw)
        if m and current is not None:
            pid = m.group("id")
            part = Part(
                part_id=pid,
                name_ru=m.group("ru").strip(),
                name_az=m.group("az").strip(),
                category=current.category,
                subcategory=current.subcategory,
                leaf_code=current.leaf_code,
                side_flag=m.group("side") == "true",
                position_flag=m.group("pos") == "true",
                group_id=current.leaf_code,
            )
            parts[pid] = part
            current.part_ids.append(pid)
            for variant in name_variants(part.name_ru):
                add_name(variant, pid)
            for variant in name_variants(part.name_az):
                add_name(variant, pid)
            continue

        m = _SYN_RE.match(raw)
        if m and current is not None:
            for syn in m.group("syns").split(","):
                key = normalize(syn)
                if not key:
                    continue
                if key not in current.synonyms:
                    current.synonyms.append(key)
                bucket = synonym_index.setdefault(key, [])
                if current.leaf_code not in bucket:
                    bucket.append(current.leaf_code)
            continue

    return Dictionary(
        parts=parts,
        groups=groups,
        name_index=name_index,
        synonym_index=synonym_index,
    )
