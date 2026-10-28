#!/bin/bash
# Collects everything needed to debug the bot into diagnose.txt (token is never printed).
cd "$(dirname "$0")"
{
echo "== date"; date
echo "== macOS"; sw_vers 2>&1
echo "== plist"; ls -l "$HOME/Library/LaunchAgents/com.$(whoami).studybot.plist" 2>&1
echo "== launchctl"; launchctl print "gui/$(id -u)/com.$(whoami).studybot" 2>&1 | grep -iE "state|pid =|last exit|runs|error|could not" | head -20
echo "== processes"; pgrep -fl bot.py 2>&1 || echo "no bot.py process"
echo "== import"; .venv/bin/python -c "import bot; print('import ok')" 2>&1 | tail -20
echo "== telegram"; .venv/bin/python - <<'PY' 2>&1 | tail -8
import os, httpx
from dotenv import load_dotenv
load_dotenv(".env")
t = os.environ.get("TELEGRAM_BOT_TOKEN", "")
print("token set:", bool(t), "length:", len(t))
r = httpx.get(f"https://api.telegram.org/bot{t}/getMe", timeout=10)
print("status:", r.status_code, r.json().get("result", {}).get("username"), r.json().get("description", ""))
PY
echo "== bot.log"; tail -40 bot.log 2>&1
} > diagnose.txt 2>&1
echo "Saved diagnose.txt. Tell Claude it's done."
