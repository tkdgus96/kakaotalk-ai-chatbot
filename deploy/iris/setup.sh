#!/usr/bin/env bash
# Iris + Redroid 셋업 헬퍼 (리눅스 노트북에서 실행).
# 각 단계는 idempotent하게 실패해도 다시 실행 가능. 대화형 단계(카톡 로그인)는 수동.
set -euo pipefail

cd "$(dirname "$0")"

ADB="adb -s 127.0.0.1:5555"
KAKAO_APK="${KAKAO_APK:-./KakaoTalk.apk}"   # 카카오톡 APK 경로 (버전 고정용으로 파일 보관 권장)
IRIS_APK="${IRIS_APK:-./Iris.apk}"           # https://github.com/dolidolih/Iris/releases
BACKEND_URL="${BACKEND_URL:-http://172.17.0.1:8000}"  # 컨테이너에서 본 호스트 백엔드 주소

step() { echo; echo "==> $*"; }

step "1/6 binder 커널 모듈 확인"
if ! ls /dev/binder* >/dev/null 2>&1; then
  echo "binder 모듈 로드 시도..."
  sudo modprobe binder_linux devices="binder,hwbinder,vndbinder" || {
    echo "실패: 커널에 binder_linux가 없습니다."
    echo "  Ubuntu: sudo apt install linux-modules-extra-\$(uname -r) 후 재시도"
    exit 1
  }
fi
echo "OK: $(ls /dev/binder* 2>/dev/null | tr '\n' ' ')"

step "2/6 redroid 컨테이너 기동"
docker compose up -d
echo "부팅 대기 (최초 부팅은 1~2분)..."
sleep 20

step "3/6 adb 연결"
adb connect 127.0.0.1:5555
$ADB wait-for-device
$ADB shell getprop sys.boot_completed | grep -q 1 || { echo "아직 부팅 중 — 잠시 후 재실행"; exit 1; }

step "4/6 카카오톡 설치"
if $ADB shell pm list packages | grep -q com.kakao.talk; then
  echo "이미 설치됨 — 건너뜀"
else
  [ -f "$KAKAO_APK" ] || { echo "카카오톡 APK가 없습니다: $KAKAO_APK (KAKAO_APK= 로 경로 지정)"; exit 1; }
  $ADB install -r "$KAKAO_APK"
  echo "!! 수동 단계: scrcpy 등으로 화면 열어 봇 전용 서브계정으로 로그인하세요."
  echo "   scrcpy --tcpip=127.0.0.1:5555"
  echo "   로그인 후 카톡 자동업데이트 비활성화(플레이스토어 미설치 상태 유지) 확인."
fi

step "5/6 Iris 설치/기동"
[ -f "$IRIS_APK" ] || { echo "Iris APK가 없습니다: $IRIS_APK (Releases에서 다운로드)"; exit 1; }
$ADB push "$IRIS_APK" /data/local/tmp/Iris.apk
# Iris 릴리스에 동봉된 iris_control 스크립트가 있으면 그것을 우선 사용
if [ -f ./iris_control ]; then
  chmod +x ./iris_control
  ./iris_control install || true
  ./iris_control start
else
  echo "iris_control 스크립트가 없습니다. Iris 릴리스 문서의 수동 실행 절차를 따르세요."
fi

step "6/6 Iris 웹훅 → 백엔드 연결"
sleep 3
curl -sf -X POST "http://127.0.0.1:3000/config/endpoint" \
  -H "Content-Type: application/json" \
  -d "{\"endpoint\": \"${BACKEND_URL}/iris\"}" \
  && echo "웹훅 endpoint 설정 완료: ${BACKEND_URL}/iris" \
  || echo "실패 — http://127.0.0.1:3000/dashboard 에서 수동 설정하세요."

echo
echo "완료. 확인 순서:"
echo "  1) http://127.0.0.1:3000/dashboard 접속해 bot_name/bot_id 확인"
echo "  2) 테스트 방에 메시지 → 백엔드 로그에 'iris webhook' 수신 확인"
echo "  3) GET ${BACKEND_URL}/iris/rooms 로 chat_id 확인 → .env IRIS_ROOM_MAP에 기존 room_id 매핑 추가"
echo "  4) .env ENABLE_IRIS_SENDER=true 로 바꾸고 백엔드 재시작 (outbox 발송 전환)"
