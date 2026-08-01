#!/usr/bin/env python3
"""
get_listed_securities.py — scrape NEPSE's listed-securities table and keep
`reference/listed_securities.csv` current.

    python get_listed_securities.py                       # scrape, diff, write
    python get_listed_securities.py --check               # scrape and diff only
    python get_listed_securities.py --from-csv old.csv    # diff a file, no browser

This is reference data, not market data, so it lives on `main` next to the code
rather than on the `data` branch: it is small, it is worth versioning, and the
git history doubles as a listing calendar — `git log -p reference/` tells you
when a symbol appeared and when a sector was reclassified.

The file is only rewritten when something actually changed. NEPSE renumbers the
table's `SN` column on every request and the scraper walks the instrument and
status filters in a fixed order, so a naive dump differs from yesterday's on a
day nothing happened. `sector_map.canonicalise` strips the volatile columns and
sorts by symbol; what is left is a diff you can read.

Exit codes
    0   ran; see `changed` / `material` in the output for what to do next
    2   the scrape came back implausibly small — nothing written, look at it
    3   the browser never started
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import traceback
from io import StringIO

import pandas as pd

import sector_map as sm

BASE_URL = "https://www.nepalstock.com/company"
PROMOTER_URL = "https://www.nepalstock.com/promoter-share"

# Values are the site's own <option> values. "" is "All Instrument", which
# catches anything NEPSE adds an instrument type for without telling anyone.
INSTRUMENT_OPTIONS = ["Equity", "", "Mutual Funds",
                      "Non-Convertible Debentures", "Preference Shares"]
STATUS_OPTIONS = ["A", "S", "D"]          # Active / Suspended / Delisted

# The promoter register is a separate page and behaves nothing like /company:
# no filter dropdowns, no page-size control, just ngx-pagination over ~15 pages
# of 20. Its columns are SN, Name, Symbol, Status, Sector Name, Email, Security
# Name, Website — so the sector is stated outright and nothing has to be
# inferred from the symbol. Which is just as well: the suffix is not consistent
# (MBLPO but NABILP, KBLPO but SHINEP, and a few with neither), so any
# strip-the-suffix-and-look-up-the-parent scheme would be wrong about half the
# time. Status here is Active / Inactive, not the company page's vocabulary.
PROMOTER_NEXT_CSS = "li.pagination-next:not(.disabled) a"
PROMOTER_ROW_CSS = "table tbody tr"
PROMOTER_SYM_CSS = "table tbody tr td:nth-child(3)"
MAX_PROMOTER_PAGES = 60

STATUS_XPATH = ("/html/body/app-root/div/main/div/app-company/div/div[2]/div/"
                "div[2]/select")
INSTRUMENT_XPATH = ("/html/body/app-root/div/main/div/app-company/div/div[2]/div/"
                    "div[4]/select")
ITEMS_PER_PAGE_XPATH = ("/html/body/app-root/div/main/div/app-company/div/div[2]/"
                        "div/div[5]/div/select")
FILTER_BUTTON_XPATH = "//button[contains(@class,'box__filter--search')]"

# Sanity gates. A scrape that trips one of these is a broken page, not a market
# event — NEPSE does not delist a fifth of the board overnight. Writing it would
# silently blank the sector map for every dashboard that reads the file.
#
# The two pages are gated separately as well as together: if the promoter page
# breaks while /company is fine, the combined row count still looks plausible
# and only the per-source floor catches it.
MIN_TOTAL_ROWS = 300
MIN_COMPANY_ROWS = 300
MIN_PROMOTER_ROWS = 150
MIN_ACTIVE_RETENTION = 0.95    # share of currently-active symbols that must survive
MIN_ROW_RETENTION = 0.80       # share of all known symbols that must survive


# ────────────────────────────────────────────────────────────────────────────
# Browser
# ────────────────────────────────────────────────────────────────────────────
def init_driver(headless: bool = True):
    """Chrome via Selenium Manager — the driver resolves itself from selenium
    4.6 up, which is why `webdriver_manager` is not a dependency here. On the
    Actions runner Chrome comes from `browser-actions/setup-chrome`."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    o = Options()
    if headless:
        o.add_argument("--headless=new")
    o.add_argument("--window-size=1920,1080")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")     # small /dev/shm on CI runners
    o.add_argument("--disable-gpu")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    try:
        return webdriver.Chrome(options=o)
    except Exception as e:                        # noqa: BLE001
        print(f"Chrome would not start: {e}", file=sys.stderr)
        return None


