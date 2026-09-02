# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-09-02

First working release. Expense receipt uploads are verified against a live
Zoho Books organization on the EU data center.

### Added

- `scripts/bridge.py` — stdlib-only core library: Self Client OAuth, multipart
  builder, Books adapters, SHA-256 read-back verification.
- `scripts/zoho_attach.py` — CLI for `--app books --target expense-receipt|bill-attachment`.
- `scripts/onboarding.py` — interactive grant code exchange that preserves
  unrelated lines in the target env file and enforces mode `0600`.
- Persistent access token cache at `~/.cache/zoho-attachment-bridge/tokens.json`
  (mode `0600`, SHA-256 cache key, no secret in clear text). Overridable with
  `ZOHO_BRIDGE_TOKEN_CACHE`.
- Exponential backoff with `Retry-After` support on HTTP 429.
- 33 unit tests using mocks and temporary files. No live calls, no secrets.
- `docs/SELF_CLIENT_SETUP.md` with the exact Books scope string.

### Fixed

Two defects found during live testing that would have made every upload fail:

- **Wrong API host.** `books_base_url()` produced
  `https://books.zoho.zoho.eu/api/v3` — a duplicated domain segment pointing at
  a host that does not serve the Books API. Corrected to
  `https://www.zohoapis.eu/books/v3`, with a dedicated `API_DC_MAP` because the
  API host differs from the accounts host on the Canadian data center
  (`accounts.zohocloud.ca` vs `www.zohoapis.ca`). The original unit test
  asserted the same broken formula and therefore passed.
- **Invented file extension allowlists.** The lists contained `tiff` and `csv`,
  which Zoho rejects, and omitted `xlsx`/`docx`, which Zoho accepts for expense
  receipts. Both lists now match the documented Books allowlists exactly, which
  differ per endpoint.

### Verified

```text
File: zab_test_receipt.pdf (152 bytes, SHA-256: 494672bb164e4e64...)
Upload response: Der Aufwendungsbeleg wurde angehängt.
SUCCESS: Verified: SHA-256 match (494672bb...)
```

Read-back returned 152 bytes with a matching digest, and
`expense_receipt_name` on the record was set to the uploaded filename.

### Known limitations

- Bill attachment upload is covered by unit tests only. No live verification yet
  (see issue tracker).
- No file size pre-check before upload.
- Only Zoho Books is implemented. CRM, Projects, Inventory and WorkDrive are planned.

## [0.1.0] — 2026-09-02

### Added

- Repository scaffolding, `SKILL.md` configuration contract, `README.md`
  documenting the Zoho MCP silent-failure behaviour, Self Client setup guide,
  roadmap, MIT license.
