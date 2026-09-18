# Self Client setup

A **Self Client** is Zoho's OAuth client type for server-to-server access. It has no redirect URI and needs no browser session at runtime, which is exactly what an autonomous agent requires.

You do this once per Zoho organization. It takes about five minutes.

---

## 1. Pick your data center

Zoho stores each account in one region and the console domain differs per region. Use the domain you log into Zoho with.

| Region | Console | `ZOHO_BRIDGE_DC` |
|---|---|---|
| Europe | `api-console.zoho.eu` | `eu` |
| United States | `api-console.zoho.com` | `com` |
| India | `api-console.zoho.in` | `in` |
| Australia | `api-console.zoho.com.au` | `com.au` |
| Japan | `api-console.zoho.jp` | `jp` |
| Canada | `api-console.zohocloud.ca` | `ca` |
| Saudi Arabia | `api-console.zoho.sa` | `sa` |
| China | `api-console.zoho.com.cn` | `com.cn` |

> Using the wrong data center produces confusing authentication errors that look like bad credentials. Check this first when something fails.

---

## 2. Create the Self Client

1. Open the API Console for your data center and sign in.
2. Click **Add Client**.
3. Choose **Self Client**.
4. Confirm the creation dialog.
5. Copy the **Client ID** and **Client Secret**.

These two values are permanent. Store them somewhere safe.

### 🔒 Security rule

**Never paste Client ID, Client Secret, grant codes or refresh tokens into chat channels, emails, or agent-to-agent messages.**

Use a password manager to store and move credentials. When running `scripts/onboarding.py`, secrets are masked on input, and grant codes can be passed via `--grant-code-file <path>` (or `-` for stdin) so they never appear in your shell history.

---

## 3. Generate a grant token

Still in the API Console, open your Self Client and switch to the **Generate Code** tab.

1. **Scope** — enter the scopes you need, comma separated. See the table below.
2. **Time Duration** — pick the longest available, usually 10 minutes.
3. **Scope Description** — any short text, for example `attachment bridge`.
4. Click **Create**, select the target organization if you are asked, then copy the generated code.

> ⚠️ The grant token expires within minutes and can only be exchanged **once**. Go straight to step 4.

### Exact scope string for the first Books prototype

Copy this value into the **Scope** field as one comma-separated line:

```text
ZohoBooks.expenses.CREATE,ZohoBooks.expenses.READ,ZohoBooks.bills.CREATE,ZohoBooks.bills.READ
```

These four scopes are the minimum for the planned prototype:

| Scope | Why it is required |
|---|---|
| `ZohoBooks.expenses.CREATE` | Upload a receipt or one or more attachments to an expense |
| `ZohoBooks.expenses.READ` | Download/read the receipt or expense again to verify that the upload really succeeded |
| `ZohoBooks.bills.CREATE` | Upload an attachment to a bill |
| `ZohoBooks.bills.READ` | Read the bill attachment again to verify the upload |

Optional discovery scopes:

```text
ZohoBooks.settings.READ,ZohoProjects.portals.READ
```

- `ZohoBooks.settings.READ`: `python3 scripts/discover.py books-organizations`
- `ZohoProjects.portals.READ`: `python3 scripts/discover.py projects-portals`

Neither scope is required when the corresponding IDs are already known and passed to upload commands.

The operation names above are taken from the official Zoho Books API documentation:

