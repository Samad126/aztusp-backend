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
   to the portal and returns an API token, which lasts 1 day.
2. Every other request sends that token as `Authorization: Bearer <token>`.
3. The service loads that user's saved portal session, scrapes the requested page, and returns JSON.
4. Students can also turn on change notifications, so they get a message when a watched result changes. See [Grade watcher](#grade-watcher).
5. `POST /api/v1/auth/logout` signs out of the university site too and ends the API session. Saved data is kept, so change notifications keep running.

### Privacy and security

- The site **password is stored**, **encrypted** (Fernet), from the login onward. Change notifications sign in with it, so students don't type it again. A successful `POST /api/v1/me/password` replaces it, and so does logging in again. Logging out and turning notifications off keep it.
- The portal's **session cookies** are stored, **encrypted** (Fernet) in PostgreSQL.
- The **profile photo** is stored in this service's database, not on the university site. It is kept until the student replaces or deletes it. Logging out keeps it.
- Logging out also signs out of the university dashboard and SSO, and drops the session cookies. If the university site can't be reached, the API session still ends.
- API tokens are stored as **SHA-256 hashes**; a lost token cannot be recovered, only replaced by logging in again. Tokens expire after 1 day, so the user logs in again then.
- When the portal session expires the API answers `401` and the user logs in again.

## API

Interactive documentation is served at `/docs` (Swagger UI) and `/redoc`; the
raw OpenAPI document is at `/openapi.json`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | – | Sign in with a site account, returns a token |
| `POST` | `/api/v1/auth/logout` | ✔ | Log out of the university dashboard and SSO, then end the API session (token and site session cookies) |
| `GET` | `/health` | – | Health check (unversioned) |
| `GET` | `/api/v1/me/profile` | ✔ | Student profile |
| `GET` | `/api/v1/me/scores` | ✔ | Scores and semester results |
| `GET` | `/api/v1/me/schedule` | ✔ | Lecture timetable (one block per semester in `sections.semesters`). Falls back to the channel's PDF when the university has none, with `?view=list` (default) or `?view=grid`, see [Timetable fallback](#timetable-fallback) |
| `GET` | `/api/v1/me/notices` | ✔ | Notices |
| `POST` | `/api/v1/me/password` | ✔ | Change the site password through the SSO change form. Returns `changed`, `url` and `messages` |
| `PUT` | `/api/v1/me/photo` | ✔ | Upload or replace the profile photo (JPEG, PNG or WebP as the raw request body, 10 MB at most) |
| `GET` | `/api/v1/me/photo` | ✔ | The profile photo |
| `DELETE` | `/api/v1/me/photo` | ✔ | Delete the profile photo |
| `GET` | `/api/v1/me/notifications` | ✔ | Change notifications: channels, watched fields, status of the last check |
| `PUT` | `/api/v1/me/notifications` | ✔ | Turn notifications on or update them, with the password saved at login |
| `DELETE` | `/api/v1/me/notifications` | ✔ | Turn notifications off and delete the saved results |
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

`POST /api/v1/me/password` submits the change form on the university site with the new password, typed twice as `password` and `confirm_password`. The answer has `changed`, `url` and `messages`: `changed` is `true` when the site answers with its sign-in page instead of the change form. Otherwise `messages` holds the alert and error texts from the page, such as the password rules. After a successful change, the saved password is replaced with the new one, so change notifications keep working.

`PUT /api/v1/me/photo` takes the image file as the request body, not as a form. The format is read from the file's first bytes, so the `Content-Type` header does not have to be right. Uploading again replaces the photo. `GET /api/v1/me/photo` returns it with its image type, so the frontend can send the `Authorization` header and show it as a blob URL. The photo is stored on this service only; the university site is not changed.

`POST /api/v1/auth/logout` clicks the university's own logout links first: the dashboard's, then the SSO admin's. Then it ends the API session: the token stops working and the site session cookies are dropped. The saved password, notification settings, Telegram link and profile photo are kept, so change notifications keep running. If the university site can't be reached, the session still ends and the failure is logged. The answer is `{"ok": true}` either way.

`/health` is unversioned (probed by Docker, CI and nginx); everything else lives under `/api/v1`.

Errors are returned as `{"detail": "..."}`:

| Status | Meaning |
|---|---|
| `401` | Missing, invalid or expired token (tokens last 1 day), expired site session, or the site rejected the saved password (`PUT /me/notifications`, then log in again) |
| `404` | Unknown target, course or notice, no password form on the site, no profile photo (`GET /me/photo`), or notifications are off (`GET /me/notifications`) |
| `409` | Disconnecting Telegram while notifications are on without an email |
| `413` | The profile photo is larger than 10 MB (`PUT /me/photo`) |
| `415` | The uploaded file is not a JPEG, PNG or WebP image (`PUT /me/photo`) |
| `422` | Invalid notification settings, no email and no Telegram connected, no saved password (log in again), or the two passwords differ (`POST /me/password`) |
| `502` | The university site could not be reached or failed |
| `503` | Telegram is not set up on the server (`TELEGRAM_BOT_USERNAME` is missing) |

For invalid input (`422`), `detail` is a list with one entry per field instead of a string. Each entry has `loc` (the field, for example `["body", "password"]`), `msg` and `type`. The submitted values are not repeated, so a password is never sent back.

### Example

```bash
# 1. log in and keep the token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "M0000000000", "password": "your-site-password"}' | jq -r .token)

# 2. use it
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/me/scores
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/courses

# 3. connect Telegram: open the returned url in Telegram and press Start
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/me/telegram/link

# 4. turn change notifications on (email is optional once Telegram is connected)
curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  http://localhost:8000/api/v1/me/notifications \
  -d '{"email": "student@example.com", "fields": ["final_score", "grade"]}'
```

In Swagger UI, call `POST /api/v1/auth/login`, click **Authorize**, and paste the token.

## Configuration

Copy [.env.example](.env.example) to `.env`.

| Variable | Required | Description |
|---|---|---|
| `APP_SECRET_KEY` | ✔ | Fernet key that encrypts stored cookies and passwords. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it stable: changing it makes stored sessions unreadable and users must log in again. |
| `BASE_DOMAIN` | ✔ | Parent domain shared by the login and dashboard hosts |
| `LOGIN_URL` | ✔ | Portal login page |
| `DASHBOARD_URL` | ✔ | Dashboard home page (must be on `BASE_DOMAIN`) |
| `DATABASE_URL` | ✔ locally | PostgreSQL URL. Overridden by Docker Compose, which points it at the `db` service |
| `POSTGRES_PASSWORD` | Docker | Password for the Compose Postgres container (default `aztusp`). Use letters and digits only, e.g. `openssl rand -hex 24`. Set it **before** the first start: Postgres only reads it when the data volume is created |
| `USERNAME_FIELD`, `PASSWORD_FIELD` | – | Names of the login form inputs (default `username`, `password`) |
| `REQUEST_TIMEOUT` | – | Seconds per request to the portal (default `20`) |
| `TELEGRAM_BOT_USERNAME` | For Telegram | The bot's username without `@`. The API uses it to build the connect link. The other watcher settings are in [Grade watcher](#grade-watcher) |

### Timetable fallback

When the university site has no lessons for a student, `GET /me/schedule` searches a Telegram channel that posts timetable
PDFs. It takes the student's group codes from their course names (`Math [M1]` gives `M1`), reads the newest PDFs for those
groups, and returns them in one of two views, chosen with `?view=`:

- `list` (default): one row per session (day, time, week, course, type, room, teacher).
- `grid`: one row per lesson time, `Dərs` then one column per weekday, as the university's own timetable is laid out.

Both weeks are in one block per group, so a student sees one table. `week` is `alt həftə`, `üst həftə` or `hər həftə` (every
week). In the grid, a card that is in one week only starts with that week's name; a card in every week does not. Both views
come from the same PDF and keep its text as printed, except that the export's `?` is read as `ə` (or `Ə` at the start of a
name). The export has lost ş, ç, ı and ğ, so those stay as plain letters, for example `masin`. `url` points at the channel post. The PDFs need one page per group,
laid out like the aSc export: the group code above a grid with the times across the top and the weekdays down the side.
The university's own timetable is returned unchanged, whatever the view.

