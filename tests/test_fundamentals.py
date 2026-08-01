#!/usr/bin/env python3
"""
tests/test_fundamentals.py — the name-to-symbol join, and the parsing quirks.

    python tests/test_fundamentals.py

The join is the part that can be wrong without anything looking wrong. Six of
the fifteen names that fuzzy matching placed at >=0.80 were different
institutions — Sana Kisan onto Gandaki, Swabalamban onto Salapa, National
Microfinance onto NMB Microfinance. So the invariant here is not "did most rows
match" but "is every row either exact, declared in the alias file, or reported
unmatched", and that no similarity threshold exists anywhere in the module.
"""

from __future__ import annotations

import inspect
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fundamentals as fm     # noqa: E402
import sector_map as sm       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "reference", "fundamentals.csv")

FAILS = 0


def ok(cond, label, extra=""):
    global FAILS
    if cond:
        print(f"  ok   {label}")
    else:
        FAILS += 1
        print(f"  FAIL {label}  {extra}")


def code_only(src: str) -> str:
    """Source with comments and string literals removed."""
    import io
    import tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def main() -> int:
    print("\njoin")
    listed = sm.load(os.path.join(ROOT, "reference", "listed_securities.csv"))
    raw = pd.read_csv(DUMP, dtype=str, keep_default_na=False)
    df = fm.derive(fm.canonicalise(raw))
    alias = fm.load_alias(os.path.join(ROOT, "reference",
                                       "fundamentals_alias.csv"))
    res = fm.resolve(df, listed, alias)

    n = len(res)
    how = res["_how"].value_counts().to_dict()
    ok(how.get("unmatched", 0) == 0,
       f"every one of {n} vendor rows resolves to a symbol",
       f"unmatched: {sorted(res.loc[res._how == 'unmatched', 'Company'])[:5]}")
    ok(how.get("exact", 0) + how.get("alias", 0) == n, "and by exact or alias only")
    ok(res["Symbol"].is_unique, "no two vendor rows claim the same symbol",
       f"dupes: {res.loc[res.Symbol.duplicated(keep=False), 'Symbol'].tolist()[:6]}")
    print(f"       {how.get('exact', 0)} exact · {how.get('alias', 0)} alias")

    # Every alias must point at a live equity, or it is a typo waiting to
    # silently drop a company.
    bad = [(k, v) for k, v in alias.items() if v not in listed.index]
    ok(not bad, "every alias target exists in the register", str(bad[:4]))
    noneq = [(k, v) for k, v in alias.items()
             if v in listed.index and listed.at[v, "Instrument"] != "Equity"]
    ok(not noneq, "and every alias target is an equity", str(noneq[:4]))

    print("\nno fuzzy matching anywhere")
    # Strings and comments are stripped first: the module docstring says the
    # word "fuzzy" a dozen times explaining why there is none, and a test that
    # cannot tell prose from code would fail on its own explanation.
    src = code_only(inspect.getsource(fm))
    for tok in ("difflib", "get_close_matches", "SequenceMatcher", "rapidfuzz",
                "fuzz", "levenshtein", "cutoff"):
        ok(tok.lower() not in src.lower(),
           f"no {tok!r} in executable code")
    ok("fuzzy" in inspect.getsource(fm).lower(),
       "and the docstring still explains why not")

    print("\nparsing")
    d = fm.load(DUMP, os.path.join(ROOT, "reference", "listed_securities.csv"))
    ok(len(d) == n, f"{n} symbols survive the load")
    ok(d.index.name == "Symbol" or d.index.is_unique, "indexed by symbol")

    # 0.00 means "not reported" on fundamentals, but a genuine unchanged
    # session on price-change columns. Both halves matter.
    ok((d["PE (D)"] == 0).sum() == 0, "zero P/E became missing")
    ok((d["Bookvalue"] == 0).sum() == 0, "zero book value became missing")
    chg = d["1 Wk Price Chg %"]
    ok(chg.notna().sum() > 0, "price-change columns survived parsing")

    probe = pd.DataFrame({"Company": ["X"], "Latest Close": ["1,234.50"],
                          "Estimated Risk Return Trade Off [Wk, 1Y]": ["11950.00%"],
                          "1 Wk Price Chg %": ["-12.81%"]})
    c = fm.canonicalise(probe)
    ok(np.isclose(c["Latest Close"].iloc[0], 1234.50), "thousands separators parse")
    ok(np.isclose(c["1 Wk Price Chg %"].iloc[0], -12.81),
       "percentages parse as plain numbers")
    rr = [x for x in c.columns if "Risk Return" in x][0]
    ok(np.isclose(c[rr].iloc[0], 119.50),
       "the risk-return trade-off is a ratio, not a percentage",
       f"got {c[rr].iloc[0]}")
    ok("%" not in rr, "and the % is dropped from its header")

    print("\nderived share count")
    s = d["shares"]
    rt = (s * d["Latest Close"] - d["Market Cap"]).abs() / d["Market Cap"]
    ok(rt.max() < 1e-9, "shares x close round-trips to market cap",
       f"max rel err {rt.max():.2e}")
    # Paidup/par disagrees for about a quarter of the board; that is why shares
    # are derived from market cap instead. Assert the disagreement is real so
    # nobody "simplifies" this later.
    paid = d["Paidup Capital"] * 1000 / 100
    off = ((s / paid - 1).abs() > 0.01).sum()
    ok(off > 40, f"paidup-derived shares disagree on {off} names — do not use them")

    print("\n" + (f"{FAILS} FAILURE(S)" if FAILS else "all assertions passed"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
