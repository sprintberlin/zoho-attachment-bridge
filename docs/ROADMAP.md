# Roadmap

Tracked in GitHub issues. This file is the index, not a second tracker.

Current release: **0.4.0 (unreleased)**. Books expense receipts are implemented and verified live. Bill and journal attachments are implemented but only unit-tested. CRM v8 record attachments and WorkDrive file/version uploads are implemented with mocked upload/download SHA-256 verification.

## 0.1.0 — scaffolding

Completed. Repository, configuration contract, MCP failure documentation, Self Client guide.

## 0.2.0 — Books

- [x] Expense receipt upload (`POST /expenses/{id}/receipt`) — verified live
- [x] Bill attachment upload (`POST /bills/{id}/attachment`) — unit tests only
- [x] Journal attachment upload (`POST /journals/{id}/attachment`) — unit tests only ([#12](https://github.com/sprintberlin/zoho-attachment-bridge/issues/12))
- [x] SHA-256 read-back verification
- [x] Persistent access token cache (mode 0600)
- [x] HTTP 429 backoff
- [x] File size pre-check (local rejection before multipart construction, configurable per target) — [#2](https://github.com/sprintberlin/zoho-attachment-bridge/issues/2)
- [ ] Live-verify bill attachments — [#1](https://github.com/sprintberlin/zoho-attachment-bridge/issues/1)

## 0.3.0 — CRM and Projects

- [x] CRM record attachments (`POST /crm/v8/{module}/{record_id}/Attachments`) — mocked upload/list/download SHA-256 verification ([#3](https://github.com/sprintberlin/zoho-attachment-bridge/issues/3))
- [x] Projects task and comment attachments (`POST /restapi/portal/{portal}/projects/{project}/tasks/{task}/attachments/` and `/comments/`) — mocked upload/list/download SHA-256 verification ([#4](https://github.com/sprintberlin/zoho-attachment-bridge/issues/4))
- [x] Organization and portal resolution helpers — [#10](https://github.com/sprintberlin/zoho-attachment-bridge/issues/10)

## 0.4.0 — Inventory and WorkDrive

Companion MCP skill: [openclaw-zoho-workdrive-mcp-skill](https://github.com/sprintberlin/openclaw-zoho-workdrive-mcp-skill) (resolves teams, team folders, folders, and resource IDs via MCP before uploads).

- [ ] Inventory item images and bill attachments — [#8](https://github.com/sprintberlin/zoho-attachment-bridge/issues/8)
- [x] WorkDrive file upload and new version (`POST /workdrive/api/v1/upload` with multipart `content`; `override-name-exist=true` for a new version), plus dedicated-download-host SHA-256 read-back — mocked verification ([#9](https://github.com/sprintberlin/zoho-attachment-bridge/issues/9))
- [x] WorkDrive download CLI (`scripts/zoho_download.py`) writing bytes from the dedicated download host to a local path with SHA-256 reporting — mocked verification ([#16](https://github.com/sprintberlin/zoho-attachment-bridge/issues/16))

## 1.0.0 — release

- [ ] Named profiles verified on two data centers — [#11](https://github.com/sprintberlin/zoho-attachment-bridge/issues/11)
- [x] Safer onboarding (hidden secret entry, grant code file/stdin support, no credentials in chat) — [#5](https://github.com/sprintberlin/zoho-attachment-bridge/issues/5)
- [x] Document multi-host Self Client setup — [#6](https://github.com/sprintberlin/zoho-attachment-bridge/issues/6)
- [ ] ClawHub publication — [#7](https://github.com/sprintberlin/zoho-attachment-bridge/issues/7)

## Working on this repo

- [x] Issue-first daily-use contribution loop for humans and agents — [#14](https://github.com/sprintberlin/zoho-attachment-bridge/issues/14)

Follow [CONTRIBUTING.md](../CONTRIBUTING.md): search or file an issue first; when feasible, implement it through branch, tests, PR, CI, and merge.

## Out of scope for now

- HTTP service mode. The scripts stay callable from the command line.
- Inline image extraction and OCR. Plain downloads are covered since [#16](https://github.com/sprintberlin/zoho-attachment-bridge/issues/16); MCP returns metadata only and cannot deliver file bytes into the agent workspace.

## Watch list

If either of these lands, re-evaluate whether this skill is still needed:

- [SEP-2631](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/1197) leaving draft status
- Zoho fixing the binary parameter mapping in its MCP servers
