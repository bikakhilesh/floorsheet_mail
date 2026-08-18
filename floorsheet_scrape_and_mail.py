# NEPSE floorsheet scraper + email — GitHub Actions edition
#
# Differences from the local/Task-Scheduler version:
#   * OUTPUT_DIR -> a temp dir (no D: drive on the runner). CSV is emailed
#     as an attachment only; it is NOT persisted anywhere.
#   * Chrome runs via Selenium Manager (Selenium 4.6+ auto-resolves the
#     driver), so no webdriver-manager needed at runtime.
#   * Exits nonzero on ERROR/MISMATCH so the Actions run is marked failed
#     and you get GitHub's own failure notification too.
#
# Scraping logic itself is unchanged from your working fixed version.

import os
import sys
import ssl
import time
import smtplib
import tempfile
from email.message import EmailMessage
from datetime import datetime

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementClickInterceptedException,
    JavascriptException, StaleElementReferenceException,
)

# ── CONFIG ──
HEADLESS = True
PARSER = "lxml"
MAX_VERIFY_ATTEMPTS = 90
# Hard wall-clock ceiling on the verify/retry loop, in seconds. Whichever of
# MAX_VERIFY_ATTEMPTS / VERIFY_BUDGET_SEC hits first stops the loop. This exists
# so the job never gets killed by the Actions timeout mid-retry — if that
# happens, send_mail() never runs and you lose the data AND the alert.
# Keep this comfortably below (timeout-minutes * 60) in the workflow.
VERIFY_BUDGET_SEC = 10800
TOLERANCE_ABS = 100.0
TOLERANCE_REL = 1e-6
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", tempfile.gettempdir())
# Default is the runner temp dir (wiped after the job). The workflow points
# OUTPUT_DIR at the workspace so the analytics steps can read the CSV.
LIMIT = 500
STABLE_POLL = 0.15
STABLE_MAX_POLLS = 30
PAGE_RETRIES = 4

# ── MAIL CONFIG (from repo secrets via workflow env) ──
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PW = os.environ.get("GMAIL_APP_PW", "")
MAIL_TO = os.environ.get("MAIL_TO", "")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

try:
    import lxml  # noqa: F401
except ImportError:
    PARSER = "html.parser"

# ── Chrome options ──
options = Options()
if HEADLESS:
    options.add_argument("--headless=new")
options.add_argument("--window-size=1920,1080")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")   # needed on CI containers
options.add_argument("--disable-gpu")
options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36")
options.page_load_strategy = "eager"
prefs = {"profile.managed_default_content_settings.images": 2}
options.add_experimental_option("prefs", prefs)

# Selenium Manager resolves the driver automatically (Selenium >= 4.6).

JS_FIRST_ROW = "var r=document.querySelector('.table-responsive tbody tr');return r?r.innerText:'';"
JS_TABLE_HTML = "var t=document.querySelector('.table-responsive');return t?t.outerHTML:'';"
JS_CLICK_NEXT = "var a=document.querySelector('li.pagination-next a');if(a){a.click();return true;}return false;"
JS_ACTIVE_PAGE = ("var e=document.querySelector('pagination-controls li.current');"
                  "return e?e.innerText.replace(/[^0-9]/g,''):'';")
JS_NEXT_DISABLED = ("var n=document.querySelector('li.pagination-next');"
                    "return n?(n.className||'').indexOf('disabled')>-1:true;")

floorsheet_url = "https://nepalstock.com.np/floor-sheet?&symbol=&floor=1&startDate=&endDate=&_limit="


def make_driver():
    return webdriver.Chrome(options=options)


def get_expected_turnover(driver):
    driver.get("https://nepalstock.com/")
    element = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//span[contains(text(), 'Total Turnover Rs:')]"))
    )
    full_text = element.text
    print(full_text)
    return float(full_text.split("|")[1].strip().replace(",", ""))


def setup_floorsheet_page(driver):
    driver.get(floorsheet_url)
    select_element = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located(
            (By.XPATH, "/html/body/app-root/div/main/div/app-floor-sheet/div/div[3]/div/div[5]/div/select/option[6]")
        )
    )
    select_element.click()
    print("Set Limit =", select_element.text)
    filter_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable(
            (By.XPATH, "/html/body/app-root/div/main/div/app-floor-sheet/div/div[3]/div/div[6]/button[1]")
        )
    )
    filter_button.click()


def grab_stable_table_html(driver):
    prev = None
    for _ in range(STABLE_MAX_POLLS):
        cur = driver.execute_script(JS_TABLE_HTML)
        if cur and "<tbody" in cur and cur == prev:
            return cur
        prev = cur
        time.sleep(STABLE_POLL)
    return prev or ""


