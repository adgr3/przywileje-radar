#!/usr/bin/env python3
"""Daily Przywileje radar runner for the always-on VPS.

This is infrastructure, not a second product copy. It keeps only the radar
state, queue and code needed to fetch ELI, compares with the public catalog,
writes a compact live-status JSON, and commits the durable state to the
public `przywileje-radar` repository so the site can read it without a deploy.

Run manually:
    python3 run_daily.py --force --no-push
Scheduled by systemd user timer at 07:00 Europe/Warsaw.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import requests
except Exception:  # pragma: no cover - reported at runtime
    requests = None

ROOT = Path(__file__).resolve().parent
RADAR = ROOT / "radar"
STATE = RADAR / "state"
QUEUE = RADAR / "queue"
REPORTS = RADAR / "reports"
LIVE = ROOT / "radar-live.json"
CATALOG = ROOT / "site" / "data" / "catalog.json"
PRODUCTION_CATALOG = "https://przywileje-demo.pages.dev/data/catalog.json"
LOCK = ROOT / ".run.lock"
WARSAW = dt.timezone(dt.timedelta(hours=1), "Europe/Warsaw")  # display; system time is Europe/Warsaw


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
    return d


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def last_success() -> dict | None:
    runs = read_jsonl(STATE / "runs.jsonl")
    for run in reversed(runs):
        errors = [e for e in run.get("errors", []) if not str(e).startswith("katalog zmieniony")]
        if not errors and not run.get("offline") and run.get("finished_at"):
            return run
    return None


def acquire_lock() -> tuple[object | None, str | None]:
    # fcntl.flock is advisory but reliable for the single systemd service and
    # a manual run. The lock file is never deleted while held.
    fh = open(LOCK, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        fh.truncate()
        fh.write(f"locked {iso(now())}\n")
        fh.flush()
        return None, "another radar run is already active"
    fh.seek(0)
    fh.truncate()
    fh.write(f"owner pid={os.getpid()} started={iso(now())}\n")
    fh.flush()
    return fh, None


def release_lock(fh) -> None:
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def fetch_catalog() -> None:
    if requests is None:
        raise RuntimeError("requests is not installed")
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(3):
        try:
            r = requests.get(PRODUCTION_CATALOG, timeout=60, headers={"User-Agent": "Przywileje-radar/1.0"})
            if r.status_code == 200:
                r.json()  # validate
                CATALOG.write_bytes(r.content)
                return
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"catalog unavailable: {last}")


def backup_state() -> Path:
    backup = Path(tempfile.mkdtemp(prefix="przywileje-radar-backup-", dir=str(ROOT)))
    for src in (STATE, QUEUE, REPORTS):
        if src.exists():
            shutil.copytree(src, backup / src.name)
    return backup


def restore_state(backup: Path) -> None:
    for name in ("state", "queue", "reports"):
        src = backup / name
        dst = RADAR / name
        if src.exists():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
    shutil.rmtree(backup, ignore_errors=True)


def write_live(status: str, attempt: dt.datetime, success: dict | None, run: dict | None,
               error: str | None, backup: Path | None) -> None:
    previous = success or last_success()
    prev_success = previous.get("finished_at") if previous else None
    if status == "OK" and run:
        prev_success = run.get("finished_at") or iso(attempt)
    queue = read_jsonl(QUEUE / "HYPOTHESES.jsonl")
    low = sum(1 for h in queue if h.get("confidence") == "niska")
    history = read_jsonl(STATE / "runs.jsonl")[-8:]
    missed = False
    if prev_success:
        try:
            prev_dt = parse_dt(prev_success)
            missed = bool(prev_dt and (attempt - prev_dt).total_seconds() > 36 * 3600)
        except ValueError:
            missed = False
    data = {
        "schema": "przywileje-radar-live/v1",
        "status": status,
        "last_attempt_at": iso(attempt),
        "last_success_at": prev_success,
        "last_run_id": (run or previous or {}).get("run_id"),
        "new_publications": (run or previous or {}).get("acts_new"),
        "potentially_affecting": (run or previous or {}).get("relevant"),
        "unclear": low,
        "queue_total": len(queue),
        "window_from": (run or previous or {}).get("window_from"),
        "window_to": (run or previous or {}).get("window_to"),
        "acts_seen": (run or previous or {}).get("acts_seen"),
        "errors": [error] if error else [],
        "missed": missed,
        "source": "VPS henryk171 / systemd user timer",
        "report_url": "https://raw.githubusercontent.com/adgr3/przywileje-radar/main/radar/reports/LATEST.md",
        "history": history,
    }
    atomic_json(LIVE, data)
    if backup:
        if status == "OK":
            shutil.rmtree(backup, ignore_errors=True)
        else:
            restore_state(backup)


def run_radar(force: bool) -> tuple[dict | None, str | None]:
    if not force:
        success = last_success()
        if success and success.get("finished_at", "")[:10] == now().date().isoformat():
            return success, None
    until = now().date().isoformat()
    success = last_success()
    since = (dt.date.fromisoformat(success["finished_at"][:10]) - dt.timedelta(days=2)).isoformat() if success else (
        dt.date.fromisoformat(until) - dt.timedelta(days=35)).isoformat()
    cmd = [sys.executable, str(RADAR / "radar.py"), "run", "--since", since, "--until", until]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=2400)
    (ROOT / "last-run.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        return None, f"radar exit {proc.returncode}: {proc.stderr[-1000:]}"
    runs = read_jsonl(STATE / "runs.jsonl")
    if not runs:
        return None, "radar produced no run record"
    run = runs[-1]
    fatal = [e for e in run.get("errors", []) if not str(e).startswith("katalog zmieniony")]
    if fatal:
        return run, "source errors: " + " | ".join(fatal[:5])
    return run, None


def git_publish() -> None:
    def run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120)

    run(["add", "radar/state", "radar/queue", "radar/reports", "radar-live.json", "last-run.log"])
    changed = run(["diff", "--cached", "--quiet"])
    if changed.returncode == 0:
        return
    run(["-c", "user.name=Przywileje Radar", "-c", "user.email=radar@invalid",
         "commit", "-m", f"radar: daily run {now().date().isoformat()}"])
    push = run(["push", "origin", "main"])
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {push.stderr[-800:]}")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--push", action="store_true", help="explicitly publish to the public state repo (default)")
    args = ap.parse_args(argv)
    fh, lock_error = acquire_lock()
    if fh is None:
        print(f"SKIP: {lock_error}")
        return 0
    attempt = now()
    backup = backup_state()
    try:
        try:
            fetch_catalog()
        except Exception as exc:  # noqa: BLE001
            write_live("ERROR", attempt, None, None, str(exc)[:1000], backup)
            return 1
        run, err = run_radar(args.force)
        if err:
            write_live("ERROR", attempt, None, run, err, backup)
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
        write_live("OK", attempt, None, run, None, backup)
        if not args.no_push:
            try:
                git_publish()
            except Exception as exc:  # noqa: BLE001
                print(f"PUBLISH_ERROR: {exc}", file=sys.stderr)
                return 1
        print(json.dumps({"status": "OK", "run_id": run.get("run_id"), "acts_seen": run.get("acts_seen"),
                          "acts_new": run.get("acts_new"), "relevant": run.get("relevant")}, ensure_ascii=False))
        return 0
    finally:
        release_lock(fh)


if __name__ == "__main__":
    raise SystemExit(main())
