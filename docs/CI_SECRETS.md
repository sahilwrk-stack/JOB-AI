# GitHub Actions — CI database

PostgreSQL in `.github/workflows/deploy.yml` is started with **Docker** on the runner. A **random password** is generated in the first step (`openssl rand -hex 24`), written to `GITHUB_ENV` as `DATABASE_URL_CI`, and **masked in logs** (`::add-mask::`). Nothing is stored in the repository or in a static workflow secret for the database.

Optional secrets used elsewhere in the same workflow (only if you enable those jobs):

| Secret | Used for |
|--------|----------|
| `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` | Push Docker image |
| `RENDER_DEPLOY_HOOK_URL` | Trigger Render deploy |
| `NETLIFY_AUTH_TOKEN` / `NETLIFY_SITE_ID` | Netlify deploy |
