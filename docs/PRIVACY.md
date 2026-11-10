# Privacy and Public-release Policy

This project is intended for a **public GitHub repository**, but that does not make real conversations public data.

## Safe to publish

- General design principles and architecture.
- Synthetic or deliberately public example memories.
- De-identified milestones, tests, and scientific limitations.

## Keep local/private unless individually authorized

- QQ chat transcripts, usernames, images, contact identifiers, and media.
- DeepSeek/OpenClaw API keys, tokens, session files, passwords and `.env` files.
- Original private discussions or personal memory profiles.
- SQLite `*.sqlite`, `*.db`, their journal/WAL files and runtime logs.
- Screenshots or logs that could reveal account credentials or machine paths.

## Enforcement in this prototype

- `add` uses `private` by default.
- There is no bulk private-memory export command.
- `export-public` writes only memories explicitly labeled `public`, excluding superseded ones; review the exported file before upload.
- Local SQLite databases are protected from ordinary Git additions via `.gitignore`.

**Warning:** `.gitignore` does not remove items committed in earlier Git history. Before turning an existing repository public, inspect its entire commit history for secrets/personal content. Revoke/rotate any exposed credentials; simply deleting a file in a later commit is insufficient.

Do not merge the original personal project log into the public journal. The public journal is a redacted research summary.
