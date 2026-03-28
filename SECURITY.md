# Security policy

## Reporting issues

Please report security vulnerabilities via [GitHub Security Advisories](https://github.com/sahilwrk-stack/JOB-AI/security/advisories/new) (or open a private issue if advisories are unavailable).

## Secrets

- **Never commit** `.env`, API keys, database passwords, or tokens.
- Use **environment variables** and your platform’s secret stores (GitHub Actions secrets, Railway/Render dashboards, etc.).
- For local Docker Compose, copy `.env.example` to `.env` and add DB/pgAdmin fields per [docs/DOCKER_ENV.md](docs/DOCKER_ENV.md) (never commit filled `.env`).

## If a password was exposed in git history

1. **Rotate** the credential everywhere it was used (databases, dashboards, third-party services).
2. **Purge** secrets from history (`git filter-repo` or BFG Repo-Cleaner) and **force-push** protected branches only after coordination with collaborators.
3. Invalidate old keys at the provider (GitHub PAT, RapidAPI, etc.).

## CI

See [docs/CI_SECRETS.md](docs/CI_SECRETS.md) for optional deploy-related secrets (Docker Hub, Render, Netlify).
