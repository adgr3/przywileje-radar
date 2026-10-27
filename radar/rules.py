"""Reguły Radaru Dziennika Ustaw: klasyfikacja zmian, analiza przepisów i generowanie hipotez.

Reguły są deterministyczne (słowniki, wzorce, relacje ELI). Nie rozstrzygają wykładni —
każda hipoteza ma dowód (dosłowny fragment), kontrdowody i status DO_PRZEGLĄDU.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# --- Klasyfikacja dokumentu ---------------------------------------------------------------

def classify_act(meta: dict) -> str:
    t = (meta.get("title") or "").lower()
    typ = (meta.get("type") or "").lower()
    if "jednolitego tekstu" in t:
        return "TEKST_JEDNOLITY"
    if "o ratyfikacji" in t or "o wypowiedzeniu" in t:
        return "UMOWA_MIEDZYNARODOWA"
    if typ == "ustawa" or t.startswith("ustawa"):
        if "o zmianie" in t or "zmieniająca" in t:
            return "NOWELIZACJA_USTAWY"
        return "NOWA_USTAWA"
    if typ == "rozporządzenie" or t.startswith("rozporządzenie"):
        if "zmieniające" in t:
            return "ZMIANA_ROZPORZADZENIA"
        return "ROZPORZADZENIE_WYKONAWCZE"
    if "umow" in t or "konwencj" in t or typ in ("umowa międzynarodowa", "oświadczenie rządowe"):
        return "UMOWA_MIEDZYNARODOWA"
    if "trybunału konstytucyjnego" in t or typ == "wyrok":
        return "WYROK_TK"
    if typ == "obwieszczenie":
        return "OBWIESZCZENIE"
    if typ == "uchwała":
        return "UCHWALA"
    return "INNY"


LABEL = {
    "TEKST_JEDNOLITY": "tekst jednolity", "NOWELIZACJA_USTAWY": "nowelizacja ustawy", "NOWA_USTAWA": "nowa ustawa",
    "ZMIANA_ROZPORZADZENIA": "zmiana rozporządzenia", "ROZPORZADZENIE_WYKONAWCZE": "rozporządzenie wykonawcze",
    "UMOWA_MIEDZYNARODOWA": "umowa międzynarodowa", "WYROK_TK": "wyrok TK", "OBWIESZCZENIE": "obwieszczenie",
    "UCHWALA": "uchwała", "INNY": "inny",
}

# --- Słowniki dziedzinowe ----------------------------------------------------------------
BENEFIT_TERMS = {
    "wynagrodzenia": ["wynagrodze", "uposażen", "uposażeni", "płac", "mnożnik", "kwota bazow", "stawk"],
    "dodatki": ["dodat", "dodatku"],
    "nagrody": ["nagrod", "premi", "gratyfikac"],
    "odprawy": ["odpraw"],
    "urlopy": ["urlop"],
    "mieszkania": ["mieszkani", "zakwaterow", "lokal mieszkal", "kwater", "równoważnik za brak", "świadczenie mieszkaniowe"],
    "deputaty": ["deputat", "umundurowan", "równoważnik", "wyżywieni", "ekwiwalent"],
    "ubezpieczenia": ["składk", "ubezpieczen", "zabezpieczen", "dopłat"],
    "emerytury": ["emerytur", "rent", "stan spoczynku", "uposażenie w stanie spoczynku", "świadczeni kompensacyjn", "pomostow"],
    "zdrowie": ["rehabilitac", "leczni", "sanatori", "poratowania zdrowia", "turnus"],
    "diety": ["diet"],
    "inne": ["świadczeni", "uprawnieni"],
}
GROUP_TERMS = {
    "samorzad": ["pracownik samorządow", "pracowników samorządowych", "samorząd"],
    "radny": ["radn", "sołtys"],
    "sluzba_cywilna": ["służb cywiln", "służby cywilnej", "korpusu służby cywilnej"],
    "nauczyciel": ["nauczyciel", "karta nauczyciela"],
    "policja": ["policj", "policjant"],
    "wojsko": ["żołnierz", "obronie ojczyzny", "sił zbrojnych", "wojsk"],
    "sluzby_mundurowe": ["straży granicznej", "straży pożarnej", "służby więziennej", "funkcjonariusz", "służby ochrony państwa", "służby celno"],
    "lasy_panstwowe": ["lasach", "lasów państwowych", "służby leśnej"],
    "park_narodowy": ["park narodow", "parku narodowego", "ochronie przyrody"],
    "prokurator": ["prokurat"],
    "sedzia": ["sędzi", "sądów powszechnych", "ustroju sądów"],
    "artysta": ["artyst", "twórc", "zawód artystyczny", "zawodu artystycznego"],
    "kolej": ["kolejow", "pkp"],
    "rolnik": ["rolnik", "krus", "ubezpieczeniu społecznym rolników"],
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    return s


def match_terms(text: str, table: dict[str, list[str]]) -> list[str]:
    t = fold(text)
    return [k for k, terms in table.items() if any(term in t for term in terms)]


# --- Segmentacja przepisów ---------------------------------------------------------------
ART_RE = re.compile(r"(?m)^\s*(?:„)?(?:Art\.\s*(\d+[a-z]*)\.|(§\s*\d+[a-z]*)\.)\s")
UST_RE = re.compile(r"(?m)^\s*(\d+[a-z]*)\.\s")


def clean(s: str) -> str:
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = re.sub(r"Dziennik Ustaw\s+–\s*\d+\s*–\s*Poz\.\s*\d+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_articles(text: str) -> list[tuple[str, str]]:
    """Zwraca listę (numer artykułu, tekst artykułu). Uwzględnia artykuły nowelizowane w cudzysłowie."""
    idx = [(m.start(), m.group(1) or m.group(2).replace(" ", "")) for m in ART_RE.finditer(text)]
    out = []
    for i, (pos, num) in enumerate(idx):
        end = idx[i + 1][0] if i + 1 < len(idx) else len(text)
        out.append((num, text[pos:end]))
    return out


def sentences(s: str) -> list[str]:
    s = clean(s)
    parts = re.split(r"(?<=[.;:])\s+(?=(?:\d+[a-z]?\)|\d+[a-z]?\.|[A-ZŁŚŻŹĆ„]))", s)
    return [p.strip() for p in parts if len(p.strip()) > 25]


# --- Ramy semantyczne ----------------------------------------------------------------------
AMOUNT_RE = re.compile(r"(\d[\d  ]*(?:,\d+)?)\s*(zł|%|proc\.|kg|dni|miesięcy|lat)", re.I)
YEARS_RE = re.compile(r"po (\d+|[a-ząćęłńóśźż]+) lat(?:ach)?", re.I)
GRANT_RE = re.compile(r"przysługuj[eą]|ma prawo do|otrzymuje|wypłaca się|przyznaje się|nabywa prawo", re.I)
NEG_RE = re.compile(r"nie przysługuj|z wyłączeniem|nie stosuje się|wyłącza się|traci prawo|zawiesza się", re.I)
FUND_RE = re.compile(r"z budżetu państwa|ze środków budżetu|dotacj|ze środków funduszu|finansowan|dopłat[ay] do składek|refundac", re.I)
COND_RE = re.compile(r"jeżeli|pod warunkiem|w przypadku gdy|o ile|po upływie|co najmniej", re.I)
TRANS_RE = re.compile(r"przepisy dotychczasowe|do dnia wejścia w życie|zachowują moc|stosuje się przepisy w brzmieniu dotychczasowym|przepis przejściow", re.I)
EXTEND_RE = re.compile(r"przysługuje również|stosuje się odpowiednio do|także\s+\w+owi|rozszerza się", re.I)
AMEND_RE = re.compile(r"otrzymuje brzmienie|dodaje się|uchyla się|wyrazy .{1,80} zastępuje się", re.I)
SUBJ_RE = re.compile(r"^(?:\d+[a-z]?[.)]\s*)?([A-ZŁŚŻŹĆ][a-ząćęłńóśźż]+(?:owi|om|ce|ej|emu|ym|i))\b")


def frame(sentence: str) -> dict:
    s = sentence
    m = SUBJ_RE.match(s)
    return {
        "grants": bool(GRANT_RE.search(s)),
        "negates": bool(NEG_RE.search(s)),
        "amounts": [a[0].strip() + " " + a[1] for a in AMOUNT_RE.findall(s)][:6],
        "years": YEARS_RE.findall(s)[:4],
        "funding": bool(FUND_RE.search(s)),
        "conditional": bool(COND_RE.search(s)),
        "transitional": bool(TRANS_RE.search(s)),
        "extends_group": bool(EXTEND_RE.search(s)),
        "amending": bool(AMEND_RE.search(s)),
        "subject": m.group(1) if m else None,
        "benefits": match_terms(s, BENEFIT_TERMS),
        "groups": match_terms(s, GROUP_TERMS),
    }


def analyse_text(text: str, max_sentences: int = 4000) -> list[dict]:
    """Zwraca listę zdań istotnych dla katalogu wraz z ramą semantyczną."""
    out = []
    arts = split_articles(text) or [("?", text)]
    n = 0
    for num, body in arts:
        for s in sentences(body):
            n += 1
            if n > max_sentences:
                return out
            f = frame(s)
            if not f["benefits"]:
                continue
            if not (f["grants"] or f["amounts"] or f["funding"] or f["transitional"] or f["extends_group"] or f["negates"]):
                continue
            out.append({"article": num, "text": s[:900], "frame": f})
    return out


# --- Hipotezy --------------------------------------------------------------------------------

def prov(num: str) -> str:
    return num if str(num).startswith("§") else f"art. {num}"


def hid(*parts: str) -> str:
    return "H-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


GENERIC_COUNTER = [
    "Fragment wyodrębniono heurystycznie — może dotyczyć innej grupy lub kontekstu niż sugeruje reguła.",
    "Wymaga sprawdzenia daty wejścia w życie, przepisów przejściowych i aktów wykonawczych.",
]


def hypotheses_for_act(act: dict, findings: list[dict], catalog_index: dict) -> list[dict]:
    """act: rekord stanu radaru; findings: wynik analyse_text; catalog_index: {base_eli: [claim_ids], 'by_group_cat': {...}}"""
    out = []
    eli = act["eli"]
    base_hits = sorted({b for b in act.get("amends", []) if b in catalog_index["by_base"]})
    entry = act.get("entry_into_force")
    common = {
        "act_eli": eli, "act_display": act["display"], "act_url": f"https://eli.gov.pl/eli/{eli}/ogl",
        "promulgation": act.get("promulgation"), "entry_into_force": entry, "status": "DO_PRZEGLĄDU",
        "legal_review": "UNREVIEWED",
    }
    # 1. Nowelizacja aktu, na którym opierają się karty katalogu
    for base in base_hits:
        claims = catalog_index["by_base"][base]
        out.append({**common,
            "hypothesis_id": hid(eli, "AMENDS_CATALOG_ACT", base),
            "type": "AMENDS_CATALOG_ACT",
            "title": f"{act['display']} zmienia akt będący podstawą {len(claims)} kart katalogu",
            "statement": f"Według relacji ELI akt zmienia {base}. Karty oparte na tym akcie mogą wymagać aktualizacji wartości, warunków lub kręgu uprawnionych.",
            "affected_claims": claims[:30],
            "evidence": [f"ELI: {eli} → „Akty zmienione” zawiera {base} (data zmiany: {act.get('amends_dates', {}).get(base) or 'brak'})."],
            "counter_evidence": ["Nowelizacja może dotyczyć przepisów niezwiązanych z kartami (np. procedury)."] + GENERIC_COUNTER[1:],
            "confidence": "średnia",
        })
    seen_types = set()
    for f in findings:
        fr = f["frame"]
        q = f["text"]
        ev = [f"{prov(f['article'])}: „{q[:500]}”"]
        groups = fr["groups"]
        affected = []
        for g in groups:
            for b in fr["benefits"]:
                affected += catalog_index["by_group_cat"].get(f"{g}|{b}", [])
        affected = sorted(set(affected))[:20]
        cand = []
        if fr["grants"] and not fr["negates"] and fr["amounts"]:
            cand.append(("AMOUNT_OR_FORMULA", "Możliwa zmiana kwoty lub formuły świadczenia",
                         f"Przepis przyznaje świadczenie ({', '.join(fr['benefits'])}) z wartością {', '.join(fr['amounts'])}."))
        if fr["grants"] and fr["years"]:
            cand.append(("THRESHOLD_CHANGE", "Możliwa zmiana progu stażu/okresu",
                         f"Przepis wiąże prawo z okresem: po {', '.join(fr['years'])} latach."))
        if fr["extends_group"]:
            cand.append(("GROUP_EXTENSION", "Możliwe rozszerzenie kręgu uprawnionych",
                         "Przepis rozciąga stosowanie świadczenia na kolejną grupę (np. „przysługuje również”, „stosuje się odpowiednio”)."))
        if fr["funding"]:
            cand.append(("NEW_FUNDING", "Możliwe nowe finansowanie publiczne",
                         "Przepis wskazuje finansowanie z budżetu państwa, dotację, dopłatę lub refundację."))
        if fr["transitional"]:
            cand.append(("TRANSITIONAL_COHORT", "Przepis przejściowy — skutki dla określonych roczników/stanów",
                         "Przepis utrzymuje lub zmienia zasady dla osób w okresie przejściowym."))
        if fr["grants"] and not fr["amounts"] and not cand and act["change_kind"] in ("NOWA_USTAWA", "NOWELIZACJA_USTAWY"):
            cand.append(("NEW_ENTITLEMENT_CANDIDATE", "Możliwe nowe uprawnienie",
                         f"Przepis przyznaje świadczenie ({', '.join(fr['benefits'])})" + (f" podmiotowi: {fr['subject']}" if fr["subject"] else "") + "."))
        for typ, title, stmt in cand:
            key = (typ, f["article"])
            if key in seen_types:
                continue
            seen_types.add(key)
            counter = list(GENERIC_COUNTER)
            if fr["negates"]:
                counter.insert(0, "Zdanie zawiera wyłączenie lub negację — możliwe ograniczenie zamiast nowego prawa.")
            if fr["amending"]:
                counter.insert(0, "Zdanie jest przepisem zmieniającym — porównaj z brzmieniem dotychczasowym przed oceną skutku.")
            if entry and act.get("promulgation") and entry > act["promulgation"]:
                counter.append(f"Wejście w życie {entry} — do tego dnia obowiązuje stan dotychczasowy.")
            out.append({**common,
                "hypothesis_id": hid(eli, typ, f["article"], sha(q)[:12]),
                "type": typ,
                "title": f"{title} — {act['display']} {prov(f['article'])}",
                "statement": stmt,
                "groups": groups, "benefits": fr["benefits"],
                "affected_claims": affected,
                "evidence": ev,
                "evidence_sha256": sha(q),
                "counter_evidence": counter,
                "confidence": "niska" if not groups else "średnia",
            })
    return out
