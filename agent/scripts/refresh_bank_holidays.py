"""Refresh the bank-holiday snapshot from gov.uk (decision 0012).

Usage, from agent/:

    uv run python scripts/refresh_bank_holidays.py

Reads https://www.gov.uk/bank-holidays.json (Open Government Licence) and writes the
England and Wales events to app/understand/bank_holidays.json. The agent reads only the
snapshot, never the feed, so a search costs no call. Run it when
test_bank_holidays_cover_six_months fails, and commit the result.
"""

import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

URL = "https://www.gov.uk/bank-holidays.json"
DIVISION = "england-and-wales"
OUT = Path(__file__).resolve().parents[1] / "app" / "understand" / "bank_holidays.json"


def snapshot(feed: dict, fetched: datetime) -> dict:
    events = feed[DIVISION]["events"]
    return {
        "source": URL,
        "division": DIVISION,
        "fetched": fetched.date().isoformat(),
        "events": [
            {"date": e["date"], "title": e["title"], "notes": e["notes"]}
            for e in sorted(events, key=lambda e: e["date"])
        ],
    }


def main() -> int:
    with urllib.request.urlopen(URL, timeout=20) as response:
        feed = json.load(response)
    data = snapshot(feed, datetime.now(UTC))
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(data['events'])} events to {OUT}, last {data['events'][-1]['date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
