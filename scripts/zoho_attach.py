#!/usr/bin/env python3
"""
Zoho Attachment Bridge — Main CLI for binary attachment uploads.

Uploads binary attachments to Zoho services using real multipart/form-data,
followed by mandatory read-back SHA-256 verification.

Usage:
    python3 scripts/zoho_attach.py \\
        --app books \\
        --target expense-receipt \\
        --id 123456000000123456 \\
        --file /path/to/receipt.pdf \\
        [--organization-id 789012345] \\
        [--profile client_a]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add project root and scripts directory to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bridge import (
    load_env,
    refresh_access_token,
    sha256_file,
    upload_books_bill_attachment,
    upload_books_expense_receipt,
    validate_file_extension,
    verify_books_bill_attachment,
    verify_books_expense_receipt,
)


def parse_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload binary attachments to Zoho applications with SHA-256 verification."
    )
    parser.add_argument(
        "--app",
        required=True,
        choices=["books"],
        help="Target Zoho application (currently: books)",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=["expense-receipt", "bill-attachment"],
        help="Target upload entity type",
    )
    parser.add_argument(
        "--id",
        required=True,
        help="ID of the target record (e.g. expense ID or bill ID)",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the local file to upload",
    )
    parser.add_argument(
        "--organization-id",
        required=False,
        default=None,
        help="Zoho Books organization ID (defaults to ZOHO_BRIDGE_BOOKS_ORG_ID env var)",
    )
    parser.add_argument(
        "--profile",
        required=False,
        default=None,
        help="Named configuration profile (e.g. 'acme' -> ZOHO_BRIDGE_ACME_*)",
    )
    return parser.parse_args(args)


def main(cli_args=None) -> int:
    args = parse_args(cli_args)

    # 1. Validate file existence and extension
    file_path = Path(args.file).resolve()
    if not file_path.is_file():
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        return 1

    try:
        validate_file_extension(str(file_path), args.target)
    except ValueError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1

    # 2. Compute local file SHA-256
    local_sha = sha256_file(str(file_path))
    file_size = file_path.stat().st_size
    print(f"File: {file_path.name} ({file_size} bytes, SHA-256: {local_sha[:16]}...)")

    # 3. Load configuration
    try:
        config = load_env(profile=args.profile)
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    org_id = args.organization_id or config.get("books_org_id")
    if not org_id and args.app == "books":
        print(
            "Error: Organization ID is required for Zoho Books. "
            "Pass --organization-id or set ZOHO_BRIDGE_BOOKS_ORG_ID.",
            file=sys.stderr,
        )
        return 1

    dc = config["dc"]

    # 4. Refresh access token
    print(f"Authenticating via Self Client (DC: {dc})...")
    try:
        access_token = refresh_access_token(
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            refresh_token=config["refresh_token"],
            dc=dc,
        )
    except Exception as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1

    # 5. Upload file
    print(f"Uploading {file_path.name} to {args.app} ({args.target} {args.id})...")
    try:
        if args.app == "books" and args.target == "expense-receipt":
            res = upload_books_expense_receipt(
                dc=dc,
                access_token=access_token,
                organization_id=org_id,
                expense_id=args.id,
                file_path=str(file_path),
            )
        elif args.app == "books" and args.target == "bill-attachment":
            res = upload_books_bill_attachment(
                dc=dc,
                access_token=access_token,
                organization_id=org_id,
                bill_id=args.id,
                file_path=str(file_path),
            )
        else:
            print(f"Error: Unsupported app/target: {args.app}/{args.target}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 1

    upload_msg = res.get("message", "Upload request completed.")
    print(f"Upload response: {upload_msg}")

    # 6. Mandatory read-back verification
    print("Verifying upload via read-back and SHA-256 check...")
    if args.app == "books" and args.target == "expense-receipt":
        verified, vmsg = verify_books_expense_receipt(
            dc=dc,
            access_token=access_token,
            organization_id=org_id,
            expense_id=args.id,
            expected_sha256=local_sha,
        )
    elif args.app == "books" and args.target == "bill-attachment":
        verified, vmsg = verify_books_bill_attachment(
            dc=dc,
            access_token=access_token,
            organization_id=org_id,
            bill_id=args.id,
            expected_sha256=local_sha,
        )
    else:
        verified, vmsg = False, "Unsupported target for verification."

    if verified:
        print(f"SUCCESS: {vmsg}")
        return 0
    else:
        print(f"FAILURE: {vmsg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
