"""Transform: turn the raw API JSON into a clean table with pandas."""

import io
import json

import pandas as pd

from common import get_logger, read_text, run_date, write_text

log = get_logger("transform")


def main() -> None:
    day = run_date()
    payload = json.loads(read_text(f"raw/{day}/rates.json"))

    df = pd.DataFrame(sorted(payload["rates"].items()), columns=["currency", "units_per_usd"])
    df["usd_per_unit"] = (1 / df["units_per_usd"]).round(6)
    df["base"] = payload["base"]
    df["rate_date"] = payload["date"]  # the API's date (weekends return Friday's rates)
    df["run_date"] = day

    # Data-quality checks: fail loudly rather than publish bad numbers.
    if df.isna().any().any():
        raise ValueError(f"null values in transformed data:\n{df}")
    if (df["units_per_usd"] <= 0).any():
        raise ValueError(f"non-positive rates in transformed data:\n{df}")

    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    location = write_text(f"processed/{day}/rates.csv", buffer.getvalue())
    log.info("wrote %d rows to %s", len(df), location)


if __name__ == "__main__":
    main()
