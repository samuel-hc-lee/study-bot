#!/bin/bash
# Enables and starts the study bot's background service, then checks it's alive.
cd "$(dirname "$0")"
L="com.$(whoami).studybot"; D="gui/$(id -u)"
{
echo "== disabled list"; launchctl print-disabled "$D" 2>&1 | grep -i studybot || echo "not listed"
echo "== enable"; launchctl enable "$D/$L" 2>&1 && echo ok
echo "== start"; launchctl kickstart -k "$D/$L" 2>&1 && echo ok
sleep 8
echo "== state"; launchctl print "$D/$L" 2>&1 | grep -iE "state|pid =|last exit|runs" | head
echo "== processes"; pgrep -fl bot.py 2>&1 || echo "no bot.py process"
echo "== telegram"; .venv/bin/python - <<'PY' 2>&1 | tail -5
import os, httpx
from dotenv import load_dotenv
load_dotenv(".env")
t = os.environ.get("TELEGRAM_BOT_TOKEN", "")
r = httpx.get(f"https://api.telegram.org/bot{t}/getMe", timeout=10).json()
print("ok:", r.get("ok"), r.get("result", {}).get("username"), r.get("description", ""))
PY
echo "== bot.log"; tail -30 bot.log 2>&1
} > fix.txt 2>&1
cat fix.txt
