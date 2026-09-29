#!/usr/bin/env python3
"""Download the open DB-IP Lite country and ASN databases (MaxMind .mmdb format) once, before going offline.

DB-IP Lite is published under CC BY 4.0 and needs no account or license key, unlike
MaxMind GeoLite2 (which also works: put GeoLite2-Country.mmdb / GeoLite2-ASN.mmdb in
data/geoip/ instead). The files are gitignored; attribution: "IP Geolocation by DB-IP"
(https://db-ip.com).
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

DATABASES = {"country": "dbip-country-lite", "asn": "dbip-asn-lite"}
URL = "https://download.db-ip.com/free/{name}-{month}.mmdb.gz"


def _months(count: int = 3) -> list[str]:
    first = date.today().replace(day=1)
    months = [first]
    for _ in range(count - 1):
        months.append((months[-1] - timedelta(days=1)).replace(day=1))
    return [month.strftime("%Y-%m") for month in months]


def download(name: str, output_dir: Path) -> Path:
    target = output_dir / f"{name}.mmdb"
    last_error: Exception | None = None
    for month in _months():  # the current month's file appears a few days into the month
        url = URL.format(name=name, month=month)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "bitcoin-investigation-platform/0.1 (+offline GeoIP setup)"})
            with urllib.request.urlopen(request, timeout=60) as response, gzip.GzipFile(fileobj=response) as unpacked, target.open("wb") as handle:
                shutil.copyfileobj(unpacked, handle)
            print(f"Downloaded {url} -> {target} ({target.stat().st_size / 1e6:.1f} MB)")
            return target
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
    raise SystemExit(f"Could not download {name}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", default="data/geoip")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in DATABASES.values():
        download(name, output_dir)
    import geoip2.database

    with geoip2.database.Reader(str(output_dir / "dbip-country-lite.mmdb")) as reader:
        print("Check: 8.8.8.8 ->", reader.country("8.8.8.8").country.iso_code)


if __name__ == "__main__":
    sys.exit(main())
