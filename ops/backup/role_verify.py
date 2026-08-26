#!/usr/bin/env python3
"""Validate recovery role properties and application table privileges."""

from __future__ import annotations

import argparse
import subprocess
import sys


DEFAULT_TABLES = ("leagues", "teams", "players", "matches", "news")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def validate_role_grants(database_url: str, role: str, tables: tuple[str, ...] = DEFAULT_TABLES) -> None:
    role_literal = _literal(role)
    role_result = subprocess.run(
        ["psql", database_url, "-v", "ON_ERROR_STOP=1", "-tA", "-c", f"SELECT rolname, rolcanlogin, rolsuper FROM pg_roles WHERE rolname = {role_literal}"],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = [line.split("|") for line in role_result.stdout.splitlines() if line]
    if not rows:
        raise RuntimeError(f"required recovery role is missing: {role}")
    if rows[0][1].lower() != "t":
        raise RuntimeError(f"recovery role cannot log in: {role}")
    if rows[0][2].lower() == "t":
        raise RuntimeError(f"application recovery role is superuser: {role}")

    privilege_checks = ", ".join(
        f"has_table_privilege({_literal(role)}, 'public.{table}', 'SELECT, INSERT, UPDATE, DELETE')"
        for table in tables
    )
    privilege_result = subprocess.run(
        ["psql", database_url, "-v", "ON_ERROR_STOP=1", "-tA", "-c", f"SELECT has_schema_privilege({_literal(role)}, 'public', 'USAGE'), has_schema_privilege({_literal(role)}, 'public', 'CREATE'), {privilege_checks}, COALESCE((SELECT bool_and(has_sequence_privilege({_literal(role)}, quote_ident(schemaname) || '.' || quote_ident(sequencename), 'USAGE, SELECT, UPDATE')) FROM pg_sequences WHERE schemaname = 'public'), true)"],
        check=True,
        capture_output=True,
        text=True,
    )
    privileges = privilege_result.stdout.strip().split("|")
    if len(privileges) != len(tables) + 3 or any(value.lower() != "t" for value in privileges):
        raise RuntimeError(f"required grant is missing for recovery role: {role}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database_url")
    parser.add_argument("role")
    parser.add_argument("tables", nargs="*", default=DEFAULT_TABLES)
    args = parser.parse_args()
    validate_role_grants(args.database_url, args.role, tuple(args.tables))
    print("recovery role and grants verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())