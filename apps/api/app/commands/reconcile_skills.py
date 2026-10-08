"""Run from apps/api: .venv/bin/python -m app.commands.reconcile_skills --dry-run."""
import argparse
import json
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.services.skill_reconciliation import reconcile_skills
from app.services.skill_taxonomy import SkillAliasConflict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded skill reconciliation; no user text in output")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--entity", choices=("profiles", "jobs", "all"), default="all")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args(argv)
    try:
        report = reconcile_skills(SessionLocal, entity=args.entity, dry_run=args.dry_run,
                                  batch_size=args.batch_size, after_id=args.after_id, limit=args.limit)
    except (SQLAlchemyError, SkillAliasConflict) as error:
        print(json.dumps({"error": "reconciliation_failed", "exception_class": type(error).__name__}), file=sys.stderr)
        return 1
    except ValueError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
