#!/bin/bash
# Start the bot automatically whenever you log in to your Mac, and restart it
# if it ever crashes. Run once from this folder:  ./install-autostart.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.$(whoami).studybot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
cd "$DIR"

if [ ! -d .venv ]; then
  echo "Setting up Python environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/bot.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/bot.log</string>
  <key>StandardErrorPath</key><string>$DIR/bot.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
# bootstrap alone sometimes leaves the job loaded but never started; force it.
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "Done. The bot is running now and will start every time you log in."
echo "Logs: $DIR/bot.log   ·   Remove with: $DIR/uninstall-autostart.sh"
