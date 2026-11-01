#!/usr/bin/env python3
"""Daily requirement recheck for Hour Vault. Same mechanism as CE Compass's scraper/verify.py.

For every template in rules/requirements.json it:
  1. downloads the official source pages listed in rules/checks.yml,
  2. checks that every number (ojtTarget, and classroomTarget when above 0) is stated on those pages with the
     same value, using the patterns in checks.yml,
  3. marks the template "verified" (with today's date) only when every number is confirmed, otherwise
     "needs_review" with the reasons,
  4. writes rules/status.json and the published copy docs/requirements.json + docs/manifest.json (GitHub Pages,
     which the app reads once a day; sha256-pinned like CE Compass's rules feed),
  5. sets needs_issue=true for GitHub Actions when a template lost its verified status.

A template that was verified stays verified for up to GRACE_DAYS if its page is temporarily unreachable.
Exit code is 0 unless the config itself is broken. Runs on the free tier; no secrets needed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

UA = os.environ.get("SCRAPER_USER_AGENT", "Mozilla/5.0 (compatible; HourVaultBot/1.0; +https://redbrooklabs.com)")
GRACE_DAYS = 7
DELAY = 1.5
FIELDS = {"ojt": "ojtTarget", "classroom": "classroomTarget"}


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def number(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", ""))
    except ValueError:
        return None


def normalize(text: str) -> str:
    for a, b in ((" ", " "), ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip().lower()


_cache: dict[str, tuple[str | None, str | None]] = {}
TEXT_DIR: Path | None = None


def slug(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", url.lower().split("://", 1)[-1]).strip("-")[:120]


def fetch(url: str) -> tuple[str | None, str | None]:
    if url in _cache:
        return _cache[url]
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US"}, timeout=40)
        if r.status_code != 200:
            result = (None, f"HTTP {r.status_code}")
        elif "pdf" in r.headers.get("content-type", "") or url.lower().endswith(".pdf") or r.content[:4] == b"%PDF":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(r.content))
            result = (normalize(" ".join(page.extract_text() or "" for page in reader.pages)), None)
        else:
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            result = (normalize(soup.get_text(" ")), None)
    except Exception as exc:  # network trouble, bad PDF...
        result = (None, f"{type(exc).__name__}: {exc}"[:200])
    _cache[url] = result
    if TEXT_DIR and result[0]:
        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        (TEXT_DIR / f"{slug(url)}.txt").write_text(re.sub(r"(?<=[.;:]) ", "\n", result[0]) + "\n")
    time.sleep(DELAY)
    return result


def required_fields(t: dict) -> dict[str, float]:
    req = {"ojt": float(t["ojtTarget"])}
    if float(t.get("classroomTarget", 0)) > 0:
        req["classroom"] = float(t["classroomTarget"])
    return req


def check_template(t: dict, spec: dict) -> tuple[bool, list[str], list[str], bool]:
    texts, problems, unreachable = [], [], False
    for url in spec["sources"]:
        text, err = fetch(url)
        if err:
            problems.append(f"Could not read {url}: {err}")
            unreachable = True
        else:
            texts.append(text)
    combined = " \n ".join(texts)
    required = required_fields(t)
    confirmed: list[str] = []
    for fact in spec.get("facts", []):
        field = fact["field"]
        if field not in required:
            problems.append(f"checks.yml has a fact for '{field}', but the template has no such number")
            continue
        m = re.search(fact["pattern"], combined, flags=re.IGNORECASE)
        if not m:
            where = "a source page could not be read" if unreachable else "the page no longer says it"
            problems.append(f"{field}: not found ({where}): '{fact['pattern']}'")
            continue
        raw = m.groupdict().get("n")
        found = number(raw) if raw else None
        if found is None:
            problems.append(f"{field}: could not read a number from '{m.group(0)}'")
            continue
        found *= fact.get("scale", 1)
        if abs(found - required[field]) > 0.01:
            problems.append(f"{field}: the page says {found:g}, requirements.json says {required[field]:g}")
            continue
        confirmed.append(field)
    for field in required:
        if field not in confirmed and not any(p.startswith(f"{field}:") for p in problems):
            problems.append(f"{field}: no check confirms this number yet")
    ok = not problems and all(f in confirmed for f in required)
    return ok, problems, confirmed, unreachable


def publish(data: dict, out_dir: Path) -> None:
    """Writes the app's feed: requirements.json plus a tiny manifest with its sha256."""
    out_dir.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(data, indent=2, sort_keys=False) + "\n").encode()
    (out_dir / "requirements.json").write_bytes(body)
    manifest = {
        "schema": 1,
        "checkedOn": today(),
        "requirements": {
            "file": "requirements.json",
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "count": len(data["templates"]),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default="rules/requirements.json")
    ap.add_argument("--checks", default="rules/checks.yml")
    ap.add_argument("--status", default="rules/status.json")
    ap.add_argument("--out-dir", default="docs")
    ap.add_argument("--report", default="verify-report.md")
    ap.add_argument("--text-dir", default="rules/source-text")
    args = ap.parse_args()
    global TEXT_DIR
    TEXT_DIR = Path(args.text_dir) if args.text_dir else None

    data = json.loads(Path(args.rules).read_text())
    specs = yaml.safe_load(Path(args.checks).read_text())
    by_key = {f"{t['state']} {t['trade']}": t for t in data["templates"]}
    previous = json.loads(Path(args.status).read_text()).get("sets", {}) if Path(args.status).exists() else {}

    status: dict = {"generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "sets": {}}
    lines, downgraded = [], []
    for spec in specs:
        t = by_key.get(spec["set"])
        if t is None:
            raise SystemExit(f"checks.yml: no template '{spec['set']}' in requirements.json")
        ok, problems, confirmed, unreachable = check_template(t, spec)
        prev = previous.get(t["id"], {})
        checked_at = prev.get("checkedAt")
        if ok:
            new_status, checked_at = "verified", today()
        elif unreachable and prev.get("status") == "verified" and checked_at and \
                datetime.fromisoformat(checked_at) >= datetime.now() - timedelta(days=GRACE_DAYS):
            new_status = "verified"
        else:
            new_status = "needs_review"
        if prev.get("status") == "verified" and new_status != "verified":
            downgraded.append(spec["set"])
        status["sets"][t["id"]] = {
            "set": spec["set"], "status": new_status,
            "checkedAt": checked_at if new_status == "verified" else None,
            "lastAttempt": today(), "sources": spec["sources"], "confirmed": confirmed, "problems": problems,
        }
        mark = "OK " if new_status == "verified" else "!! "
        lines.append(f"- {mark}**{spec['set']}**: {new_status}" + "".join(f"\n  - {p}" for p in problems))

    for key, t in by_key.items():
        if t["id"] not in status["sets"]:
            status["sets"][t["id"]] = {"set": key, "status": "needs_review", "checkedAt": None, "lastAttempt": today(),
                                       "sources": [], "confirmed": [], "problems": ["no checks written for this template"]}
            lines.append(f"- !! **{key}**: needs_review\n  - no checks written for this template")

    Path(args.status).write_text(json.dumps(status, indent=2) + "\n")

    for t in data["templates"]:
        s = status["sets"][t["id"]]
        t["verified"] = s["status"] == "verified"
        t["verifiedAt"] = s["checkedAt"]
        if s["sources"]:
            t["sourceURLs"] = s["sources"]
    data["updatedOn"] = today()
    publish(data, Path(args.out_dir))

    verified = sum(1 for s in status["sets"].values() if s["status"] == "verified")
    report = [f"# Requirement recheck {today()}", "",
              f"{verified} of {len(status['sets'])} state templates confirmed against the official pages.", "", *lines]
    if downgraded:
        report[2:2] = ["**Lost verified status:** " + ", ".join(downgraded) +
                       ". The app shows these as unconfirmed until requirements.json or checks.yml is fixed.", ""]
    Path(args.report).write_text("\n".join(report) + "\n")
    print("\n".join(report))

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"needs_issue={'true' if downgraded else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
