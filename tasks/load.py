"""Load: publish the processed table to its final, partitioned location."""

from common import get_logger, read_text, run_date, write_text

log = get_logger("load")


def main() -> None:
    day = run_date()
    csv_text = read_text(f"processed/{day}/rates.csv")

    # "run_date=YYYY-MM-DD" folders (Hive-style partitions) let query engines like Athena read
    # one day at a time. Same date -> same key, so re-running overwrites instead of duplicating.
    location = write_text(f"published/exchange_rates/run_date={day}/rates.csv", csv_text)
    log.info("published %d rows to %s", csv_text.count("\n") - 1, location)


if __name__ == "__main__":
    main()
