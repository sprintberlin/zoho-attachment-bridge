# Contributing

Contributions from humans and agents are explicitly welcome. Daily use is the discovery loop: every reproducible defect, missing capability, safer check, or useful simplification belongs upstream.

## Workflow

1. Search open issues first:
   ```bash
   REPO=sprintberlin/zoho-attachment-bridge
   gh issue list --repo "$REPO" --state open --search "<terms>"
   ```
2. If a matching issue exists, link it. Otherwise create one immediately:
   ```bash
   gh issue create --repo "$REPO" \
     --title "<short imperative summary>" \
     --body "<sanitized requirement, actual vs. expected behavior, and acceptance criteria>"
   ```
3. If implementation is feasible now, deliver it end-to-end:
   ```bash
   git switch -c "<type>/<short-name>"
   python3 -m unittest discover -s tests -v
   git add <files>
   git commit -m "<conventional commit message>" -m "Closes #<issue>"
   git push -u origin HEAD
   gh pr create --repo "$REPO" --base main --title "<title>" --body "Closes #<issue>"
   gh pr checks --repo "$REPO" --watch
   gh pr merge --repo "$REPO" --squash --delete-branch
   git switch main && git pull --ff-only
   ```
4. If implementation is not feasible now, filing the issue is mandatory. Return the issue URL.
5. Never leave uncommitted repository changes behind. A task is only complete when `main` is clean, or when the PR URL and blocker are explicitly reported.

## Rules

- Verify official Zoho API docs for the target app before writing upload code: endpoint, OAuth scopes, multipart field name, file-type allowlist, size limits. Do not copy Books allowlists or scopes to another app.
- Real `multipart/form-data`; never trust a success response with an empty `attachment` array.
- Exit `0` only after downloading the uploaded file and verifying its SHA-256 digest.
- Never include secrets, tokens, customer data, record IDs, organization IDs, portal IDs, or live filenames in issues, PRs, or commit messages.
- CI runs unit tests only; never make live network calls from GitHub Actions.
- Keep `SKILL.md` compact and imperative for agents. Put deeper explanations in `README.md` or `docs/`.
- Use native `git` and `gh`; do not add wrapper scripts for GitHub interactions.

## Docs to update with a change

- `CHANGELOG.md`
- `docs/ROADMAP.md` if an issue is completed
- `SKILL.md` / `README.md` coverage tables if status changes
- `docs/SELF_CLIENT_SETUP.md` if new OAuth scopes are required
