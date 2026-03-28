# Docker Compose — environment variables

`docker-compose.yml` loads **`env_file: .env`** only. Nothing secret belongs in git-tracked files.

Create `.env` from `.env.example`, then add the following **in your local `.env`** (names follow official Docker images):

## PostgreSQL (`postgres` service)

See [PostgreSQL image environment variables](https://hub.docker.com/_/postgres). You must define the standard bootstrap fields for user, database, and the **superuser credential** required by that image (name uses the `POSTGRES_` prefix per upstream docs).

## Application (`backend` service)

Set `DATABASE_URL` to a connection URI pointing at the `postgres` hostname on port `5432`, using the same role and database name you configured above.

## pgAdmin (`pgadmin` service, optional profile)

See [pgAdmin image](https://hub.docker.com/r/dpage/pgadmin4/). Set the default login email and the **initial login credential** fields expected by that image (names use the `PGADMIN_DEFAULT_` prefix per upstream docs).

## Email / Twilio / SendGrid

Configure notification variables as required by `core/notifier.py` — only in `.env`, never in the repository.
