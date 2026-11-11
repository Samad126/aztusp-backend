---
description: Read-only probe of the live portal to see what a page or endpoint really returns
argument-hint: <what to look at, e.g. "tasks tab of the first course">
---
Investigate $ARGUMENTS against the live portal.

1. Write a throwaway script in the scratchpad that loads `.env` with `python-dotenv`, builds `SiteScraper(get_settings(), os.environ["UNI_USER"])`, calls `.login(os.environ["UNI_PASS"])`, and uses the existing helpers in `app/scraping/`.
2. Run it with `PYTHONPATH=. .venv/bin/python <script>`. Read-only: GET pages, never submit forms or upload.
3. Never print credentials, cookies or the student's personal data beyond what is needed (course names and scores are fine).
4. Report what the portal returns and how the scraper should read it. Save raw HTML only in the scratchpad.
