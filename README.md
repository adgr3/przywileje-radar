# Przywileje Radar — daily state

Minimal, always-on daily runner for the Przywileje V1 radar.

This repository intentionally contains **only** the radar code, state, queue and
public live-status JSON. It is not a copy of the website.

## Runtime

- host: `henryk171` (always-on Linux VPS)
- scheduler: systemd user timer `przywileje-radar.timer`
- time: `07:00 Europe/Warsaw`, `Persistent=true`
- runner: `run_daily.py`
- source of catalog: `https://przywileje-demo.pages.dev/data/catalog.json`
- source of law: ELI/Sejm API (`https://api.sejm.gov.pl/eli`)
- public state: `radar-live.json` in this public repository (raw URL read by the site)

## Manual test

```bash
python3 run_daily.py --force --no-push
```

This fetches the production catalog, runs the existing radar on the real ELI
API, writes `radar-live.json`, and does not push.

## Production run

```bash
python3 run_daily.py --push
```

The runner:

- takes an exclusive `fcntl` lock;
- backs up state before a run;
- fetches the public catalog with retries;
- runs the existing deterministic radar;
- refuses to replace the last successful state when the source run reports
  errors (it restores the backup and writes an error status);
- updates `radar-live.json`;
- commits state, reports and live status and pushes.

## systemd user timer

Install once on the VPS:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/przywileje-radar.service ~/.config/systemd/user/
cp systemd/przywileje-radar.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now przywileje-radar.timer
systemctl --user list-timers przywileje-radar.timer
```

Linger is already enabled for `grantadmin`, so the timer runs without a login
session or laptop.

## Public status

`radar-live.json` is served by GitHub raw and rendered by `#/radar`. The site
does not need a full redeploy when the daily status changes.