def js_select(driver, wait, xpath, value, retries=3) -> bool:
    """Set a <select> through JavaScript and fire a native change event.

    Selenium's `Select` clicks, and the site is an Angular app that re-renders
    after every interaction — clicking gets you `ElementClickIntercepted` from
    the custom overlay and `StaleElementReference` from the re-render. Setting
    `.value` and dispatching the event does the same job, and re-locating the
    element on every attempt handles the staleness.
    """
    from selenium.common.exceptions import (StaleElementReferenceException,
                                            TimeoutException)
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    last = None
    for _ in range(retries):
        try:
            el = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            driver.execute_script(
                "const e=arguments[0];e.value=arguments[1];"
                "e.dispatchEvent(new Event('change',{bubbles:true}));"
                "e.dispatchEvent(new Event('input',{bubbles:true}));", el, value)
            return True
        except (StaleElementReferenceException, TimeoutException) as e:
            last = e
            time.sleep(0.7)
    print(f"  warn: could not set {xpath} to {value!r}: {last}")
    return False


def max_page_size(driver, wait) -> None:
    from selenium.common.exceptions import (StaleElementReferenceException,
                                            TimeoutException)
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import Select

    for _ in range(3):
        try:
            el = wait.until(EC.presence_of_element_located(
                (By.XPATH, ITEMS_PER_PAGE_XPATH)))
            vals = [o.get_attribute("value") for o in Select(el).options]
            nums = sorted((int(v) for v in vals if v and v.isdigit()), reverse=True)
            if nums:
                js_select(driver, wait, ITEMS_PER_PAGE_XPATH, str(nums[0]))
            time.sleep(1.5)
            return
        except (StaleElementReferenceException, TimeoutException):
            time.sleep(0.7)
    print("  warn: could not max out the page size")


def click_filter(driver, wait, retries=3) -> bool:
    from selenium.common.exceptions import (ElementClickInterceptedException,
                                            StaleElementReferenceException,
                                            TimeoutException)
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    last = None
    for _ in range(retries):
        try:
            btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, FILTER_BUTTON_XPATH)))
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(2.5)
            return True
        except (StaleElementReferenceException, ElementClickInterceptedException,
                TimeoutException) as e:
            last = e
            time.sleep(0.7)
    print(f"  warn: could not click Filter: {last}")
    return False


def scrape_combo(driver, wait, instrument: str, status: str) -> pd.DataFrame:
    js_select(driver, wait, STATUS_XPATH, status)
    time.sleep(0.6)
    js_select(driver, wait, INSTRUMENT_XPATH, instrument)
    time.sleep(0.6)
    click_filter(driver, wait)
    max_page_size(driver, wait)          # filtering resets pagination
    try:
        frames = pd.read_html(StringIO(driver.page_source))
    except ValueError:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def scrape_company(driver, wait) -> pd.DataFrame:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    driver.get(BASE_URL)
    wait.until(EC.presence_of_element_located((By.XPATH, INSTRUMENT_XPATH)))
    time.sleep(2)                        # let Angular finish the first render

    frames = []
    for inst in INSTRUMENT_OPTIONS:
        for st in STATUS_OPTIONS:
            label = f"{inst or 'All Instrument'}/{st}"
            try:
                df = scrape_combo(driver, wait, inst, st)
                print(f"  {label:<34} {len(df):>4} rows")
                if not df.empty:
                    frames.append(df)
            except Exception:            # noqa: BLE001 — one bad combo is not fatal
                print(f"  {label:<34} FAILED")
                traceback.print_exc()
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _first_symbol(driver) -> str:
    """The current page's first symbol, read in a single round trip.

    `find_elements` then `.text` is two calls, and the gap between them is
    precisely the moment this function exists to detect: Angular swaps the
    tbody, the handle dies, StaleElementReferenceException. Doing the query and
    the read in one `execute_script` leaves no handle to go stale — the same
    move `js_select` makes above, for the same reason.
    """
    return driver.execute_script(
        "const c = document.querySelector(arguments[0]);"
        "return c ? c.textContent.trim() : '';", PROMOTER_SYM_CSS) or ""


def _click_next(driver) -> bool:
    """Advance one page. False when there is no next link, i.e. the last page.

    Same two-round-trip hazard as `_first_symbol`: locating the anchor and then
    clicking it are separate calls, and a re-render in between kills the handle.
    Query and click together.
    """
    return bool(driver.execute_script(
        "const a = document.querySelector(arguments[0]);"
        "if (!a) return false; a.click(); return true;", PROMOTER_NEXT_CSS))


