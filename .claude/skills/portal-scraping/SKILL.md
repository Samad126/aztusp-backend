---
name: portal-scraping
description: How to scrape and verify pages of the AzTU student portal in this repo. Use when adding or fixing a scraper, parsing a portal table, or when scraped values look wrong.
---

# Scraping the AzTU portal

1. **Look at the real page first.** Log in with a throwaway script (see `/probe-portal`), save the raw HTML to the scratchpad, and read it. Never guess selectors.
2. **Values may not be in the HTML.** The portal fills many cells with JavaScript (`$.ajax` in an inline `<script>`). If a cell shows a spinner or placeholder, read the script, find the endpoint, and call it through `SiteScraper._request` (example: task scores via `lecture_task_ajax2.php`).
3. **Don't trust a similar-looking field.** The task detail's `30/30` is the task's maximum, not the student's score. Cross-check any value against a total the portal shows elsewhere (e.g. `Məşğələ(40)` total vs the sum of quizzes + midterm).
4. **Headers.** Inner dashboard pages need pjax headers (`PJAX_HEADERS`); AJAX endpoints need `X-Requested-With: XMLHttpRequest`.
5. **Context reuse.** `_course_context` costs a request. Fetch `list_courses` once and pass `listing=`; reuse `course_params`.
6. **Tests** use trimmed HTML fixtures in `tests/` (no personal data). Real saved pages stay out of git (`samples/`).
7. **Verify end to end** with `/probe-portal` after the change and compare against what the student sees on the site.
