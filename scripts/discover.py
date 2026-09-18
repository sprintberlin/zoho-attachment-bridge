#!/usr/bin/env python3
"""
List Zoho Books organizations or Zoho Projects portals for the configured token.
Prints identifiers and non-secret metadata only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bridge import (
    list_books_organizations,
    list_projects_portals,
    load_env,
    refresh_access_token,
)


def parse_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover Zoho Books organization IDs or Zoho Projects portal IDs."
    )
    parser.add_argument(
        "resource",
        choices=["books-organizations", "projects-portals"],
        help="Resource identifiers to list",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Named configuration profile (e.g. 'acme' -> ZOHO_BRIDGE_ACME_*)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a tab-separated table",
    )
    return parser.parse_args(args)


def _print_table(resource: str, rows) -> None:
    if resource == "books-organizations":
        print("organization_id\tname\tdefault\tcurrency\ttime_zone")
        for row in rows:
            print(
                f"{row['organization_id']}\t{row['name']}\t"
                f"{str(row['is_default_org']).lower()}\t"
                f"{row.get('currency_code', '')}\t{row.get('time_zone', '')}"
            )
    else:
        print("portal_id\tname\tdefault\tplan")
        for row in rows:
            print(
                f"{row['portal_id']}\t{row['name']}\t"
                f"{str(row['is_default_portal']).lower()}\t{row.get('project_plan', '')}"
            )


def main(cli_args=None) -> int:
    args = parse_args(cli_args)
    try:
        cfg = load_env(args.profile)
        access_token = refresh_access_token(
            cfg["client_id"],
            cfg["client_secret"],
            cfg["refresh_token"],
            cfg["dc"],
        )
        if args.resource == "books-organizations":
            rows = list_books_organizations(cfg["dc"], access_token)
        else:
            rows = list_projects_portals(cfg["dc"], access_token)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        _print_table(args.resource, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
