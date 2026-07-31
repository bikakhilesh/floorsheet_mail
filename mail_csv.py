#!/usr/bin/env python3
"""
mail_csv.py — send the raw floor sheet with a short text summary.

This is the data mail: it goes out as soon as the scrape lands, carries the csv
and its parquet copy, and does not touch charts, the site or the dashboard. It
therefore needs neither matplotlib nor the archive, so it cannot be delayed or
broken by anything on the analysis side.

    python mail_csv.py --data data/2026.07.31.csv --parquet out/f.parquet

Recipients come from MAIL_TO_CSV, falling back to MAIL_TO so an existing setup
keeps working before the new secret is added.
"""

from __future__ import annotations

import argparse
import gzip
import mimetypes
import os
import shutil
import smtplib
import ssl
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import floorsheet_viz as fv

NAVY, GOLD, GREY, INK, LIGHT = "#0B2545", "#C9A227", "#8A94A6", "#1C2331", "#EEF1F6"


def creds(to_env: str) -> tuple[str, str, str, list[str]]:
    user = os.environ.get("GMAIL_USER") or os.environ.get("SMTP_USER", "")
    pw = os.environ.get("GMAIL_APP_PW") or os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("MAIL_FROM") or user
    raw = os.environ.get(to_env) or os.environ.get("MAIL_TO", "")
    return user, pw, sender, [x.strip() for x in raw.split(",") if x.strip()]


def summary_text(a: fv.Analytics, files: list[str]) -> str:
    k = a.kpi
    out = [
        f"NEPSE Floor Sheet — {a.date}",
        "=" * 44,
        f"Turnover   : {fv.npr(k['turnover'])}",
        f"Volume     : {fv.qty(k['volume'])} shares",
        f"Trades     : {fv.qty(k['trades'])}",
        f"Scrips     : {k['scrips']}    Brokers: {k['brokers']}",
        f"Avg ticket : {fv.npr(k['avg_ticket'])}  (median {fv.npr(k['median_ticket'])})",
        f"Cross      : {fv.npr(k['cross_amt'])} ({k['cross_pct']:.1f}% of turnover)",
        "",
        "Top 5 scrips by turnover",
    ]
    for sym, r in a.scrip.head(5).iterrows():
        out.append(f"  {sym:<10} {fv.npr(r['turnover']):>13}  VWAP {r['vwap']:>9,.1f}")
    out += ["", "Top 5 brokers by gross turnover"]
    for code, r in a.broker.head(5).iterrows():
        out.append(f"  {fv.bl(code, False):<20} {fv.npr(r['gross']):>13}  "
                   f"net {fv.npr(r['net']):>13}")
    out += ["", "Attached: " + ", ".join(os.path.basename(f) for f in files)]
    return "\n".join(out)


def summary_html(a: fv.Analytics) -> str:
    k = a.kpi
    tiles = "".join(
        f'<td style="background:{LIGHT};border:1px solid #DDE3ED;padding:8px 4px;'
        f'text-align:center"><div style="font:700 15px Calibri,Arial;color:{NAVY}">'
        f'{v}</div><div style="font:10px Calibri,Arial;color:{GREY};'
        f'text-transform:uppercase">{l}</div></td>'
        for l, v in [("Turnover", fv.npr(k["turnover"])),
                     ("Trades", fv.qty(k["trades"])),
                     ("Volume", fv.qty(k["volume"])),
                     ("Scrips", k["scrips"]),
                     ("Brokers", k["brokers"]),
                     ("Cross", f"{k['cross_pct']:.1f}%")])

    def table(title, rows, head):
        th = "".join(f'<th style="background:{NAVY};color:#fff;text-align:left;'
                     f'padding:6px 8px;font:600 12px Calibri,Arial">{h}</th>'
                     for h in head)
        tr = "".join(
            "<tr>" + "".join(
                f'<td style="padding:5px 8px;border-bottom:1px solid #E6EAF2;'
                f'background:{"#FAFBFD" if i % 2 else "#fff"};'
                f'font:12px Calibri,Arial">{c}</td>' for c in r) + "</tr>"
            for i, r in enumerate(rows))
        return (f'<h3 style="font:600 14px Calibri,Arial;color:{NAVY};'
                f'border-left:4px solid {GOLD};padding-left:8px;margin:16px 0 4px">'
                f'{title}</h3><table cellpadding="0" cellspacing="0" width="100%" '
                f'style="border-collapse:collapse">{th and "<tr>" + th + "</tr>"}'
                f'{tr}</table>')

    scrips = [(s, fv.npr(r["turnover"]), f"{r['vwap']:,.1f}", f"{r['range_pct']:.1f}%")
              for s, r in a.scrip.head(8).iterrows()]
    brokers = [(fv.bl(c, False), fv.npr(r["buy"] if "buy" in r else r["buy_amt"]),
                fv.npr(r["sell_amt"]), fv.npr(r["net"]))
               for c, r in a.broker.head(8).iterrows()]
    return f"""<html><body style="margin:0;background:#F7F9FC">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table width="680" cellpadding="0" cellspacing="0" style="background:#fff;padding:20px">
<tr><td style="border-bottom:3px solid {GOLD};padding-bottom:10px">
 <div style="font:700 20px Calibri,Arial;color:{NAVY}">NEPSE Floor Sheet</div>
 <div style="font:13px Calibri,Arial;color:{GREY}">Trading day {a.date} · raw data</div>
</td></tr>
<tr><td style="padding-top:12px"><table width="100%" cellspacing="4"><tr>{tiles}</tr></table></td></tr>
<tr><td>{table("Top scrips", scrips, ["Scrip", "Turnover", "VWAP", "Range"])}
{table("Top brokers", brokers, ["Broker", "Buy", "Sell", "Net"])}</td></tr>
<tr><td style="border-top:1px solid #E1E6EF;padding-top:10px;font:11px Calibri,Arial;
 color:{GREY}">The floor sheet is attached as csv and parquet. Charts and the
 interactive dashboard arrive separately.</td></tr>
</table></td></tr></table></body></html>"""


