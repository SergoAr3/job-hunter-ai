"""Finite read-only shadow comparison. There is intentionally no write mode."""
import argparse
import json
import sys

from app.database import SessionLocal
from app.services.matching_shadow_data import compare_matching


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only saved-application matching comparison; aggregate JSON by default")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--after-id", type=int, default=0, help="Resume after this Application ID")
    parser.add_argument("--per-pair", action="store_true", help="Include bounded IDs/counts, never private labels")
    parser.add_argument("--pair-limit", type=int, default=100, help="Maximum per-pair diagnostics (1..1000)")
    args = parser.parse_args(argv)
    if (not 1 <= args.batch_size <= 1000 or not 1 <= args.limit <= 10000
            or args.after_id < 0 or not 1 <= args.pair_limit <= 1000):
        print(json.dumps({"error": "invalid_shadow_options"}), file=sys.stderr)
        return 2
    try:
        report = compare_matching(SessionLocal, batch_size=args.batch_size, limit=args.limit,
                                  after_id=args.after_id, pair_limit=args.pair_limit if args.per_pair else 0)
    except Exception as error:
        # SQLAlchemy errors may include parameters/private labels. No traceback,
        # message, connection URL or interpolated SQL enters the CLI output.
        print(json.dumps({"error": "shadow_comparison_failed", "exception_class": type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["schema_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
