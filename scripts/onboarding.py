#!/usr/bin/env python3
"""
Zoho Attachment Bridge — Interactive Self Client Onboarding.

Walks through exchanging an authorization grant code for a long-lived refresh token
and safely writes credentials to an env file with 0600 permissions while preserving
unrelated comments and variables.

Usage:
    python3 scripts/onboarding.py [--env-file .env] [--profile <name>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bridge import (
    DC_MAP,
    exchange_grant_token,
    resolve_dc,
    update_env_file,
)


def parse_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive onboarding for Zoho Attachment Bridge Self Client credentials."
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the .env file to update (default: .env)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional named profile prefix (e.g. 'acme' -> ZOHO_BRIDGE_ACME_*)",
    )
    return parser.parse_args(args)


def prompt_input(prompt_text: str, default: str = "") -> str:
    if default:
        full_prompt = f"{prompt_text} [{default}]: "
    else:
        full_prompt = f"{prompt_text}: "
    val = input(full_prompt).strip()
    return val if val else default


def main(cli_args=None) -> int:
    args = parse_args(cli_args)

    print("======================================================")
    print("      Zoho Attachment Bridge — Self Client Setup      ")
    print("======================================================\n")
    print("This utility exchanges a Self Client grant token for a refresh token")
    print("and safely saves credentials to your environment file.\n")

    # 1. Data Center
    print(f"Supported Data Centers: {', '.join(sorted(DC_MAP.keys()))}")
    dc_input = prompt_input("Enter Data Center", default="eu")
    try:
        resolve_dc(dc_input)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    dc = dc_input.lower().strip()

    # 2. Client ID & Secret
    client_id = prompt_input("Enter Self Client ID")
    if not client_id:
        print("Error: Client ID is required.", file=sys.stderr)
        return 1

    client_secret = prompt_input("Enter Self Client Secret")
    if not client_secret:
        print("Error: Client Secret is required.", file=sys.stderr)
        return 1

    # 3. Grant Code
    print("\nIn Zoho API Console -> Self Client -> Generate Code:")
    print("Required scopes for Books:")
    print("  ZohoBooks.expenses.CREATE,ZohoBooks.expenses.READ,ZohoBooks.bills.CREATE,ZohoBooks.bills.READ")
    grant_code = prompt_input("\nEnter generated Grant Code (expires in 10 mins)")
    if not grant_code:
        print("Error: Grant Code is required.", file=sys.stderr)
        return 1

    profile = args.profile
    if not profile:
        profile_input = prompt_input("Profile name (press Enter for default)", default="")
        if profile_input:
            profile = profile_input

    # 4. Exchange grant code for refresh token
    print(f"\nExchanging grant code with accounts.{resolve_dc(dc)}...")
    try:
        token_data = exchange_grant_token(
            client_id=client_id,
            client_secret=client_secret,
            code=grant_code,
            dc=dc,
        )
    except Exception as exc:
        print(f"Error exchanging grant token: {exc}", file=sys.stderr)
        return 1

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("Error: No refresh token returned in Zoho response.", file=sys.stderr)
        return 1

    print("✓ Grant token exchanged successfully.")

    # 5. Write to env file
    prefix = "ZOHO_BRIDGE"
    if profile:
        prefix = f"ZOHO_BRIDGE_{profile.upper()}"

    updates = {
        f"{prefix}_CLIENT_ID": client_id,
        f"{prefix}_CLIENT_SECRET": client_secret,
        f"{prefix}_REFRESH_TOKEN": refresh_token,
        f"{prefix}_DC": dc,
    }

    env_path = Path(args.env_file).resolve()
    print(f"\nWriting credentials to {env_path} (mode 0600)...")
    try:
        update_env_file(env_path, updates)
        print("✓ Credentials stored securely.")
    except Exception as exc:
        print(f"Error saving to env file: {exc}", file=sys.stderr)
        return 1

    print("\nOnboarding completed successfully!")
    print(f"You can now test uploads with:")
    print(f"  python3 scripts/zoho_attach.py --app books --target expense-receipt --id <EXPENSE_ID> --file <RECEIPT_PATH> --organization-id <ORG_ID>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
