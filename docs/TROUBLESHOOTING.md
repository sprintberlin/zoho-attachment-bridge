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

### File extension rejected

Zoho enforces a different allowlist per endpoint, and they are not consistent:

| Target | Allowed extensions |
|---|---|
| `expense-receipt` | gif, png, jpeg, jpg, bmp, pdf, xls, xlsx, doc, docx |
| `bill-attachment` | gif, png, jpeg, jpg, bmp, pdf |

Note that spreadsheets and Word documents are accepted for expense receipts but
**not** for bill attachments. The bridge rejects violations locally before
spending an API call.

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

### `This user belongs to multiple organizations`

```json
{"code": 6024, "message": "This user belongs to multiple organizations, hence the parameter CompanyID/CompanyName is required..."}
```

Pass `--organization-id` or set `ZOHO_BRIDGE_BOOKS_ORG_ID`. Helpfully, the error
response itself lists the available organizations under `error_info`, including
which one is the default.

---

## Data centers

Zoho uses **two different host families**, which is easy to get wrong:

| Purpose | Host pattern | Example (EU) | Example (CA) |
|---|---|---|---|
| OAuth | `accounts.<domain>` | `accounts.zoho.eu` | `accounts.zohocloud.ca` |
| API | `www.zohoapis.<tld>` | `www.zohoapis.eu` | `www.zohoapis.ca` |

Canada is the trap: the accounts host is `zohocloud.ca` while the API host is
`zohoapis.ca`. Deriving one from the other produces a host that does not exist.

Verify what the bridge resolves for your data center:

```bash
python3 -c "import sys; sys.path.insert(0,'scripts'); import bridge; \
print(bridge.accounts_base_url('eu'), bridge.books_base_url('eu'))"
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
missing (`ZohoBooks.expenses.READ` / `ZohoBooks.bills.READ`). Check the record
in the Zoho UI before re-uploading, otherwise you risk duplicates.
