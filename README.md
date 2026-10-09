# UserHelper Backend

FastAPI service that lets each user log in with their own site account and scrape their data.

## Run

```bash
cp .env.example .env   # fill in the site URLs and APP_SECRET_KEY
docker compose up --build
```

Open http://localhost:8000/docs, call `POST /auth/login`, then use **Authorize** with the returned token.

Without Docker: `pip install -r requirements.txt && uvicorn app.main:app --reload`.

Passwords are never stored; session cookies are kept encrypted in SQLite. Keep `APP_SECRET_KEY` stable.
