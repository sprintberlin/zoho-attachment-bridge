<p align="center">
  <h1 align="center">Zoho Attachment Bridge</h1>
</p>

<p align="center">
  Upload <b>binary attachments</b> to Zoho Books, CRM, Projects, Inventory and WorkDrive —
  reliably, verified, and without the silent failures of Zoho MCP upload actions.
</p>

<p align="center">
  <b>Agent skill</b> · Self Client OAuth 2.0 · multipart/form-data · multi-data-center · MIT
</p>

<p align="center">
  <b>Public repository:</b>
  <a href="https://github.com/sprintberlin/zoho-attachment-bridge">github.com/sprintberlin/zoho-attachment-bridge</a>
</p>

---

> **Status: 0.4.0 (unreleased).** Books expense receipts remain implemented and verified live; CRM v8 record attachments and WorkDrive file/version uploads are implemented with mocked upload/download SHA-256 verification. Bill attachments are implemented but only unit-tested. Next work is in
> [`docs/ROADMAP.md`](docs/ROADMAP.md) and the [issue tracker](https://github.com/sprintberlin/zoho-attachment-bridge/issues).

---

## 🧩 The problem

Zoho offers MCP (Model Context Protocol) servers for CRM, Books, Projects, Inventory, WorkDrive and more. They work well for reading and writing records.

**They cannot upload files.**

The upload actions exist. They appear in `tools/list`. Their schema even declares a binary parameter:

```json
"upload_file": {
  "type": "array",
  "items": { "type": "string", "format": "binary" }
}
```

But `format: "binary"` is only an OpenAPI annotation. Nothing in MCP instructs a client to read a local file, encode it, and build a `multipart/form-data` request — and the Zoho MCP server does not do it either. Binary parameters get mapped into query strings and silently dropped.

### The dangerous part

The call does not fail. It reports success.

| What you send | What Zoho returns | What actually happened |
|---|---|---|
| `entity_id` as integer | `401 INVALID_OAUTHSCOPE` | Request never reached the endpoint |
| `entity_id` as string | `{"status":"success","data":{"attachment":[]}}` | **Nothing was attached** |
| Absolute path, `file://`, `@file` | Same empty array | A remote server cannot read your filesystem |

An agent that trusts `"status": "success"` will confidently report "file uploaded" while the record stays empty. We lost an afternoon to exactly this.

> **Rule of thumb: an empty `attachment` array is a failure, never a success.**

### Is this an MCP problem or a Zoho problem?

Both, but mostly Zoho.

**What MCP can do:** binary transfer from server to client is standardized — `BlobResourceContents.blob` carries base64, tool results can return images and audio, and `resources/read` can return binary resources.

**What MCP cannot do:** there is no standardized client-to-server file upload with file picker, filename, MIME type and byte stream. Proposals exist but none has landed:

| Proposal | Topic | Status |
|---|---|---|
| [SEP-1306](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1306) | Binary mode elicitation | superseded |
| [SEP-2356](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2356) | File input for tools and elicitation | closed in favour of SEP-2631 |
| [SEP-2631](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/1197) | File objects and transfer | **draft** |

So MCP has a gap, and Zoho tried to paper over it with a schema that its own server does not honour. Zoho appears to know: the WorkDrive MCP tools `Upload File` and `Upload New Version` still declare `format: "binary"` but their descriptions were narrowed to **"text-format file only"**.

**Waiting is not a strategy.** Until SEP-2631 is ratified *and* Zoho implements it correctly, a bridge is the only reliable path.

---

## 🌉 The solution

Keep MCP for what it is good at. Route bytes around it.

```
                ┌──────────────────────────┐
   records,     │                          │
   metadata,    │       Zoho MCP           │  ✅ reads, writes, queries
   reads   ───► │                          │  ❌ binary uploads
                └──────────────────────────┘

                ┌──────────────────────────┐
   files,       │  Zoho Attachment Bridge  │  ✅ real multipart/form-data
   binaries ──► │  (this skill)            │  ✅ Self Client OAuth
                │                          │  ✅ verified after upload
                └──────────────────────────┘
                             │
                             ▼
                    Zoho REST API
```

The agent keeps using MCP for everything else. The moment a file is involved, it calls this skill instead.

---

## ✨ What makes it different

- **🔐 Self Client OAuth** — server-to-server auth with no redirect URI, no browser round trip, no user session. Exactly what an autonomous agent needs.
- **📎 Real multipart uploads** — the request Zoho's REST API actually expects, built properly.
- **✅ Verified, not assumed** — every upload is confirmed by re-reading the record's attachments. Exit code `0` only when the file is provably there.
- **🌍 Multi-data-center** — EU, US, IN, AU, JP, CA, SA, CN.
- **🧰 One tool, many apps** — a single entry point with per-app adapters. Auth, retries, verification and error handling are written once.
- **🏢 Multi-tenant** — named profiles so agencies can serve many Zoho organizations without mixing customer data.

---

## 📋 Requirements

| Requirement | Details |
|---|---|
| Python | 3.9 or newer (tested up to 3.12, CI runs 3.9–3.13) |
| Zoho account | with admin access to the [Zoho API Console](https://api-console.zoho.com) |
| Self Client | created once per organization, see [setup guide](docs/SELF_CLIENT_SETUP.md) |
| Network | outbound HTTPS to your Zoho data center |

No OpenClaw dependency. This is a portable agent skill and works standalone from the command line.

---

## ⚙️ Configuration

Deliberately minimal. **Four variables** are all that is globally required.

| Variable | Required | Description |
|---|---|---|
| `ZOHO_BRIDGE_CLIENT_ID` | ✅ | Self Client ID from the Zoho API Console |
| `ZOHO_BRIDGE_CLIENT_SECRET` | ✅ | Self Client secret |
| `ZOHO_BRIDGE_REFRESH_TOKEN` | ✅ | Long-lived refresh token |
| `ZOHO_BRIDGE_DC` | ✅ | Data center: `eu`, `com`, `in`, `com.au`, `jp`, `ca`, `sa`, `com.cn` |
| `ZOHO_BRIDGE_BOOKS_ORG_ID` | ➖ | Convenience default for Books |
| `ZOHO_BRIDGE_PROJECTS_PORTAL_ID` | ➖ | Convenience default for Projects |
| `ZOHO_BRIDGE_TOKEN_CACHE` | ➖ | Override path for the access token cache |

### Why so few?

Everything else — organization id, portal id, record id, entity type — is a **call argument**, not configuration. An agent already knows which record it is working on, or can look it up via MCP in one call. Baking those into environment variables would only create stale state and a bigger blast radius when it drifts.

**Access tokens are never stored.** They are derived from the refresh token and cached in `~/.cache/zoho-attachment-bridge/tokens.json` with mode `0600` until shortly before expiry. The cache key is a SHA-256 hash, so no client secret or refresh token is written in clear text. This matters: Zoho rate-limits its token endpoint, and refreshing on every call will eventually be rejected with *"You have made too many requests continuously"*.

### File type limits

Zoho enforces a different allowlist per endpoint, and the bridge rejects violations before wasting an API call:

| Target | Allowed extensions |
|---|---|
| `expense-receipt` | gif, png, jpeg, jpg, bmp, pdf, xls, xlsx, doc, docx |
| `bill-attachment` | gif, png, jpeg, jpg, bmp, pdf |
| `record-attachment` (CRM) | Zoho publishes no extension allowlist for this endpoint; the bridge requires a filename extension and leaves enforcement to the API |
| `file-upload`, `new-version` (WorkDrive) | Blocked and allowed extensions are an organization policy (API errors `D9236` / `D9237`); the bridge requires a filename extension and enforces the documented 250 MB limit of the multipart endpoint |

### File size limits

The bridge rejects oversized files locally before building the multipart body:

| Target | Documented default |
|---|---:|
| `expense-receipt` | 7 MB |
| `bill-attachment` | 5 MB |
| `record-attachment` | — (configurable) |
| `file-upload`, `new-version` | 250 MB |

Override per call with `--max-bytes <bytes>` or set `ZOHO_BRIDGE_MAX_BYTES_<TARGET>` (e.g. `ZOHO_BRIDGE_MAX_BYTES_EXPENSE_RECEIPT=10485760`). Named profiles use `ZOHO_BRIDGE_<PROFILE>_MAX_BYTES_<TARGET>`.

### Secure onboarding

```bash
python3 scripts/onboarding.py --grant-code-file ./grant-code.txt
# or read the code from stdin
cat ./grant-code.txt | python3 scripts/onboarding.py --grant-code-file -
```

The onboarding utility masks secret inputs via `getpass`. Never paste Client ID, Client Secret, grant codes, or refresh tokens into chat, email, or agent-to-agent messages.

### Example

```bash
export ZOHO_BRIDGE_CLIENT_ID="1000.XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
export ZOHO_BRIDGE_CLIENT_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export ZOHO_BRIDGE_REFRESH_TOKEN="1000.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export ZOHO_BRIDGE_DC="eu"
```

### Multiple Zoho organizations

Prefix each profile and select it at call time:

```bash
export ZOHO_BRIDGE_ACME_CLIENT_ID="..."
export ZOHO_BRIDGE_ACME_CLIENT_SECRET="..."
export ZOHO_BRIDGE_ACME_REFRESH_TOKEN="..."
export ZOHO_BRIDGE_ACME_DC="com"
```

```bash
python3 scripts/zoho_attach.py --profile acme ...
```

> **⚠️ Uploading a customer file into the wrong organization is the worst failure mode this tool has.** Always confirm the target organization before running against a shared environment.

---

## 🚀 Getting started

### 1. Create a Self Client

Follow [`docs/SELF_CLIENT_SETUP.md`](docs/SELF_CLIENT_SETUP.md). It takes about five minutes and only has to be done once per Zoho organization.

### 2. Run onboarding

```bash
python3 scripts/onboarding.py
```

The script walks you through the grant token exchange, writes the four environment variables to your `.env` file with `0600` permissions, and preserves any existing comments and unrelated variables. Follow it with a real upload via `zoho_attach.py` to confirm the setup end to end.

### 3. Upload something

```bash
# Expense receipt
python3 scripts/zoho_attach.py \
  --app books \
  --target expense-receipt \
  --id 123456000000123456 \
  --organization-id 789012345 \
  --file ~/receipts/taxi.pdf

# Bill attachment
python3 scripts/zoho_attach.py \
  --app books \
  --target bill-attachment \
  --id 987654000000987654 \
  --organization-id 789012345 \
  --file ~/invoices/vendor.pdf

# CRM record attachment — no organization ID; --module is required
python3 scripts/zoho_attach.py \
  --app crm \
  --target record-attachment \
  --module Deals \
  --id 123456000000123456 \
  --file ~/contracts/customer.pdf

# WorkDrive file upload — --id is the destination folder ID
python3 scripts/zoho_attach.py \
  --app workdrive \
  --target file-upload \
  --id ly9zm0170fb40015f4e2297a144e2b68cfa68 \
  --file ~/documents/report.pdf

# WorkDrive new version over an existing file of the same name
python3 scripts/zoho_attach.py \
  --app workdrive \
  --target new-version \
  --id ly9zm0170fb40015f4e2297a144e2b68cfa68 \
  --filename report.pdf \
  --file ~/documents/report-v2.pdf
```

The `--organization-id` can be omitted if `ZOHO_BRIDGE_BOOKS_ORG_ID` is set in the environment; it applies to Books only. **CRM does not use an organization ID** and requires `--module` (for example `Leads`, `Contacts`, `Deals`, or `Accounts`).

For CRM record attachments, create the refresh token with this exact scope set:

```text
ZohoCRM.modules.ALL,ZohoCRM.modules.attachments.CREATE,ZohoCRM.modules.attachments.READ
```

`ZohoCRM.modules.ALL` grants access to the parent record module; the attachment scopes authorize the upload and the mandatory list/download SHA-256 verification. These scopes are fixed in the refresh token, so generate a new grant token if an existing token lacks any of them.

For WorkDrive uploads, create the refresh token with:

```text
WorkDrive.files.CREATE,WorkDrive.files.READ
```

`WorkDrive.files.CREATE` authorizes `POST /workdrive/api/v1/upload`; `WorkDrive.files.READ` authorizes the download used for verification. A Books- or CRM-only token returns `F7007 Invalid OAuth scope` on every WorkDrive call.

Exit code `0` only after the uploaded file was confirmed present on the record via SHA-256 read-back verification.

---

## Coverage

| App | Target | Status | Issue |
|---|---|---|---|
| Books | expense receipt | implemented, verified live | — |
| Books | bill attachment | implemented, unit tests only | [#1](https://github.com/sprintberlin/zoho-attachment-bridge/issues/1) |
| Books / CRM / WorkDrive | file size pre-check | implemented, configurable per target | [#2](https://github.com/sprintberlin/zoho-attachment-bridge/issues/2) |
| CRM | record attachment | implemented, mocked upload/list/download verification | [#3](https://github.com/sprintberlin/zoho-attachment-bridge/issues/3) |
| WorkDrive | file upload, new version | implemented, mocked upload/download verification | [#9](https://github.com/sprintberlin/zoho-attachment-bridge/issues/9) |
| Projects | task and comment attachment | planned | [#4](https://github.com/sprintberlin/zoho-attachment-bridge/issues/4) |
| Inventory | item image, bill attachment | planned | [#8](https://github.com/sprintberlin/zoho-attachment-bridge/issues/8) |

Progress and next work: [`docs/ROADMAP.md`](docs/ROADMAP.md). Open issues: [sprintberlin/zoho-attachment-bridge/issues](https://github.com/sprintberlin/zoho-attachment-bridge/issues).

### Companion MCP skills

This bridge only moves bytes. Record lookup, navigation, and metadata stay with the matching Zoho MCP skill, which resolves the target ID before an upload:

| Zoho app | Companion skill |
|---|---|
| WorkDrive | [openclaw-zoho-workdrive-mcp-skill](https://github.com/sprintberlin/openclaw-zoho-workdrive-mcp-skill) |
| CRM | [openclaw-zoho-crm-mcp-skill](https://github.com/sprintberlin/openclaw-zoho-crm-mcp-skill) |
| Books | [openclaw-zoho-books-mcp-skill](https://github.com/sprintberlin/openclaw-zoho-books-mcp-skill) |

Typical WorkDrive split: resolve the destination folder ID with the WorkDrive MCP skill, upload the bytes here with `--app workdrive`, then read the file back and create share links over MCP again.

WorkDrive uses a different host for each direction, which is easy to get wrong:

| Direction | Host | Endpoint |
|---|---|---|
| Upload | `https://www.zohoapis.<tld>/workdrive` | `POST /api/v1/upload` |
| Download (verification) | `https://download.zoho.<tld>` | `GET /v1/workdrive/download/{resource_id}` |

Both a new file and a new version use the same upload endpoint. The `override-name-exist` form field decides: `true` stores the bytes as a new top version of an existing file with that name, `false` appends a timestamp instead.

---

## 🔒 Security

- Treat `ZOHO_BRIDGE_REFRESH_TOKEN` like a password. It grants standing API access until revoked. Never commit it, never print it, never paste it into a chat.
- Request the **narrowest scope** per app. Do not use `ZohoBooks.fullaccess.ALL`. For CRM record attachments, `ZohoCRM.modules.ALL` is additionally needed for the parent module, alongside `ZohoCRM.modules.attachments.CREATE` and `ZohoCRM.modules.attachments.READ`. WorkDrive uploads need `WorkDrive.files.CREATE` and `WorkDrive.files.READ`.
- WorkDrive resource IDs are opaque strings. Resolve them through the WorkDrive MCP skill and never derive one from a path or file name.
- Revoke unused Self Clients in the API Console.
- Uploads are subject to Zoho rate limits and per-plan file size limits. The bridge backs off on HTTP 429. Local file size pre-checks enforce documented limits before upload (Books 7 MB / 5 MB, WorkDrive 250 MB; CRM only when configured) via `--max-bytes` or env vars ([#2](https://github.com/sprintberlin/zoho-attachment-bridge/issues/2)).

---

## 🤝 Contributing

Start with [`docs/ROADMAP.md`](docs/ROADMAP.md) and pick an [open issue](https://github.com/sprintberlin/zoho-attachment-bridge/issues).

Rules that keep the bridge honest:

- Verify official Zoho API docs (endpoint, OAuth scopes, file types) before writing upload code. Do not copy the Books allowlists to another app.
- Real `multipart/form-data`. Never trust a success status with an empty attachment array.
- Exit code `0` only after SHA-256 read-back of the uploaded bytes.
- No secrets, account IDs, record IDs or live filenames in this public repository.
- Unit tests only in CI. No live Zoho calls from GitHub Actions.

Pull requests that add a new silent-failure pattern from Zoho MCP are especially welcome.

---

## 📄 License

MIT — see [`LICENSE`](LICENSE). Changes are tracked in [`CHANGELOG.md`](CHANGELOG.md); common failure modes in [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

---

<p align="center">
  Built by <a href="https://sprintcx.de"><b>SprintCX</b></a> — Zoho consulting and automation.
</p>
