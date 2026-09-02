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

> **✅ Status: Books prototype implemented and verified live.**
> Self Client OAuth with persistent token caching, expense receipt upload, bill attachment upload,
> and mandatory SHA-256 read-back verification are working against a real Zoho Books organization.
> Track progress in [`docs/ROADMAP.md`](docs/ROADMAP.md).

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
| Python | 3.9 or newer |
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

The script walks you through the grant token exchange, writes the four environment variables to your `.env` file with `0600` permissions, and preserves any existing comments and unrelated variables.

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
```

The `--organization-id` can be omitted if `ZOHO_BRIDGE_BOOKS_ORG_ID` is set in the environment.

Exit code `0` only after the uploaded file was confirmed present on the record via SHA-256 read-back verification.

---

## 🗺️ Coverage

| App | Target | Status |
|---|---|---|
| **Books** | expense receipt | ✅ implemented |
| **Books** | bill attachment | ✅ implemented |
| CRM | record attachment | 📋 planned |
| Projects | task and comment attachment | 📋 planned |
| Inventory | item image, bill attachment | 📋 planned |
| WorkDrive | file upload, new version | 📋 planned |

Books comes first because that is where our own invoice pipeline breaks today.

---

## 🔒 Security

- Treat `ZOHO_BRIDGE_REFRESH_TOKEN` like a password. It grants standing API access until revoked. Never commit it, never print it, never paste it into a chat.
- Request the **narrowest scope** per app. Do not use `ZohoBooks.fullaccess.ALL` when `ZohoBooks.expenses.CREATE` is enough.
- Revoke unused Self Clients in the API Console.
- Uploads are subject to Zoho rate limits and per-plan file size limits. The bridge backs off on HTTP 429 and refuses oversized files before wasting bandwidth.

---

## 🤝 Contributing

Issues and pull requests are welcome, especially:

- additional app or entity coverage
- data-center-specific quirks
- confirmed Zoho API behaviour that contradicts the documentation

If you hit a **new silent-failure pattern** in Zoho MCP, please open an issue with the exact request and response. Documenting those is half the value of this project.

---

## 📄 License

MIT — see [`LICENSE`](LICENSE).

---

<p align="center">
  Built by <a href="https://sprintcx.de"><b>SprintCX</b></a> — Zoho consulting and automation.
</p>
