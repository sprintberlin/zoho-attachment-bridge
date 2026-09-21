# Troubleshooting

Symptoms observed in practice, with the actual cause behind them.

---

## Uploads

### `SUCCESS` from Zoho MCP but nothing is attached

```json
{"status": "success", "data": {"attachment": []}}
```

This is the exact failure this project exists for. The Zoho MCP server accepted
the JSON-RPC call, forwarded a request without a multipart body, and the Books
or Projects backend processed zero files without error.

**An empty `attachment` array is a failure, never a success.** Use this bridge
instead, or verify manually by re-reading the record's attachments.

### `401 INVALID_OAUTHSCOPE` when `entity_id` is an integer

Seen on Zoho Projects MCP. The same call with `entity_id` as a string returns
`success` with an empty array. The scope is usually fine; the request is routed
to a fallback handler before scope evaluation. Neither variant uploads anything.

### `Invalid keys found in path variable`

The MCP tool schema allows fewer path variables than the REST API documentation
suggests. On Zoho Projects only `portal_id` is accepted, not `project_id`.

### File size exceeded

The bridge validates file size locally before building the multipart body:

| Target | Documented default limit | Override environment variable |
|---|---|---|
| `expense-receipt` | 7 MB | `ZOHO_BRIDGE_MAX_BYTES_EXPENSE_RECEIPT` |
| `bill-attachment` | 5 MB | `ZOHO_BRIDGE_MAX_BYTES_BILL_ATTACHMENT` |
| `journal-attachment` | — (configurable) | `ZOHO_BRIDGE_MAX_BYTES_JOURNAL_ATTACHMENT` |
| `record-attachment` | — (configurable) | `ZOHO_BRIDGE_MAX_BYTES_RECORD_ATTACHMENT` |
| `file-upload` | 250 MB | `ZOHO_BRIDGE_MAX_BYTES_FILE_UPLOAD` |
| `new-version` | 250 MB | `ZOHO_BRIDGE_MAX_BYTES_NEW_VERSION` |
| `task-attachment` | — (configurable) | `ZOHO_BRIDGE_MAX_BYTES_TASK_ATTACHMENT` |
| `comment-attachment` | — (configurable) | `ZOHO_BRIDGE_MAX_BYTES_COMMENT_ATTACHMENT` |

You can also pass `--max-bytes <bytes>` on the CLI to override the limit for a single run.

### File extension rejected

Zoho enforces a different allowlist per endpoint, and they are not consistent:

| Target | Allowed extensions |
|---|---|
| `expense-receipt` | gif, png, jpeg, jpg, bmp, pdf, xls, xlsx, doc, docx |
| `bill-attachment` | gif, png, jpeg, jpg, bmp, pdf |
| `journal-attachment` | no published allowlist; filename extension required |

Note that spreadsheets and Word documents are accepted for expense receipts but
**not** for bill attachments. The bridge rejects those violations locally before
spending an API call. Journals publish no allowlist, so the bridge only requires
a filename extension.

For WorkDrive, allowed and blocked extensions are configured per organization.
The bridge only requires an extension, then lets WorkDrive enforce the real
account policy. WorkDrive returns `D9236` when the extension is blocked and
`D9237` when it is not in the organization's allowed list.

Zoho Projects publishes no extension allowlist for task or comment attachments.
The bridge only requires a filename extension, then lets the API apply the
account policy.

### WorkDrive upload is larger than 250 MB

The multipart endpoint used by `--app workdrive` has a documented maximum of
250 MB. The bridge rejects larger files locally. Files above 250 MB require
WorkDrive's separate stream upload endpoint, which is not implemented yet.

### WorkDrive download writes an empty file

`scripts/zoho_download.py` refuses an empty response body and exits non-zero
without creating the output file. Re-check the resource ID and the requested
version. MCP cannot deliver file bytes; downloads go through the dedicated
download host with `WorkDrive.files.READ`.

### WorkDrive download returns HTTP 404 right after an upload

The dedicated download host needs a moment to propagate a freshly uploaded
resource. Observed live: HTTP 404 with an empty body at `t+0s`, then HTTP 200
with byte-exact content at `t+3s`. The bridge therefore retries a 404 download
a bounded number of times (`WORKDRIVE_DOWNLOAD_404_RETRIES`, default 4, 3s
apart) before failing. Authorization errors and other statuses are never
retried. A 404 that survives the full retry window is a genuinely missing
resource or version; re-check the resource ID.

### WorkDrive upload response contains no resource ID

The bridge refuses to report success because it cannot perform SHA-256 read-back
without the uploaded file's `resource_id`. Re-list the destination folder with
the WorkDrive MCP skill before retrying, otherwise you may create a duplicate.

---

## Authentication

### `You have made too many requests continuously`

```json
{"error": "Access Denied", "error_description": "You have made too many requests continuously. Please try again after some time."}
```

Zoho rate-limits its OAuth token endpoint. Requesting a fresh access token on
every invocation triggers this within a handful of calls.

