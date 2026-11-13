"""Command line entry point.

steward run <data_dir> [--out decisions.jsonl]     the one documented pipeline command
steward validate <decisions.jsonl> [--data-dir D]  contract + building checks
steward info <data_dir>                            what is in a dataset
"""

from __future__ import annotations

import argparse
import sys

from steward import pipeline
from steward.contract import validate_file
from steward.dataset import Dataset, DatasetError
from steward.detectors import DETECTORS

TABLES = (
    "bookings",
    "access_log",
    "room_telemetry",
    "room_water",
    "water_main",
    "energy",
    "weather",
    "weather_forecast",
    "tariff",
    "equipment",
    "logistics",
    "staff_roster",
    "work_orders",
    "guest_requests",
    "events",
)


def _cmd_run(args: argparse.Namespace) -> int:
    _, failed = pipeline.run(
        args.data_dir, out=args.out, trail=args.trail, only=args.only, strict=args.strict
    )
    report = validate_file(args.out, Dataset(args.data_dir))
    print(report.summary())
    if failed:
        print(f"steward: detectors FAILED and were skipped: {', '.join(failed)}", file=sys.stderr)
    return 0 if report.ok and not failed else 1


def _cmd_validate(args: argparse.Namespace) -> int:
    ds = Dataset(args.data_dir) if args.data_dir else None
    report = validate_file(args.decisions, ds)
    print(report.summary())
    return 0 if report.ok else 1


def _cmd_info(args: argparse.Namespace) -> int:
    ds = Dataset(args.data_dir)
    print(
        f"{ds.profile}: {ds.start} -> {ds.end}  ({len(ds.room_ids)} rooms, local tz {ds.local_tz})"
    )
    for name in TABLES:
        df = getattr(ds, name)
        missing = int(df.isna().sum().sum())
        print(
            f"  {name:<18} {len(df):>9,} rows  {missing:>7,} missing cells  {', '.join(df.columns)}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="steward", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the pipeline on a data directory and write decisions")
    run.add_argument("data_dir", help="directory with building.json, *.csv, events.jsonl")
    run.add_argument(
        "--out", default="decisions.jsonl", help="output file (default: decisions.jsonl)"
    )
    run.add_argument("--trail", help="also write the mock-executor action trail to this file")
    run.add_argument(
        "--only", nargs="+", choices=sorted(DETECTORS), help="run only these detectors"
    )
    run.add_argument("--strict", action="store_true", help="abort on the first detector error")
    run.set_defaults(func=_cmd_run)

    validate = sub.add_parser("validate", help="validate a decisions file against the contract")
    validate.add_argument("decisions")
    validate.add_argument(
        "--data-dir", help="also check asset IDs, resources and requests against this dataset"
    )
    validate.set_defaults(func=_cmd_validate)

    info = sub.add_parser("info", help="summarise a dataset")
    info.add_argument("data_dir")
    info.set_defaults(func=_cmd_info)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DatasetError as e:
        print(f"steward: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
