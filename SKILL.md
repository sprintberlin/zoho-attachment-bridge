---
name: zoho-attachment-bridge
description: Upload and SHA-256-verify binary files in Zoho via REST when MCP uploads fail; improve the skill through issue-first GitHub contributions.
---

# Zoho Attachment Bridge

Use REST multipart uploads for local binary files. Keep MCP for record lookup, reads, and writes.

Source: [sprintberlin/zoho-attachment-bridge](https://github.com/sprintberlin/zoho-attachment-bridge)

## Contract

- An empty `attachment` array is failure, not success.
- Exit `0` only after downloading the uploaded bytes and matching SHA-256.
- Confirm the Books organization or Projects portal before upload.
- Never print, log, commit, or report credentials, endpoints, customer data, record IDs, organization IDs, portal IDs, or live filenames.
- Respect HTTP 429 and `Retry-After`.

## Improve during daily use

Daily use is the discovery loop. Treat every reusable defect, missing capability, safer check, or useful simplification as a contribution.

1. Search open issues immediately; link a match or create one with native `gh`.
2. Include the need, sanitized evidence, expected behavior, and acceptance criteria.
3. If implementation is feasible now: issue → branch → code/docs/tests → PR → CI → merge.
4. If implementation is not feasible now: file the issue before finishing and return its URL.
5. Do not leave uncommitted repository changes. Finish on clean `main`; if merge access is unavailable, return the PR URL and blocker.

```bash
REPO=sprintberlin/zoho-attachment-bridge
gh issue list --repo "$REPO" --state open --search "<terms>"
gh issue create --repo "$REPO" --title "<title>" --body "<sanitized requirement and acceptance criteria>"

python3 -m unittest discover -s tests -v
gh pr create --repo "$REPO" --base main --title "<title>" --body "Closes #<issue>"
gh pr checks --repo "$REPO" --watch
gh pr merge --repo "$REPO" --squash --delete-branch
```

Do not file issues for one-off authentication, rate limits, timeouts, or tenant-specific policy unless bridge code or guidance should change. Use native `git` and `gh`; do not add an issue-reporting wrapper. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Configuration

Required:

| Variable | Meaning |
|---|---|
| `ZOHO_BRIDGE_CLIENT_ID` | Self Client ID |
| `ZOHO_BRIDGE_CLIENT_SECRET` | Self Client secret |
| `ZOHO_BRIDGE_REFRESH_TOKEN` | Refresh token |
| `ZOHO_BRIDGE_DC` | `eu`, `com`, `in`, `com.au`, `jp`, `ca`, `sa`, or `com.cn` |

Optional: `ZOHO_BRIDGE_BOOKS_ORG_ID`, `ZOHO_BRIDGE_PROJECTS_PORTAL_ID`, `ZOHO_BRIDGE_TOKEN_CACHE`.

Named profile: prefix variables with `ZOHO_BRIDGE_<PROFILE>_` and pass `--profile <profile>`.

Access tokens are cached at `~/.cache/zoho-attachment-bridge/tokens.json` with mode `0600`; cache keys are hashed.

## Limits

| Target | Extensions | Default size limit |
|---|---|---:|
| `expense-receipt` | gif, png, jpeg, jpg, bmp, pdf, xls, xlsx, doc, docx | 7 MB |
| `bill-attachment` | gif, png, jpeg, jpg, bmp, pdf | 5 MB |
| `journal-attachment` | extension required; Zoho publishes no allowlist | configurable |
| `record-attachment` | extension required; Zoho publishes no allowlist | configurable |
| `file-upload`, `new-version` | extension required; organization policy applies | 250 MB |
| `task-attachment`, `comment-attachment` | extension required; Zoho publishes no allowlist | configurable |

Override with `--max-bytes` or `ZOHO_BRIDGE_[<PROFILE>_]MAX_BYTES_<TARGET>`.

## Scopes and onboarding

Run `python3 scripts/onboarding.py`.

- Books: `ZohoBooks.expenses.CREATE,ZohoBooks.expenses.READ,ZohoBooks.bills.CREATE,ZohoBooks.bills.READ,ZohoBooks.accountants.CREATE,ZohoBooks.accountants.READ`
- CRM: `ZohoCRM.modules.ALL,ZohoCRM.modules.attachments.CREATE,ZohoCRM.modules.attachments.READ`
- WorkDrive: `WorkDrive.files.CREATE,WorkDrive.files.READ`
- Projects: `ZohoProjects.tasks.READ,ZohoProjects.tasks.CREATE,ZohoPC.files.ALL`
- Optional discovery: `ZohoBooks.settings.READ,ZohoProjects.portals.READ`

Do not use `ZohoBooks.fullaccess.ALL`. Details: [docs/SELF_CLIENT_SETUP.md](docs/SELF_CLIENT_SETUP.md).

## Discover IDs

```bash
python3 scripts/discover.py books-organizations [--profile <name>] [--json]
python3 scripts/discover.py projects-portals [--profile <name>] [--json]
```

## Upload

```bash
# Books
python3 scripts/zoho_attach.py --app books --target expense-receipt --id <expense_id> --file <path> [--organization-id <org_id>]
python3 scripts/zoho_attach.py --app books --target bill-attachment --id <bill_id> --file <path> [--organization-id <org_id>]
python3 scripts/zoho_attach.py --app books --target journal-attachment --id <journal_id> --file <path> [--organization-id <org_id>]

# CRM
python3 scripts/zoho_attach.py --app crm --target record-attachment --module <module> --id <record_id> --file <path>

# WorkDrive: resolve folder ID through MCP first
python3 scripts/zoho_attach.py --app workdrive --target file-upload --id <folder_id> --file <path> [--filename <name>]
python3 scripts/zoho_attach.py --app workdrive --target new-version --id <folder_id> --file <path> --filename <existing_name>

# Projects
python3 scripts/zoho_attach.py --app projects --target task-attachment --project-id <project_id> --id <task_id> --file <path> [--portal-id <portal_id>]
python3 scripts/zoho_attach.py --app projects --target comment-attachment --project-id <project_id> --id <task_id> --file <path> [--comment <text>] [--portal-id <portal_id>]
```

## Coverage

| App | Target | Status |
|---|---|---|
| Books | expense receipt | live verified |
| Books | bill attachment | unit tested; live verification [#1](https://github.com/sprintberlin/zoho-attachment-bridge/issues/1) |
| Books | journal attachment | unit tested ([#12](https://github.com/sprintberlin/zoho-attachment-bridge/issues/12)) |
| CRM | record attachment | mocked upload/list/download verification |
| WorkDrive | file upload, new version | mocked upload/download verification ([#9](https://github.com/sprintberlin/zoho-attachment-bridge/issues/9)) |
| Projects | task and comment attachment | mocked upload/list/download verification |
| Inventory | item image, bill attachment | planned ([#8](https://github.com/sprintberlin/zoho-attachment-bridge/issues/8)) |

Next work: [docs/ROADMAP.md](docs/ROADMAP.md) and [issues](https://github.com/sprintberlin/zoho-attachment-bridge/issues).
