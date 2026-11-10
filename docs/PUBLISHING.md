# Publishing Project ECHO safely

This package is public-ready **only for the new files listed in `README.md`**. No assumption has been made about any pre-existing repository content or Git commit history.

## Before switching an existing repository to public

1. Review every existing file and commit for private chats, identity information, tokens, keys, local paths and credentials.
2. If a credential has ever been committed, revoke or rotate it. A later commit deleting a key does not make an earlier commit safe.
3. Do not upload local SQLite memory files, the original private inception transcript, `.env` or OpenClaw/QQ session files.
4. Review generated public journal and curated seed data for suitability.

## Simple browser publishing

1. Open the existing repository at `https://github.com/ouo-77/Project-ECHO` while signed in to the owning GitHub account.
2. On **Code**, use **Add file > Upload files** to upload the **contents** of this package (retain directory paths). Review changes and commit to the intended branch. If there are already conflicting paths, inspect instead of overwriting blindly.
3. In the repository **Settings**, scroll to **Danger Zone** and choose **Change repository visibility > Make public**, confirming GitHub's warnings **only after reviewing the repository and its commit history**.
4. Verify the README, both public journal entries, and tests are visible without being signed in.

An authorized local Git/CLI workflow is also possible, but never paste GitHub tokens into this chat or scripts. 

## Future collaborator automation

Grant the ChatGPT GitHub connection access to `ouo-77/Project-ECHO` if you want assistant-created commits in the future. Without an installed repository grant, GitHub returns `404` for this private repository. Current connector actions cannot change repository visibility by themselves.
