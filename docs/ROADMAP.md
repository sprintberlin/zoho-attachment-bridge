# Roadmap

## 0.1.0 — scaffolding (current)

- [x] Repository structure
- [x] `SKILL.md` with the configuration contract
- [x] `README.md` explaining the MCP failure mode
- [x] Self Client setup guide
- [x] `scripts/onboarding.py` — interactive grant code exchange, writes `.env` with mode 0600
- [x] `scripts/zoho_attach.py` with the Books adapter

## 0.2.0 — Books

- [x] Expense receipt upload (`POST /expenses/{id}/receipt`)
- [x] Bill attachment upload (`POST /bills/{id}/attachment`)
- [x] Read-back verification with SHA-256 for both
- [x] Token cache with automatic refresh (persistent, mode 0600, avoids Zoho token-endpoint rate limiting)
- [x] Retry with exponential backoff on HTTP 429
- [x] Verified live against a Zoho Books test account
- [ ] File size pre-check against the per-plan limit

## 0.3.0 — CRM and Projects

- [ ] CRM record attachment
- [ ] Projects task attachment
- [ ] Projects comment attachment
- [ ] Portal and organization resolution helpers

## 0.4.0 — Inventory and WorkDrive

- [ ] Inventory item image
- [ ] Inventory bill attachment
- [ ] WorkDrive file upload
- [ ] WorkDrive new version

## 1.0.0 — release

- [ ] Multi-profile support verified across two data centers
- [ ] Full test coverage of the verification path
- [ ] ClawHub publication

## Out of scope for now

- HTTP service mode. The scripts stay callable from the command line. If a remote agent ever needs this, a thin wrapper can be added without touching the adapters.
- Download and inline image extraction. Reading is what MCP already does well.

## Watch list

If either of these lands, re-evaluate whether this skill is still needed:

- [SEP-2631](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/1197) leaving draft status
- Zoho fixing the binary parameter mapping in its MCP servers
