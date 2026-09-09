#!/usr/bin/env bash
# Keeps the Iris bridge alive regardless of WHY it went down (host reboot,
# container recreate, Iris crash). Installed as iris-watchdog.timer.
#
# Deliberately a no-op while Iris is healthy: iris-autostart.sh foregrounds
# KakaoTalk via monkey every run, which would steal focus from whatever is
# on screen if we ran it unconditionally on a timer.
set -u

if curl -s --max-time 3 http://127.0.0.1:3000/config >/dev/null 2>&1; then
  exit 0
fi

echo "iris down - running autostart"
exec /home/sang/workspace/kakao-talk-ai-bot/deploy/iris/iris-autostart.sh