The bridge caches access tokens in `~/.cache/zoho-attachment-bridge/tokens.json`
until shortly before expiry. If you hit this anyway:

1. Confirm the cache file exists and is writable.
2. Wait a few minutes — the block clears on its own.
3. Check that you are not passing `use_cache=False`.

### `invalid_code`

The grant token expired or was already exchanged. Grant tokens are valid for
minutes and are single use. Generate a new one.

### `invalid_client`

The Client ID and Secret belong to a different data center than the one you are
calling. A Self Client created on `api-console.zoho.eu` does not work against
`accounts.zoho.com`.

### `401 You are not authorized to perform this operation` on a working token

The token is valid but the scope does not cover this specific operation.
Scopes are fixed when the refresh token is created and cannot be extended
afterwards — generate a new grant token with the complete scope list.

Common case: `GET /organizations` requires `ZohoBooks.settings.READ`, which is
not part of the minimal upload scope set. Likewise, deleting a record requires a
`DELETE` scope that the upload-only setup deliberately omits.

Journal attachments (`--target journal-attachment`) need both accountant scopes:

```text
ZohoBooks.accountants.CREATE,ZohoBooks.accountants.READ
```

`CREATE` uploads the file; `READ` lists the journal documents and downloads the
just-uploaded document for SHA-256 verification. A Books token that only has
expense and bill scopes returns HTTP 401 `You are not authorized to perform this
operation` on journal upload and document download.

For WorkDrive, all three scopes are mandatory:

```text
WorkDrive.files.CREATE,WorkDrive.files.READ,ZohoFiles.files.READ
```

| Scope | Purpose |
|---|---|
| `WorkDrive.files.CREATE` | Uploads file bytes. |
| `WorkDrive.files.READ` | Metadata authorization on the WorkDrive API host. |
| `ZohoFiles.files.READ` | `GET https://download.zoho.<dc>/v1/workdrive/download/{resource_id}` serves the actual file bytes for SHA-256 verification and for `scripts/zoho_download.py`. The dedicated download host validates this scope separately; a token without it uploads fine but every download returns HTTP 401 `INVALID_OAUTHSCOPE`. |

All three are required: `WorkDrive.files.CREATE` uploads the bytes, `WorkDrive.files.READ` authorizes metadata reads, and `ZohoFiles.files.READ` authorizes byte downloads from the download server. A Books- or CRM-only refresh token returns `F7007 Invalid OAuth scope`. A token missing `ZohoFiles.files.READ` can upload, but every SHA-256 verification and `zoho_download.py` invocation fails with HTTP 401 `INVALID_OAUTHSCOPE`. Scopes are fixed when the refresh token is created; generate a new grant with the complete scope string and exchange it for a new refresh token.

### `This user belongs to multiple organizations`

```json
{"code": 6024, "message": "This user belongs to multiple organizations, hence the parameter CompanyID/CompanyName is required..."}
```

Pass `--organization-id` or set `ZOHO_BRIDGE_BOOKS_ORG_ID`. List accessible
organizations with:

```bash
python3 scripts/discover.py books-organizations
```

This requires `ZohoBooks.settings.READ`. If the token lacks that scope, the 6024
payload may still include candidate organizations; `parse_books_6024_organizations`
extracts IDs and names from `organizations` or `error_info` without printing tokens.

Projects portal IDs are listed with `python3 scripts/discover.py projects-portals`
and require `ZohoProjects.portals.READ`.

---

## Data centers

Zoho uses **two different host families**, which is easy to get wrong:

| Purpose | Host pattern | Example (EU) | Example (CA) |
|---|---|---|---|
| OAuth | `accounts.<domain>` | `accounts.zoho.eu` | `accounts.zohocloud.ca` |
| API | `www.zohoapis.<tld>` | `www.zohoapis.eu` | `www.zohoapis.ca` |
| WorkDrive download | dedicated host | `download.zoho.eu` | `download.zohocloud.ca` |

Canada is the trap: the accounts host is `zohocloud.ca` while the API host is
`zohoapis.ca`. Deriving one from the other produces a host that does not exist.

Verify what the bridge resolves for your data center:

```bash
python3 -c "import sys; sys.path.insert(0,'scripts'); import bridge; \
print(bridge.accounts_base_url('eu'), bridge.books_base_url('eu'), bridge.workdrive_download_base_url('eu'))"
```

---

## Verification

### `FAILURE: SHA-256 mismatch`

The upload was accepted but the bytes read back differ. Possible causes:

- Zoho re-encoded or compressed the file (observed with some image formats).
- A different file was already attached and the endpoint returned that one.
- The record only stores one attachment and a previous file was returned.

Investigate before assuming the bridge is broken. The mismatch itself is the
feature working as intended: it refuses to report success it cannot prove.

### `Verification failed: unable to read back`

The upload may still have succeeded. This usually means the read scope is
missing (`ZohoBooks.expenses.READ` / `ZohoBooks.bills.READ` /
`WorkDrive.files.READ`). Check the record or WorkDrive folder before re-uploading,
otherwise you risk duplicates.
