#!/usr/bin/env python3
"""
tests/test_promoter_walk.py — the promoter pagination walk, without a browser.

    python tests/test_promoter_walk.py

The walk is the part of the scraper that can fail quietly. A run that gives up
at page eight still returns ~160 rows, which clears MIN_PROMOTER_ROWS and would
commit a truncated register while the job goes green. So the invariant under
test is not "did it get some rows" but "did it reach the end of the register" —
every other ending has to raise.

The fake driver models the two things that actually go wrong on the real page:
Angular replacing the tbody between a locate and a read (which is what killed
runs #2 and #3), and a click that lands but never re-renders.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The walk imports By and EC but never drives a browser, so stub the package
# rather than make a browserless unit test depend on selenium being installed.
# Only what _walk_promoter touches is provided; anything else would fail loudly.
if "selenium" not in sys.modules:
    def _mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    _mod("selenium")
    _mod("selenium.webdriver")
    _mod("selenium.webdriver.common")
    _mod("selenium.webdriver.support")
    by = _mod("selenium.webdriver.common.by")
    by.By = type("By", (), {"CSS_SELECTOR": "css selector", "XPATH": "xpath"})
    ec = _mod("selenium.webdriver.support.expected_conditions")
    ec.presence_of_element_located = lambda locator: locator

import get_listed_securities as g   # noqa: E402
import sector_map as sm             # noqa: E402

COLS = ["SN", "Name", "Symbol", "Status", "Sector Name", "Email",
        "Security Name", "Website"]


def page_html(page: int, per: int = 20) -> str:
    head = "".join(f"<th>{c}</th>" for c in COLS)
    rows = ""
    for i in range(per):
        n = page * per + i
        rows += ("<tr>" + "".join(f"<td>{v}</td>" for v in (
            n + 1, f"Company {n}", f"SYM{n:03d}P", "Active",
            "Commercial Banks", "x@y.com", f"Company {n} Promoter", "")) +
            "</tr>")
    return (f"<html><body><table><thead><tr>{head}</tr></thead>"
            f"<tbody>{rows}</tbody></table></body></html>")


class FakeWait:
    def until(self, *_a, **_k):
        return True


class FakeDriver:
    """`pages` pages of 20.

    `stale_at`   the symbol read raises once, the way a dead handle does
    `stall_at`   the click lands but nothing re-renders
    `vanish_at`  the next-link is missing for `vanish_for` consecutive reads,
                 which is the pagination control being mid-render — the failure
                 that shipped a 216-row register
    `pager`      what the pager reports as the last page; None hides it
    """

    def __init__(self, pages=15, stale_at=None, stall_at=None,
                 vanish_at=None, vanish_for=99, pager="real"):
        self.pages, self.page = pages, 0
        self.stale_at, self.stall_at = stale_at, stall_at
        self.vanish_at, self.vanish_for = vanish_at, vanish_for
        self.pager = pages if pager == "real" else pager
        self.clicks = 0
        self._stale_fired = False
        self._vanished = 0

    # -- selenium surface the walk actually uses ---------------------------
    def get(self, _url):
        self.page = 0

    @property
    def page_source(self):
        return page_html(self.page)

    def execute_script(self, script, arg=None):
        if "a.click()" in script:
            return self._click()
        if "textContent || ''" in script or "match(/\\d+/g)" in script:
            return self.pager or 0
        return self._symbol()

    # -- behaviour ---------------------------------------------------------
    def _symbol(self):
        if (self.stale_at is not None and self.page == self.stale_at
                and not self._stale_fired):
            self._stale_fired = True
            # Selenium surfaces this as StaleElementReferenceException; any
            # exception escaping the poll loop has the same effect.
            raise RuntimeError("stale element reference: element is not attached")
        return f"SYM{self.page * 20:03d}P"

    def _click(self):
        if self.page >= self.pages - 1:
            return False                      # no next link on the last page
        if self.vanish_at is not None and self.page == self.vanish_at \
                and self._vanished < self.vanish_for:
            self._vanished += 1
            return False                      # control is mid-render, not absent
        self.clicks += 1
        if self.stall_at is not None and self.page == self.stall_at:
            return True                       # click lands, nothing re-renders
        self.page += 1
        return True


FAILS = 0


def ok(cond, label, extra=""):
    global FAILS
    if cond:
        print(f"  ok   {label}")
    else:
        FAILS += 1
        print(f"  FAIL {label}  {extra}")


def expect_raises(fn, needle, label):
    try:
        fn()
    except Exception as e:                     # noqa: BLE001
        ok(needle.lower() in str(e).lower(), label, f"raised {e!r}")
        return
    ok(False, label, "did not raise")


def main() -> int:
    g.time.sleep = lambda *_: None             # no real waiting in tests
    w = FakeWait()

    print("\ncomplete walk")
    d = FakeDriver(pages=15)
    df = g._walk_promoter(d, w)
    ok(len(df) == 300, "every page collected", f"got {len(df)}")
    ok(d.clicks == 14, "clicked through to the last page", f"got {d.clicks}")
    ok("Instrument" in df.columns and set(df["Instrument"]) == {sm.PROMOTER},
       "instrument synthesised — the page has no such column")
    ok("Sector" in df.columns and "Sector Name" not in df.columns,
       "'Sector Name' normalised before it can collide with /company")
    ok("Security Name" not in df.columns, "the descriptive column is dropped")

    print("\na next-link that vanishes mid-render is not the last page")
    # This is the bug that shipped: one false reading of the pagination control
    # ended the walk at page eleven with 216 rows and no exception.
    d = FakeDriver(pages=15, vanish_at=10, vanish_for=3)
    df = g._walk_promoter(d, w)
    ok(len(df) == 300, "the walk re-checks and carries on", f"got {len(df)}")
    expect_raises(
        lambda: g._walk_promoter(FakeDriver(pages=15, vanish_at=10), w),
        "11 of 15",
        "a permanently missing link is caught by the page count, not the link")

    print("\ntruncation is not allowed to look like success")
    expect_raises(lambda: g._walk_promoter(FakeDriver(pages=15, stall_at=8), w),
                  "never advanced",
                  "a stalled page raises instead of returning ~180 rows")
    expect_raises(lambda: g._walk_promoter(FakeDriver(pages=99), w),
                  "cap", "hitting the page cap raises")
    ok(g._page_count(FakeDriver(pages=15)) == 15, "the pager is read")
    d = FakeDriver(pages=15, pager=None)
    ok(len(g._walk_promoter(d, w)) == 300,
       "an unreadable pager falls back to the next-link rather than failing")

    print("\nstaleness")
    expect_raises(lambda: g._walk_promoter(FakeDriver(pages=15, stale_at=3), w),
                  "stale", "a stale read still escapes a single walk")
    d = FakeDriver(pages=15, stale_at=3)
    df = g.scrape_promoter(d, w, attempts=3)
    ok(len(df) == 300, "and the retry recovers a complete register",
       f"got {len(df)}")

    print("\nretries give up rather than returning a partial register")
    expect_raises(lambda: g.scrape_promoter(FakeDriver(pages=15, stall_at=8), w,
                                            attempts=2),
                  "failed 2 times", "a persistent stall exhausts the attempts")

    print("\nthe gate catches what the walk lets through")
    import pandas as pd
    new = sm.canonicalise(g._walk_promoter(FakeDriver(pages=15), w))
    ok(g.gate(new, None, 641, 300)[0], "a full register passes")
    ok(not g.gate(new, None, 641, 100)[0], "a short register does not")
    ok(not g.gate(new, None, 100, 300)[0], "nor does a short company page")
    ok(isinstance(new, pd.DataFrame) and len(new) == 300, "canonical round trip")

    print("\n" + (f"{FAILS} FAILURE(S)" if FAILS else "all assertions passed"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
