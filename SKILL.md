---
name: zoho-attachment-bridge
version: 0.4.0
description: Upload binary attachments to Zoho Books, CRM, Projects, Inventory and WorkDrive via the REST API when Zoho MCP upload actions fail silently. Self Client OAuth, multipart/form-data, verified uploads.
---

# Zoho Attachment Bridge

Uploads binary files to Zoho when MCP cannot. Sits next to a Zoho MCP server: MCP handles records and reads, this skill handles bytes.

> **Status: Books, CRM v8, WorkDrive, and Projects adapters implemented.** Books expense receipts are verified live; CRM, WorkDrive, and Projects are covered by verified mock tests. See `CHANGELOG.md`, `docs/ROADMAP.md` and `docs/TROUBLESHOOTING.md`.

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
| `file-upload` (WorkDrive) | Zoho enforces blocked/allowed extensions per organization policy; the bridge requires a filename extension and leaves enforcement to the API |
| `new-version` (WorkDrive) | Same policy as `file-upload`; creates a new top version over an existing file with the same name |
| `task-attachment` (Projects) | No published extension allowlist; filename extension required |
| `comment-attachment` (Projects) | Same as `task-attachment`; comment text via `--comment` |

## File size limits

Reject before multipart: Books receipts 7 MB, bills 5 MB, WorkDrive 250 MB. CRM and Projects have no documented default. Override with `--max-bytes`; target and profile env variables are listed in the README.

## Scopes & Onboarding

Run `python3 scripts/onboarding.py`. Minimale Scopes:
- Books: `ZohoBooks.expenses.CREATE,ZohoBooks.expenses.READ,ZohoBooks.bills.CREATE,ZohoBooks.bills.READ`
- CRM: `ZohoCRM.modules.ALL,ZohoCRM.modules.attachments.CREATE,ZohoCRM.modules.attachments.READ`
- WorkDrive: `WorkDrive.files.CREATE,WorkDrive.files.READ`
- Projects: `ZohoProjects.tasks.READ,ZohoProjects.tasks.CREATE,ZohoPC.files.ALL`
- Optional Discovery: `ZohoBooks.settings.READ,ZohoProjects.portals.READ`

Never paste credentials into chats or emails. Details: `docs/SELF_CLIENT_SETUP.md`.

## Discover IDs

```bash
python3 scripts/discover.py books-organizations [--profile <name>] [--json]
python3 scripts/discover.py projects-portals [--profile <name>] [--json]
```

Scopes: `ZohoBooks.settings.READ` for Books organizations, `ZohoProjects.portals.READ` for Projects portals. Output contains IDs and non-secret metadata only.

## Usage

```bash
# Expense receipt upload with verification (enforces 7 MB default limit)
python3 scripts/zoho_attach.py --app books --target expense-receipt --id <expense_id> --file <path> [--max-bytes <bytes>]

# Bill attachment upload with verification
python3 scripts/zoho_attach.py --app books --target bill-attachment --id <bill_id> --file <path>

# CRM v8 record attachment upload with verification
# --module is required; CRM does not use --organization-id.
python3 scripts/zoho_attach.py --app crm --target record-attachment --module <module> --id <record_id> --file <path>

# WorkDrive file upload into a folder with SHA-256 verification
# --id is the destination folder ID (resolved through MCP).
python3 scripts/zoho_attach.py --app workdrive --target file-upload --id <folder_id> --file <path> [--filename <name>]

# WorkDrive new version upload over an existing file
# --id is the destination folder ID where the existing file lives.
# --filename must match the existing file name exactly.
python3 scripts/zoho_attach.py --app workdrive --target new-version --id <folder_id> --file <path> --filename <existing_name>

# Projects task attachment. --id is the task ID. Portal ID can come from ZOHO_BRIDGE_PROJECTS_PORTAL_ID.
python3 scripts/zoho_attach.py --app projects --target task-attachment --project-id <project_id> --id <task_id> --file <path>

# Projects comment attachment. --comment is optional.
python3 scripts/zoho_attach.py --app projects --target comment-attachment --project-id <project_id> --id <task_id> [--comment "text"] --file <path>
```

Pass `--organization-id <id>` or set `ZOHO_BRIDGE_BOOKS_ORG_ID` for Books. CRM does not require or use an organization ID; pass the parent module explicitly via `--module` (such as `Leads`, `Contacts`, `Deals`, or `Accounts`). WorkDrive takes the destination folder ID via `--id` and does not use an organization ID.

For CRM verification, the bridge performs:

1. `POST /crm/v8/{module}/{record_id}/Attachments` with multipart field `file`.
2. `GET /crm/v8/{module}/{record_id}/Attachments?fields=id,File_Name` to identify the newly uploaded attachment.
3. `GET /crm/v8/{module}/{record_id}/Attachments/{attachment_id}` and a SHA-256 comparison against the local file.

For WorkDrive verification, the bridge performs:

1. `POST /workdrive/api/v1/upload` with multipart `content`, `parent_id`, `filename`, and `override-name-exist`.
2. Extracts the created/updated `resource_id` from the JSON:API response.
3. `GET https://download.zoho.<dc>/v1/workdrive/download/{resource_id}` from the dedicated download server.
4. Compares the downloaded bytes against the local file's SHA-256 digest.

Exit code `0` only after the uploaded file was confirmed present in WorkDrive via SHA-256 read-back verification.

## Scope

| App | Target | Status |
|---|---|---|
| Books | expense receipt | implemented, verified live |
| Books | bill attachment | implemented, unit tests only |
| CRM | record attachment | implemented, mocked upload/list/download verification |
| WorkDrive | file upload, new version | implemented, mocked upload/download SHA-256 verification ([#9](https://github.com/sprintberlin/zoho-attachment-bridge/issues/9)) |
| Projects | task and comment attachment | implemented, mocked upload/download SHA-256 verification |
| Inventory | item image, bill attachment | planned |

Next work: `docs/ROADMAP.md` and the issue tracker.

Resolve the destination folder through the WorkDrive MCP skill, then pass its ID to `zoho_attach.py --app workdrive --target file-upload|new-version --id <folder_id> --file <path>`.

## Safety

- Treat `ZOHO_BRIDGE_REFRESH_TOKEN` as a password. Never print or log it.
- Request the narrowest OAuth scope per app. Do not use `ZohoBooks.fullaccess.ALL`. CRM record attachments need `ZohoCRM.modules.ALL` for their parent record module plus `ZohoCRM.modules.attachments.CREATE` and `ZohoCRM.modules.attachments.READ`. WorkDrive uploads need `WorkDrive.files.CREATE` and `WorkDrive.files.READ`. Projects attachments need `ZohoProjects.tasks.READ`, `ZohoProjects.tasks.CREATE`, and `ZohoPC.files.ALL`.
- Confirm the target organization or portal id before uploading customer files.
- Respect Zoho rate limits. Back off on HTTP 429.
