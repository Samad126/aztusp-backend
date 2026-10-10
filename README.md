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

- The site **password is not stored**, with one exception: students who turn on change notifications. Their password is kept **encrypted** so the watcher can check their scores every 30 minutes. Turning notifications off or logging out deletes it.
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
| `GET` | `/api/v1/me/schedule` | ✔ | Lecture timetable (one block per semester in `sections.semesters`) |
| `GET` | `/api/v1/me/notices` | ✔ | Notices |
| `GET` | `/api/v1/me/notifications` | ✔ | Change notifications: channels, watched fields, status of the last check |
| `PUT` | `/api/v1/me/notifications` | ✔ | Turn notifications on or update them; the password is checked with the site |
| `DELETE` | `/api/v1/me/notifications` | ✔ | Turn notifications off and delete the saved password |
| `GET` | `/api/v1/me/telegram` | ✔ | Whether a Telegram chat is connected |
| `POST` | `/api/v1/me/telegram/link` | ✔ | Link that connects Telegram when opened and started (works once, 15 minutes) |
| `DELETE` | `/api/v1/me/telegram` | ✔ | Disconnect Telegram |
| `GET` | `/api/v1/courses` | ✔ | Courses linked from the dashboard |
| `GET` | `/api/v1/courses/{lec_open_idx}/plan` | ✔ | Lecture plan of one course |
| `GET` | `/api/v1/courses/{lec_open_idx}/notices` | ✔ | Course notices (Bildiriş) |
| `GET` | `/api/v1/courses/{lec_open_idx}/board` | ✔ | Course forum (Forum) |
| `GET` | `/api/v1/courses/{lec_open_idx}/materials` | ✔ | Course materials (Didaktik materiallar) |
| `GET` | `/api/v1/courses/{lec_open_idx}/tasks` | ✔ | Course assessments and assignments (Qiymətləndirmə) |
| `GET` | `/api/v1/courses/{lec_open_idx}/scores` | ✔ | Current points per component and the total |
| `GET` | `/api/v1/courses/{lec_open_idx}/attendance` | ✔ | Attendance journal: per-class marks, score and percentage |

The list tabs (`notices`, `board`, `materials`, `tasks`) return `items`: rows keyed in English (`subject`, `author`, `date`, `views`, ...), plus `id` when the row opens a detail view and `link` for its first link. An empty `items` means nothing was posted. `scores` returns the table as shown (`table`, every cell kept), the same split into `components` (name, max, score) and the `total`. `attendance` returns `info`, the mark `legend`, `header` values, the class `sessions` (dates) and every `students` row with one mark per class.

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
| `APP_SECRET_KEY` | ✔ | Fernet key that encrypts stored cookies and notification passwords. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it stable: changing it makes stored sessions unreadable and users must log in again. |
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
  db.py              PostgreSQL store (hashed tokens, encrypted cookies, notification settings and passwords)
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
  watcher/           grade watcher (python -m app.watcher): checks, saved snapshot, mail and Telegram
tests/               pytest suite
deploy/nginx/   nginx config for the API domain
.github/workflows/ci.yml   tests, then deploy to the server
```

## Deployment

[ci.yml](.github/workflows/ci.yml) runs the tests and a Docker build on every push and
pull request. On a push to `main` it then deploys over SSH: it resets the server checkout
to `origin/main`, runs `docker compose up -d --build api watcher`, and polls `/health` until the
API container is healthy.

GitHub Actions secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `SSH_HOST` | Server hostname or IP |
| `SSH_USER` | SSH user |
| `SSH_PASSWORD` | That user's password |

On the server, the checkout needs a `.env` next to `docker-compose.yml`. The API binds to
`127.0.0.1:3003`; put nginx in front of it using [deploy/nginx/aztuapi.alakbaroff.com.conf](deploy/nginx/aztuapi.alakbaroff.com.conf).
The nginx config is not installed by the workflow; copy it to `/etc/nginx/conf.d/` and reload nginx by hand.

## Grade watcher

`python -m app.watcher` checks the scores of every student who turned change notifications on, every
`WATCH_INTERVAL_MINUTES` (default 30). For each student it signs in with their saved password, reads the scores
table, and sends an email and/or a Telegram message when a watched field changes (by default `final_score` and
`grade`). Each message names the course, the semester and the new result. The first check only records the
current results, so nothing old is announced.

**Students never send a chat id.** Telegram is connected from the frontend:

1. `POST /api/v1/me/telegram/link` returns a `url` like `https://t.me/<bot>?start=<code>`. Show it as a button.
2. The student opens it and presses **Start** in Telegram. The watcher receives `/start <code>`, connects that
   chat to the account, and the bot confirms.
3. `GET /api/v1/me/telegram` returns `{"linked": true}` once it worked.

The code works once and expires after 15 minutes. Then the student turns notifications on with
`PUT /api/v1/me/notifications`, sending their site password, the fields to watch and, optionally, an email.
An email, a connected Telegram, or both are accepted. The response includes:

- `status`: `ok` (the last check worked), `wrong_password` (the site rejected the saved password; checks stop for
  that student until they save settings again, so the watcher doesn't keep retrying and risk locking the account),
  or `error` (the last check failed, usually because the site was down).
- `last_checked_at`: when the last check ran.
- `telegram_linked`: whether a Telegram chat is connected.

The frontend should show `wrong_password` and `error` to the student.

**What is stored:** the site password, encrypted with `APP_SECRET_KEY`, plus the email, the fields, the last seen
results and the Telegram chat id. Turning notifications off keeps the Telegram link. Disconnecting Telegram
(`DELETE /api/v1/me/telegram`) needs an email set first, so messages don't silently stop. Logging out deletes
all of it. The watcher signs in on every check rather than reusing the session, because a portal session lasts
too short a time.

Shared settings (in `.env`, used for every student):

| Variable | Description |
|---|---|
| `WATCH_INTERVAL_MINUTES` | Minutes between checks (default `30`) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM` | The mail account that sends emails. Port 587 uses STARTTLS, 465 uses SSL |
| `TELEGRAM_BOT_TOKEN` | Token of the bot. The watcher uses it to answer link messages and send messages |
| `TELEGRAM_BOT_USERNAME` | The bot's username without `@`. The API uses it to build the link |

Set at least one channel. A channel is enabled once any of its variables is set, and a partly filled channel is a
startup error.

**Brevo (mail):** verify the sender address in Brevo first (Senders). Then, on the "SMTP & API" page, copy the SMTP
login and generate an SMTP key (not an API key). Use `SMTP_HOST=smtp-relay.brevo.com`, `SMTP_PORT=587`,
`SMTP_USERNAME` = the login shown there, `SMTP_PASSWORD` = the SMTP key, and `MAIL_FROM` = the verified sender.

**Telegram:** create a bot with @BotFather (`/newbot`). Put its token in `TELEGRAM_BOT_TOKEN` and its username in
`TELEGRAM_BOT_USERNAME`. Only one process can read the bot's messages at a time, so don't run the watcher on the
server and locally together.

```bash
python -m app.watcher --test-email you@example.com   # test the mail account
python -m app.watcher --test-telegram 123456789      # test the bot with one chat id (operator only)
python -m app.watcher --once                         # check every student once
docker compose up -d --build watcher                 # run the loop on the server
```

The deploy workflow rebuilds `api` and `watcher` on every push to `main`. The watcher's health is not checked,
so look at `docker compose logs watcher` after a deploy.

## Disclaimer

This project is not affiliated with or endorsed by the university. It only accesses a
user's own data, using credentials that user provides. Use it responsibly and in line
with the portal's terms.

## License

[MIT](LICENSE)
