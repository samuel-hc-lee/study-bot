#!/bin/bash
# Stop the bot and stop it starting at login.
LABEL="com.$(whoami).studybot"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Autostart removed and bot stopped."
