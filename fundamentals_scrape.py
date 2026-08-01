#!/usr/bin/env python3
"""
fundamentals_scrape.py — pull the npstocks comparative table into
`reference/fundamentals.csv`.

    python fundamentals_scrape.py                   # scrape, diff, write
    python fundamentals_scrape.py --check           # scrape and diff only
    python fundamentals_scrape.py --from-csv E.csv  # no browser, test the join

Needs NPSTOCKS_EMAIL and NPSTOCKS_PASSWORD in the environment. On Actions they
come from repository secrets; nothing is ever printed, and the password is not
passed on the command line where it would land in the process table.

Unlike the floor sheet, this is a snapshot, not a session record — yesterday's
fundamentals are superseded, not lost. So it lives on `main` as a single file
that gets overwritten, and the git history is the archive.

The table is behind a login and the row set barely moves day to day, so the
gates are about "did the page actually render" rather than "is this plausible
market data": row and column floors, and a retention check against the symbols
already known. A login that silently lands on an error page produces a table
with zero rows, which is exactly what MIN_ROWS catches.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import traceback

import pandas as pd

import fundamentals as fm

LOGIN_URL = "https://app.npstocks.com/auth/login"
COMPARATIVE_URL = "https://app.npstocks.com/comparative/saved-view?view=1"

# The comparative view carried 385 rows historically and 282 in the latest
# dump; 150 is a floor for "the table rendered", not a market expectation.
MIN_ROWS = 150
MIN_COLS = 20
MIN_RETENTION = 0.90       # share of previously known companies that must return


def init_driver(headless: bool = True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    o = Options()
    if headless:
        o.add_argument("--headless=new")
    o.add_argument("--window-size=1920,1080")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_argument("--lang=en-US,en")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    try:
        return webdriver.Chrome(options=o)
    except Exception as e:                            # noqa: BLE001
        print(f"Chrome would not start: {e}", file=sys.stderr)
        return None


def login(driver, wait, email: str, password: str) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    driver.get(LOGIN_URL)
    f = wait.until(EC.presence_of_element_located((By.ID, "username")))
    f.clear()
    f.send_keys(email)
    p = driver.find_element(By.ID, "password")
    p.clear()
    p.send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    try:
        wait.until(EC.url_changes(LOGIN_URL))
    except Exception as e:                            # noqa: BLE001
        raise RuntimeError(
            "still on the login page after submitting — bad credentials, or "
            "npstocks changed the form") from e
    # Never print the URL blind: it can carry a session token on some flows.
    print(f"  logged in (landed on {driver.current_url.split('?')[0]})")


def grab_table(driver, wait) -> pd.DataFrame:
    """One atomic read of headers and rows.

    Same discipline as the promoter walk: locate-then-read across two round
    trips is what gives you a stale handle on an SPA. Both come back from a
    single execute_script.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    driver.get(COMPARATIVE_URL)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
    wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR,
                                             "table tbody tr")) > 0)
    time.sleep(1.5)                                   # let the last rows paint

    got = driver.execute_script("""
        const head = Array.from(document.querySelectorAll('table thead th'))
                          .map(th => th.innerText.trim());
        const rows = Array.from(document.querySelectorAll('table tbody tr'))
                          .map(tr => Array.from(tr.children)
                                          .map(td => td.innerText.trim()));
        return {head: head, rows: rows};
    """)
    head, rows = got.get("head") or [], got.get("rows") or []
    if not rows:
        raise RuntimeError("table rendered but no data rows came back")
    width = len(rows[0])
    if len(head) != width:
        raise RuntimeError(f"{len(head)} headers for {width} columns — the "
                           f"saved view layout changed")
    ragged = [i for i, r in enumerate(rows) if len(r) != width]
    if ragged:
        raise RuntimeError(f"{len(ragged)} row(s) have the wrong column count "
                           f"(first at index {ragged[0]})")
    return pd.DataFrame(rows, columns=head)


def scrape(headless: bool = True, attempts: int = 3) -> pd.DataFrame:
    email = os.environ.get("NPSTOCKS_EMAIL")
    password = os.environ.get("NPSTOCKS_PASSWORD")
    if not email or not password:
        raise SystemExit(
            "NPSTOCKS_EMAIL and NPSTOCKS_PASSWORD must be set.\n"
            "  local : export them in your shell for this run\n"
            "  CI    : add them as repository secrets")

    from selenium.webdriver.support.ui import WebDriverWait

    last = None
    for i in range(1, attempts + 1):
        driver = init_driver(headless)
        if driver is None:
            raise SystemExit(3)
        try:
            wait = WebDriverWait(driver, 40)
            login(driver, wait, email, password)
            df = grab_table(driver, wait)
            print(f"  comparative table: {len(df)} rows x {len(df.columns)} cols")
            return df
        except Exception as e:                        # noqa: BLE001 — retried
            last = e
            print(f"  attempt {i}/{attempts} failed: {e}")
            if i < attempts:
                time.sleep(10)
        finally:
            driver.quit()
    raise RuntimeError(f"npstocks scrape failed {attempts} times; last: {last}")


