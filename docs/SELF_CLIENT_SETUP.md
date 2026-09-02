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

---

## 3. Generate a grant token

Still in the API Console, open your Self Client and switch to the **Generate Code** tab.

1. **Scope** — enter the scopes you need, comma separated. See the table below.
2. **Time Duration** — pick the longest available, usually 10 minutes.
3. **Scope Description** — any short text, for example `attachment bridge`.
4. Click **Create**, select the target organization if you are asked, then copy the generated code.

> ⚠️ The grant token expires within minutes and can only be exchanged **once**. Go straight to step 4.

### Scopes

Request the narrowest scope that covers your use case.

| App | Purpose | Scope |
|---|---|---|
| Books | expense receipts | `ZohoBooks.expenses.CREATE` |
| Books | bill attachments | `ZohoBooks.bills.CREATE` |
| Books | verify uploads by reading back | `ZohoBooks.expenses.READ`, `ZohoBooks.bills.READ` |
| CRM | record attachments | `ZohoCRM.modules.attachments.CREATE`, `ZohoCRM.modules.attachments.READ` |
| Projects | task and comment attachments | `ZohoProjects.projects.ALL`, `ZohoProjects.tasks.ALL` |
| WorkDrive | file upload | `WorkDrive.files.CREATE`, `WorkDrive.files.READ` |

Read scopes are not optional. The bridge verifies every upload by reading the record back, and without read access it cannot tell success from silent failure.

Avoid `*.fullaccess.ALL`. It works, but it hands an automated tool far more power than it needs.

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

Refresh tokens do not expire on their own. To revoke access, delete the Self Client in the API Console. Any agent using it stops working immediately, so plan for that.

Zoho limits the number of refresh tokens per client. Reuse one refresh token per organization instead of generating a fresh one for every host.
