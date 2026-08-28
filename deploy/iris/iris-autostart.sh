#!/usr/bin/env bash
# Boot autostart for the Iris bridge (run by systemd after docker).
# The redroid container auto-restarts via its restart policy; this waits for
# the Android to finish booting, launches KakaoTalk (so its DB keeps
# receiving new messages), then starts Iris via app_process. Iris keeps its
# saved web_server_endpoint / config across restarts.
#
# Installed on the server as /etc/systemd/system/iris-bridge.service ->
# ExecStart of this script. See iris-bridge.service (same dir) and README.
set -u
ADB="${ADB:-/usr/bin/adb}"
DEV="127.0.0.1:5555"
APK="/data/local/tmp/Iris.apk"
STAGE="/home/sang/workspace/kakao-talk-ai-bot/deploy/iris/Iris.apk"

$ADB connect "$DEV" >/dev/null 2>&1
for i in $(seq 1 60); do
  bc=$($ADB -s "$DEV" shell getprop sys.boot_completed 2>/dev/null | tr -d "\r")
  [ "$bc" = "1" ] && break
  sleep 5
done
# re-stage Iris apk if /data was wiped
$ADB -s "$DEV" shell "ls $APK" >/dev/null 2>&1 || $ADB -s "$DEV" push "$STAGE" "$APK" >/dev/null 2>&1
# launch KakaoTalk so its DB keeps updating (Iris observes the DB)
$ADB -s "$DEV" shell "monkey -p com.kakao.talk -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1
sleep 8
# start Iris only if not already listening
if ! curl -s --max-time 3 http://127.0.0.1:3000/config >/dev/null 2>&1; then
  $ADB -s "$DEV" shell "su root sh -c \"CLASSPATH=$APK nohup app_process / party.qwer.iris.Main >/dev/null 2>&1 &\""
  sleep 5
fi
curl -s --max-time 3 http://127.0.0.1:3000/config >/dev/null 2>&1 && echo "iris up" || echo "iris FAILED"
