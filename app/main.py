import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import get_store, get_timetable_source
from .api.errors import register_error_handlers
from .api.routers import auth, courses, me, notifications, system, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

API_PREFIX = "/api/v1"

DESCRIPTION = """
Lets each student sign in with **their own** university account, read their data
(profile, scores, timetable, notices, courses and lecture plans) as JSON, change their
site password, and set a profile photo. Students can also get a Telegram or email message when a watched result changes.

## How to use

1. `POST /auth/login` with your site username and password. The response contains an API **token**, which lasts 1 day.
2. Click **Authorize** (top right) and paste the token, or send `Authorization: Bearer <token>`.
3. Call any endpoint under *My data* and *Courses*. Data is read from the university site on every request, so calls can take a few seconds.
4. For result messages: `POST /me/telegram/link` and open the link in Telegram, then `PUT /me/notifications` with the fields to watch. Your login password is saved and used for the checks, and an email is optional once Telegram is connected. See *Notifications*.
5. To change the site password: `POST /me/password` with `password` and `confirm_password`. `changed` is `true` when the site answers with its sign-in page instead of the change form; otherwise `messages` says why. After a change, the saved password is updated too, so notifications keep working.
6. To set a profile photo: `PUT /me/photo` with the image file as the request body (JPEG, PNG or WebP, 10 MB at most). `GET /me/photo` returns it and `DELETE /me/photo` removes it. The photo is kept in this service, not on the university site.
7. To sign out: `POST /auth/logout`. It also signs out of the university site and ends this login, so the token stops working. Your saved data is kept, so change notifications keep running.

## Privacy

* Your site password is kept **encrypted** from the login onward, so change notifications can sign in every 30 minutes and check your scores. A password change or a new login replaces it. Logging out and turning notifications off keep it.
* Your email, watched fields, last results and Telegram chat are kept only while notifications are on. Turning them off deletes the email and results (the Telegram link stays until you disconnect it). Logging out does not change them.
* Only the resulting site session cookies are kept, **encrypted** in the database. Logging out also signs out of the university dashboard and SSO, and drops those cookies.
* Your profile photo is kept until you replace or delete it. Logging out keeps it.
* Your API token is stored as a SHA-256 hash, so it cannot be recovered. It expires after 1 day. Logging in again issues a new token and invalidates the old one.
* If the site session expires, data endpoints answer `401` and you need to log in again.
"""

TAGS = [
    {"name": "Auth", "description": "Sign in with a site account and manage the API token."},
    {"name": "My data", "description": "Your pages on the university site as JSON, scraped live on each request, and your profile photo."},
    {"name": "Courses", "description": "Courses linked from the dashboard and their lecture plans."},
    {
        "name": "Notifications",
        "description": "Get a message by email, on Telegram, or both when a watched result changes. Connect Telegram with a link, then turn notifications on.",
    },
    {"name": "System", "description": "Service status and metadata."},
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect and create tables now, so a bad config or unreachable database stops startup
    # with a clear error instead of failing every request later. The timetable fallback is checked the same way.
    get_store()
    get_timetable_source()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="AZTUSP Backend",
    summary="Per-user scraping API for the university student portal.",
    description=DESCRIPTION,
    version="2.3.1",
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
    allow_methods=["GET", "POST", "PUT", "DELETE"],
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
