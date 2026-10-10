import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import get_store
from .api.errors import register_error_handlers
from .api.routers import auth, courses, me, notifications, system, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

API_PREFIX = "/api/v1"

DESCRIPTION = """
Lets each student sign in with **their own** university account and read their data
(profile, scores, timetable, notices, courses and lecture plans) as JSON.

## How to use

1. `POST /auth/login` with your site username and password. The response contains an API **token**.
2. Click **Authorize** (top right) and paste the token, or send `Authorization: Bearer <token>`.
3. Call any endpoint under *My data* and *Courses*. Data is read from the university site on every request, so calls can take a few seconds.

## Privacy

* Your site password is used to sign in and is **not stored**, with one exception: if you turn on change notifications, it is kept **encrypted** so the watcher can sign in every 30 minutes and check your scores. Turning notifications off, or logging out, deletes it.
* Only the resulting site session cookies are kept, **encrypted** in the database.
* Your API token is stored as a SHA-256 hash, so it cannot be recovered. Logging in again issues a new token and invalidates the old one.
* If the site session expires, data endpoints answer `401` and you need to log in again.
"""

TAGS = [
    {"name": "Auth", "description": "Sign in with a site account and manage the API token."},
    {"name": "My data", "description": "Your dashboard pages as JSON, scraped live on each request."},
    {"name": "Courses", "description": "Courses linked from the dashboard and their lecture plans."},
    {"name": "Notifications", "description": "Email and Telegram messages when a watched result changes."},
    {"name": "System", "description": "Service status and metadata."},
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect and create tables now, so a bad config or unreachable database stops startup
    # with a clear error instead of failing every request later.
    get_store()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="AZTUSP Backend",
    summary="Per-user scraping API for the university student portal.",
    description=DESCRIPTION,
    version="1.0.0",
    license_info={"name": "MIT", "identifier": "MIT"},
    openapi_tags=TAGS,
)
register_error_handlers(app)

# The web frontend lives on another origin. Auth is a bearer header, not cookies, so no credentials are needed.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "https://aztu.alakbaroff.com,http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],
)

# Health stays unversioned: Docker, CI and the reverse proxy probe it.
app.include_router(system.router)

# Current, documented API.
v1 = APIRouter(prefix=API_PREFIX)
for router in (auth.router, me.router, notifications.router, telegram.router, courses.router):
    v1.include_router(router)
app.include_router(v1)
