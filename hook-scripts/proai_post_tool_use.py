#!/usr/bin/env python3
"""A gentle one-time quality reminder for file edits; no file content or secrets logged."""
import json
import pathlib
import sys
import time


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        print('{"continue":true,"suppressOutput":true}')
        return 0
    tool = str(payload.get("tool_name", ""))
    if tool not in ("Write", "Edit", "MultiEdit"):
        print('{"continue":true,"suppressOutput":true}')
        return 0
    stamp = pathlib.Path.home() / ".zcode" / "proai-starter" / "quality-reminder-timestamp"
    try:
        last = float(stamp.read_text()) if stamp.is_file() else 0.0
        if time.time() - last > 900:  # At most once every 15 minutes.
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(str(time.time()), encoding="utf-8")
            print(json.dumps({"continue": True,
               "systemMessage": "PROAI: после правок проверь реальный запуск и главный пользовательский сценарий. Не утверждай, что всё готово, без проверки."}, ensure_ascii=False))
            return 0
    except (OSError, ValueError):
        pass
    print('{"continue":true,"suppressOutput":true}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
