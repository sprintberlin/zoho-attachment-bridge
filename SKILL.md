---
name: zoho-attachment-bridge
version: 0.1.0
description: Upload binary attachments to Zoho Books, CRM, Projects, Inventory and WorkDrive via the REST API when Zoho MCP upload actions fail silently. Self Client OAuth, multipart/form-data, verified uploads.
---

# Zoho Attachment Bridge

Uploads binary files to Zoho when MCP cannot. Sits next to a Zoho MCP server: MCP handles records and reads, this skill handles bytes.

> **Status: scaffolding.** Configuration contract is stable. Upload scripts are not implemented yet. See `docs/ROADMAP.md`.

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

Multi-tenant: prefix per profile, e.g. `ZOHO_BRIDGE_ACME_CLIENT_ID`, selected with `--profile acme`.

Access tokens are never stored in env. They are derived from the refresh token at runtime and cached for their one-hour lifetime.

## Onboarding

```bash
python3 scripts/onboarding.py
```

Interactive: creates the Self Client grant flow, exchanges the grant token, writes the four variables, then verifies with a real upload and a read-back check.

Manual steps are documented in `docs/SELF_CLIENT_SETUP.md`.

## Usage

```bash
# planned interface, not yet implemented
python3 scripts/zoho_attach.py --app books --target expense-receipt --id <expense_id> --file <path>
python3 scripts/zoho_attach.py --app books --target bill-attachment  --id <bill_id>    --file <path>
```

Exit code `0` only after the uploaded file was confirmed present on the record.

## Scope

| App | Target | Status |
|---|---|---|
| Books | expense receipt, bill attachment | in progress |
| CRM | record attachment | planned |
| Projects | task and comment attachment | planned |
| Inventory | item image, bill attachment | planned |
| WorkDrive | file upload, new version | planned |

## Safety

- Treat `ZOHO_BRIDGE_REFRESH_TOKEN` as a password. Never print or log it.
- Request the narrowest OAuth scope per app. Do not use `ZohoBooks.fullaccess.ALL`.
- Confirm the target organization or portal id before uploading customer files.
- Respect Zoho rate limits. Back off on HTTP 429.
