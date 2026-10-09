# AZTUSP Backend

[![CI](https://github.com/Samad126/aztusp-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/Samad126/aztusp-backend/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A FastAPI service that lets each student sign in with **their own** university
account and read their data (profile, scores, timetable, notices, courses and
lecture plans) as clean JSON.

It logs in to the university portal on the user's behalf, scrapes the dashboard
pages, and returns structured data with English field names.

## How it works

1. `POST /api/v1/auth/login` with the site username and password. The service signs in
   to the portal and returns an API token.
2. Every other request sends that token as `Authorization: Bearer <token>`.
3. The service loads that user's saved portal session, scrapes the requested page, and returns JSON.

### Privacy and security

- The site **password is never stored**. It is used once to sign in.
- Only the portal's **session cookies** are stored, **encrypted** (Fernet) in PostgreSQL.
- API tokens are stored as **SHA-256 hashes**; a lost token cannot be recovered, only replaced by logging in again.
- When the portal session expires the API answers `401` and the user logs in again.

## API

Interactive documentation is served at `/docs` (Swagger UI) and `/redoc`; the
raw OpenAPI document is at `/openapi.json`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | – | Sign in with a site account, returns a token |
| `POST` | `/api/v1/auth/logout` | ✔ | Delete the token and stored session |
| `GET` | `/health` | – | Health check (unversioned) |
| `GET` | `/api/v1/me/profile` | ✔ | Student profile |
| `GET` | `/api/v1/me/scores` | ✔ | Scores and semester results |
| `GET` | `/api/v1/me/schedule` | ✔ | Lecture timetable |
| `GET` | `/api/v1/me/notices` | ✔ | Notices |
| `GET` | `/api/v1/courses` | ✔ | Courses linked from the dashboard |
| `GET` | `/api/v1/courses/{lec_open_idx}/plan` | ✔ | Lecture plan of one course |
| `GET` | `/api/v1/courses/{lec_open_idx}/notices` | ✔ | Course notices |
| `GET` | `/api/v1/courses/{lec_open_idx}/board` | ✔ | Course board |
| `GET` | `/api/v1/courses/{lec_open_idx}/materials` | ✔ | Course materials (lecture data) |
| `GET` | `/api/v1/courses/{lec_open_idx}/tasks` | ✔ | Course tasks |
| `GET` | `/api/v1/courses/{lec_open_idx}/scores` | ✔ | Course scores |
| `GET` | `/api/v1/courses/{lec_open_idx}/attendance` | ✔ | Course attendance |

The course tab endpoints (`notices`, `board`, `materials`, `tasks`, `scores`, `attendance`) return each page's tables as `blocks` of records, with any link in a row under `link`. They read pages generically, so column names are the site's own (Azerbaijani) unless listed in `FIELD_MAP`.

Every data endpoint scrapes the university site live, so a call takes as long as the portal needs to respond. If the portal session has expired the endpoint answers `401` and you log in again.

`/health` is unversioned (probed by Docker, CI and nginx); everything else lives under `/api/v1`.

Errors are returned as `{"detail": "..."}`: `401` bad/missing token or expired
site session, `404` unknown target or course, `502` the portal is unreachable.

### Example

```bash
# 1. log in and keep the token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "M0000000000", "password": "your-site-password"}' | jq -r .token)

# 2. use it
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/me/scores
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/courses
```

In Swagger UI, call `POST /api/v1/auth/login`, click **Authorize**, and paste the token.

## Configuration

Copy [.env.example](.env.example) to `.env`.

| Variable | Required | Description |
|---|---|---|
| `APP_SECRET_KEY` | ✔ | Fernet key that encrypts stored cookies. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it stable: changing it makes stored sessions unreadable and users must log in again. |
| `BASE_DOMAIN` | ✔ | Parent domain shared by the login and dashboard hosts |
| `LOGIN_URL` | ✔ | Portal login page |
| `DASHBOARD_URL` | ✔ | Dashboard home page (must be on `BASE_DOMAIN`) |
| `DATABASE_URL` | ✔ locally | PostgreSQL URL. Overridden by Docker Compose, which points it at the `db` service |
| `POSTGRES_PASSWORD` | Docker | Password for the Compose Postgres container (default `aztusp`). Use letters and digits only, e.g. `openssl rand -hex 24`. Set it **before** the first start: Postgres only reads it when the data volume is created |
| `USERNAME_FIELD`, `PASSWORD_FIELD` | – | Names of the login form inputs (default `username`, `password`) |
| `REQUEST_TIMEOUT` | – | Seconds per request to the portal (default `20`) |

## Running

### Docker Compose

```bash
cp .env.example .env      # fill it in
docker compose up --build
```

The API is on `http://127.0.0.1:8000` (change with `API_PORT`). The stack is
`api` + `db` (PostgreSQL 16, data in the `pgdata` volume). Locally, `docker-compose.override.yml`
also publishes Postgres on `127.0.0.1:5432`; it is skipped when you run with `-f docker-compose.yml`.

### Without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d db                 # PostgreSQL
uvicorn app.main:app --reload
```

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## Project layout

```
app/
  main.py            app factory: metadata, error handlers, router mounting under /api/v1
  config.py          environment configuration
  db.py              PostgreSQL user store (hashed tokens, encrypted cookies)
  schemas.py         request/response models
  api/
    deps.py          settings, user store and per-request authenticated scraper
    errors.py        exception -> HTTP status handlers, shared error responses
    routers/         auth, me (profile/scores/...), courses, system (health)
  scraping/
    client.py        portal login, session handling, redirects
    parsing.py       HTML table/pair parsing and field-name translation
    targets.py       what to scrape (CSS selectors) and the field-name translations
    courses.py       course list and lecture plan scraping
    cookies.py       shares session cookies across portal subdomains
tests/               pytest suite
deploy/nginx/   nginx config for the API and frontend domains
.github/workflows/ci.yml   tests, then deploy to the server
```

## Deployment

[ci.yml](.github/workflows/ci.yml) runs the tests and a Docker build on every push and
pull request. On a push to `main` it then deploys over SSH: it resets the server checkout
to `origin/main`, runs `docker compose up -d --build api`, and polls `/health` until the
container is healthy.

GitHub Actions secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `SSH_HOST` | Server hostname or IP |
| `SSH_USER` | SSH user |
| `SSH_PASSWORD` | That user's password |

On the server, the checkout needs a `.env` next to `docker-compose.yml`. The API binds to
`127.0.0.1:3003`; put nginx in front of it using [deploy/nginx/aztu.alakbaroff.com.conf](deploy/nginx/aztu.alakbaroff.com.conf).
The nginx config is not installed by the workflow; copy it to `/etc/nginx/conf.d/` and reload nginx by hand.

## Disclaimer

This project is not affiliated with or endorsed by the university. It only accesses a
user's own data, using credentials that user provides. Use it responsibly and in line
with the portal's terms.

## License

[MIT](LICENSE)
