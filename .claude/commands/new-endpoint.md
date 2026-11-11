---
description: Add a scraped endpoint end to end (scraper, schema, route, test, README)
argument-hint: <name and purpose of the endpoint>
---
Add an endpoint for $ARGUMENTS:

1. Probe the portal first (`/probe-portal`) so the parser is built on the real HTML, not a guess.
2. Parser in `app/scraping/`, response models in `app/schemas.py` (with `Field(description=...)` text), route in `app/api/routers/`.
3. A test in `tests/` using a small HTML fixture. No live network in tests.
4. Add a row to the API table in README.md.
5. Run `/test`.
