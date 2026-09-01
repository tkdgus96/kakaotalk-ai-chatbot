#!/usr/bin/env python3
"""Codec-free remote control for the redroid device via adb screencap + input.

Serves a simple web page that shows periodic screenshots and forwards taps /
swipes / text / keys to the device with `adb ... input`. Because it relies on
`adb screencap` (which works even when scrcpy's H.264 stream renders black on
software-GPU redroid), it works from any browser, including iOS Safari.

Run behind Tailscale (bind localhost, expose via `tailscale serve`):
    python3 remote_control.py            # listens on 127.0.0.1:8090
Env: RC_PORT (default 8090), RC_ADB_SERIAL (default 127.0.0.1:5555),
     RC_ADB (adb binary path).
"""

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ADB = os.environ.get("RC_ADB", "adb")
SERIAL = os.environ.get("RC_ADB_SERIAL", "127.0.0.1:5555")
PORT = int(os.environ.get("RC_PORT", "8090"))


def adb(*args, binary=False, timeout=15):
    cmd = [ADB, "-s", SERIAL, *args]
    out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return out.stdout if binary else out.stdout.decode(errors="replace")


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>redroid remote</title>
<style>
 body{margin:0;background:#111;color:#ddd;font-family:sans-serif;text-align:center}
 #wrap{display:inline-block;position:relative;max-width:100vw}
 #screen{max-width:100vw;max-height:80vh;touch-action:none;display:block}
 .bar{padding:8px;display:flex;gap:6px;justify-content:center;flex-wrap:wrap}
 button{padding:10px 14px;font-size:15px;border-radius:8px;border:0;background:#333;color:#fff}
 input{padding:9px;font-size:15px;border-radius:8px;border:0;width:60%}
</style></head><body>
<div id="wrap"><img id="screen" src=""></div>
<div class="bar">
 <button onclick="key(3)">🏠 홈</button>
 <button onclick="key(4)">◀ 뒤로</button>
 <button onclick="key(187)">▣ 최근</button>
 <button onclick="key(26)">⏻ 전원</button>
 <button onclick="key(66)">⏎ 엔터</button>
 <button onclick="refreshNow()">↻ 새로고침</button>
</div>
<div class="bar">
 <button onclick="openApp('com.kakao.talk')" style="background:#f7d000;color:#111">📱 카톡 열기</button>
</div>
<div class="bar">
 <input id="txt" placeholder="텍스트 입력 후 전송" autocapitalize="off" autocorrect="off">
 <button onclick="sendText()">전송</button>
</div>
<script>
const img=document.getElementById("screen");
let iw=720, ih=1280, dragging=false, sx=0, sy=0, st=0;
function refresh(){ img.src="/shot?t="+Date.now(); }
function refreshNow(){ refresh(); }
img.onload=()=>{ iw=img.naturalWidth; ih=img.naturalHeight; };
setInterval(refresh, 900);
function toDev(ev){
 const r=img.getBoundingClientRect();
 const cx=(ev.touches?ev.touches[0].clientX:ev.clientX)-r.left;
 const cy=(ev.touches?ev.touches[0].clientY:ev.clientY)-r.top;
 return [Math.round(cx/r.width*iw), Math.round(cy/r.height*ih)];
}
function down(ev){ ev.preventDefault(); const [x,y]=toDev(ev); dragging=true; sx=x; sy=y; st=Date.now(); }
function up(ev){ if(!dragging)return; dragging=false; ev.preventDefault();
 const [x,y]=toDev(ev.changedTouches?{touches:ev.changedTouches}:ev);
 const dt=Date.now()-st, dist=Math.hypot(x-sx,y-sy);
 if(dist>15){ post("/swipe",{x1:sx,y1:sy,x2:x,y2:y,ms:Math.max(dt,120)}); }
 else { post("/tap",{x:sx,y:sy}); }
 setTimeout(refresh,350);
}
img.addEventListener("mousedown",down); img.addEventListener("mouseup",up);
img.addEventListener("touchstart",down,{passive:false}); img.addEventListener("touchend",up,{passive:false});
function key(k){ post("/key",{k}); setTimeout(refresh,350); }
function sendText(){ const t=document.getElementById("txt").value; if(t){ post("/text",{t}); document.getElementById("txt").value=""; setTimeout(refresh,400);} }
function openApp(pkg){ post("/app",{pkg}); setTimeout(refresh,1500); setTimeout(refresh,3000); }
function post(u,b){ fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}); }
refresh();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
        elif path == "/shot":
            try:
                png = adb("exec-out", "screencap", "-p", binary=True)
            except Exception:
                png = b""
            self._send(200 if png else 500, "image/png", png)
        else:
            self._send(404, "text/plain", b"nope")

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        try:
            if path == "/tap":
                adb("shell", "input", "tap", str(int(body["x"])), str(int(body["y"])))
            elif path == "/swipe":
                adb("shell", "input", "swipe", str(int(body["x1"])), str(int(body["y1"])),
                    str(int(body["x2"])), str(int(body["y2"])), str(int(body.get("ms", 200))))
            elif path == "/key":
                adb("shell", "input", "keyevent", str(int(body["k"])))
            elif path == "/text":
                t = str(body.get("t", "")).replace(" ", "%s")
                adb("shell", "input", "text", t)
            elif path == "/app":
                pkg = str(body.get("pkg", "")).strip()
                if pkg == "com.kakao.talk":
                    adb("shell", "am", "start", "-n", "com.kakao.talk/.activity.SplashActivity")
                elif pkg:
                    adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
            else:
                return self._send(404, "text/plain", b"nope")
        except Exception as e:
            return self._send(500, "text/plain", str(e).encode())
        self._send(200, "application/json", b'{"ok":true}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
