"""Stop: run the test suite once Claude finishes; a failure sends Claude back to fix it."""
import json
import os
import subprocess
import sys

payload = json.load(sys.stdin)
if payload.get("stop_hook_active"):  # already retried once this turn
    sys.exit(0)
root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
python = os.path.join(root, ".venv", "bin", "python")
if not os.path.exists(python):
    sys.exit(0)
result = subprocess.run([python, "-m", "pytest", "-q", "-x"], cwd=root, capture_output=True, text=True)
if result.returncode != 0:
    print(result.stdout[-2000:] + result.stderr[-500:], file=sys.stderr)
    sys.exit(2)
