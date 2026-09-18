---
name: zoho-attachment-bridge
version: 0.3.0
description: Upload binary attachments to Zoho Books, CRM, Projects, Inventory and WorkDrive via the REST API when Zoho MCP upload actions fail silently. Self Client OAuth, multipart/form-data, verified uploads.
---

# Zoho Attachment Bridge

Uploads binary files to Zoho when MCP cannot. Sits next to a Zoho MCP server: MCP handles records and reads, this skill handles bytes.

> **Status: Books adapter implemented, expense receipts verified live; CRM v8 record attachments implemented with mocked upload/list/download verification.** See `CHANGELOG.md`, `docs/ROADMAP.md` and `docs/TROUBLESHOOTING.md`.

## When to use

| Situation | Use |
|---|---|
| Read records, list attachments, update fields | Zoho MCP |
| Attach PDF, image, receipt, any binary file | This skill |
| MCP upload returned `success` with empty array | This skill |
| Upload a file into a WorkDrive folder or add a new version | This skill, after resolving the folder through MCP |

Companion MCP skills resolve records and resource IDs before an upload. For WorkDrive, use [openclaw-zoho-workdrive-mcp-skill](https://github.com/sprintberlin/openclaw-zoho-workdrive-mcp-skill) to find the team, team folder, folder, or file ID, then hand the bytes to this skill.

## Why MCP fails

Zoho MCP exposes upload actions whose schema declares `format: "binary"`, but the server does not build a `multipart/form-data` request. Binary parameters are mapped into query strings and dropped.

Zoho itself appears to know: the WorkDrive MCP tools `Upload File` and `Upload New Version` still declare `format: "binary"`, but their descriptions were narrowed to "text-format file only".

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
| `record-attachment` (CRM) | Zoho publishes no extension allowlist for this endpoint; the bridge requires a filename extension and leaves enforcement to the API |

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

For CRM record attachments, create a separate refresh token (or regenerate the complete grant) with:

```text
ZohoCRM.modules.ALL,ZohoCRM.modules.attachments.CREATE,ZohoCRM.modules.attachments.READ
```

- `ZohoCRM.modules.ALL`: access to the parent record module named with `--module`.
- `ZohoCRM.modules.attachments.CREATE`: upload the multipart `file` attachment.
- `ZohoCRM.modules.attachments.READ`: list the attachment and download it for mandatory SHA-256 verification.

The parent module scope and both attachment scopes are required. Zoho scopes are fixed when the refresh token is created, so an existing token missing any one of them needs a new grant and refresh token.

Interactive: walks through the Self Client grant flow, exchanges the grant token, and writes the four variables to the env file (mode 0600, unrelated lines preserved).

Manual steps are documented in `docs/SELF_CLIENT_SETUP.md`. Verify the setup with a real upload via `zoho_attach.py`.

## Usage

```bash
# Expense receipt upload with verification
python3 scripts/zoho_attach.py --app books --target expense-receipt --id <expense_id> --file <path>

# Bill attachment upload with verification
python3 scripts/zoho_attach.py --app books --target bill-attachment --id <bill_id> --file <path>

# CRM v8 record attachment upload with verification
# --module is required; CRM does not use --organization-id.
python3 scripts/zoho_attach.py --app crm --target record-attachment --module <module> --id <record_id> --file <path>
```

Pass `--organization-id <id>` or set `ZOHO_BRIDGE_BOOKS_ORG_ID` for Books. CRM does not require or use an organization ID; pass the parent module explicitly via `--module` (such as `Leads`, `Contacts`, `Deals`, or `Accounts`).

For CRM verification, the bridge performs:

1. `POST /crm/v8/{module}/{record_id}/Attachments` with multipart field `file`.
2. `GET /crm/v8/{module}/{record_id}/Attachments?fields=id,File_Name` to identify the newly uploaded attachment.
3. `GET /crm/v8/{module}/{record_id}/Attachments/{attachment_id}` and a SHA-256 comparison against the local file.

Exit code `0` only after the uploaded file was confirmed present on the record via SHA-256 read-back verification.

## Scope

| App | Target | Status |
|---|---|---|
| Books | expense receipt | implemented, verified live |
| Books | bill attachment | implemented, unit tests only |
| CRM | record attachment | implemented, mocked upload/list/download verification |
| Projects | task and comment attachment | planned |
| Inventory | item image, bill attachment | planned |
| WorkDrive | file upload, new version | planned, [#9](https://github.com/sprintberlin/zoho-attachment-bridge/issues/9) |

Next work: `docs/ROADMAP.md` and the issue tracker.

Until the WorkDrive adapter lands, do not promise a working WorkDrive upload. Resolve the destination folder through the WorkDrive MCP skill, then either wait for the adapter or perform a direct REST `multipart/form-data` upload and verify it by re-listing the folder.

## Safety

- Treat `ZOHO_BRIDGE_REFRESH_TOKEN` as a password. Never print or log it.
- Request the narrowest OAuth scope per app. Do not use `ZohoBooks.fullaccess.ALL`. CRM record attachments need `ZohoCRM.modules.ALL` for their parent record module plus `ZohoCRM.modules.attachments.CREATE` and `ZohoCRM.modules.attachments.READ`.
- Confirm the target organization or portal id before uploading customer files.
- Respect Zoho rate limits. Back off on HTTP 429.
