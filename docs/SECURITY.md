# Security

## Local secrets

- Use **`.env`** for real values (create from **`.env.example`**). It must **never** be committed.
- If you are unsure whether `.env` was tracked:  
  `git ls-files --error-unmatch .env` → error means good (not tracked).  
  `git log --all --oneline -- .env` → should be empty.

## If credentials may have been exposed

1. **Jobber Developer Center** — rotate or regenerate the **client secret**, and revoke / invalidate **access tokens** you no longer trust. Jobber may also offer “reset secret” or app reinstall in their docs.
2. **Tokens in `.env`** — treat `JOBBER_ACCESS_TOKEN` as compromised; generate a new token or reconnect via OAuth after rotating secrets.
3. **Git history** — if `.env` was **ever** pushed to GitHub, deleting the file in a new commit is **not** enough. You must remove it from history (e.g. [GitHub: Removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)) or contact GitHub Support, then rotate all exposed secrets anyway.
4. **Enable GitHub secret scanning** on the repository where available.

## This repository

Only **`.env.example`** (placeholders) belongs in git. Real `JOBBER_CLIENT_ID`, `JOBBER_CLIENT_SECRET`, and `JOBBER_ACCESS_TOKEN` must exist only in your local `.env` or your deployment secret store.