# ────────────────────────────────────────────────────────────────────────────
def gate(new: pd.DataFrame, old: pd.DataFrame | None) -> tuple[bool, str]:
    if len(new) < MIN_ROWS:
        return False, f"only {len(new)} rows, floor is {MIN_ROWS}"
    if len(new.columns) < MIN_COLS:
        return False, f"only {len(new.columns)} columns, floor is {MIN_COLS}"
    if "Company" not in new.columns:
        return False, "no Company column — the saved view changed shape"
    if old is None or old.empty:
        return True, "no previous file to compare against"
    keep = len(set(new["Company"]) & set(old["Company"])) / len(old)
    if keep < MIN_RETENTION:
        return False, (f"only {keep:.0%} of the {len(old)} known companies came "
                       f"back, floor is {MIN_RETENTION:.0%}")
    return True, "retention check passed"


def gh_output(**kw) -> None:
    p = os.environ.get("GITHUB_OUTPUT")
    if not p:
        return
    with open(p, "a", encoding="utf-8") as fh:
        for k, v in kw.items():
            fh.write(f"{k}={v}\n")


def gh_summary(md: str) -> None:
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if not p:
        return
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(md + "\n")


def npt_today() -> str:
    """Runners are UTC; NPT is UTC+5:45, so a late job would stamp yesterday."""
    return (dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(hours=5, minutes=45)).strftime("%Y-%m-%d")


# ────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Scrape npstocks fundamentals into the reference file")
    ap.add_argument("--out", default=fm.DEFAULT_PATH)
    ap.add_argument("--from-csv", default=None,
                    help="skip the browser and use this csv as the scrape")
    ap.add_argument("--check", action="store_true", help="write nothing")
    ap.add_argument("--force", action="store_true", help="ignore the gates")
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args(argv)

    print(dt.datetime.now(dt.timezone.utc).strftime(
        "fundamentals_scrape  %Y-%m-%d %H:%M UTC"))

    if args.from_csv:
        new = pd.read_csv(args.from_csv, dtype=str, keep_default_na=False)
        new.columns = [str(c).replace("﻿", "").strip() for c in new.columns]
        key = next((c for c in new.columns
                    if c.lower() in ("company", "company name", "name")), None)
        if key:
            new = new.rename(columns={key: "Company"})
    else:
        new = scrape(headless=not args.no_headless)
        new.columns = [str(c).replace("﻿", "").strip() for c in new.columns]
        key = next((c for c in new.columns
                    if c.lower() in ("company", "company name", "name")), None)
        if key:
            new = new.rename(columns={key: "Company"})

    old = None
    if os.path.exists(args.out):
        old = pd.read_csv(args.out, dtype=str, keep_default_na=False)
        old.columns = [str(c).replace("﻿", "").strip() for c in old.columns]

    ok, why = gate(new, old)
    print(f"Sanity: {'ok' if ok else 'FAILED'} — {why}")
    if not ok and not args.force:
        gh_output(changed="false", ok="false")
        gh_summary(f"### Fundamentals\n\n:x: Scrape rejected — {why}. "
                   f"`{args.out}` left untouched.")
        return 2

    # The join is checked here, not at dashboard build time: a name npstocks
    # renamed is a two-minute fix to the alias file, and it should surface in
    # the run that introduced it rather than three hours later in a rebuild.
    import sector_map as sm
    listed = sm.load()
    joined = fm.resolve(fm.derive(fm.canonicalise(new)), listed, fm.load_alias())
    unmatched = sorted(joined.loc[joined["_how"] == "unmatched", "Company"])
    n_alias = int((joined["_how"] == "alias").sum())
    print(f"Join: {len(joined) - len(unmatched)}/{len(joined)} resolved "
          f"({n_alias} via alias), {len(unmatched)} unmatched")
    for c in unmatched:
        print(f"  unmatched: {c}")

    changed = old is None or not new.equals(old)
    print(f"Diff: {'changed' if changed else 'identical to what is committed'}")

    gh_output(changed=str(changed).lower(), ok="true", rows=len(new),
              unmatched=len(unmatched), aliased=n_alias)
    summary = (f"### Fundamentals\n\n{len(new)} companies · "
               f"{len(joined) - len(unmatched)} joined to symbols · "
               f"{n_alias} via alias · {len(unmatched)} unmatched\n")
    if unmatched:
        summary += ("\n:warning: **Unmatched — add to "
                    "`reference/fundamentals_alias.csv`**\n\n"
                    + "\n".join(f"- `{c}`" for c in unmatched) + "\n")
    gh_summary(summary)

    if args.check:
        print("\n--check: nothing written.")
        return 0
    if not changed:
        print(f"\n{args.out} already current — not rewritten.")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        new.to_csv(fh, index=False)
    print(f"\nWrote {args.out} ({len(new)} rows, "
          f"{os.path.getsize(args.out) / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                                 # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
