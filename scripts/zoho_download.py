#!/usr/bin/env python3
"""
Zoho Attachment Bridge — Download CLI.

Fetches binary file bytes from Zoho and writes them to a local path.
WorkDrive is served from the dedicated download host (download.zoho.<dc>)
with Self Client OAuth. MCP cannot transfer file bytes into the workspace;
this command closes that gap for downloads.

Usage:
    python3 scripts/zoho_download.py \
        --app workdrive \
        --id <resource_id> \
        --out /path/to/file.pdf \
        [--version 2] \
        [--profile client_a] \
        [--overwrite]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bridge import (
    download_workdrive_file,
    load_env,
    refresh_access_token,
    sha256_bytes,
    validate_workdrive_resource_id,
)


def parse_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download a binary file from Zoho to the local workspace "
            "and print its size and SHA-256 digest."
        )
    )
    parser.add_argument(
        "--app",
        required=True,
        choices=["workdrive"],
        help="Source Zoho application (workdrive)",
    )
    parser.add_argument(
        "--id",
        required=True,
        help=(
            "Opaque WorkDrive resource ID of the file. Resolve it through the "
            "WorkDrive MCP skill; never derive it from a path or file name."
        ),
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Local output file path (existing directories only; refuses to overwrite without --overwrite)",
    )
    parser.add_argument(
        "--version",
        required=False,
        default=None,
        help="Optional WorkDrive file version to download",
    )
    parser.add_argument(
        "--profile",
        required=False,
        default=None,
        help="Named configuration profile (e.g. 'acme' -> ZOHO_BRIDGE_ACME_*)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output file if it already exists",
    )
    return parser.parse_args(args)


def write_download_atomic(out_path: Path, data: bytes, overwrite: bool) -> None:
    """Write bytes through a same-directory temporary file, then publish atomically."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            dir=str(out_path.parent),
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        if overwrite:
            os.replace(temp_path, out_path)
        else:
            # link() is atomic and refuses to replace a path created after the
            # initial existence check. Unlinking the temporary name leaves the
            # downloaded inode at the requested destination.
            os.link(temp_path, out_path)
            temp_path.unlink()
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def main(cli_args=None) -> int:
    args = parse_args(cli_args)

    # 1. Validate the resource ID before any network activity
    try:
        resource_id = validate_workdrive_resource_id(args.id, "WorkDrive resource ID")
    except ValueError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1

    # 2. Validate the output path: normalized path, existing parent directory,
    # no symlink target, and no silent overwrite.
    raw_out_path = Path(args.out).expanduser()
    if ".." in raw_out_path.parts:
        print(
            "Error: Output path must not contain '..'; pass the normalized destination path.",
            file=sys.stderr,
        )
        return 1
    if raw_out_path.is_symlink():
        print("Error: Output path must not be a symbolic link.", file=sys.stderr)
        return 1
    try:
        out_path = raw_out_path.resolve(strict=False)
    except OSError as exc:
        print(f"Error: Invalid output path: {exc}", file=sys.stderr)
        return 1
    if out_path.is_dir():
        print(f"Error: Output path is a directory: {out_path}", file=sys.stderr)
        return 1
    if not out_path.parent.is_dir():
        print(
            f"Error: Output parent directory does not exist: {out_path.parent}",
            file=sys.stderr,
        )
        return 1
    if out_path.exists() and not args.overwrite:
        print(
            f"Error: Output file already exists: {out_path} (use --overwrite to replace it)",
            file=sys.stderr,
        )
        return 1

    # 3. Load configuration and authenticate
    try:
        config = load_env(profile=args.profile)
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    dc = config["dc"]
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

    # 4. Download the bytes
    print(f"Downloading WorkDrive resource {resource_id}...")
    try:
        data = download_workdrive_file(
            dc=dc,
            access_token=access_token,
            resource_id=resource_id,
            version=args.version,
        )
    except Exception as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1

    if not data:
        # Consistent with the bridge contract: an empty response body is a
        # failure, never a success.
        print(
            "Download failed: the response body was empty. Re-check the "
            "resource ID and file version before retrying.",
            file=sys.stderr,
        )
        return 1

    # 5. Write atomically and report the digest
    try:
        write_download_atomic(out_path, data, overwrite=args.overwrite)
    except FileExistsError:
        print(
            f"Error: Output file already exists: {out_path} (use --overwrite to replace it)",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"Error: Could not write output file: {exc}", file=sys.stderr)
        return 1

    digest = sha256_bytes(data)
    print(f"Downloaded: {out_path.name} ({len(data)} bytes)")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
