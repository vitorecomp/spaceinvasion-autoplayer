"""CLI manager for Alembic schema status, upgrades, verification, backups, and history."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
import json
from pathlib import Path
import sys

from alembic.script import ScriptDirectory
from sqlalchemy.exc import SQLAlchemyError

from spaceinvasion.db.dsn import redact_db_url, resolve_database_config
from spaceinvasion.db.migrations.backup import NO_BACKUP, BackupOption, make_pg_dump_backup_fn
from spaceinvasion.db.migrations.runner import (
    BASELINE_REVISION,
    SchemaDriftError,
    build_alembic_config,
    get_current_revision,
    upgrade_database,
    verify_models_match_migrations,
)
from spaceinvasion.db.queries.dashboard import query_db_verify
from spaceinvasion.db.repos.settings import SettingsRepository
from spaceinvasion.db.store import DatabaseStore

PrepareFn = Callable[[str, Path, bool], Mapping[str, object]]
BackupHook = Callable[[str, str, Path | None], Path | str]


def _emit(text: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(f"{text}\n")


def _dump_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)


def _pending_revisions(current_rev: str | None, target: str = "head") -> tuple[str | None, list[str]]:
    script_dir = ScriptDirectory.from_config(build_alembic_config())
    head_rev = script_dir.get_current_head()
    target_obj = script_dir.get_revision(target)
    if target_obj is None:
        return head_rev, []
    if current_rev == target_obj.revision:
        return head_rev, []
    base_marker = current_rev if current_rev is not None else "base"
    revs = list(script_dir.walk_revisions(base=base_marker, head=target_obj.revision))
    revs.reverse()
    return head_rev, [r.revision for r in revs if r.revision != current_rev]


def _add_upgrade_flags(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--target", default=None, help="Target Alembic revision (default: head).")
    subparser.add_argument("--all", action="store_true", help="Apply all pending revisions through head.")
    subparser.add_argument("--dry-run", action="store_true", help="Preview pending revisions without writing.")
    subparser.add_argument("--backup", action="store_true", help="Create PostgreSQL backup before migrating.")
    subparser.add_argument("--backup-dir", type=Path, default=None, help="Optional backup output directory.")


def build_cli_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `scripts/db_migrate.py`."""
    parser = argparse.ArgumentParser(
        description="SpaceInvasion Autoplayer — PostgreSQL Alembic Migration Manager",
    )
    parser.add_argument("--root", type=Path, default=Path("."), help="Workspace root directory.")
    parser.add_argument("--db-url", default=None, help="Explicit PostgreSQL DSN.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    sub = parser.add_subparsers(dest="subcommand")

    p_status = sub.add_parser("status", help="Show active revision and pending migrations.")
    p_status.add_argument("--events", action="store_true", help="Include recent migration audit events.")

    _add_upgrade_flags(sub.add_parser("migrate", help="Apply pending Alembic migrations."))
    _add_upgrade_flags(sub.add_parser("upgrade", help="Alias for `migrate`."))

    p_ver = sub.add_parser("verify", help="Verify PostgreSQL tables, ORM metadata match, and ledger.")
    p_ver.add_argument("--full-integrity", action="store_true", help="Run full schema integrity verification.")

    p_bkp = sub.add_parser("backup", help="Create a PostgreSQL custom-format backup snapshot.")
    p_bkp.add_argument("--label", default="manual", help="Label embedded in the backup filename.")
    p_bkp.add_argument("--backup-dir", type=Path, default=None, help="Optional backup output directory.")

    p_hist = sub.add_parser("history", help="Show applied migrations and audit events.")
    p_hist.add_argument("--limit", type=int, default=50, help="Max audit events to display.")
    return parser


def _resolve_backup_dir(root_dir: Path, explicit_dir: Path | None) -> Path:
    return explicit_dir if explicit_dir is not None else (root_dir / "data" / "backups")


def _cmd_status(store: DatabaseStore, disp: str, args: argparse.Namespace) -> int:
    current_rev = get_current_revision(store.db_engine)
    head_rev, pending = _pending_revisions(current_rev, "head")
    payload: dict[str, object] = {
        "backend": "postgres",
        "db_path": disp,
        "exists": True,
        "current_revision": current_rev,
        "head_revision": head_rev or BASELINE_REVISION,
        "pending_revisions": pending,
        "pending_count": len(pending),
        "up_to_date": len(pending) == 0 and current_rev is not None,
    }
    if bool(args.events):
        with store.session() as session:
            events = SettingsRepository(session).list_schema_migration_events(limit=50)
        payload["events"] = [asdict(ev) for ev in events]

    if bool(args.json):
        _emit(_dump_json(payload))
    else:
        state_lbl = "UP-TO-DATE" if payload["up_to_date"] else f"PENDING ({len(pending)})"
        _emit(
            f"Active DB Backend: PostgreSQL | Revision: {current_rev or 'none'} / "
            f"{head_rev or BASELINE_REVISION} [{state_lbl}]\n  [Postgres] {disp}"
        )
    return 0


def _cmd_migrate(
    store: DatabaseStore,
    pg_dsn: str,
    disp: str,
    args: argparse.Namespace,
    backup_hook: BackupHook | None,
) -> int:
    target = "head" if (bool(args.all) or not args.target) else str(args.target).strip()
    current_rev = get_current_revision(store.db_engine)
    head_rev, pending = _pending_revisions(current_rev, target)
    if bool(args.dry_run):
        _emit(
            _dump_json(
                {
                    "ok": True,
                    "dry_run": True,
                    "backend": "postgres",
                    "db_path": disp,
                    "current_revision": current_rev,
                    "target_revision": target if target != "head" else (head_rev or BASELINE_REVISION),
                    "pending_revisions": pending,
                    "pending_count": len(pending),
                }
            )
        )
        return 0

    bkp_opt: BackupOption = NO_BACKUP
    if bool(args.backup):
        bkp_dir = _resolve_backup_dir(Path(args.root), args.backup_dir)
        if backup_hook is not None:
            bkp_opt = lambda: backup_hook(pg_dsn, "pre_migrate", bkp_dir)  # noqa: E731
        else:
            bkp_opt = make_pg_dump_backup_fn(pg_dsn, bkp_dir, label="pre_migrate")

    if target == "head":
        mig_res = store.ensure_ready(backup_fn=bkp_opt, verify_schema=True)
    else:
        mig_res = upgrade_database(store.db_engine, target, backup_fn=bkp_opt)
        diffs = verify_models_match_migrations(store.db_engine)
        if diffs:
            raise SchemaDriftError(diffs)

    _emit(
        _dump_json(
            {
                "ok": True,
                "backend": "postgres",
                "db_path": disp,
                "previous_revision": mig_res.previous_revision,
                "current_revision": mig_res.current_revision,
                "applied_revisions": [asdict(item) for item in mig_res.applied_revisions],
                "backup_path": mig_res.backup_path,
            }
        )
    )
    return 0


def _cmd_verify(store: DatabaseStore, disp: str, args: argparse.Namespace) -> int:
    with store.session() as session:
        ver = query_db_verify(session, store.db_engine)
    payload: dict[str, object] = {
        "ok": ver.ok,
        "backend": ver.backend,
        "db_path": disp,
        "full_integrity": bool(args.full_integrity),
        "alembic_revision": ver.alembic_revision,
        "target_revision": ver.target_revision,
        "up_to_date": ver.up_to_date,
        "missing_tables": list(ver.missing_tables),
        "schema_diffs": list(ver.schema_diffs),
        "ledger_ok": ver.ledger_ok,
        "ledger_events_count": ver.ledger_events_count,
        "table_tiers": asdict(ver.table_tiers),
    }
    _emit(_dump_json(payload))
    return 0 if ver.ok else 2


def _cmd_backup(
    pg_dsn: str,
    disp: str,
    args: argparse.Namespace,
    backup_hook: BackupHook | None,
) -> int:
    bkp_dir = _resolve_backup_dir(Path(args.root), args.backup_dir)
    label = str(args.label or "manual").strip() or "manual"
    if backup_hook is not None:
        out_path = str(backup_hook(pg_dsn, label, bkp_dir))
    else:
        out_path = str(make_pg_dump_backup_fn(pg_dsn, bkp_dir, label=label)())
    _emit(
        _dump_json(
            {
                "ok": True,
                "backend": "postgres",
                "db_path": disp,
                "label": label,
                "backup_path": out_path,
            }
        )
    )
    return 0


def _cmd_history(store: DatabaseStore, disp: str, args: argparse.Namespace) -> int:
    limit = max(1, min(500, int(args.limit)))
    current_rev = get_current_revision(store.db_engine)
    with store.session() as session:
        settings = SettingsRepository(session)
        legacy = settings.list_schema_migrations()
        events = settings.list_schema_migration_events(limit=limit)
    _emit(
        _dump_json(
            {
                "ok": True,
                "backend": "postgres",
                "db_path": disp,
                "current_revision": current_rev,
                "legacy_migrations": [asdict(item) for item in legacy],
                "events": [asdict(ev) for ev in events],
            }
        )
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    prepare_fn: PrepareFn | None = None,
    backup_hook: BackupHook | None = None,
) -> int:
    """Execute the Alembic database migration CLI."""
    parser = build_cli_parser()
    args = parser.parse_args(argv)
    cmd = str(args.subcommand or "status")

    resolved = resolve_database_config(explicit_db_url=args.db_url, root_dir=args.root, env=env)
    pg_dsn = resolved.postgres_url
    disp = redact_db_url(pg_dsn)

    if prepare_fn is not None:
        ready = prepare_fn(pg_dsn, Path(args.root), resolved.auto_start_podman)
        if not bool(ready.get("ok")):
            err_obj = {"ok": False, "backend": "postgres", "target": disp, "error": ready.get("error")}
            _emit(_dump_json(err_obj), err=not bool(args.json))
            return 1

    if cmd == "backup":
        try:
            return _cmd_backup(pg_dsn, disp, args, backup_hook)
        except (OSError, RuntimeError, ValueError) as exc:
            _emit(_dump_json({"ok": False, "backend": "postgres", "target": disp, "error": str(exc)}), err=True)
            return 1

    try:
        with DatabaseStore.from_dsn(pg_dsn, pool_size=1, max_overflow=0) as store:
            if cmd == "status":
                return _cmd_status(store, disp, args)
            if cmd in ("migrate", "upgrade"):
                return _cmd_migrate(store, pg_dsn, disp, args, backup_hook)
            if cmd == "verify":
                return _cmd_verify(store, disp, args)
            if cmd == "history":
                return _cmd_history(store, disp, args)
    except (SQLAlchemyError, SchemaDriftError, OSError, RuntimeError, ValueError) as exc:
        _emit(_dump_json({"ok": False, "backend": "postgres", "target": disp, "error": str(exc)}), err=True)
        return 1
    return 0


__all__ = ["BackupHook", "PrepareFn", "build_cli_parser", "main"]