def _walk_promoter(driver, wait) -> pd.DataFrame:
    """One complete walk of the promoter register, or an exception.

    Every early exit raises rather than returning what it has. A walk that gives
    up at page eight still yields ~160 rows, which clears MIN_PROMOTER_ROWS and
    would quietly commit a truncated register — the same failure the caller is
    careful to avoid on the exception path, arriving through the success path
    instead. The only clean ending is running out of pages.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    driver.get(PROMOTER_URL)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, PROMOTER_ROW_CSS)))
    time.sleep(1.5)

    frames, pages, complete = [], 0, False
    for _ in range(MAX_PROMOTER_PAGES):
        before = _first_symbol(driver)
        try:
            frames.append(pd.concat(pd.read_html(StringIO(driver.page_source)),
                                    ignore_index=True))
            pages += 1
        except ValueError as e:
            raise RuntimeError(f"no table on promoter page {pages + 1}") from e

        if not _click_next(driver):
            complete = True                          # ran out of pages: done
            break

        for _ in range(40):                          # up to 10s for the re-render
            time.sleep(0.25)
            now = _first_symbol(driver)
            if now and now != before:
                break
        else:
            raise RuntimeError(
                f"promoter page {pages} never advanced past {before!r}")

    if not complete:
        raise RuntimeError(f"promoter walk hit the {MAX_PROMOTER_PAGES}-page cap "
                           f"without reaching the end")
    if not frames:
        raise RuntimeError("promoter walk produced no rows")

    out = pd.concat(frames, ignore_index=True)
    # Normalise here rather than after the concat with /company: this page calls
    # the sector column "Sector Name", and letting both spellings reach the
    # combined frame would end in two columns sharing one label.
    out = out.rename(columns={"Sector Name": "Sector"})
    out = out.drop(columns=[c for c in ("Security Name",) if c in out.columns])
    # No instrument column on this page — every row on it is a promoter share.
    out["Instrument"] = sm.PROMOTER
    print(f"  {'promoter register':<34} {len(out):>4} rows over {pages} page(s)")
    return out


def scrape_promoter(driver, wait, attempts: int = 3) -> pd.DataFrame:
    """Retry the walk from the top rather than salvaging a partial one.

    The failure this guards against is a re-render landing in the wrong
    millisecond, which is uncorrelated between attempts — a coin flip becomes a
    near-certainty across three. Restarting from page one is cheap (about twenty
    seconds) and is the only way to get a complete register once a walk has been
    interrupted.
    """
    last = None
    for i in range(1, attempts + 1):
        try:
            return _walk_promoter(driver, wait)
        except Exception as e:                       # noqa: BLE001 — retried
            last = e
            print(f"  promoter attempt {i}/{attempts} failed: {e}")
            if i < attempts:
                time.sleep(2.0)
    raise RuntimeError(f"promoter register failed {attempts} times; last: {last}")


def scrape(headless: bool = True) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    from selenium.webdriver.support.ui import WebDriverWait

    driver = init_driver(headless)
    if driver is None:
        return None

    wait = WebDriverWait(driver, 20)
    try:
        company = scrape_company(driver, wait)
        try:
            promoter = scrape_promoter(driver, wait)
        except Exception:                # noqa: BLE001 — gated below, not here
            print("  promoter register FAILED")
            traceback.print_exc()
            promoter = pd.DataFrame()
        if company.empty and promoter.empty:
            return None
        return company, promoter
    finally:
        driver.quit()


# ────────────────────────────────────────────────────────────────────────────
# Gates, output
# ────────────────────────────────────────────────────────────────────────────
def gate(new: pd.DataFrame, old: pd.DataFrame | None,
         n_company: int | None = None,
         n_promoter: int | None = None) -> tuple[bool, str]:
    # Per-source first. If the promoter page breaks while /company is fine, the
    # combined count still looks plausible and only this catches it.
    if n_company is not None and n_company < MIN_COMPANY_ROWS:
        return False, (f"the company page returned {n_company} rows, floor is "
                       f"{MIN_COMPANY_ROWS}")
    if n_promoter is not None and n_promoter < MIN_PROMOTER_ROWS:
        return False, (f"the promoter register returned {n_promoter} rows, floor "
                       f"is {MIN_PROMOTER_ROWS}")
    if len(new) < MIN_TOTAL_ROWS:
        return False, (f"only {len(new)} rows scraped, floor is {MIN_TOTAL_ROWS} "
                       f"— the page almost certainly did not load")
    if old is None or old.empty:
        return True, "no previous file, nothing to compare against"

    keep = len(set(new["Symbol"]) & set(old["Symbol"])) / len(old)
    if keep < MIN_ROW_RETENTION:
        return False, (f"only {keep:.0%} of the {len(old)} known symbols came "
                       f"back, floor is {MIN_ROW_RETENTION:.0%}")

    act = old[old["Status"] == "Active"]["Symbol"]
    if len(act):
        akeep = len(set(new["Symbol"]) & set(act)) / len(act)
        if akeep < MIN_ACTIVE_RETENTION:
            return False, (f"only {akeep:.0%} of the {len(act)} active symbols "
                           f"came back, floor is {MIN_ACTIVE_RETENTION:.0%}")
    return True, "retention checks passed"


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


def prepend_changelog(path: str, block: str, stamp: str) -> None:
    head = ("# Listed securities changelog\n\n"
            "Written by `get_listed_securities.py`. Newest first.\n")
    entry = f"\n## {stamp}\n\n{block}\n"
    old = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        old = old[len(head):] if old.startswith(head) else "\n" + old
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + entry + old)


# ────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Scrape NEPSE listed securities and update the sector map")
    ap.add_argument("--out", default=sm.DEFAULT_PATH)
    ap.add_argument("--changelog", default=os.path.join("reference",
                                                        "listed_changelog.md"))
    ap.add_argument("--from-csv", default=None,
                    help="skip the browser and treat this csv as the /company "
                         "scrape (for testing the diff path)")
    ap.add_argument("--from-promoter-csv", default=None,
                    help="likewise for the promoter register; Instrument is "
                         "filled in for you")
    ap.add_argument("--check", action="store_true",
                    help="report the diff, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="write even if the sanity gates fail")
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args(argv)

    print(dt.datetime.now(dt.timezone.utc).strftime(
        "get_listed_securities  %Y-%m-%d %H:%M UTC"))

    n_company = n_promoter = None
    if args.from_csv:
        parts = [pd.read_csv(args.from_csv, dtype=str, keep_default_na=False)]
        if args.from_promoter_csv:
            p = pd.read_csv(args.from_promoter_csv, dtype=str,
                            keep_default_na=False)
            p = p.rename(columns={"Sector Name": "Sector"})
            p["Instrument"] = sm.PROMOTER
            parts.append(p)
        raw = pd.concat(parts, ignore_index=True)
    else:
        got = scrape(headless=not args.no_headless)
        if got is None:
            print("Nothing scraped.", file=sys.stderr)
            gh_output(changed="false", material="false", ok="false")
            return 3
        company, promoter = got
        n_company, n_promoter = len(company), len(promoter)
        raw = pd.concat([company, promoter], ignore_index=True)

    new = sm.canonicalise(raw)
    print(f"\n{len(raw):,} raw rows -> {len(new):,} unique securities")
    counts = new["Instrument"].value_counts()
    for inst, n in counts.items():
        print(f"    {inst:<30} {n:>4}")

    old = None
    if os.path.exists(args.out):
        old = sm.canonicalise(pd.read_csv(args.out, dtype=str,
                                          keep_default_na=False))

    ok, why = gate(new, old, n_company, n_promoter)
    print(f"Sanity: {'ok' if ok else 'FAILED'} — {why}")
    if not ok and not args.force:
        gh_output(changed="false", material="false", ok="false")
        gh_summary(f"### Listed securities\n\n:x: Scrape rejected — {why}. "
                   f"`{args.out}` left untouched.")
        return 2

    if old is None:
        d = {"added": sorted(new["Symbol"]), "removed": [], "changed": [],
             "cosmetic": 0, "material": True, "any": True}
        block = f"_First build: {len(new)} securities._\n"
    else:
        d = sm.diff(old, new)
        block = sm.format_diff(d, new)

    counts = (f"{len(d['added'])} added · {len(d['removed'])} removed · "
              f"{len(d['changed'])} reclassified · {d['cosmetic']} contact")
    print(f"Diff: {counts}")
    if d["material"] and old is not None:
        print()
        print(block)

    gh_output(changed=str(d["any"]).lower(), material=str(d["material"]).lower(),
              ok="true", added=len(d["added"]), removed=len(d["removed"]),
              reclassified=len(d["changed"]), total=len(new))
    gh_summary(f"### Listed securities\n\n{len(new)} securities · {counts}\n\n"
               + (block if d["material"] else ""))

    if args.check:
        print("\n--check: nothing written.")
        return 0

    if not d["any"]:
        print(f"\n{args.out} already current — not rewritten.")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    # LF through the handle rather than to_csv(lineterminator=...), which only
    # exists from pandas 1.5. This runs on the Actions runner against pandas 2.x
    # and on a laptop against whatever is installed, and a line-ending flip would
    # rewrite all 641 rows in the diff.
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        new.to_csv(fh, index=False)
    print(f"\nWrote {args.out} ({len(new)} rows, "
          f"{os.path.getsize(args.out) / 1024:,.0f} KB)")

    if d["material"] and args.changelog:
        prepend_changelog(args.changelog, block,
                          dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
        print(f"Changelog: {args.changelog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
