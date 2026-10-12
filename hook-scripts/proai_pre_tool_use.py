#!/usr/bin/env python3
"""Conservative beginner guard for ZCode PreToolUse. No external dependencies/network."""
import json
import re
import sys


def respond(reason=None):
    if reason:
        # Verified ZCode/Claude-style PreToolUse hook output schema.
        output = {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }
    else:
        output = {"continue": True, "suppressOutput": True}
    print(json.dumps(output, ensure_ascii=False))
    return 0


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return respond()  # No accidental interruption if payload schema changes.
    name = str(payload.get("tool_name", ""))
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict) or name not in ("Bash", "execute_command", "shell"):
        return respond()
    cmd = str(args.get("command") or args.get("cmd") or "")
    # Simple guardrails; deliberately NOT a security sandbox / full shell parser.
    if re.search(r"(?:^|[;&|]\s*)\s*sudo\s+", cmd, flags=re.I):
        return respond("PROAI: команда sudo требует отдельного разрешения пользователя.")
    if re.search(r"\bgit\s+reset\s+--hard\b", cmd, flags=re.I):
        return respond("PROAI: необратимый git reset --hard запрещён без отдельного решения.")
    if re.search(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+(?:/\s*(?:$|[;&|])|~/?\s*(?:$|[;&|])|\$HOME/?\s*(?:$|[;&|]))", cmd):
        return respond("PROAI: массовое удаление корня или домашней папки запрещено.")
    if re.search(r"\b(?:curl|wget)\b[^\n|]{1,300}\|\s*(?:bash|sh)(?:\s|$)", cmd, flags=re.I):
        return respond("PROAI: нельзя исполнять удалённые скрипты в shell без проверки и согласования.")
    return respond()


if __name__ == "__main__":
    raise SystemExit(main())