def parse_rows(table_html, seen_contracts, all_data):
    soup = BeautifulSoup(table_html, PARSER)
    rows = soup.select("tbody tr")
    new_rows = 0
    for row in rows:
        cols = row.find_all("td")
        if len(cols) >= 8:
            contract_no = cols[1].get_text(strip=True)
            if contract_no in seen_contracts:
                continue
            seen_contracts.add(contract_no)
            all_data.append({
                "Contract No.": contract_no,
                "Stock Symbol": cols[2].get_text(strip=True),
                "Buyer": cols[3].get_text(strip=True),
                "Seller": cols[4].get_text(strip=True),
                "Quantity": int(cols[5].get_text(strip=True).replace(",", "")),
                "Rate (Rs)": float(cols[6].get_text(strip=True).replace(",", "")),
                "Amount (Rs)": float(cols[7].get_text(strip=True).replace(",", "")),
            })
            new_rows += 1
    return new_rows, len(rows)


def scrape_current_page(driver, seen_contracts, all_data, is_last_page):
    rows_on_page = 0
    for attempt in range(PAGE_RETRIES):
        table_html = grab_stable_table_html(driver)
        if not table_html:
            time.sleep(0.5)
            continue
        _, rows_on_page = parse_rows(table_html, seen_contracts, all_data)
        if rows_on_page >= LIMIT or is_last_page:
            return rows_on_page
        time.sleep(0.5)
    return rows_on_page


def scrape_all_pages(driver):
    all_data, seen_contracts = [], set()
    page_no = 1
    short_pages = []

    WebDriverWait(driver, 20).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, ".table-responsive tbody tr"))
    )

    while True:
        print(f"Scraping page {page_no}...", end="\r")
        try:
            is_last = driver.execute_script(JS_NEXT_DISABLED)
            rows_on_page = scrape_current_page(driver, seen_contracts, all_data, is_last)

            if rows_on_page < LIMIT and not is_last:
                short_pages.append((page_no, rows_on_page))
                print(f"\nWARNING: page {page_no} yielded only {rows_on_page} rows after retries.")

            if is_last:
                print(f"\nNext button is disabled. Finished scraping {page_no} pages.")
                break

            old_page = driver.execute_script(JS_ACTIVE_PAGE)
            old_first_row = driver.execute_script(JS_FIRST_ROW)

            clicked = driver.execute_script(JS_CLICK_NEXT)
            if not clicked:
                print("\nNext link not found. Finished.")
                break

            def advanced(d):
                ap = d.execute_script(JS_ACTIVE_PAGE)
                if ap and old_page and ap != old_page:
                    return True
                return d.execute_script(JS_FIRST_ROW) != old_first_row

            WebDriverWait(driver, 20, poll_frequency=0.2).until(advanced)

            new_page = driver.execute_script(JS_ACTIVE_PAGE)
            if new_page and old_page and new_page.isdigit() and old_page.isdigit():
                jump = int(new_page) - int(old_page)
                if jump not in (0, 1):
                    print(f"\nWARNING: pagination jumped from {old_page} to {new_page}.")
            page_no += 1

        except (TimeoutException, NoSuchElementException,
                ElementClickInterceptedException, StaleElementReferenceException,
                JavascriptException) as e:
            print(f"\nNo more pages or error while paginating: {type(e).__name__}")
            break
        except Exception as e:
            print("\nError during scraping:", e)
            break

    if short_pages:
        print(f"Short pages encountered: {short_pages}")
    return all_data


def build_analysis_text(df):
    lines = []

    top_trades = df.nlargest(10, "Amount (Rs)")[["Stock Symbol", "Buyer", "Seller", "Quantity", "Rate (Rs)", "Amount (Rs)"]]
    lines.append("Top 10 Largest Single Transactions (by Amount):")
    for _, r in top_trades.iterrows():
        lines.append(f"  {r['Stock Symbol']:<8} Buyer {r['Buyer']:<6} Seller {r['Seller']:<6} Qty {r['Quantity']:>8,} @ Rs {r['Rate (Rs)']:>10,.2f} = Rs {r['Amount (Rs)']:>14,.2f}")

    by_turnover = df.groupby("Stock Symbol")["Amount (Rs)"].sum().nlargest(5)
    lines.append("\nTop 5 Stocks by Turnover:")
    for sym, amt in by_turnover.items():
        lines.append(f"  {sym:<8} Rs {amt:,.2f}")

    by_volume = df.groupby("Stock Symbol")["Quantity"].sum().nlargest(5)
    lines.append("\nTop 5 Stocks by Volume:")
    for sym, qty in by_volume.items():
        lines.append(f"  {sym:<8} {qty:,} shares")

    lines.append(f"\nUnique symbols traded: {df['Stock Symbol'].nunique()}")
    lines.append(f"Average trade size: Rs {df['Amount (Rs)'].mean():,.2f}")

    top_buyers = df.groupby("Buyer")["Amount (Rs)"].sum().nlargest(5)
    lines.append("\nTop 5 Buyer Broker Codes (by Amount):")
    for broker, amt in top_buyers.items():
        lines.append(f"  {broker:<8} Rs {amt:,.2f}")

    top_sellers = df.groupby("Seller")["Amount (Rs)"].sum().nlargest(5)
    lines.append("\nTop 5 Seller Broker Codes (by Amount):")
    for broker, amt in top_sellers.items():
        lines.append(f"  {broker:<8} Rs {amt:,.2f}")

    return "\n".join(lines)


