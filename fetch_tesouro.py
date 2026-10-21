"""Downloads the Tesouro Direto official price/rate file and saves a smaller copy.

Why: iCUE's built-in browser cannot download the official file directly, but it can
read a small file from GitHub. This script runs on GitHub's servers.

The official Tesouro Transparente CSV is sometimes days behind the rates shown on
the Tesouro Direto website. When that happens, this script appends the latest
website quotes (same buy/sell rate and price) so the widget is not stuck on the
last published morning. Those extra rows are dropped automatically once the
official file catches up.

Usage:  python fetch_tesouro.py [output_path] [source_url]
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/"
    "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
    "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv"
)
LIVE_URL = "https://api.tesouroemfoco.com/v1/live-quotes/lookup"
KEEP_DAYS = 450        # a bit more than a year of history (widget's longest chart is 1Y)
MIN_ROWS = 500         # safety check: a healthy file has thousands of rows
BRT = timezone(timedelta(hours=-3))

# Longer names first so "Tesouro IPCA+" does not swallow the semestral bond.
# Third field is the maturity-year minus label-year gap (0 = pass maturity year).
PRODUCTS = [
    ("Tesouro IPCA+ com Juros Semestrais", "ipca-mais-com-juros-semestrais", 0),
    ("Tesouro Prefixado com Juros Semestrais", "prefixado-com-juros-semestrais", 0),
    ("Tesouro Renda+ Aposentadoria Extra", "renda-mais", 19),
    ("Tesouro Educa+", "educa-mais", 4),
    ("Tesouro IPCA+", "ipca-mais", 0),
    ("Tesouro Prefixado", "prefixado", 0),
    ("Tesouro Selic", "selic", 0),
]


def download(url, attempts=4):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tesouro-relay)"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - retry any transport/HTTP failure, then raise
            last = exc
            wait = 5 * (i + 1)
            print("Download failed (%s). Retrying in %ds..." % (exc, wait), file=sys.stderr)
            time.sleep(wait)
    raise last


def decode(raw):
    # The official file is Windows-1252; accept UTF-8 too in case that ever changes.
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def filter_recent(text, today):
    lines = text.splitlines()
    header = lines[0]
    if "Data Base" not in header or "Taxa Compra" not in header:
        raise ValueError("Unexpected header, the file layout may have changed: " + header)
    cutoff = today - timedelta(days=KEEP_DAYS)
    kept = []
    for line in lines[1:]:
        parts = line.split(";")
        if len(parts) < 7:
            continue
        try:
            base_date = datetime.strptime(parts[2].strip(), "%d/%m/%Y")
        except ValueError:
            continue
        if base_date >= cutoff:
            kept.append(line)
    if len(kept) < MIN_ROWS:
        raise ValueError("Only %d recent rows found, refusing to overwrite good data" % len(kept))
    return [header] + kept


def br_num(value):
    return ("%.2f" % value).replace(".", ",")


def product_for(name):
    for prefix, product_id, gap in PRODUCTS:
        if name == prefix or name.startswith(prefix + " "):
            return product_id, gap
    return None, None


def latest_bonds(rows):
    """Bonds present on the newest official date. Returns (date, list of (name, vencimento))."""
    newest = None
    found = []
    for line in rows[1:]:
        parts = line.split(";")
        if len(parts) < 3:
            continue
        try:
            day = datetime.strptime(parts[2].strip(), "%d/%m/%Y").date()
        except ValueError:
            continue
        if newest is None or day > newest:
            newest = day
            found = [(parts[0].strip(), parts[1].strip())]
        elif day == newest:
            found.append((parts[0].strip(), parts[1].strip()))
    return newest, found


def fetch_live(queries):
    out = []
    for i in range(0, len(queries), 40):
        chunk = queries[i:i + 40]
        body = json.dumps({"queries": chunk}).encode("utf-8")
        req = urllib.request.Request(
            LIVE_URL,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (tesouro-relay)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        out.extend(payload.get("results") or [])
    return out


def append_live(rows):
    """Add website quotes newer than the official file. Returns how many rows were added."""
    newest, bonds = latest_bonds(rows)
    if newest is None:
        return 0
    queries = []
    owners = []
    for name, venc in bonds:
        product_id, gap = product_for(name)
        if not product_id:
            continue
        try:
            year = int(venc[-4:]) - gap
        except ValueError:
            continue
        query = {"productId": product_id}
        query["conversionYear" if gap else "maturityYear"] = year
        queries.append(query)
        owners.append((name, venc))
    if not queries:
        return 0
    try:
        results = fetch_live(queries)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print("Live quotes unavailable (%s). Keeping the official file only." % exc, file=sys.stderr)
        return 0
    added = []
    for (name, venc), result in zip(owners, results):
        if not result or not result.get("found"):
            continue
        buy = (result.get("investorBuy") or {})
        sell = (result.get("investorSell") or {})
        try:
            buy_rate = float(buy["rate"]) * 100
            sell_rate = float(sell["rate"]) * 100
            buy_px = float(buy["price"])
            sell_px = float(sell["price"])
            quoted = datetime.fromisoformat(result["fetchedAt"].replace("Z", "+00:00")).astimezone(BRT).date()
        except (KeyError, TypeError, ValueError):
            continue
        if quoted <= newest:
            continue
        added.append(";".join([
            name, venc, quoted.strftime("%d/%m/%Y"),
            br_num(buy_rate), br_num(sell_rate), br_num(buy_px), br_num(sell_px), br_num(sell_px),
        ]))
    if added:
        print("Official file stops at %s. Added %d live website quotes for %s." % (
            newest.isoformat(), len(added), added[0].split(";")[2]))
    else:
        print("Official file is current through %s. No live rows added." % newest.isoformat())
    rows.extend(added)
    return len(added)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "data/tesouro.csv"
    url = sys.argv[2] if len(sys.argv) > 2 else SOURCE_URL
    text = decode(download(url))
    today = datetime.now(BRT).date()
    result = filter_recent(text, datetime.combine(today, datetime.min.time()))
    added = append_live(result)
    folder = os.path.dirname(out_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(result) + "\n")
    print("Saved %d rows to %s (%d from the website)" % (len(result) - 1, out_path, added))


if __name__ == "__main__":
    main()
