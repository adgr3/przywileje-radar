"""Klient API ELI (api.sejm.gov.pl/eli) z pamięcią podręczną, ponawianiem i trybem offline.

Tryb offline (RADAR_OFFLINE=1 lub ELIClient(offline=True)) czyta wyłącznie z cache/fixtures —
używany w testach i przy braku sieci. Błędy sieci są zwracane jako ELIError, a nie kończą procesu.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

API = "https://api.sejm.gov.pl/eli"
UA = "Przywileje-radar/1.0 (lokalny prototyp badawczy)"


class ELIError(RuntimeError):
    pass


class ELIClient:
    def __init__(self, cache_dir: Path, offline: bool | None = None, fixtures_dir: Path | None = None,
                 retries: int = 3, timeout: int = 60, ttl_listing_s: int = 3600):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = bool(int(os.environ.get("RADAR_OFFLINE", "0"))) if offline is None else offline
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None
        self.retries = retries
        self.timeout = timeout
        self.ttl_listing_s = ttl_listing_s
        self.requests_made = 0
        self.errors: list[str] = []

    def _key(self, path: str) -> Path:
        safe = path.strip("/").replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "-")
        return self.cache_dir / safe

    def _fixture(self, path: str) -> Path | None:
        if not self.fixtures_dir:
            return None
        p = self.fixtures_dir / path.strip("/").replace("/", "_")
        return p if p.exists() else None

    def get_bytes(self, path: str, max_age_s: int | None = None) -> bytes:
        fx = self._fixture(path)
        if fx:
            return fx.read_bytes()
        cache = self._key(path)
        if cache.exists() and (max_age_s is None or time.time() - cache.stat().st_mtime < max_age_s):
            return cache.read_bytes()
        if self.offline:
            if cache.exists():
                return cache.read_bytes()
            raise ELIError(f"offline: brak w cache {path}")
        import requests

        last = None
        for attempt in range(self.retries):
            try:
                self.requests_made += 1
                r = requests.get(API + path, headers={"User-Agent": UA}, timeout=self.timeout)
                if r.status_code == 200:
                    cache.write_bytes(r.content)
                    return r.content
                last = f"HTTP {r.status_code}"
                if r.status_code in (400, 404):
                    break
            except Exception as e:  # sieć, DNS, timeout
                last = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
        if cache.exists():  # degradacja: ostatnia znana wersja
            self.errors.append(f"{path}: {last} — użyto cache")
            return cache.read_bytes()
        self.errors.append(f"{path}: {last}")
        raise ELIError(f"{path}: {last}")

    def get_json(self, path: str, max_age_s: int | None = None):
        return json.loads(self.get_bytes(path, max_age_s))

    # --- wygodne metody ---
    def list_year(self, year: int, publisher: str = "DU") -> list[dict]:
        return self.get_json(f"/acts/{publisher}/{year}", max_age_s=self.ttl_listing_s).get("items", [])

    def act(self, eli: str, refresh: bool = False) -> dict:
        return self.get_json(f"/acts/{eli}", max_age_s=0 if refresh else None)

    def text(self, eli: str) -> tuple[str, bytes, str]:
        """Zwraca (tekst, surowe bajty, url). Preferuje PDF (tekst urzędowy), potem HTML."""
        for kind in ("text.pdf", "text.html"):
            try:
                raw = self.get_bytes(f"/acts/{eli}/{kind}")
            except ELIError:
                continue
            if kind == "text.pdf" and raw[:4] == b"%PDF":
                return pdf_to_text(raw, self.cache_dir), raw, f"{API}/acts/{eli}/{kind}"
            if kind == "text.html":
                from bs4 import BeautifulSoup

                return BeautifulSoup(raw, "html.parser").get_text("\n"), raw, f"{API}/acts/{eli}/{kind}"
        raise ELIError(f"{eli}: brak tekstu")


def pdf_to_text(raw: bytes, tmp_dir: Path) -> str:
    import subprocess

    tmp = Path(tmp_dir) / ("_tmp_" + hashlib.sha256(raw).hexdigest()[:16] + ".pdf")
    tmp.write_bytes(raw)
    try:
        out = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(tmp), "-"], capture_output=True, check=True)
        return out.stdout.decode("utf-8", errors="replace")
    except Exception:
        from pypdf import PdfReader

        return "\n".join((p.extract_text() or "") for p in PdfReader(str(tmp)).pages)
    finally:
        tmp.unlink(missing_ok=True)
