# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-09-11

### Added

- CRM v8 record attachment adapter with `--app crm --target record-attachment`
  and mandatory `--module`; CRM does not use a Books `organization_id`.
- Multipart upload using `POST https://www.zohoapis.<dc>/crm/v8/{module}/{record_id}/Attachments`
  and form field `file`.
- Mandatory CRM read-back verification: list attachments with `fields=id,File_Name`,
  identify the just-uploaded attachment, download it, and compare SHA-256.
  Verification rejects ambiguous matching filenames rather than accepting an
  unprovable result.
- Mock-only unit coverage for CRM URLs, multipart field, attachment discovery,
  download, SHA-256 match/failure paths, CLI module requirement, and the fact
  that CRM does not require an organization ID.
- CRM Self Client scope documentation: `ZohoCRM.modules.ALL`,
  `ZohoCRM.modules.attachments.CREATE`, and `ZohoCRM.modules.attachments.READ`.
- Strict validation for CRM module API names and numeric record/attachment IDs;
  the bridge does not invent a CRM file-extension allowlist that Zoho's v8
  attachment documentation does not publish.

### Verified

- The CRM implementation was tested entirely with mocks and temporary files;
  no live Zoho call, account ID, record ID, or secret was used.

## [0.2.0] — 2026-09-02

First working release. Expense receipt uploads are verified against a live
Zoho Books test account.

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

The live test returned the expected success response, and read-back returned
the exact uploaded bytes with a matching SHA-256 digest. No account IDs,
record IDs, filenames or credentials belong in this public repository.

### Known limitations

- Bill attachment upload is covered by unit tests only. No live verification yet
  (see issue tracker).
- No file size pre-check before upload.
- CRM, Projects, Inventory and WorkDrive were not implemented in this release.

## [0.1.0] — 2026-09-02

### Added

- Repository scaffolding, `SKILL.md` configuration contract, `README.md`
  documenting the Zoho MCP silent-failure behaviour, Self Client setup guide,
  roadmap, MIT license.
