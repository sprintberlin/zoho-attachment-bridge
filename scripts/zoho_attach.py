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
    extract_workdrive_resource_id,
    load_env,
    refresh_access_token,
    sha256_file,
    upload_books_bill_attachment,
    upload_books_expense_receipt,
    upload_crm_record_attachment,
    upload_workdrive_file,
    validate_file_extension,
    verify_books_bill_attachment,
    verify_books_expense_receipt,
    verify_crm_record_attachment,
    verify_workdrive_file,
)


def parse_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload binary attachments to Zoho applications with SHA-256 verification."
    )
    parser.add_argument(
        "--app",
        required=True,
        choices=["books", "crm", "workdrive"],
        help="Target Zoho application (books, crm, workdrive)",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=[
            "expense-receipt",
            "bill-attachment",
            "record-attachment",
            "file-upload",
            "new-version",
        ],
        help="Target upload entity type",
    )
    parser.add_argument(
        "--id",
        required=True,
        help=(
            "ID of the target record (expense ID, bill ID, CRM record ID, or the "
            "WorkDrive destination folder ID)"
        ),
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the local file to upload",
    )
    parser.add_argument(
        "--module",
        required=False,
        default=None,
        help="Zoho CRM module name (required for CRM, e.g. Leads, Contacts, Deals, Accounts)",
    )
    parser.add_argument(
        "--organization-id",
        required=False,
        default=None,
        help="Zoho Books organization ID (defaults to ZOHO_BRIDGE_BOOKS_ORG_ID env var)",
    )
    parser.add_argument(
        "--filename",
        required=False,
        default=None,
        help=(
            "WorkDrive file name to store, including its extension "
            "(defaults to the local file name). For --target new-version this must "
            "match the existing file name exactly."
        ),
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
    if args.app == "books":
        if args.target not in ("expense-receipt", "bill-attachment"):
            print(
                f"Error: Invalid target '{args.target}' for Books. "
                "Supported targets: expense-receipt, bill-attachment.",
                file=sys.stderr,
            )
            return 1
        if not org_id:
            print(
                "Error: Organization ID is required for Zoho Books. "
                "Pass --organization-id or set ZOHO_BRIDGE_BOOKS_ORG_ID.",
                file=sys.stderr,
            )
            return 1
    elif args.app == "crm":
        if args.target != "record-attachment":
            print(
                f"Error: Invalid target '{args.target}' for CRM. "
                "Supported targets: record-attachment.",
                file=sys.stderr,
            )
            return 1
        if not args.module:
            print(
                "Error: --module is required for Zoho CRM "
                "(e.g. Leads, Contacts, Deals, Accounts).",
                file=sys.stderr,
            )
            return 1
    elif args.app == "workdrive":
        if args.target not in ("file-upload", "new-version"):
            print(
                f"Error: Invalid target '{args.target}' for WorkDrive. "
                "Supported targets: file-upload, new-version.",
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
    if args.app == "crm":
        target_desc = f"{args.module} {args.id}"
    elif args.app == "workdrive":
        target_desc = f"folder {args.id}"
    else:
        target_desc = f"{args.target} {args.id}"
    print(f"Uploading {file_path.name} to {args.app} ({target_desc})...")
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
        elif args.app == "crm" and args.target == "record-attachment":
            res = upload_crm_record_attachment(
                dc=dc,
                access_token=access_token,
                module=args.module,
                record_id=args.id,
                file_path=str(file_path),
            )
        elif args.app == "workdrive":
            res = upload_workdrive_file(
                dc=dc,
                access_token=access_token,
                parent_id=args.id,
                file_path=str(file_path),
                filename=args.filename,
                override_name_exist=(args.target == "new-version"),
            )
        else:
            print(f"Error: Unsupported app/target: {args.app}/{args.target}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 1

    upload_msg = "Upload request completed."
    if isinstance(res, dict):
        if res.get("message"):
            upload_msg = str(res["message"])
        elif "data" in res and isinstance(res["data"], list) and res["data"]:
            first = res["data"][0]
            if isinstance(first, dict) and first.get("message"):
                upload_msg = str(first["message"])

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
    elif args.app == "crm" and args.target == "record-attachment":
        uploaded_attachment_id = None
        if isinstance(res, dict) and "data" in res and isinstance(res["data"], list) and res["data"]:
            first_item = res["data"][0]
            if isinstance(first_item, dict):
                details = first_item.get("details")
                if isinstance(details, dict):
                    uploaded_attachment_id = details.get("id")
                if not uploaded_attachment_id:
                    uploaded_attachment_id = first_item.get("id")

        verified, vmsg = verify_crm_record_attachment(
            dc=dc,
            access_token=access_token,
            module=args.module,
            record_id=args.id,
            file_name=file_path.name,
            expected_sha256=local_sha,
            attachment_id=str(uploaded_attachment_id) if uploaded_attachment_id else None,
        )
    elif args.app == "workdrive":
        resource_id = extract_workdrive_resource_id(res) if isinstance(res, dict) else None
        if not resource_id:
            # No resource ID means the upload cannot be proven. Never treat an
            # unverifiable WorkDrive response as success.
            verified, vmsg = False, (
                "WorkDrive upload response did not contain a resource ID, so the "
                "upload cannot be verified. Re-list the destination folder before "
                "assuming anything was stored."
            )
        else:
            print(f"Uploaded WorkDrive resource ID: {resource_id}")
            verified, vmsg = verify_workdrive_file(
                dc=dc,
                access_token=access_token,
                resource_id=resource_id,
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
