"""Downloads the Tesouro Direto official price/rate file and saves a smaller copy.

Why: iCUE's built-in browser cannot download the official file directly, but it can
read a small file from GitHub. This script runs on GitHub's servers once or twice a day.

Usage:  python fetch_tesouro.py [output_path] [source_url]
"""
import sys
import urllib.request
from datetime import datetime, timedelta

SOURCE_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/"
    "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
    "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv"
)
KEEP_DAYS = 450        # a bit more than a year of history (widget's longest chart is 1Y)
MIN_ROWS = 500         # safety check: a healthy file has thousands of rows


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tesouro-relay)"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


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


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "data/tesouro.csv"
    url = sys.argv[2] if len(sys.argv) > 2 else SOURCE_URL
    text = decode(download(url))
    result = filter_recent(text, datetime.now())
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(result) + "\n")
    print("Saved %d rows to %s" % (len(result) - 1, out_path))


if __name__ == "__main__":
    main()
