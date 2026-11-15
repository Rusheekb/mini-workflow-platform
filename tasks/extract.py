"""Extract: fetch USD exchange rates for the run date from a free public API (no key needed)."""

import json

import requests

from common import get_logger, run_date, write_text

log = get_logger("extract")

API_URL = "https://api.frankfurter.dev/v1/{day}"
CURRENCIES = "EUR,GBP,JPY,INR,CAD"
TIMEOUT_SECONDS = 10  # never call the network without a timeout, or a hung API hangs the task forever


def main() -> None:
    day = run_date()
    url = API_URL.format(day=day)
    log.info("fetching %s", url)
    try:
        response = requests.get(url, params={"base": "USD", "symbols": CURRENCIES}, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()  # turn 4xx/5xx into an exception instead of parsing an error page
        payload = response.json()
    except requests.RequestException as exc:  # specific: network errors, timeouts, HTTP errors, bad JSON
        log.error("could not fetch exchange rates for %s: %s", day, exc)
        raise  # re-raise so Airflow marks the task failed and retries it

    if not payload.get("rates"):
        raise ValueError(f"API response for {day} has no rates: {payload}")

    location = write_text(f"raw/{day}/rates.json", json.dumps(payload))
    log.info("saved %d rates (API date %s) to %s", len(payload["rates"]), payload.get("date"), location)


if __name__ == "__main__":
    main()
