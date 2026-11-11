"""PreToolUse: refuse edits to files that hold secrets or live sessions."""
import json
import os
import sys

PROTECTED = {".env", "cookies.txt"}
payload = json.load(sys.stdin)
path = (payload.get("tool_input") or {}).get("file_path", "")
if os.path.basename(path) in PROTECTED or path.endswith(".db"):
    print(f"{path} holds secrets or live sessions; edit it by hand.", file=sys.stderr)
    sys.exit(2)
