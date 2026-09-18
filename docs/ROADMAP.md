# Roadmap

Tracked in GitHub issues. This file is the index, not a second tracker.

Current release: **0.4.0 (unreleased)**. Books expense receipts are implemented and verified live. Bill attachments are implemented but only unit-tested. CRM v8 record attachments and WorkDrive file/version uploads are implemented with mocked upload/download SHA-256 verification.

## 0.1.0 — scaffolding

Completed. Repository, configuration contract, MCP failure documentation, Self Client guide.

## 0.2.0 — Books

- [x] Expense receipt upload (`POST /expenses/{id}/receipt`) — verified live
- [x] Bill attachment upload (`POST /bills/{id}/attachment`) — unit tests only
- [x] SHA-256 read-back verification
- [x] Persistent access token cache (mode 0600)
- [x] HTTP 429 backoff
- [x] File size pre-check (local rejection before multipart construction, configurable per target) — [#2](https://github.com/sprintberlin/zoho-attachment-bridge/issues/2)
- [ ] Live-verify bill attachments — [#1](https://github.com/sprintberlin/zoho-attachment-bridge/issues/1)

## 0.3.0 — CRM and Projects

- [x] CRM record attachments (`POST /crm/v8/{module}/{record_id}/Attachments`) — mocked upload/list/download SHA-256 verification ([#3](https://github.com/sprintberlin/zoho-attachment-bridge/issues/3))
- [ ] Projects task and comment attachments — [#4](https://github.com/sprintberlin/zoho-attachment-bridge/issues/4)
- [x] Organization and portal resolution helpers — [#10](https://github.com/sprintberlin/zoho-attachment-bridge/issues/10)

## 0.4.0 — Inventory and WorkDrive

Companion MCP skill: [openclaw-zoho-workdrive-mcp-skill](https://github.com/sprintberlin/openclaw-zoho-workdrive-mcp-skill) (resolves teams, team folders, folders, and resource IDs via MCP before uploads).

- [ ] Inventory item images and bill attachments — [#8](https://github.com/sprintberlin/zoho-attachment-bridge/issues/8)
- [x] WorkDrive file upload and new version (`POST /workdrive/api/v1/upload` with multipart `content`; `override-name-exist=true` for a new version), plus dedicated-download-host SHA-256 read-back — mocked verification ([#9](https://github.com/sprintberlin/zoho-attachment-bridge/issues/9))

## 1.0.0 — release

- [ ] Named profiles verified on two data centers — [#11](https://github.com/sprintberlin/zoho-attachment-bridge/issues/11)
- [x] Safer onboarding (hidden secret entry, grant code file/stdin support, no credentials in chat) — [#5](https://github.com/sprintberlin/zoho-attachment-bridge/issues/5)
- [x] Document multi-host Self Client setup — [#6](https://github.com/sprintberlin/zoho-attachment-bridge/issues/6)
- [ ] ClawHub publication — [#7](https://github.com/sprintberlin/zoho-attachment-bridge/issues/7)

## Working on this repo

1. Read `README.md` and `SKILL.md`.
2. Pick an open issue. Verify official Zoho API docs (endpoint, scopes, file types) **before** writing upload code. Do not copy the Books allowlists.
3. Keep the contract: real `multipart/form-data`, exit 0 only after SHA-256 read-back, no secrets in logs or the public repo.
4. Add or update unit tests. Do not make live Zoho calls from CI.
5. Update `CHANGELOG.md` and this file when the issue lands.

## Out of scope for now

- HTTP service mode. The scripts stay callable from the command line.
- Download and inline image extraction. Reading is what MCP already does well.

## Watch list

If either of these lands, re-evaluate whether this skill is still needed:

- [SEP-2631](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/1197) leaving draft status
- Zoho fixing the binary parameter mapping in its MCP servers
