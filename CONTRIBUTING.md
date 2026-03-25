# Contributing to HERMES

Thanks for your interest in improving HERMES.

This project combines firmware, edge Linux services, and an optional home-mode cognition pipeline. Changes should be incremental, testable, and easy to roll back.

## Development Workflow

- `main` is the only long-lived branch.
- Create a short-lived branch for each change.
- Open a PR into `main`.
- Delete the branch after merge.

## Ground Rules

- Prefer small, focused PRs over broad refactors.
- Reuse existing patterns before introducing abstractions.
- Keep hardware-adjacent changes explicit and well documented.
- Avoid unrelated edits in the same PR.

## Local Validation

Run relevant checks before requesting review.

For `hermes-brain` changes:

```bash
cd hermes-brain
pytest
```

For dashboard changes, run the service locally and verify UI/API behavior:

```bash
cd hermes/linux/odroid/services/dashboard
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Firmware and Hardware Notes

- Firmware and hardware paths are sensitive to physical setup and bench wiring.
- If your change depends on specific hardware behavior, document assumptions in the PR.
- Do not merge changes that modify production wiring expectations without matching docs updates.

## PR Checklist

- [ ] Scope is focused and clearly described
- [ ] Relevant tests/checks were run locally
- [ ] Docs updated for user-visible or operator-visible changes
- [ ] No accidental secrets, credentials, or machine-local files are included

## Security

If you discover a security issue, avoid opening a public issue with exploit details. Follow the project's security process in repository docs.
