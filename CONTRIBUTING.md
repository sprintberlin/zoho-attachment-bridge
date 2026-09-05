# Contributing

This is a public repository. Treat it as such.

## Start here

1. Read `README.md` and `SKILL.md`.
2. Pick an [open issue](https://github.com/sprintberlin/zoho-attachment-bridge/issues). The index is `docs/ROADMAP.md`.
3. Verify the official Zoho API docs for the target app **before** writing upload code: endpoint, OAuth scopes, multipart field name, file-type allowlist, size limits.
4. Do not copy the Books allowlists or scopes to another app.

## Contract

- Real `multipart/form-data`. Never trust a success status with an empty attachment array.
- Exit code `0` only after SHA-256 read-back of the uploaded bytes.
- No secrets, account IDs, record IDs or live filenames in this repository.
- Unit tests only in CI. No live Zoho calls from GitHub Actions.
- Keep `SKILL.md` compact. Longer explanation belongs in `README.md`, `docs/` or the changelog.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Docs to update with the change

- `CHANGELOG.md`
- `docs/ROADMAP.md` if an issue is completed
- `SKILL.md` / `README.md` coverage tables if status changes
- `docs/SELF_CLIENT_SETUP.md` if new OAuth scopes are required
