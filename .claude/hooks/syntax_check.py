"""PostToolUse: compile an edited Python file so a syntax error shows up straight away."""
import json
import py_compile
import sys

payload = json.load(sys.stdin)
path = (payload.get("tool_input") or {}).get("file_path", "")
if path.endswith(".py"):
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as exc:
        print(exc.msg, file=sys.stderr)
        sys.exit(2)
