---
name: zoho-attachment-bridge
version: 0.2.0
description: Upload binary attachments to Zoho Books, CRM, Projects, Inventory and WorkDrive via the REST API when Zoho MCP upload actions fail silently. Self Client OAuth, multipart/form-data, verified uploads.
---

# Zoho Attachment Bridge

Uploads binary files to Zoho when MCP cannot. Sits next to a Zoho MCP server: MCP handles records and reads, this skill handles bytes.

> **Status: Books adapter implemented, expense receipts verified live.** See `CHANGELOG.md`, `docs/ROADMAP.md` and `docs/TROUBLESHOOTING.md`.

## When to use

| Situation | Use |
|---|---|
| Read records, list attachments, update fields | Zoho MCP |
| Attach PDF, image, receipt, any binary file | This skill |
| MCP upload returned `success` with empty array | This skill |

## Why MCP fails

Zoho MCP exposes upload actions whose schema declares `format: "binary"`, but the server does not build a `multipart/form-data` request. Binary parameters are mapped into query strings and dropped.

Observed failure modes:

| Input | Zoho response | Reality |
|---|---|---|
| `entity_id` as integer | `401 INVALID_OAUTHSCOPE` | Request never reaches the endpoint |
| `entity_id` as string | `{"status":"success","data":{"attachment":[]}}` | Nothing attached |
| Local path, `file://`, `@file` | Same empty array | Remote server has no filesystem access |

**Rule: an empty `attachment` array is a failure, never a success.** Always verify by re-reading attachments after upload.

## Configuration

Four required environment variables. Everything else is a call argument.

| Variable | Required | Description |
|---|---|---|
| `ZOHO_BRIDGE_CLIENT_ID` | yes | Self Client ID from the Zoho API Console |
| `ZOHO_BRIDGE_CLIENT_SECRET` | yes | Self Client secret |
| `ZOHO_BRIDGE_REFRESH_TOKEN` | yes | Long-lived refresh token |
| `ZOHO_BRIDGE_DC` | yes | Data center: `eu`, `com`, `in`, `com.au`, `jp`, `ca`, `sa`, `com.cn` |
| `ZOHO_BRIDGE_BOOKS_ORG_ID` | no | Default Books organization id |
| `ZOHO_BRIDGE_PROJECTS_PORTAL_ID` | no | Default Projects portal id |
| `ZOHO_BRIDGE_TOKEN_CACHE` | no | Override path for the access token cache |

Multi-tenant: prefix per profile, e.g. `ZOHO_BRIDGE_ACME_CLIENT_ID`, selected with `--profile acme`.

Access tokens are never stored in env. They are derived from the refresh token and cached in `~/.cache/zoho-attachment-bridge/tokens.json` (mode 0600) until shortly before expiry. The cache key is a hash; no secret is written in clear text. Without it, Zoho rate-limits the token endpoint after repeated calls.

## File type limits

Zoho enforces different allowlists per endpoint:

| Target | Allowed extensions |
|---|---|
| `expense-receipt` | gif, png, jpeg, jpg, bmp, pdf, xls, xlsx, doc, docx |
| `bill-attachment` | gif, png, jpeg, jpg, bmp, pdf |

## Onboarding

```bash
python3 scripts/onboarding.py
```

For the first Books prototype, create the Self Client grant with this exact comma-separated scope string:

```text
ZohoBooks.expenses.CREATE,ZohoBooks.expenses.READ,ZohoBooks.bills.CREATE,ZohoBooks.bills.READ
```

- `expenses.CREATE`: upload expense receipts and attachments
- `expenses.READ`: verify expense uploads by reading them back
- `bills.CREATE`: upload bill attachments
- `bills.READ`: verify bill uploads by reading them back
- Optional `ZohoBooks.settings.READ`: discover `organization_id` through `GET /organizations`

Do not add `ZohoBooks.fullaccess.ALL`. Scopes are fixed when the refresh token is created; adding one later requires a new grant and refresh token.

Interactive: walks through the Self Client grant flow, exchanges the grant token, and writes the four variables to the env file (mode 0600, unrelated lines preserved).

Manual steps are documented in `docs/SELF_CLIENT_SETUP.md`. Verify the setup with a real upload via `zoho_attach.py`.

## Usage

```bash
# Expense receipt upload with verification
python3 scripts/zoho_attach.py --app books --target expense-receipt --id <expense_id> --file <path>

# Bill attachment upload with verification
python3 scripts/zoho_attach.py --app books --target bill-attachment --id <bill_id> --file <path>
```

Pass `--organization-id <id>` or set `ZOHO_BRIDGE_BOOKS_ORG_ID`.

Exit code `0` only after the uploaded file was confirmed present on the record via SHA-256 read-back verification.

## Scope

| App | Target | Status |
|---|---|---|
| Books | expense receipt, bill attachment | implemented |
| CRM | record attachment | planned |
| Projects | task and comment attachment | planned |
| Inventory | item image, bill attachment | planned |
| WorkDrive | file upload, new version | planned |

## Safety

- Treat `ZOHO_BRIDGE_REFRESH_TOKEN` as a password. Never print or log it.
- Request the narrowest OAuth scope per app. Do not use `ZohoBooks.fullaccess.ALL`.
- Confirm the target organization or portal id before uploading customer files.
- Respect Zoho rate limits. Back off on HTTP 429.
