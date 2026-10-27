"""Analiza aktów zmieniających: które artykuły wskazanej ustawy zmienia nowelizacja (Mission #022).

amended_articles(text, base_title) → {"found": bool, "articles": {"18", "8", ...}, "block": "..."}
  text       — tekst aktu zmieniającego (pdftotext),
  base_title — fragment tytułu ustawy zmienianej, np. "o systemie ubezpieczeń społecznych".
Rozpoznaje: „w art. N”, „art. N otrzymuje brzmienie”, „uchyla się art. N”, „po art. N dodaje się art. M”.
Heurystyka — wynik służy do triażu; zmiana artykułu ≠ zmiana normy z karty (wymaga odczytu).
"""
from __future__ import annotations

import re
import unicodedata

TOP_ART = re.compile(r"(?m)^\s{0,24}Art\.\s*(\d+[a-z]*)\.\s")
# \s+ między słowami: pdftotext łamie linie w środku fraz („dodaje się\r\nart. 15h”)
TOUCH = [
    re.compile(r"\bw\s+art\.\s*(\d+[a-z]*)", re.I),
    re.compile(r"\bart\.\s*(\d+[a-z]*)\s+otrzymuje\s+brzmienie", re.I),
    re.compile(r"\buchyla\s+się\s+art\.\s*(\d+[a-z]*)", re.I),
    re.compile(r"\bpo\s+art\.\s*(\d+[a-z]*)\s+dodaje\s+się\s+art\.\s*(\d+[a-z]*)(?:\s*[–-]\s*(\d+[a-z]*))?", re.I),
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    return s


def _blocks(text: str):
    """Dzieli akt zmieniający na artykuły najwyższego poziomu (bez artykułów cytowanych w „…”)."""
    t = _norm(text)
    heads = {m.start(): m.group(1) for m in TOP_ART.finditer(t)}
    # „Art. N. W ustawie z dnia …” jest zawsze artykułem najwyższego poziomu, także gdy PDF łamie go w środku linii
    for m in re.finditer(r"Art\.\s*(\d+[a-z]*)\.\s+W\s+ustawie\s+z\s+dnia", t):
        heads.setdefault(m.start(), m.group(1))
    heads = sorted(heads.items())
    out = []
    for i, (pos, num) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(t)
        out.append((num, t[pos:end]))
    return out


def amended_articles(text: str, base_title: str) -> dict:
    key = re.sub(r"\s+", " ", base_title.lower()).strip()
    found_blocks = []
    # tytuł musi stać bezpośrednio po „W ustawie z dnia … r. [–]” — samo odesłanie do ustawy w treści nie wystarcza
    head_rx = re.compile(r"w ustawie z dnia \d{1,2} \w+ \d{4} r\.\s*(?:[–-]\s*)?" + re.escape(key))
    for num, body in _blocks(text):
        head = re.sub(r"\s+", " ", body[:600].lower())
        if head_rx.search(head):
            found_blocks.append(body)
    if not found_blocks and len(_blocks(text)) <= 1:
        # ustawa „o zmianie ustawy X” bez podziału na artykuły dla wielu ustaw
        flat = re.sub(r"\s+", " ", _norm(text)[:1500].lower())
        if key in flat and "o zmianie ustawy" in flat:
            found_blocks = [_norm(text)]
    arts: set[str] = set()
    for b in found_blocks:
        # pomiń treść w cudzysłowach (nowe brzmienie może cytować inne artykuły)
        stripped = re.sub(r"„[^”]{0,4000}”", " ", b, flags=re.S)
        for rx in TOUCH:
            for m in rx.finditer(stripped):
                for g in m.groups():
                    if g:
                        arts.add(g.lower())
    return {"found": bool(found_blocks), "articles": arts, "blocks": len(found_blocks)}


def base_title_from_meta(title: str) -> str:
    """'Ustawa z dnia 13 października 1998 r. o systemie ubezpieczeń społecznych' → 'o systemie ubezpieczeń społecznych'."""
    m = re.search(r"\br\.\s+(.*)$", title or "")
    t = (m.group(1) if m else title or "").strip()
    return re.sub(r"\s+", " ", t).rstrip(". ").lstrip("–- ")  # „r. – Karta Nauczyciela” → „Karta Nauczyciela”


def cited_articles(provision: str) -> set[str]:
    return {x.lower() for x in re.findall(r"art\.\s*(\d+[a-z]*)", provision or "", re.I)}