def send_mail(subject, body, attachment_path=None):
    if not (GMAIL_USER and GMAIL_APP_PW and MAIL_TO):
        print("Mail skipped: secrets not all set.")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = MAIL_TO
    msg.set_content(body)

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            data = f.read()
        msg.add_attachment(
            data, maintype="text", subtype="csv",
            filename=os.path.basename(attachment_path),
        )

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.send_message(msg)
    print(f"Email sent to {MAIL_TO}")


def main():
    start_time = datetime.now()
    status = "OK"
    saved_path = None
    rows = 0
    verify_attempts = 0
    expected_amount = actual_amount = 0.0

    try:
        driver = make_driver()
        expected_amount = get_expected_turnover(driver)
        setup_floorsheet_page(driver)
        all_data = scrape_all_pages(driver)
        driver.quit()

        df = pd.DataFrame(all_data)
        if df.empty:
            raise RuntimeError("No rows scraped — NEPSE may have blocked the runner IP.")
        actual_amount = df["Amount (Rs)"].sum()

        tol = max(TOLERANCE_ABS, TOLERANCE_REL * abs(expected_amount))

        # Keep the closest attempt seen so far, so a MISMATCH still emails the
        # best available snapshot rather than whatever the last attempt returned.
        best_df, best_diff = df, abs(expected_amount - actual_amount)

        counter = 1
        while best_diff > tol and counter < MAX_VERIFY_ATTEMPTS:
            elapsed_s = (datetime.now() - start_time).total_seconds()
            if elapsed_s > VERIFY_BUDGET_SEC:
                print(f"Verify budget exhausted after {elapsed_s:.0f}s "
                      f"({counter - 1} retries). Emailing best attempt.")
                break

            print(f"Wrong Data! Attempt {counter} (diff Rs {best_diff:,.2f}, "
                  f"{elapsed_s:.0f}s elapsed)")
            counter += 1
            driver = None
            try:
                driver = make_driver()
                setup_floorsheet_page(driver)
                all_data = scrape_all_pages(driver)
            finally:
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass

            attempt_df = pd.DataFrame(all_data)
            if attempt_df.empty:
                continue
            attempt_diff = abs(expected_amount - attempt_df["Amount (Rs)"].sum())
            if attempt_diff < best_diff:
                best_df, best_diff = attempt_df, attempt_diff

        df = best_df
        actual_amount = df["Amount (Rs)"].sum()
        verify_attempts = counter

        if best_diff > tol:
            status = "MISMATCH"
        else:
            print("Correct Data Downloaded")

        contract_val = df["Contract No."].iloc[-1]
        date_str = str(contract_val)[:8]
        formatted_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:]}"
        saved_path = os.path.join(OUTPUT_DIR, formatted_date + ".csv")
        df.to_csv(saved_path, index=False)
        rows = len(df)
        print(f"Data saved to: {saved_path}")

        # The archive keeps whichever copy of a session sits closest to NEPSE's
        # own turnover, so leave that figure where the later steps can read it.
        with open(os.path.join(OUTPUT_DIR, "expected_turnover.txt"), "w") as fh:
            fh.write(f"{expected_amount:.2f}")

    except Exception as e:
        status = "ERROR"
        print("FATAL:", e)

    elapsed = datetime.now() - start_time
    diff = expected_amount - actual_amount

    analysis_text = build_analysis_text(df) if status == "OK" else "Analysis unavailable (scrape did not complete successfully)."

    subject = f"[NEPSE Floorsheet {status}] {datetime.now():%Y-%m-%d} — {rows:,} rows"
    body = (
        f"Floorsheet scrape run (GitHub Actions): {datetime.now():%Y-%m-%d %H:%M:%S} UTC\n"
        f"Status:            {status}\n"
        f"Rows scraped:      {rows:,}\n"
        f"Expected turnover: Rs {expected_amount:,.2f}\n"
        f"Scraped turnover:  Rs {actual_amount:,.2f}\n"
        f"Difference:        Rs {diff:,.2f}\n"
        f"Verify attempts:   {verify_attempts} (cap {MAX_VERIFY_ATTEMPTS}, "
        f"budget {VERIFY_BUDGET_SEC}s)\n"
        f"Run time:          {elapsed}\n"
        f"\n{analysis_text}\n"
    )
    send_mail(subject, body, saved_path)

    if status != "OK":
        sys.exit(1)


if __name__ == "__main__":
    main()