def attach(msg: MIMEMultipart, path: str) -> None:
    ctype, _ = mimetypes.guess_type(path)
    maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
    with open(path, "rb") as f:
        part = MIMEBase(maintype, subtype)
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment",
                    filename=os.path.basename(path))
    msg.attach(part)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mail the raw floor sheet")
    ap.add_argument("--data", required=True, help="the scraped csv (or parquet)")
    ap.add_argument("--parquet", default=None, help="parquet copy to attach")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--date", default=None)
    ap.add_argument("--tag", default=os.environ.get("SCRAPE_STATUS") or None)
    ap.add_argument("--to-env", default="MAIL_TO_CSV",
                    help="env var holding the recipients")
    ap.add_argument("--gzip", action="store_true",
                    help="gzip the csv before attaching (~4x smaller)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)
    df = fv.load_floorsheet(args.data)
    date_str = args.date or fv.derive_trade_date(df) or fv.infer_date(args.data)
    a = fv.build_analytics(df, date_str)

    files = []
    src = args.data
    if args.gzip and not src.lower().endswith(".gz"):
        gz = os.path.join(args.outdir, os.path.basename(src) + ".gz")
        with open(src, "rb") as fi, gzip.open(gz, "wb", 6) as fo:
            shutil.copyfileobj(fi, fo)
        src = gz
    files.append(src)
    pq = args.parquet
    if pq and os.path.exists(pq):
        files.append(pq)

    user, pw, sender, to = creds(args.to_env)

    msg = MIMEMultipart("mixed")
    prefix = f"[{args.tag}] " if args.tag and args.tag.upper() != "OK" else ""
    msg["Subject"] = (f"{prefix}NEPSE Floor Sheet Data — {date_str} | "
                      f"{a.kpi['trades']:,} rows | T/O {fv.npr(a.kpi['turnover'])}")
    msg["From"] = sender or "noreply@example.com"
    msg["To"] = ", ".join(to or ["test@example.com"])
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="floorsheet.local")

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(summary_text(a, files), "plain", "utf-8"))
    alt.attach(MIMEText(summary_html(a), "html", "utf-8"))
    msg.attach(alt)
    for f in files:
        attach(msg, f)

    size = len(msg.as_bytes()) / 1e6
    print(f"Data mail: {date_str} | {len(df):,} rows | {size:.1f} MB | "
          f"attachments: {', '.join(os.path.basename(f) for f in files)}")

    if args.dry_run:
        p = os.path.join(args.outdir, "data_message.eml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(msg.as_string())
        print(f"Dry run — wrote {p}")
        return 0

    if not (user and pw and to):
        print(f"GMAIL_USER/GMAIL_APP_PW and {args.to_env} (or MAIL_TO) must be set.",
              file=sys.stderr)
        return 2

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, pw)
            s.sendmail(sender, to, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(user, pw)
            s.sendmail(sender, to, msg.as_string())
    print(f"Sent to {len(to)} recipient(s) from {args.to_env}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
