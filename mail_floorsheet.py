#!/usr/bin/env python3
"""
mail_floorsheet.py — build the floor sheet analytics pack and mail it.

MIME nesting matters here (this is where inline images silently break):

    multipart/mixed
    ├── multipart/related          <- must be the FIRST child of mixed
    │   ├── multipart/alternative  <- must be the FIRST child of related
    │   │   ├── text/plain
    │   │   └── text/html          <- references cid: images
    │   └── image/png × N          <- Content-ID matches the cid: refs
    └── application/… attachments  <- full report, tables zip

If the images are attached before the alternative part, or the attachments are
added to `related` instead of `mixed`, Gmail and Outlook fall back to showing
everything as a download list.

Mail clients strip <script>, so the interactive dashboard cannot be inlined in
the body and stay interactive. It is attached (opens with full interactivity
from the browser) and, when --pages-url is given, linked with a CTA button.

Env vars (repo secret names from floorsheet_mail, with generic fallbacks):
    GMAIL_USER  / SMTP_USER
    GMAIL_APP_PW / SMTP_PASS
    MAIL_TO     (comma-separated)
    MAIL_FROM   (defaults to the user)
    SMTP_HOST   (default smtp.gmail.com), SMTP_PORT (default 465 = implicit TLS)

Usage:
    python mail_floorsheet.py --csv data/2026.07.30.csv --outdir out --dry-run
    python mail_floorsheet.py --csv data/2026.07.30.csv --outdir out \
        --pages-url https://bikakhilesh.github.io/floorsheet_mail
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import ssl
import sys
import zipfile
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import dashboard_site as ds
import floorsheet_viz as fv
import interactive_report as ir


def creds() -> tuple[str, str, str, list[str]]:
    user = os.environ.get("GMAIL_USER") or os.environ.get("SMTP_USER", "")
    pw = os.environ.get("GMAIL_APP_PW") or os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("MAIL_FROM") or user
    to = [r.strip() for r in os.environ.get("MAIL_TO", "").split(",") if r.strip()]
    return user, pw, sender, to


def plain_text_summary(a: fv.Analytics, dashboard_url: str | None = None) -> str:
    k = a.kpi
    lines = [
        f"NEPSE Floor Sheet Analytics — {a.date}",
        "=" * 46,
        f"Turnover      : {fv.npr(k['turnover'])}",
        f"Volume        : {fv.qty(k['volume'])} shares",
        f"Trades        : {fv.qty(k['trades'])}",
        f"Scrips        : {k['scrips']}   Brokers: {k['brokers']}",
        f"Avg ticket    : {fv.npr(k['avg_ticket'])}  (median {fv.npr(k['median_ticket'])})",
        f"Top-10 broker : {k['top10_broker_pct']:.1f}% of participation",
        f"Cross trades  : {fv.npr(k['cross_amt'])} ({k['cross_pct']:.1f}% of turnover)",
        "",
        "Top 5 brokers by gross turnover:",
    ]
    for code, r in a.broker.head(5).iterrows():
        lines.append(f"  {fv.bl(code, False):<22} gross {fv.npr(r['gross']):>12} "
                     f"net {fv.npr(r['net']):>12}")
    lines += ["", "Top 5 scrips by turnover:"]
    for sym, r in a.scrip.head(5).iterrows():
        lines.append(f"  {sym:<10} {fv.npr(r['turnover']):>12}  "
                     f"VWAP {r['vwap']:>9,.1f}  range {r['range_pct']:.1f}%")
    lines += ["", "Interactive dashboard and the full chart pack are attached."]
    if dashboard_url:
        lines += [f"Online dashboard: {dashboard_url}"]
    return "\n".join(lines)


def zip_dir(src: str, dst: str) -> str:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, os.path.dirname(src)))
    return dst


def build_message(a, body_html: str, images: dict, attachments: list[str],
                  sender: str, recipients: list[str],
                  dashboard_url: str | None = None,
                  tag: str | None = None) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    prefix = f"[{tag}] " if tag and tag.upper() != "OK" else ""
    msg["Subject"] = (f"{prefix}NEPSE Floor Sheet — {a.date} | "
                      f"T/O {fv.npr(a.kpi['turnover'])} | {a.kpi['trades']:,} trades")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="floorsheet.local")

    related = MIMEMultipart("related")
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain_text_summary(a, dashboard_url), "plain", "utf-8"))
    alt.attach(MIMEText(body_html, "html", "utf-8"))
    related.attach(alt)                      # alternative FIRST inside related

    for cid, path in images.items():
        with open(path, "rb") as f:
            img = MIMEImage(f.read(), _subtype="png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline",
                       filename=os.path.basename(path))
        related.attach(img)

    msg.attach(related)                      # related FIRST inside mixed

    for path in attachments:
        if not os.path.exists(path):
            continue
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as f:
            part = MIMEBase(maintype, subtype)
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment",
                        filename=os.path.basename(path))
        msg.attach(part)
    return msg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", "--data", dest="csv", required=True,
                    help="floor sheet: .parquet, .csv, or .csv.gz")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--dpi", type=int, default=120)
    ap.add_argument("--brokers", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--tag", default=os.environ.get("SCRAPE_STATUS") or None,
                    help="status word prefixed to the subject, e.g. MISMATCH")
    ap.add_argument("--pages-url", default=os.environ.get("PAGES_URL") or None,
                    help="GitHub Pages site root; the dashboard link is built from it")
    ap.add_argument("--site", default=None,
                    help="built site directory; enables the multi-day attachment")
    ap.add_argument("--offline-days", type=int, default=22,
                    help="sessions to embed in the attachment (0 = all)")
    ap.add_argument("--no-interactive", action="store_true",
                    help="skip building/attaching the interactive dashboard")
    ap.add_argument("--dry-run", action="store_true",
                    help="build everything and write message.eml, do not send")
    args = ap.parse_args(argv)

    date_guess = args.date or fv.derive_trade_date(
        fv.load_floorsheet(args.csv)) or fv.infer_date(args.csv)
    dash_url = (f"{args.pages_url.rstrip('/')}/reports/floorsheet_{date_guess}.html"
                if args.pages_url else None)

    res = fv.run(args.csv, args.outdir, top=args.top, dpi=args.dpi,
                 broker_map=args.brokers, date_str=args.date,
                 dashboard_url=dash_url)
    a = res["analytics"]

    attachments = [res["html"]]
    if not args.no_interactive:
        out = os.path.join(args.outdir, f"dashboard_{a.date}.html")
        if args.site and os.path.isdir(os.path.join(args.site, "data", "day")):
            # Multi-day copy with the timeline slicer, built from the site data
            interactive = ds.build_offline(args.site, out, args.offline_days)
            note = f"{args.offline_days or 'all'} sessions, timeline"
        else:
            # No site data available — fall back to the single-day file
            interactive = ir.build_interactive(a, out)
            note = "single day"
        attachments.insert(0, interactive)
        print(f"Dashboard: {interactive} "
              f"({os.path.getsize(interactive) / 1e6:.2f} MB, {note})")

    with open(res["email_body"], encoding="utf-8") as f:
        body_html = f.read()

    tables_zip = zip_dir(os.path.join(args.outdir, "tables"),
                         os.path.join(args.outdir, f"floorsheet_tables_{a.date}.zip"))
    attachments.append(tables_zip)

    user, pw, sender, recipients = creds()

    if args.dry_run:
        msg = build_message(a, body_html, res["email_images"], attachments,
                            sender or "noreply@example.com",
                            recipients or ["test@example.com"], dash_url, args.tag)
        eml = os.path.join(args.outdir, "message.eml")
        with open(eml, "w", encoding="utf-8") as f:
            f.write(msg.as_string())
        size = os.path.getsize(eml) / 1024
        print(f"Dry run — wrote {eml} ({size:,.0f} KB). Body HTML "
              f"{len(body_html) / 1024:.0f} KB (Gmail clips above ~102 KB).")
        return 0

    if not (user and pw and recipients):
        print("GMAIL_USER/GMAIL_APP_PW/MAIL_TO must be set.", file=sys.stderr)
        return 2

    msg = build_message(a, body_html, res["email_images"], attachments,
                        sender, recipients, dash_url, args.tag)
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as srv:
            srv.login(user, pw)
            srv.sendmail(sender, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=60) as srv:
            srv.ehlo()
            srv.starttls(context=ctx)
            srv.login(user, pw)
            srv.sendmail(sender, recipients, msg.as_string())
    print(f"Sent floor sheet mail for {a.date} to {len(recipients)} recipient(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
