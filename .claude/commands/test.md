---
description: Run the backend test suite and summarise failures
---
Run `.venv/bin/python -m pytest -q`. If anything fails, show the failing test names and the assertion, find the cause in the code (not the test, unless the test is wrong), and propose the fix.