- [Expenses API](https://www.zoho.com/books/api/v3/expenses/): `Add receipt to an expense` and `Add attachment to an expense` require `ZohoBooks.expenses.CREATE`; `Get an expense receipt` requires `ZohoBooks.expenses.READ`.
- [Bills API](https://www.zoho.com/books/api/v3/bills/): `Add attachment to a bill` requires `ZohoBooks.bills.CREATE`; `Get a bill attachment` requires `ZohoBooks.bills.READ`.
- [OAuth scopes](https://www.zoho.com/books/api/v3/oauth/): Books scopes follow `service.scope.operation`, with `CREATE`, `READ`, `UPDATE`, `DELETE`, or `ALL`.

Do not add `ZohoBooks.fullaccess.ALL`. It is unnecessary for attachment uploads and grants substantially broader access.

### Exact scope string for CRM record attachments

CRM record attachment upload and mandatory SHA-256 read-back require this complete, comma-separated scope string:

```text
ZohoCRM.modules.ALL,ZohoCRM.modules.attachments.CREATE,ZohoCRM.modules.attachments.READ
```

| Scope | Why it is required |
|---|---|
| `ZohoCRM.modules.ALL` | The attachment belongs to a parent module record passed with `--module` (for example `Leads`, `Contacts`, `Deals`, or `Accounts`). The bridge needs access to that parent record/module. |
| `ZohoCRM.modules.attachments.CREATE` | `POST /crm/v8/{module}/{record_id}/Attachments` uploads the multipart form field named `file`. |
| `ZohoCRM.modules.attachments.READ` | The bridge lists `id,File_Name`, downloads the just-uploaded attachment, and compares its SHA-256 digest. |

All three are required: parent-module access plus attachment create/read access. If an existing refresh token lacks one of these scopes, generate a new code with the complete scope string and exchange it for a new refresh token. CRM does **not** use a Books `organization_id` — call the bridge with `--module <module>` instead.

CRM endpoint sequence:

```text
POST https://www.zohoapis.<dc>/crm/v8/{module}/{record_id}/Attachments
GET  https://www.zohoapis.<dc>/crm/v8/{module}/{record_id}/Attachments?fields=id,File_Name
GET  https://www.zohoapis.<dc>/crm/v8/{module}/{record_id}/Attachments/{attachment_id}
```

### Exact scope string for WorkDrive file uploads and new versions

WorkDrive file upload, new-version upload, and mandatory SHA-256 read-back require this complete, comma-separated scope string:

```text
WorkDrive.files.CREATE,WorkDrive.files.READ
```

| Scope | Why it is required |
|---|---|
| `WorkDrive.files.CREATE` | `POST /workdrive/api/v1/upload` uploads the multipart form field named `content`. The same endpoint stores a new top version when `override-name-exist=true`. |
| `WorkDrive.files.READ` | `GET https://download.zoho.<dc>/v1/workdrive/download/{resource_id}` downloads the uploaded bytes for SHA-256 verification. |

Both are required. A Books- or CRM-only refresh token returns `F7007 Invalid OAuth scope` on every WorkDrive call. Generate a new grant with the complete scope string and exchange it for a new refresh token. WorkDrive does **not** use a Books `organization_id`; `--id` is the destination folder ID, resolved through the companion WorkDrive MCP skill.

WorkDrive endpoint sequence:

```text
POST https://www.zohoapis.<dc>/workdrive/api/v1/upload
     multipart: content, parent_id, filename, override-name-exist
GET  https://download.zoho.<dc>/v1/workdrive/download/{resource_id}
```

The documented maximum for this multipart endpoint is 250 MB. Larger files need the separate stream-upload API, which this bridge does not implement. Blocked and allowed file types are an organization policy, not a published global allowlist; the API reports them as `D9236` or `D9237`.

### Future app scopes

The following entries are planning notes for later adapters and must be rechecked against the exact endpoint before implementation:

| App | Purpose | Expected scope family |
|---|---|---|
| Projects | task and comment attachments | app-specific Projects create/read scopes |

Read access is part of the bridge contract. The bridge must verify every upload instead of trusting an HTTP status or success message.

---


## Multi-host deployment

Use one Self Client and one refresh token **per host**. Do not copy a refresh token between machines.

| Concept | Purpose |
|---|---|
| Separate Self Client / refresh token per host | Revocation, rotation and audit isolation for each agent machine |
| Named profile (`--profile`) | Multiple Zoho organizations or tenants on the same host |

For every additional host:

1. Create a new Self Client in the correct data center.
2. Generate a grant with the complete scope set required on that host.
3. Run `scripts/onboarding.py` locally on that host.
4. Transfer Client ID, Client Secret and one-time grant code only through a password manager or a local mode-0600 file.
5. Perform a real upload plus SHA-256 read-back before the host is considered operational.

Zoho allows up to 20 active refresh tokens per user. Creating the 21st invalidates the oldest token. Remove or revoke tokens from decommissioned hosts.

Named profiles do not replace host isolation. They select another organization configuration on the same machine; they are not a mechanism for sharing credentials between machines.

---

## 4. Exchange the grant token for a refresh token

The onboarding script does this for you:

```bash
python3 scripts/onboarding.py
```

If you prefer to do it manually, replace `<DC>` with your data center domain:

```bash
curl -s -X POST "https://accounts.zoho.<DC>/oauth/v2/token" \
  -d "grant_type=authorization_code" \
  -d "client_id=<CLIENT_ID>" \
  -d "client_secret=<CLIENT_SECRET>" \
  -d "code=<GRANT_TOKEN>"
```

A successful response contains a `refresh_token`:

```json
{
  "access_token": "1000.xxxx.xxxx",
  "refresh_token": "1000.xxxx.xxxx",
  "expires_in": 3600,
  "token_type": "Bearer"
}
```

> The `refresh_token` appears **only in this one response**. If you lose it, generate a new grant token and repeat.

---

## 5. Store the credentials

```bash
export ZOHO_BRIDGE_CLIENT_ID="1000.XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
export ZOHO_BRIDGE_CLIENT_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export ZOHO_BRIDGE_REFRESH_TOKEN="1000.xxxxxxxxxxxxxxxxxxxx.xxxxxxxxxxxxxxxxxxxx"
export ZOHO_BRIDGE_DC="eu"
```

For OpenClaw agents these belong in `~/.openclaw/.env`, which the gateway loads on start. Restart the gateway afterwards.

Never commit these values.

---

## 6. Verify

The onboarding script finishes with a real upload followed by a read-back check. Do not skip this. A refresh token can be valid while the scope is still too narrow for the operation you actually need, and the only way to find out is to try.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `invalid_code` | Grant token expired or already used. Generate a new one. |
| `invalid_client` | Client ID and secret belong to a different data center. |
| `INVALID_OAUTHSCOPE` on a real call | Scope missing at grant time. Scopes cannot be added later, generate a new grant token. |
| Upload succeeds but the file is not on the record | Not an auth problem. This is the Zoho MCP failure mode the bridge exists to avoid. |

---

## Rotating or revoking

Refresh tokens preserve the scopes selected during grant creation. To add or change scopes, generate a new grant token with the complete desired scope list and exchange it for a new refresh token.

A refresh token remains valid until it is revoked. To revoke access, delete or revoke the Self Client/token in the Zoho API Console. Any agent using it stops working immediately, so plan for that.

Zoho limits active refresh tokens to 20 per user. When the limit is exceeded, the oldest token is invalidated automatically. Reuse one refresh token per organization instead of generating a fresh one for every host.
