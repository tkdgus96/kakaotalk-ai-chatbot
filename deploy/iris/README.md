# Iris 브리지 (메신저봇R 폰 대체)

Redroid(Docker 안드로이드) 안에서 카카오톡 + [Iris](https://github.com/dolidolih/Iris)를 돌리고,
백엔드가 폰 없이 메시지를 수신/발송하는 구성.

```
카카오톡 (redroid, root) ← Iris가 카톡 DB observe
        │  새 메시지 → POST http://<백엔드>:8000/iris   (웹훅)
        ▼
   FastAPI 백엔드 ── 답장/리마인더 → POST http://127.0.0.1:3000/reply (Iris HTTP API)
```

- 수신: Iris가 `web_server_endpoint`로 웹훅 POST → `POST /iris`가 `KakaoMsg`로 변환해 기존 파이프라인 실행
- 발송: 답장은 즉시 Iris `/reply`로. `bot_outbox`(보스 알림, `!매일` 리마인더)는
  `ENABLE_IRIS_SENDER=true`일 때 백엔드 내 로컬 발송 루프가 5초 간격으로 처리 — 폰 폴링 불필요, 자정 정각 발송 보장.

## 사전 확인 (리눅스 노트북)

1. **binder 커널 모듈**: `ls /dev/binder*` 가 비어 있으면
   `sudo apt install linux-modules-extra-$(uname -r)` 후
   `sudo modprobe binder_linux devices="binder,hwbinder,vndbinder"`.
   부팅 시 자동 로드: `/etc/modules-load.d/binder.conf`에 `binder_linux` 추가 +
   `/etc/modprobe.d/binder.conf`에 `options binder_linux devices=binder,hwbinder,vndbinder`.
2. **CPU 아키텍처**: `uname -m`
   - `aarch64` → 기본 `redroid/redroid` 이미지 그대로 사용 가능.
   - `x86_64` → 카카오톡이 ARM 네이티브 라이브러리를 쓰므로 **ARM 변환(libndk) 포함 커스텀 이미지 필요**.
     [redroid-script](https://github.com/ayasa520/redroid-script)로 빌드:
     `python redroid.py -a 12.0.0 -n` → 생성된 이미지로 `docker-compose.yml`의 `image:` 교체.
3. **노트북 절전 끄기**: 뚜껑 닫아도 suspend 안 되게
   (`/etc/systemd/logind.conf` → `HandleLidSwitch=ignore`).

## 셋업

```bash
# 이 디렉토리에 준비물 배치: KakaoTalk.apk (버전 고정 보관), Iris.apk + iris_control (Releases)
cd deploy/iris
BACKEND_URL=http://172.17.0.1:8000 ./setup.sh
```

> **compose 버전 주의**: 이 호스트(192.168.0.51)에는 compose v2 플러그인이 없고
> 레거시 `docker-compose` v1.29.2만 있다. `docker compose`(공백)가 아니라
> `docker-compose`(하이픈)로 실행할 것. docker 데몬 접근에 sudo가 필요하면 `sudo docker-compose ...`.
> (sang을 docker 그룹에 넣어뒀으므로 재로그인 후에는 sudo 없이 가능.)

> **x86_64/AMD 이미지**: 이미 `redroid/redroid:11.0.0_ndk_magisk`를 빌드해 두었다
> (`redroid-script -a 11.0.0 -n -m`, AMD이므로 houdini 아닌 **libndk**). compose의 image가 이걸 가리킨다.
> ABI 목록에 arm64-v8a가 잡히는 것으로 ARM 변환 동작을 확인함.

카톡 로그인은 수동 (scrcpy로 화면 연결, **반드시 봇 전용 서브계정**):
```bash
scrcpy --tcpip=127.0.0.1:5555
```

## 백엔드 .env

```bash
IRIS_BASE_URL=http://127.0.0.1:3000
IRIS_BOT_TRIGGERS=!,！,온반봇        # 기존 폰 브리지의 is_command 규칙과 동일하게
IRIS_SELF_NAMES=온반봇               # 봇 계정 이름 (자기 메시지 echo 무시)
IRIS_ROOM_MAP=418123456789:12345    # 카톡chat_id:기존room_id — 아래 참고
ENABLE_IRIS_SENDER=true             # outbox를 서버가 직접 발송 (폰 폴링 대체)
```

### room_id 매핑 (중요 — 기억 연속성)

기존 데이터(Chroma, FTS 로그, persona, allowed_rooms, boss 테이블)는 메신저봇R이 주던
`room_id`에 키잉되어 있고, Iris는 카톡 내부 `chat_id`를 준다. 매핑 없이는 봇이 방의 기억을 잃는다.

1. Iris 연결 후 대상 방에 아무 메시지나 발송
2. `GET /iris/rooms` 로 새로 잡힌 `chat_id` 확인 (미매핑 방은 room_id=chat_id로 자동 등록됨)
3. `.env`의 `IRIS_ROOM_MAP`에 `chat_id:기존room_id` 추가 후 백엔드 재시작
   (재시작 시 seed가 기존 identity 매핑을 덮어씀)
4. `ALLOWED_ROOMS`는 기존 room_id 그대로 유지

신규 방(과거 기억 없음)은 매핑 없이 그대로 쓰면 된다.

## 전환 절차 (병행 운영)

1. redroid+Iris를 **playground 방**으로 먼저 검증 (`IRIS_ROOM_MAP`으로 playground room_id에 매핑)
2. 실제 방 매핑 추가, 폰 브리지는 켜둔 채 며칠 병행
   — 이때 중복 응답 방지를 위해 **둘 중 하나만** 응답하게: 폰 쪽 스크립트를 끄거나 Iris 웹훅만 연결
3. 안정화 확인 후: 폰의 outbox 폴링 중지 → `ENABLE_IRIS_SENDER=true` → 폰 은퇴 (백업 보관)

## 운영 주의

- **계정**: 봇 전용 서브계정만. DB 접근 + 비공식 전송은 약관 위반이라 정지 리스크가 있다.
- **카톡 버전 고정**: 카톡 업데이트가 Iris(DB 스키마/복호화)를 깨뜨릴 수 있다. APK 파일을 보관하고
  자동 업데이트를 막은 뒤, Iris 릴리스가 신버전을 지원할 때만 함께 올린다.
- **포트 노출 금지**: Iris HTTP API(3000)와 백엔드 `/iris`, `/bot/outbox`는 무인증.
  compose는 127.0.0.1에만 바인딩하도록 되어 있음 — 그대로 유지할 것.
- **재부팅 복구**: binder 모듈 자동 로드 + `restart: unless-stopped` + 백엔드 PM2면
  전원만 들어오면 전체 스택이 자동 복구된다. redroid 부팅 후 Iris는 `iris_control start` 필요
  → cron `@reboot`이나 systemd unit으로 걸어두는 것을 권장.
