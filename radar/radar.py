"""Radar Dziennika Ustaw — MVP pipeline (Mission #021).

ELI → NOWY AKT → REGUŁY → POWIĄZANIA → HIPOTEZY → DOWODY → KOLEJKA REDAKCYJNA

  python radar/radar.py run                 # okno domyślne: ostatnie 30 dni ogłoszeń DU
  python radar/radar.py run --since 2026-09-01 --until 2026-10-25
  python radar/radar.py run --offline       # wyłącznie cache/fixtures, bez sieci
  python radar/radar.py currency            # kontrola aktualności podstaw prawnych kart katalogu
  python radar/radar.py export              # zapis site/data/radar.js (podgląd kolejki na stronie)

Idempotencja: akty są kluczowane ELI + changeDate, hipotezy deterministycznym hypothesis_id;
ponowne uruchomienie nie dopisuje duplikatów. Nic nie trafia do katalogu automatycznie:
kolejka (radar/queue/HYPOTHESES.jsonl) jest kwarantanną do przeglądu redakcyjno-prawnego.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from eli import ELIClient, ELIError  # noqa: E402
import rules  # noqa: E402

STATE = HERE / "state"
QUEUE = HERE / "queue"
REPORTS = HERE / "reports"
CATALOG = ROOT / "site" / "data" / "catalog.json"


def jl_read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def jl_write(p: Path, rows: list[dict], key: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r[key])
    p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8", newline="\n")


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


# --- Indeks katalogu: akt bazowy → karty ------------------------------------------------------

def base_acts_for(client: ELIClient, eli: str, memo: dict) -> list[str]:
    """Dla tekstu jednolitego zwraca ELI aktu pierwotnego; dla aktu pierwotnego — jego samego."""
    if eli in memo:
        return memo[eli]
    out = [eli]
    try:
        meta = client.act(eli)
        for r in (meta.get("references") or {}).get("Tekst jednolity dla aktu", []):
            out.append(r["id"])
    except ELIError:
        pass
    memo[eli] = out
    return out


def catalog_index(client: ELIClient, catalog: dict, memo: dict) -> dict:
    by_base: dict[str, list[str]] = {}
    by_group_cat: dict[str, list[str]] = {}
    cat_map = {"wynagrodzenie_zasadnicze": "wynagrodzenia"}
    for c in catalog["claims"]:
        if c.get("display") == "merged":
            continue
        for b in c["legal_basis"]:
            if b.get("eli"):
                for base in base_acts_for(client, b["eli"], memo):
                    by_base.setdefault(base, [])
                    if c["claim_id"] not in by_base[base]:
                        by_base[base].append(c["claim_id"])
        for g in [c["group_id"]] + (c.get("also_groups") or []):
            k = f"{g}|{cat_map.get(c['category'], c['category'])}"
            by_group_cat.setdefault(k, []).append(c["claim_id"])
    return {"by_base": by_base, "by_group_cat": by_group_cat}


# --- Ingest ------------------------------------------------------------------------------------

def ingest(client: ELIClient, since: str, until: str, acts_state: dict) -> tuple[list[dict], list[str]]:
    years = sorted({int(since[:4]), int(until[:4])})
    new_or_changed, errors = [], []
    for y in range(years[0], years[-1] + 1):
        try:
            items = client.list_year(y)
        except ELIError as e:
            errors.append(f"lista DU {y}: {e}")
            continue
        for it in items:
            prom = it.get("promulgation") or ""
            if not (since <= prom <= until):
                continue
            eli = it["ELI"]
            prev = acts_state.get(eli)
            if prev and prev.get("change_date") == it.get("changeDate"):
                continue
            new_or_changed.append(it)
    return new_or_changed, errors


def process_act(client: ELIClient, item: dict, cindex: dict, fetch_text: bool) -> tuple[dict, list[dict]]:
    eli = item["ELI"]
    meta = item
    err = None
    try:
        meta = {**item, **client.act(eli)}
    except ELIError as e:
        err = f"metadane: {e}"
    refs = meta.get("references") or {}
    amends = [r["id"] for r in refs.get("Akty zmienione", [])]
    amends_dates = {r["id"]: r.get("date") for r in refs.get("Akty zmienione", [])}
    consolidated_for = [r["id"] for r in refs.get("Tekst jednolity dla aktu", [])]
    kind = rules.classify_act(meta)
    title = meta.get("title") or ""
    title_benefits = [b for b in rules.match_terms(title, rules.BENEFIT_TERMS) if b != "inne"]
    title_groups = rules.match_terms(title, rules.GROUP_TERMS)
    hits_catalog = sorted({b for b in amends + consolidated_for if b in cindex["by_base"]})
    rule_hits = []
    if hits_catalog:
        rule_hits.append("R1:relacja_ELI_do_aktu_z_katalogu")
    if title_benefits:
        rule_hits.append("R2:tytuł_świadczenia")
    if title_groups:
        rule_hits.append("R3:tytuł_grupa")
    relevant = bool(hits_catalog) or (bool(title_benefits) and bool(title_groups)) or (kind in ("NOWA_USTAWA", "NOWELIZACJA_USTAWY") and bool(title_benefits))
    act = {
        "eli": eli,
        "display": meta.get("displayAddress") or eli,
        "title": title,
        "type": meta.get("type"),
        "change_kind": rules.LABEL[kind],
        "change_kind_code": kind,
        "announcement_date": meta.get("announcementDate"),
        "promulgation": meta.get("promulgation"),
        "entry_into_force": meta.get("entryIntoForce"),
        "legal_status_date": meta.get("legalStatusDate"),
        "in_force": meta.get("inForce"),
        "status": meta.get("status"),
        "change_date": meta.get("changeDate"),
        "url": f"https://eli.gov.pl/eli/{eli}/ogl",
        "amends": amends,
        "amends_dates": amends_dates,
        "consolidated_for": consolidated_for,
        "keywords": meta.get("keywords") or [],
        "rules": rule_hits,
        "relevant": relevant,
        "catalog_acts_hit": hits_catalog,
        "text_sha256": None,
        "text_url": None,
        "fetch_error": err,
        "processed_at": now(),
    }
    hyps: list[dict] = []
    if relevant and kind != "TEKST_JEDNOLITY" and fetch_text:
        try:
            text, raw, url = client.text(eli)
            act["text_sha256"] = hashlib.sha256(raw).hexdigest()
            act["text_url"] = url
            findings = rules.analyse_text(text)
            act["findings"] = len(findings)
            hyps = rules.hypotheses_for_act(act, findings, cindex)
            hyps += comparator_gap_hypotheses(act, findings, cindex)
        except ELIError as e:
            act["fetch_error"] = f"tekst: {e}"
            hyps = rules.hypotheses_for_act(act, [], cindex)
    elif relevant and kind == "TEKST_JEDNOLITY" and hits_catalog:
        hyps.append({
            "hypothesis_id": rules.hid(eli, "NEW_CONSOLIDATED_TEXT"),
            "type": "NEW_CONSOLIDATED_TEXT", "status": "DO_PRZEGLĄDU", "legal_review": "UNREVIEWED",
            "title": f"Nowy tekst jednolity ({act['display']}) aktu z katalogu",
            "statement": "Ogłoszono nowy tekst jednolity aktu, na którym opierają się karty katalogu. Cytaty i odesłania kart warto porównać z nowym tekstem.",
            "act_eli": eli, "act_display": act["display"], "act_url": act["url"], "promulgation": act["promulgation"],
            "entry_into_force": None, "affected_claims": sorted({c for b in hits_catalog for c in cindex["by_base"][b]})[:30],
            "evidence": [f"ELI: „Tekst jednolity dla aktu” → {', '.join(hits_catalog)}"],
            "counter_evidence": ["Tekst jednolity nie zmienia prawa — porządkuje brzmienie; zmiana merytoryczna wynika z aktów zmieniających."],
            "confidence": "wysoka",
        })
    return act, hyps


def comparator_gap_hypotheses(act: dict, findings: list[dict], cindex: dict) -> list[dict]:
    out, seen = [], set()
    for f in findings:
        fr = f["frame"]
        if not (fr["grants"] and fr["groups"] and fr["benefits"]) or fr["negates"]:
            continue
        for g in fr["groups"]:
            for b in fr["benefits"]:
                if cindex["by_group_cat"].get(f"{g}|{b}"):
                    continue
                others = sorted({k.split("|")[0] for k in cindex["by_group_cat"] if k.endswith("|" + b)})
                if not others or (g, b) in seen:
                    continue
                seen.add((g, b))
                out.append({
                    "hypothesis_id": rules.hid(act["eli"], "COMPARATOR_GAP", g, b),
                    "type": "COMPARATOR_GAP", "status": "DO_PRZEGLĄDU", "legal_review": "UNREVIEWED",
                    "title": f"Nowe dane do porównania: {g} / {b} — {act['display']} {rules.prov(f['article'])}",
                    "statement": f"Katalog nie ma karty dla grupy „{g}” w kategorii „{b}”, a ma dla: {', '.join(others[:6])}. Przepis może uzupełnić porównanie.",
                    "act_eli": act["eli"], "act_display": act["display"], "act_url": act["url"],
                    "promulgation": act["promulgation"], "entry_into_force": act.get("entry_into_force"),
                    "groups": [g], "benefits": [b], "affected_claims": [],
                    "evidence": [f"{rules.prov(f['article'])}: „{f['text'][:500]}”"], "evidence_sha256": rules.sha(f["text"]),
                    "counter_evidence": rules.GENERIC_COUNTER, "confidence": "niska",
                })
    return out


# --- Kontrola aktualności kart katalogu -------------------------------------------------------

def currency_check(client: ELIClient, catalog: dict, memo: dict) -> list[dict]:
    """Dla każdej karty: czy akt bazowy ma akty zmieniające z datą późniejszą niż data stanu/sprawdzenia."""
    out = []
    base_meta: dict[str, dict] = {}
    for c in catalog["claims"]:
        if c.get("display") == "merged":
            continue
        for b in c["legal_basis"]:
            if not b.get("eli"):
                continue
            ref_date = b.get("text_state_date") or c.get("checked_on")
            for base in base_acts_for(client, b["eli"], memo):
                if base == b["eli"] and len(base_acts_for(client, b["eli"], memo)) > 1:
                    continue  # sam tekst jednolity — sprawdzamy akt pierwotny
                if base not in base_meta:
                    try:
                        base_meta[base] = client.act(base)
                    except ELIError as e:
                        base_meta[base] = {"_error": str(e)}
                m = base_meta[base]
                if "_error" in m:
                    out.append({"claim_id": c["claim_id"], "base_act": base, "status": "CHECK_FAILED", "detail": m["_error"]})
                    continue
                refs = m.get("references") or {}
                later = sorted({(r["id"], r.get("date")) for r in refs.get("Akty zmieniające", []) if r.get("date") and ref_date and r["date"] > ref_date})
                newer_tj = sorted({r["id"] for r in refs.get("Inf. o tekście jednolitym", []) if r["id"] != b["eli"] and _pos_after(r["id"], b["eli"])})
                today = dt.date.today().isoformat()
                if not later:
                    status = "NO_LATER_AMENDMENT_FOUND"
                elif all(d > today for _, d in later):
                    status = "DEFERRED_AMENDMENT"  # zmiana ogłoszona, skutek w przyszłości
                else:
                    status = "POSSIBLY_OUTDATED"
                if m.get("inForce") == "NOT_IN_FORCE" or (m.get("status") or "").startswith("uchylon"):
                    status = "BASE_ACT_REPEALED"
                out.append({"claim_id": c["claim_id"], "basis_eli": b["eli"], "base_act": base, "reference_date": ref_date,
                            "status": status, "later_amendments": [{"eli": e, "date": d} for e, d in later],
                            "newer_consolidated_texts": newer_tj, "base_in_force": m.get("inForce")})
    return out


def _pos_after(a: str, b: str) -> bool:
    try:
        ya, pa = int(a.split("/")[1]), int(a.split("/")[2])
        yb, pb = int(b.split("/")[1]), int(b.split("/")[2])
        return (ya, pa) > (yb, pb)
    except Exception:
        return False


# --- Komendy ----------------------------------------------------------------------------------

def cmd_run(args) -> int:
    client = ELIClient(HERE / "cache", offline=args.offline, fixtures_dir=Path(args.fixtures) if args.fixtures else None)
    until = args.until or dt.date.today().isoformat()
    since = args.since or (dt.date.fromisoformat(until) - dt.timedelta(days=30)).isoformat()
    state_dir = Path(args.state) if args.state else STATE
    queue_dir = Path(args.queue) if args.queue else QUEUE
    acts_path, runs_path, q_path = state_dir / "acts.jsonl", state_dir / "runs.jsonl", queue_dir / "HYPOTHESES.jsonl"
    acts_state = {a["eli"]: a for a in jl_read(acts_path)}
    queue = {h["hypothesis_id"]: h for h in jl_read(q_path)}
    catalog = json.loads(Path(args.catalog or CATALOG).read_text(encoding="utf-8"))
    memo_path = state_dir / "base_acts_memo.json"
    memo = json.loads(memo_path.read_text(encoding="utf-8")) if memo_path.exists() else {}
    started = now()
    cindex = catalog_index(client, catalog, memo)
    # Zmiana katalogu (nowe karty/podstawy) → ponowne powiązanie aktów z okna (z cache; hipotezy mają stałe ID, więc bez duplikatów).
    idx_hash = hashlib.sha256(json.dumps(cindex, sort_keys=True).encode("utf-8")).hexdigest()
    idx_path = state_dir / "catalog_index.sha256"
    prev_hash = idx_path.read_text(encoding="utf-8").strip() if idx_path.exists() else None
    reprocess = getattr(args, "reprocess", False) or (prev_hash is not None and prev_hash != idx_hash)
    items, errors = ingest(client, since, until, {} if reprocess else acts_state)
    if reprocess:
        errors.append(f"katalog zmieniony od ostatniego przebiegu — ponownie powiązano {len(items)} aktów z okna")
    state_dir.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(idx_hash + "\n", encoding="utf-8")
    if args.limit:
        items = items[: args.limit]
    added_h, relevant = 0, 0
    for it in items:
        act, hyps = process_act(client, it, cindex, fetch_text=not args.no_text)
        acts_state[act["eli"]] = act
        relevant += int(act["relevant"])
        for h in hyps:
            if h["hypothesis_id"] not in queue:
                h["first_seen"] = started
                queue[h["hypothesis_id"]] = h
                added_h += 1
            else:  # idempotencja: aktualizacja bez duplikatu, z zachowaniem statusu przeglądu
                keep = {k: queue[h["hypothesis_id"]][k] for k in ("status", "legal_review", "first_seen", "review_note") if k in queue[h["hypothesis_id"]]}
                queue[h["hypothesis_id"]] = {**h, **keep}
        if act.get("fetch_error"):
            errors.append(f"{act['eli']}: {act['fetch_error']}")
    jl_write(acts_path, list(acts_state.values()), "eli")
    jl_write(q_path, list(queue.values()), "hypothesis_id")
    memo_path.write_text(json.dumps(memo, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    in_window = [a for a in acts_state.values() if since <= (a.get("promulgation") or "") <= until]
    run = {"run_id": started, "finished_at": now(), "window_from": since, "window_to": until, "acts_seen": len(in_window),
           "acts_new": len(items), "relevant": sum(1 for a in in_window if a["relevant"]), "hypotheses_added": added_h,
           "queue_total": len(queue), "http_requests": client.requests_made, "errors": errors[:50], "offline": client.offline}
    runs = jl_read(runs_path) + [run]
    jl_write(runs_path, runs, "run_id")
    write_report(run, in_window, queue, state_dir.parent / "reports" if args.state else REPORTS)
    print(json.dumps({k: run[k] for k in ("window_from", "window_to", "acts_seen", "acts_new", "relevant", "hypotheses_added", "queue_total", "http_requests")}, ensure_ascii=False))
    if errors:
        print(f"Błędy/ostrzeżenia ({len(errors)}): " + " | ".join(errors[:5]))
    return 0


def write_report(run: dict, acts: list[dict], queue: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = [a for a in acts if a["relevant"]]
    lines = [f"# Radar DU — przebieg {run['run_id']}", "",
             f"Okno ogłoszeń: {run['window_from']} – {run['window_to']}. Aktów w oknie: {run['acts_seen']}; nowych/zmienionych w tym przebiegu: {run['acts_new']}; istotnych: {run['relevant']}.",
             f"Hipotezy dodane: {run['hypotheses_added']}; kolejka łącznie: {run['queue_total']}. Żadna hipoteza nie jest publikowana automatycznie.", "",
             "## Akty istotne", "", "| Pozycja | Typ zmiany | Ogłoszono | Wejście w życie | Reguły | Tytuł |", "|---|---|---|---|---|---|"]
    for a in sorted(rel, key=lambda x: x["promulgation"] or ""):
        lines.append(f"| [{a['display']}]({a['url']}) | {a['change_kind']} | {a['promulgation']} | {a.get('entry_into_force') or '—'} | {', '.join(a['rules'])} | {a['title'][:140].replace('|', '/')} |")
    lines += ["", "## Kolejka hipotez (najnowsze 40)", ""]
    for h in sorted(queue.values(), key=lambda x: (x.get("promulgation") or "", x["hypothesis_id"]), reverse=True)[:40]:
        lines.append(f"- **{h['type']}** · {h['title']} · pewność: {h.get('confidence')} · {h['status']}")
    if run["errors"]:
        lines += ["", "## Błędy pobierania", ""] + [f"- {e}" for e in run["errors"]]
    (out_dir / f"RUN_{run['window_to']}.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (out_dir / "LATEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def cmd_currency(args) -> int:
    client = ELIClient(HERE / "cache", offline=args.offline)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    memo_path = STATE / "base_acts_memo.json"
    memo = json.loads(memo_path.read_text(encoding="utf-8")) if memo_path.exists() else {}
    res = currency_check(client, catalog, memo)
    STATE.mkdir(parents=True, exist_ok=True)
    memo_path.write_text(json.dumps(memo, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    (STATE / "catalog_currency.json").write_text(json.dumps({"checked_at": now(), "results": res}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    from collections import Counter
    print(json.dumps(Counter(r["status"] for r in res), ensure_ascii=False))
    return 0


def cmd_export(args) -> int:
    runs = jl_read(STATE / "runs.jsonl")
    if not runs:
        print("Brak przebiegów radaru.")
        return 1
    run = runs[-1]
    acts = [a for a in jl_read(STATE / "acts.jsonl") if run["window_from"] <= (a.get("promulgation") or "") <= run["window_to"]]
    queue = jl_read(QUEUE / "HYPOTHESES.jsonl")
    queue = [h for h in queue if run["window_from"] <= (h.get("promulgation") or "") <= run["window_to"]]
    order = {"AMENDS_CATALOG_ACT": 0, "NEW_CONSOLIDATED_TEXT": 1, "AMOUNT_OR_FORMULA": 2, "THRESHOLD_CHANGE": 3, "GROUP_EXTENSION": 4,
             "NEW_FUNDING": 5, "NEW_ENTITLEMENT_CANDIDATE": 6, "TRANSITIONAL_COHORT": 7, "COMPARATOR_GAP": 8}
    queue.sort(key=lambda h: (order.get(h["type"], 9), h.get("promulgation") or ""))
    slim_acts = [{k: a.get(k) for k in ("eli", "display", "title", "change_kind", "promulgation", "entry_into_force", "url", "rules", "relevant")} for a in acts]
    payload = {"run": run, "acts": slim_acts, "queue": queue[: args.max]}
    out = ROOT / "site" / "data" / "radar.js"
    out.write_text("window.PRZYWILEJE_RADAR=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8", newline="\n")
    print(f"Zapisano {out} (hipotez: {len(payload['queue'])}/{len(queue)}, aktów: {len(slim_acts)})")
    return 0


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--since"); r.add_argument("--until"); r.add_argument("--offline", action="store_true")
    r.add_argument("--fixtures"); r.add_argument("--state"); r.add_argument("--queue"); r.add_argument("--catalog")
    r.add_argument("--limit", type=int, default=0); r.add_argument("--no-text", action="store_true")
    r.add_argument("--reprocess", action="store_true", help="ponownie powiąż wszystkie akty z okna z bieżącym katalogiem")
    c = sub.add_parser("currency"); c.add_argument("--offline", action="store_true")
    e = sub.add_parser("export"); e.add_argument("--max", type=int, default=60)
    args = ap.parse_args(argv)
    return {"run": cmd_run, "currency": cmd_currency, "export": cmd_export}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