| Variable | Description |
|---|---|
| `SCHEDULE_CHANNEL` | Public username of the channel, without `@`, or the `-100...` id of a private channel (from its link, `#-1004368645921` gives `-1004368645921`). The account must be a member of a private channel. Leave all four variables unset to turn the fallback off |
| `SCHEDULE_TELEGRAM_API_ID`, `SCHEDULE_TELEGRAM_API_HASH` | From [my.telegram.org](https://my.telegram.org), under *API development tools* |
| `SCHEDULE_TELEGRAM_SESSION` | Login string for a Telegram account that can read the channel. Create it with `python -m app.timetable.login` |

A bot cannot list a channel's older posts, so the fallback signs in as a user account. Keep the session string as secret as
a password, and use it only on the server: Telegram can revoke a session that is used from two networks at once. If the
session stops working, the endpoint still answers with the university's (empty) timetable and logs the error.

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

Database tests (the API and notification storage) run only when `TEST_DATABASE_URL` points at a PostgreSQL
database. Without it they are skipped. CI sets it.

## Project layout

```
app/
  main.py            app factory: metadata, error handlers, router mounting under /api/v1
  config.py          environment configuration
  db.py              PostgreSQL store (hashed tokens, encrypted cookies and passwords, notification settings, profile photos)
  photos.py          profile photo size limit and format check (read from the file's first bytes)
  schemas.py         request/response models
  api/
    deps.py          settings, user store and per-request authenticated scraper
    errors.py        exception -> HTTP status handlers, shared error responses
    routers/         auth, me (profile/scores/...), notifications, telegram, courses, system (health)
  scraping/
    client.py        portal login and logout, session handling, redirects
    parsing.py       HTML table/pair parsing and field-name translation
    targets.py       what to scrape (CSS selectors) and the field-name translations
    courses.py       course list and lecture plan scraping
  timetable/         fallback timetable from the channel's PDFs
    service.py       university timetable, or the channel's when the university has none
    channel.py       searches the channel's newest PDFs for the student's groups (Telethon)
    pdf.py           reads one group's lessons from a timetable PDF (one page per group)
    config.py        channel and Telegram session settings
    login.py         one-time sign in that prints the session string
    cookies.py       shares session cookies across portal subdomains
  watcher/           grade watcher (python -m app.watcher)
    __main__.py      the loop, --once and the test flags
    checker.py       one check per student: sign in, read scores, compare, notify
    notify.py        email (SMTP) and Telegram messages
    telegram.py      connects chats from /start <code> messages
    results.py       picks the watched fields and finds changes
    config.py        shared watcher settings
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
`PUT /api/v1/me/notifications`, sending the fields to watch and, optionally, an email. The password saved at login is
used, so the student doesn't type it again. An email, a connected Telegram, or both are accepted. If the site rejects
the saved password (for example after a change made on the site), the student logs in again, which saves the new one.
The response includes:

- `status`: `ok` (the last check worked), `wrong_password` (the site rejected the saved password; checks stop for
  that student until they log in again, so the watcher doesn't keep retrying and risk locking
  the account), or `error` (the last check failed, usually because the site was down).
- `last_checked_at`: when the last check ran.
- `telegram_linked`: whether a Telegram chat is connected.

The frontend should show `wrong_password` and `error` to the student.

**What is stored:** the site password, encrypted with `APP_SECRET_KEY`, plus the email, the fields, the last seen
results and the Telegram chat id. Turning notifications off keeps the password and the Telegram link. Disconnecting Telegram
(`DELETE /api/v1/me/telegram`) needs an email set first, so messages don't silently stop. Logging out keeps
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
