# -*- coding: utf-8 -*-
"""
کنسول ریموت وب: استریم زنده‌ی صفحه + کنترل ماوس/کیبورد از مرورگر.

اجرا توسط دستور /remote در بات: یک سرور وب روی localhost بالا می‌آید و با
تونل (Cloudflare یا ngrok) یک لینک https موقت می‌سازد. همه‌ی آدرس‌ها زیر
یک توکن تصادفی قرار دارد (مسیر /R/<token>/) تا کسی که لینک را ندارد نتواند
صفحه را ببیند یا ماوس را تکان دهد.

عمداً بدون وابستگی جدید: http.server از کتابخانه‌ی استاندارد کافی است؛
استریم با MJPEG و فرمان‌ها با POST های کوچک JSON انجام می‌شود (نه
WebSocket) تا requirements.txt دست‌نخورده بماند. تأخیر POST از داخل تونل
برای «حرکت ماوس و کلیک» کاملاً قابل قبول است.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import pyautogui

import config
from core import imaging, mouse, windows

logger = logging.getLogger(__name__)

# CREATE_NO_WINDOW: جلوی باز شدن پنجره‌ی کنسول سیاه cloudflared را می‌گیرد
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# آدرس‌های تونل سریع کلودفلر: چیزی مثل https://xxx-yyy-zzz.trycloudflare.com
_TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# API محلی ngrok برای گرفتن آدرس عمومی تونل (پیش‌فرض خود ngrok)
_NGROK_API = "http://127.0.0.1:4040/api/tunnels"

# کلیدهای مجاز از مرورگر (نام‌های pyautogui). اینجا بیشتر از mouse.KEYS است
# چون کنترل ریموت به کلیدهای جهت‌نما و صفحه‌کلید کامل نیاز دارد.
_REMOTE_KEYS = {
    "enter", "esc", "tab", "space", "backspace", "delete",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown", "f5",
}


class RemoteError(Exception):
    """خطای قابل نمایش به کاربر (پیامش فارسی و عملیاتی است)."""


def parse_tunnel_url(text: str) -> Optional[str]:
    """آدرس تونل trycloudflare را از خروجی cloudflared پیدا می‌کند."""
    m = _TUNNEL_RE.search(text or "")
    return m.group(0) if m else None


def _page_url(base: str, token: str) -> str:
    """
    آدرس کامل صفحه‌ی کنسول (با مسیر توکن) از آدرس پایه.

    تونل کلودفلر فقط آدرس پایه (https://xxx.trycloudflare.com) می‌دهد؛
    بدون افزودن /R/<token>/ مرورگر به / می‌رود و همان «not found» خود
    سرور برمی‌گردد (باگی که لینک /remote را بی‌استفاده کرده بود).
    """
    return f"{base.rstrip('/')}/R/{token}/"


def ensure_cloudflared() -> str:
    """
    مسیر binary کلودفلر را پیدا می‌کند؛ در صورت نبود دانلودش می‌کند.

    ترتیب جست‌وجو: CLOUDFLARED_PATH در config → PATH سیستم →
    tools\\cloudflared.exe (دانلود خودکار).
    """
    cfg = (getattr(config, "CLOUDFLARED_PATH", "") or "").strip()
    if cfg and os.path.isfile(cfg):
        return cfg
    found = shutil.which("cloudflared")
    if found:
        return found
    local = os.path.join(config.BASE_DIR, "tools", "cloudflared.exe")
    if os.path.isfile(local):
        return local

    logger.info("در حال دانلود cloudflared از %s", config.CLOUDFLARED_DOWNLOAD_URL)
    os.makedirs(os.path.dirname(local), exist_ok=True)
    tmp = local + ".part"
    try:
        with urllib.request.urlopen(config.CLOUDFLARED_DOWNLOAD_URL, timeout=120) as resp:
            with open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out)
    except Exception as exc:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise RemoteError(
            f"دانلود cloudflared ناموفق بود: {exc}\n"
            "خودت فایل cloudflared.exe را در tools\\cloudflared.exe بگذار "
            "یا مسیرش را در CLOUDFLARED_PATH داخل config.py بنویس."
        ) from exc
    os.replace(tmp, local)
    return local


def ensure_ngrok() -> str:
    """
    مسیر binary ngrok را پیدا می‌کند.

    ترتیب جست‌وجو: NGROK_PATH در config → PATH سیستم → tools\\ngrok.exe.
    دانلود خودکار انجام نمی‌شود چون نسخه‌های جدید ngrok معمولاً نیاز به
    حساب و authtoken دارند؛ در صورت نبود، راهنمای فارسی برگردانده می‌شود.
    """
    cfg = (getattr(config, "NGROK_PATH", "") or "").strip()
    if cfg and os.path.isfile(cfg):
        return cfg
    found = shutil.which("ngrok")
    if found:
        return found
    local = os.path.join(config.BASE_DIR, "tools", "ngrok.exe")
    if os.path.isfile(local):
        return local
    raise RemoteError(
        "برنامه‌ی ngrok پیدا نشد.\n"
        "آن را نصب کن و در PATH بگذار، یا فایل ngrok.exe را در "
        "tools\\ngrok.exe بگذار، یا مسیرش را در NGROK_PATH داخل "
        "config.py بنویس.\n"
        "دانلود: https://ngrok.com/download\n"
        "نکته: نسخه‌های جدید ngrok معمولاً نیاز به حساب رایگان و "
        "authtoken دارند (یک‌بار: ngrok config add-authtoken ...)."
    )


def _ngrok_public_url(api_url: str = _NGROK_API) -> Optional[str]:
    """آدرس عمومی https تونل را از API محلی ngrok می‌خواند (اگر آماده باشد)."""
    try:
        with urllib.request.urlopen(api_url, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    tunnels = data.get("tunnels") or []
    for t in tunnels:
        url = str((t or {}).get("public_url") or "")
        if url.startswith("https://"):
            return url
    for t in tunnels:
        url = str((t or {}).get("public_url") or "")
        if url.startswith("http"):
            return url
    return None


class _Hub:
    """آخرین فریم JPEG را نگه می‌دارد تا هر بیننده‌ی استریم همان را بفرستد."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._frame: Optional[bytes] = None
        self._seq = 0

    def publish(self, jpeg: bytes) -> None:
        with self._cv:
            self._frame = jpeg
            self._seq += 1
            self._cv.notify_all()

    def wait(self, last: int, timeout: float = 5.0):
        """(seq, frame) تازه را برمی‌گرداند؛ اگر فریمی نیامد (last, None)."""
        with self._cv:
            ok = self._cv.wait_for(lambda: self._seq != last, timeout=timeout)
            if ok and self._frame is not None:
                return self._seq, self._frame
            return last, None


# صفحه‌ی HTML کنسول. جای __W__ و __H__ با اندازه‌ی واقعی صفحه پر می‌شود
# تا مختصات کلیک مرورگر به پیکسل‌های واقعی ویندوز تبدیل شود.
#
# نکته‌ی ریسپانسیو: عرض تصویر همیشه ۱۰۰٪ است و نسبت ابعادش با JS حفظ
# می‌شود (چون MJPEG اندازه‌ی تصویر را با استایل می‌دهد، نه با width/height).
# روی موبایل ارتفاع ثابت به‌جای نسبت، تصویر را له می‌کرد.
_PAGE = r"""<!doctype html>
<html lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>کنسول ریموت</title>
<style>
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html,body{margin:0;padding:0;background:#111;color:#ddd;font-family:Tahoma,sans-serif}
  body{
    display:flex;flex-direction:column;height:100vh;height:100dvh;
    padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom);
  }
  header{
    flex:0 0 auto;padding:6px 8px;background:#1b1b1b;
    display:flex;gap:6px;flex-wrap:wrap;align-items:center;
    border-bottom:1px solid #2a2a2a;max-height:34vh;overflow-y:auto;-webkit-overflow-scrolling:touch;
  }
  /* تصویر: عرض کامل، ارتفاع متناسب با نسبت واقعی صفحه (JS ست می‌کند) */
  #v{
    display:block;width:100%;flex:0 0 auto;background:#000;cursor:crosshair;
    user-select:none;-webkit-user-select:none;touch-action:none;
  }
  button,input[type=text]{
    background:#2a2a2a;color:#eee;border:1px solid #444;border-radius:6px;
    padding:6px 10px;font-size:14px;min-height:34px;font-family:inherit;
  }
  button:active{background:#3a3a3a}
  button.on{background:#2d6a3d;border-color:#3d8a50}
  input[type=text]{flex:1 1 130px;min-width:110px}
  #st{font-size:12px;color:#8c8;flex:0 0 100%}
  .hint{font-size:11px;color:#888;flex:0 0 100%;line-height:1.5}
  /* روی صفحه‌های کوچک، دکمه‌ها بزرگ‌تر و راحت‌تر لمس می‌شوند */
  @media (max-width:600px){
    button,input[type=text]{font-size:15px;padding:9px 12px;min-height:42px}
    header{gap:5px;padding:6px}
  }
  @media (max-width:380px){
    button,input[type=text]{font-size:14px;padding:8px 9px;min-height:40px}
  }
</style>
</head>
<body>
<header>
  <span id="st">اتصال…</span>
  <button data-c="alt+tab">🔀 Alt+Tab</button>
  <button data-c="alt+shift+tab">⇩ Alt+Shift+Tab</button>
  <button data-k="enter">Enter</button>
  <button data-k="esc">Esc</button>
  <button data-k="tab">Tab</button>
  <button data-k="space">Space</button>
  <button data-k="backspace">&#9003;</button>
  <button data-k="delete">Del</button>
  <button data-k="up">&#9650;</button>
  <button data-k="down">&#9660;</button>
  <button data-k="left">&#9664;</button>
  <button data-k="right">&#9654;</button>
  <button data-c="ctrl+a">Ctrl+A</button>
  <button data-c="ctrl+c">Ctrl+C</button>
  <button data-c="ctrl+v">Ctrl+V</button>
  <button data-c="ctrl+x">Ctrl+X</button>
  <button data-c="ctrl+z">Ctrl+Z</button>
  <button data-c="alt+left">&#9664; قبلی</button>
  <button data-c="alt+right">بعدی &#9654;</button>
  <button data-k="f5">F5</button>
  <input id="txt" type="text" placeholder="متن برای تایپ/چسباندن…">
  <button id="send">&#9000; ارسال متن</button>
  <span class="hint">کلیک=انتخاب • راست‌کلیک=منو • چرخ=اسکرول • درگ با نگه داشتن • لمس=کلیک و کشیدن</span>
</header>
<img id="v" src="stream" alt="">
<script>
const W=__W__, H=__H__;
const v=document.getElementById('v'), st=document.getElementById('st');
// نسبت ابعاد واقعی صفحه را نگه می‌داریم تا تصویر روی موبایل کشیده/له نشود.
// (روی موبایل اگر فقط width:100% باشد، ارتفاع از نسبت ذاتی تصویر می‌آید که
//  همان نسبت درست است؛ این تابع آن را صریح و پایدار می‌کند.)
function fit(){
  const r=v.getBoundingClientRect();
  if(!r.width) return;
  v.style.height=Math.round(r.width*H/W)+'px';
}
window.addEventListener('resize',fit);
window.addEventListener('orientationchange',()=>setTimeout(fit,150));
// خطای ۵۰۲ از طرف کلودفلر گذراست: یعنی درخواست روی کانالی رفت که مبدأ
// بسته بود، ولی خود کار انجام می‌شود و درخواست بعدی موفق است. پس
// نباید کاربر را بترسانیم؛ فقط بعد از چند بار پیاپی هشدار می‌دهیم.
let netFails=0, shown502=false;
async function post(path, data){
  try{
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})});
    if(!r.ok){
      if(r.status===502){ netFails++; if(netFails>=3&&!shown502){ shown502=true; st.textContent='اتصال ناپایدار — در حال تلاش…'; } return; }
      st.textContent='خطای '+r.status;
    } else { netFails=0; shown502=false; if(st.textContent!=='متصل ✓') st.textContent='متصل ✓'; }
  }catch(e){ netFails++; st.textContent='قطع ارتباط — در حال تلاش دوباره…'; }
}
function pos(ev){
  const r=v.getBoundingClientRect();
  return {x:Math.round((ev.clientX-r.left)/r.width*W), y:Math.round((ev.clientY-r.top)/r.height*H)};
}
let down=false, lastP={x:0,y:0}, lastMove=0;
v.addEventListener('contextmenu',e=>e.preventDefault());
v.addEventListener('mousedown',e=>{
  e.preventDefault();
  const p=pos(e); lastP=p;
  if(e.button===2){ post('mouse',{action:'click',kind:'right',x:p.x,y:p.y}); return; }
  down=true; post('mouse',{action:'down',x:p.x,y:p.y});
});
window.addEventListener('mouseup',e=>{
  if(!down) return; down=false;
  const p=pos(e); lastP=p; post('mouse',{action:'up',x:p.x,y:p.y});
});
v.addEventListener('mousemove',e=>{
  if(!down) return;
  lastP=pos(e);
  const now=Date.now();
  if(now-lastMove<40) return;
  lastMove=now; post('mouse',{action:'move',x:lastP.x,y:lastP.y});
});
v.addEventListener('dblclick',e=>{
  const p=pos(e); post('mouse',{action:'click',kind:'double',x:p.x,y:p.y});
});
v.addEventListener('wheel',e=>{
  e.preventDefault();
  // deltaY: مثبت = پایین؛ مقدار تقریبی تعداد کلیک چرخ (۱ تا ۳۰)
  const n=Math.max(1,Math.min(30,Math.round(Math.abs(e.deltaY)/100)||1));
  post('mouse',{action:'scroll',dir:e.deltaY>0?'down':'up',amount:n});
},{passive:false});
// ---- لمسی (موبایل): Tap کوتاه=کلیک، نگه‌داشتن/کشیدن=درگ
// قبلاً روی touchstart بلافاصله down فرستاده می‌شد و هر ضربه به «نگه داشتن»
// تبدیل می‌شد. حالا تا وقتی انگشت واقعاً نگه داشته شود یا حرکت کند، down
// ارسال نمی‌شود.
let tState=null, tTimer=null;
const TAP_MOVE=12, HOLD_MS=350;
function tPoint(e){
  const t=(e.touches&&e.touches[0])||(e.changedTouches&&e.changedTouches[0]);
  return t?pos(t):lastP;
}
function clearTHold(){ if(tTimer){ clearTimeout(tTimer); tTimer=null; } }
v.addEventListener('touchstart',e=>{
  e.preventDefault();
  clearTHold();
  const p=tPoint(e); lastP=p;
  tState={sx:p.x,sy:p.y,x:p.x,y:p.y,downSent:false};
  // اگر انگشت به‌اندازه‌ی HOLD_MS نگه داشته شود، وارد حالت نگه‌داشتن/درگ می‌شویم
  tTimer=setTimeout(()=>{
    if(!tState || tState.downSent) return;
    tState.downSent=true; down=true;
    post('mouse',{action:'down',x:tState.sx,y:tState.sy});
  }, HOLD_MS);
},{passive:false});
v.addEventListener('touchmove',e=>{
  e.preventDefault();
  if(!tState) return;
  const p=tPoint(e); lastP=p; tState.x=p.x; tState.y=p.y;
  const moved=Math.abs(p.x-tState.sx)+Math.abs(p.y-tState.sy);
  if(!tState.downSent && moved>TAP_MOVE){
    // حرکت محسوس قبل از پایان تایمر => شروع درگ (نگه داشتن)
    clearTHold();
    tState.downSent=true; down=true;
    post('mouse',{action:'down',x:tState.sx,y:tState.sy});
  }
  if(tState.downSent){
    const now=Date.now();
    if(now-lastMove<40) return;
    lastMove=now; post('mouse',{action:'move',x:p.x,y:p.y});
  }
},{passive:false});
function endTouch(e,cancelled){
  e.preventDefault();
  clearTHold();
  if(!tState) return;
  const p=tPoint(e)||lastP; lastP=p;
  if(tState.downSent){
    down=false;
    post('mouse',{action:'up',x:p.x,y:p.y});
  }else if(!cancelled){
    // ضربه‌ی کوتاه بدون نگه‌داشتن و بدون کشیدن => کلیک
    post('mouse',{action:'click',kind:'left',x:tState.sx,y:tState.sy});
  }
  tState=null;
}
v.addEventListener('touchend',e=>endTouch(e,false),{passive:false});
v.addEventListener('touchcancel',e=>endTouch(e,true),{passive:false});
// ---- دکمه‌ها و کیبورد
document.querySelectorAll('button[data-k]').forEach(b=>b.onclick=()=>post('key',{key:b.dataset.k}));
document.querySelectorAll('button[data-c]').forEach(b=>b.onclick=()=>post('key',{combo:b.dataset.c}));
document.getElementById('send').onclick=()=>{
  const t=document.getElementById('txt').value;
  if(t) post('text',{text:t});
};
const KEYMAP={Enter:'enter',Escape:'esc',Tab:'tab',' ':'space',Backspace:'backspace',Delete:'delete',
  ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Home:'home',End:'end',
  PageUp:'pageup',PageDown:'pagedown',F5:'f5'};
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT') return;
  const k=e.key;
  if(k==='Control'||k==='Shift'||k==='Alt') return;
  // Alt+Tab را مرورگر خودش می‌گیرد، پس با keydown معمولی نمی‌رسد؛ دکمه‌ی
  // اختصاصی بالا برای همین هست. این‌ها فقط حالت «بدون Alt» را می‌فرستند.
  const low=k.length===1?k.toLowerCase():k;
  if(e.ctrlKey && ['a','c','v','x','z'].includes(low)){
    e.preventDefault(); post('key',{combo:'ctrl+'+low}); return;
  }
  if(e.altKey && (k==='ArrowLeft'||k==='ArrowRight')){
    e.preventDefault(); post('key',{combo:'alt+'+(k==='ArrowLeft'?'left':'right')}); return;
  }
  if(KEYMAP[k]){ e.preventDefault(); post('key',{key:KEYMAP[k]}); return; }
  if(k.length===1){ post('text',{text:k}); }
});
v.addEventListener('load',fit);
v.addEventListener('error',()=>{
  st.textContent='در حال اتصال دوباره…';
  setTimeout(()=>{v.src='stream?t='+Date.now();},1500);
});
fit();
</script>
</body>
</html>
"""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, console: "RemoteConsole"):
        super().__init__(addr, handler)
        self.console = console

    def handle_error(self, request, client_address) -> None:
        """
        قطع اتصال توسط مرورگر/تونل را خطا حساب نمی‌کند.

        با HTTP/1.1 (که برای رفع خطای ۵۰۲ لازم شد) اتصال‌ها باز می‌مانند و
        هر بار که مرورگر صفحه را ترک می‌کند یا cloudflared کانال را دور
        می‌اندازد، سرور در `readline` خطای ConnectionResetError می‌گیرد.
        کتابخانه‌ی استاندارد برای هر کدام یک traceback کامل روی stderr
        می‌چاپد که کنسول را پر از پیام‌های تکراری می‌کرد و هیچ ربطی به
        سلامت کنسول نداشت.

        این‌ها قطع اتصالِ عادی از سمت کلاینت‌اند، نه باگ؛ بقیه‌ی خطاها
        مثل قبل لاگ می‌شوند تا واقعاً دیده شوند.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            logger.debug(
                "کلاینت کنسول ریموت اتصال را بست: %s", client_address
            )
            return
        if isinstance(exc, socket.timeout):
            # اتصال بی‌کار افتاده (مثلاً بعد از رفتن کاربر)؛ بی‌خطر است
            logger.debug("اتصال بی‌کار کنسول ریموت بسته شد: %s", client_address)
            return
        logger.warning("خطای پردازش درخواست کنسول ریموت", exc_info=True)


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 با keep-alive، نه HTTP/1.0.
    #
    # ریشه‌ی خطای ۵۰۲ که روی صفحه دیده می‌شد همین بود: با HTTP/1.0 بعد از
    # هر درخواست کانال بسته می‌شد. cloudflared کانال‌های اتصال به مبدأ را
    # نگه می‌دارد و روی کانالِ بسته‌شده دوباره می‌نویسد، پس درخواست بعدی
    # خطای ۵۰۲ می‌گرفت (ولی چون درخواست بعدی روی کانال تازه موفق می‌شد،
    # کنترل کار می‌کرد و فقط عدد ۵۰۲ سبز روی صفحه دیده می‌شد).
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        # لاگ پیش‌فرض stderr هر درخواست را چاپ می‌کند؛ برای استریم پرحرف است
        logger.debug("remote-http: " + fmt, *args)

    @property
    def console(self) -> RemoteConsole:
        return self.server.console  # type: ignore[attr-defined]

    def _send(self, code: int, body, ctype: str = "application/json; charset=utf-8") -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _route(self) -> Optional[str]:
        """
        مسیر درخواست را با توکن تطبیق می‌دهد.

        خروجی: نام کاره ('page','stream','mouse','key','text')،
        '__done__' اگر خودش جواب داده باشد، یا None اگر توکن اشتباه/نبود.
        """
        tok = self.console.token
        if not tok:
            return None
        path = urllib.parse.urlparse(self.path).path
        prefix = f"/R/{tok}"
        if path == prefix:
            # بدون اسلش آخر؛ ریدایرکت تا لینک‌های نسبی صفحه درست حل شوند
            self.send_response(302)
            self.send_header("Location", prefix + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return "__done__"
        if path == prefix + "/":
            return "page"
        if path == prefix + "/stream":
            return "stream"
        for name in ("mouse", "key", "text"):
            if path == f"{prefix}/{name}":
                return name
        return None

    def do_GET(self) -> None:  # noqa: N802 (نام استاندارد http.server)
        kind = self._route()
        if kind == "__done__":
            return
        if kind == "page":
            html = _PAGE.replace("__W__", str(self.console.screen_w)).replace(
                "__H__", str(self.console.screen_h)
            )
            self._send(200, html, "text/html; charset=utf-8")
            return
        if kind == "stream":
            self._handle_stream()
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        kind = self._route()
        if kind in (None, "__done__", "page", "stream"):
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        try:
            data = self._json_body()
        except Exception:
            self._json(400, {"ok": False, "error": "json نامعتبر"})
            return
        if data is None:
            self._json(400, {"ok": False, "error": "بدنه‌ی خالی"})
            return
        try:
            if kind == "mouse":
                self.console.do_mouse(data)
            elif kind == "key":
                self.console.do_key(data)
            else:
                self.console.do_text(data)
        except Exception as exc:
            logger.debug("فرمان کنسول ریموت رد شد: %s", exc)
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(200, {"ok": True})

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 65536:
            return None
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _handle_stream(self) -> None:
        """
        استریم MJPEG: هر بیننده آخرین فریم را می‌گیرد تا قطع شود.

        دو نکته‌ی مهم برای HTTP/1.1:

          ۱) چون طول محتوا معلوم نیست، هدر «Connection: close» لازم است تا
             مرورگر بداند پایان استریم با بسته شدن کانال است. بدون آن، مرورگر
             منتظر می‌ماند و تصویر اصلاً نمایش داده نمی‌شود.
          ۲) هرگز نباید فقط به‌خاطر «بی‌فریم بودن» بسته شود. قبلاً با
             timeout=5 ثانیه، اگر فریمی نمی‌رسید کل استریم قطع می‌شد و
             مرورگر بعد از ۱٫۵ ثانیه دوباره وصل می‌شد؛ همین رفت‌وبرگشت‌ها
             تأخیر چندثانیه‌ای می‌ساخت. حالا فقط منتظر می‌مانیم.
        """
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        # بدون این هدر و با HTTP/1.1، مرورگر منتظر پایان بدنه می‌ماند.
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        # بیننده را **قبل** از حلقه ثبت می‌کنیم. حلقه‌ی کپچر تا وقتی
        # بیننده‌ای نباشد عکس نمی‌گیرد (برای صرفه‌جویی CPU)، پس اگر بعد از
        # ارسال هدر ثبتش می‌کردیم، اولین فریم‌ها تا وصل شدن کاملِ خواننده
        # تولید نمی‌شدند و صفحه چند ثانیه خالی می‌ماند.
        self.console.client_join()
        last = 0
        try:
            while not self.console.is_stopping():
                seq, jpeg = self.console.hub.wait(last, timeout=1.0)
                if jpeg is None:
                    # موقتاً فریمی نیست (کپچر کند شده یا هنوز شروع نشده).
                    # استریم را نمی‌بندیم؛ فقط دوباره منتظر می‌مانیم.
                    continue
                last = seq
                try:
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        b"Content-Length: %d\r\n\r\n" % len(jpeg) + jpeg + b"\r\n"
                    )
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
        finally:
            self.console.client_leave()


class RemoteConsole:
    """سرور وب + استریم + تونل (Cloudflare/ngrok)؛ همه‌ی وضعیت در یک نمونه."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._httpd: Optional[_Server] = None
        self._port = 0
        self._token = ""
        self._url: Optional[str] = None
        self._hub = _Hub()
        self._stop: Optional[threading.Event] = None
        self._serve_thread: Optional[threading.Thread] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._url_event = threading.Event()
        self._clients = 0
        self._clients_lock = threading.Lock()
        self.screen_w = 0
        self.screen_h = 0
        # نوع تونل فعال/آخرین: cloudflare | ngrok | local
        self._provider = "cloudflare"
        # چند خط آخر خروجی تونل برای نمایش خطای قابل‌فهم
        self._tunnel_log: list[str] = []
        # پنجره‌ی هدفِ کیبورد: بعد از هر کلیک ثبت می‌شود و قبل از
        # فرستادن کلید/متن دوباره جلو می‌آید (تلگرام دسکتاپ فوکوس را
        # از مقصد می‌گیرد وگرنه شورتکات‌ها به تلگرام می‌خورد).
        self._target_hwnd = 0
        # شمارنده‌ی «از چند کلیک یک‌بار هدف تازه شود»؛ خواندن handle پنجره‌ی
        # فعال دو فراخوانی win32 دارد و با هر کلیک حس کندی می‌داد.
        # منطق _maybe_remember_target در صورت نبود هدف، فوراً ثبت می‌کند.
        self._since_remember = 0

    # ------------------------------------------------------------ ویژگی‌ها
    @property
    def hub(self) -> _Hub:
        return self._hub

    @property
    def token(self) -> str:
        return self._token

    def is_stopping(self) -> bool:
        return self._stop is None or self._stop.is_set()

    def client_join(self) -> None:
        with self._clients_lock:
            self._clients += 1

    def client_leave(self) -> None:
        with self._clients_lock:
            self._clients = max(0, self._clients - 1)

    # ------------------------------------------------------------ اجرا
    def start(
        self,
        timeout: Optional[float] = None,
        tunnel: bool = True,
        provider: str = "cloudflare",
    ) -> str:
        """
        سرور را بالا می‌آورد و آدرس تمام‌شده را برمی‌گرداند.

        provider: "cloudflare" (پیش‌فرض) یا "ngrok".
        tunnel=False فقط سرور محلی را بالا می‌آورد (برای تست بدون شبکه).
        اگر از قبل کاملاً فعال باشد همان آدرس قبلی برمی‌گردد؛ اگر نیمه‌کاره
        باشد اول همه‌چیز بسته می‌شود و از نو ساخته می‌شود.
        """
        with self._lock:
            if self._httpd and self._url and (self._proc is None or self._proc.poll() is None):
                return self._url
            self._stop_locked()
            timeout = config.REMOTE_TUNNEL_TIMEOUT if timeout is None else timeout
            provider = str(provider or "cloudflare").strip().lower()

            self._token = secrets.token_urlsafe(12)
            self._stop = threading.Event()
            self._hub = _Hub()
            self._url = None
            self._url_event = threading.Event()
            self._clients = 0
            self._provider = provider
            self._tunnel_log = []
            size = pyautogui.size()
            self.screen_w, self.screen_h = int(size[0]), int(size[1])

            self._httpd = _Server((config.REMOTE_HOST, config.REMOTE_PORT), _Handler, self)
            self._port = self._httpd.server_address[1]
            self._serve_thread = threading.Thread(
                target=self._httpd.serve_forever, name="remote-http", daemon=True
            )
            self._serve_thread.start()
            self._capture_thread = threading.Thread(
                target=self._capture_loop, name="remote-capture", daemon=True
            )
            self._capture_thread.start()

            if not tunnel:
                self._provider = "local"
                self._url = _page_url(
                    f"http://{config.REMOTE_HOST}:{self._port}", self._token
                )
                return self._url

            # تلاش چندباره: گاهی تونل اول URL می‌دهد ولی فوراً می‌میرد
            # (مثلاً قطعی لحظه‌ای شبکه)؛ در این حالت کاربر لینک می‌گیرد ولی
            # با خطای 1033/عدم دسترسی مواجه می‌شود.
            last_error = "تونل هیچ آدرس برنگرداند"
            for attempt in range(3):
                if self._stop is not None and self._stop.is_set():
                    break
                try:
                    if provider == "ngrok":
                        return self._start_ngrok_once(timeout)
                    self._provider = "cloudflare"
                    return self._start_cloudflare_once(timeout)
                except RemoteError as exc:
                    last_error = str(exc)
                    logger.warning(
                        "تلاش %d تونل %s ناموفق: %s", attempt + 1, self._provider, exc
                    )
                    self._stop_tunnel_only()
                    time.sleep(1.0)
            raise RemoteError(last_error)

    def stop(self) -> bool:
        with self._lock:
            return self._stop_locked()

    def _stop_locked(self) -> bool:
        had = bool(self._httpd or self._proc)
        if self._stop is not None:
            self._stop.set()
        if self._httpd is not None:
            with contextlib.suppress(Exception):
                self._httpd.shutdown()
            with contextlib.suppress(Exception):
                self._httpd.server_close()
            self._httpd = None
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
            with contextlib.suppress(Exception):
                self._proc.wait(5)
            with contextlib.suppress(Exception):
                if self._proc.poll() is None:
                    self._proc.kill()
            self._proc = None
        for t in (self._serve_thread, self._capture_thread, self._reader_thread):
            if t is not None and t.is_alive():
                with contextlib.suppress(Exception):
                    t.join(2)
        self._serve_thread = self._capture_thread = self._reader_thread = None
        self._url = None
        self._port = 0
        self._token = ""
        self._target_hwnd = 0
        self._since_remember = 0
        return had

    def _stop_tunnel_only(self) -> None:
        """
        فقط پروسه‌ی cloudflared و ترد خواندنش را می‌بندد.

        برای retry تونل به کار می‌رود؛ سرور HTTP و ترد capture
        (که وابسته به شبکه نیستند) دست نمی‌خورند تا لازم نباشد
        هر بار از نو ساخته شوند.
        """
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
            with contextlib.suppress(Exception):
                self._proc.wait(5)
            with contextlib.suppress(Exception):
                if self._proc.poll() is None:
                    self._proc.kill()
            self._proc = None
        if self._reader_thread is not None and self._reader_thread.is_alive():
            with contextlib.suppress(Exception):
                self._reader_thread.join(2)
        self._reader_thread = None
        self._url = None

    def status(self) -> dict:
        with self._lock:
            http_up = self._httpd is not None
            proc_ok = self._proc is None or self._proc.poll() is None
            running = http_up and proc_ok and bool(self._url)
            return {
                "running": running,
                "url": self._url if running else None,
                "port": self._port,
                "provider": self._provider if running else None,
            }

    # ------------------------------------------------------------ فریم‌گیری
    def _capture_loop(self) -> None:
        """
        حلقه‌ی ساخت فریم‌های استریم.

        برای کم کردن تأخیر، تصویر **قبل از** annotate کوچک می‌شود
        (max_width به annotated_screenshot داده می‌شود). قبلاً تصویر کامل
        ۱۹۲۰×۱۰۸۰ گرفته و annotate می‌شد و تازه بعداً resize می‌شد؛ کشیدن
        چند خط روی تصویر بزرگ گران‌تر از کل کارِ resize بود و همین باعث
        می‌شد نرخ واقعی فریم خیلی کمتر از REMOTE_FPS شود و صفحه عقب بیفتد.

        screen_size هم یک‌بار خوانده و نگه داشته می‌شود تا هر فریم دوباره
        اندازه‌ی صفحه پرسیده نشود.
        """
        interval = 1.0 / max(1, config.REMOTE_FPS)
        max_w = int(config.REMOTE_MAX_WIDTH)
        try:
            screen = pyautogui.size()
            screen_size = (int(screen[0]), int(screen[1]))
        except Exception:
            screen_size = None

        while not self.is_stopping():
            # بدون بیننده، عکس نگیر تا CPU و باتری هدر نرود
            with self._clients_lock:
                clients = self._clients
            if clients <= 0:
                self._stop and self._stop.wait(0.4)
                continue
            t0 = time.time()
            try:
                img = mouse.annotated_screenshot(
                    max_width=max_w, screen_size=screen_size
                )
                self._hub.publish(
                    imaging.encode_jpeg(img, quality=config.REMOTE_JPEG_QUALITY)
                )
            except Exception:
                logger.debug("فریم استریم ساخته نشد", exc_info=True)
            elapsed = time.time() - t0
            self._stop and self._stop.wait(max(0.0, interval - elapsed))

    def _pump(self) -> None:
        """
        خروجی فرایند تونل را می‌خواند و خالی می‌کند.

        برای Cloudflare، آدرس trycloudflare از همان خروجی پیدا و signal
        می‌شود. برای همه‌ی تونل‌ها خروجی در بافر کوتاهی نگه داشته می‌شود تا
        در صورت خطا پیام قابل‌فهم‌تری به کاربر داده شود.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            logger.debug("tunnel(%s): %s", self._provider, line)
            # نگه داشتن چند خط آخر برای گزارش خطا (بدون رشد بی‌پایان)
            self._tunnel_log.append(line)
            if len(self._tunnel_log) > 80:
                del self._tunnel_log[:-80]
            if self._provider == "cloudflare":
                url = parse_tunnel_url(line)
                if url and not self._url_event.is_set():
                    self._url = url
                    self._url_event.set()

    def _start_cloudflare_once(self, timeout: float) -> str:
        """
        یک بار تونل Cloudflare را بالا می‌آورد، منتظر URL می‌ماند و سلامت آن
        را قبل از تحویل به کاربر بررسی می‌کند.
        """
        self._url = None
        self._url_event = threading.Event()
        self._tunnel_log = []
        exe = ensure_cloudflared()
        # پروکسی سیستم عمداً به cloudflared پاس نمی‌شود: پورت 10808
        # معمولاً SOCKS است (v2ray/xray) و پاس دادنش به‌صورت HTTPS_PROXY
        # باعث کرش فوری cloudflared می‌شود.
        self._proc = subprocess.Popen(
            [exe, "tunnel", "--url", f"http://{config.REMOTE_HOST}:{self._port}",
             "--no-autoupdate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
        self._reader_thread = threading.Thread(
            target=self._pump, name="remote-cloudflared", daemon=True
        )
        self._reader_thread.start()

        if not self._url_event.wait(timeout):
            detail = " (cloudflared بسته شد)" if self._proc.poll() is not None else ""
            raise RemoteError(
                f"تونل Cloudflare در {timeout:.0f} ثانیه آماده نشد{detail}. "
                "اینترنت را بررسی کن و دوباره بزن."
            )
        base = (self._url or "").rstrip("/")
        if not base:
            raise RemoteError("آدرس تونل خالی است.")

        # URL چند ثانیه قبل از برقراری واقعی مسیر چاپ می‌شود؛ کمی صبر و چک
        # زنده‌بودن پروسه کافی است.
        settle = 4.0
        if self._stop is not None and self._stop.wait(settle):
            raise RemoteError("توقف تونل درخواست شد.")
        if self._proc.poll() is not None:
            raise RemoteError(
                "cloudflared بعد از ساخت تونل بسته شد؛ اینترنت/فیلترینگ را بررسی کن."
            )

        self._url = _page_url(base, self._token)
        return self._url

    # سازگاری با کد/تست‌هایی که نام قدیمی را صدا می‌زنند
    def _start_tunnel_once(self, timeout: float) -> str:
        return self._start_cloudflare_once(timeout)

    def _start_ngrok_once(self, timeout: float) -> str:
        """
        یک بار تونل ngrok را بالا می‌آورد و آدرس عمومی را از API محلی
        (127.0.0.1:4040) می‌خواند.
        """
        self._url = None
        self._url_event = threading.Event()
        self._tunnel_log = []
        exe = ensure_ngrok()
        self._proc = subprocess.Popen(
            [exe, "http", f"http://{config.REMOTE_HOST}:{self._port}", "--log=stdout"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
        self._reader_thread = threading.Thread(
            target=self._pump, name="remote-ngrok", daemon=True
        )
        self._reader_thread.start()

        deadline = time.time() + timeout
        base = None
        while time.time() < deadline:
            if self._stop is not None and self._stop.is_set():
                raise RemoteError("توقف تونل درخواست شد.")
            if self._proc.poll() is not None:
                tail = "\n".join(self._tunnel_log[-6:]).strip()
                extra = f"\nجزئیات:\n{tail}" if tail else ""
                raise RemoteError(
                    "ngrok اجرا نشد یا بسته شد. اگر نسخه‌ی جدید است ممکن است "
                    "نیاز به حساب رایگان و authtoken داشته باشد "
                    "(یک‌بار: ngrok config add-authtoken ...)."
                    + extra
                )
            base = _ngrok_public_url()
            if base:
                break
            time.sleep(0.5)

        if not base:
            detail = " (ngrok بسته شد)" if self._proc and self._proc.poll() is not None else ""
            raise RemoteError(
                f"تونل ngrok در {timeout:.0f} ثانیه آماده نشد{detail}. "
                "نصب/لاگین ngrok و اتصال اینترنت را بررسی کن."
            )

        if self._stop is not None and self._stop.wait(2.0):
            raise RemoteError("توقف تونل درخواست شد.")
        if self._proc.poll() is not None:
            raise RemoteError("ngrok بعد از ساخت تونل بسته شد.")

        self._url = _page_url(base, self._token)
        return self._url

# ------------------------------------------------------------ فرمان‌ها
    def _remember_target(self) -> None:
        """پنجره‌ی فعال فعلی را هدف کیبورد نگه می‌دارد."""
        h = windows.foreground()
        if h and windows.is_window(h):
            self._target_hwnd = h
            self._since_remember = 0

    def _maybe_remember_target(self, settle: float = 0.0) -> None:
        """
        ثبت هدف پنجره؛ فوراً اگر هدف نامعلوم/نامعتبر است، وگرنه هر چند فرمان یک‌بار.

        خواندن handle پنجره‌ی فعال دو فراخوانی win32 دارد و روی هر کلیک
        این کار حس کندی می‌داد، پس در حالت عادی هر REMOTE_REMEMBER_EVERY
        فرمان یک‌بار تازه می‌شود. اما اولین کلیک پس از شروع/از دست رفتن هدف
        باید حتماً ثبت شود، وگرنه refocus و چسباندن متن (کنسول ریموت) به
        پنجره‌ی درست نمی‌رسد.

        settle: مکث کوتاه پیش از خواندن پنجره‌ی فعال. بعد از کلیک/درگ،
        ویندوز ممکن است با تأخیر پنجره‌ی زیر نشانگر را فعال کند؛ بدون این
        مکث، پنجره‌ی قبلی (مثلاً مرورگر کنسول ریموت) به‌اشتباه هدف ثبت می‌شود.
        """
        h = self._target_hwnd
        if not h or not windows.is_window(h):
            if settle > 0:
                time.sleep(settle)
            self._remember_target()
            return
        self._since_remember += 1
        every = max(1, int(getattr(config, "REMOTE_REMEMBER_EVERY", 5)))
        if self._since_remember >= every:
            if settle > 0:
                time.sleep(settle)
            self._remember_target()

    def _refocus_target(self) -> bool:
        """
        قبل از کلید/ترکیب/متن، هدف را جلو می‌آورد.

        اگر هدف شناخته‌شده باشد و پنجره‌ی دیگری (مثلاً مرورگر/تلگرام روی
        همین PC) فوکوس گرفته باشد، هدف دوباره جلو آورده می‌شود تا ورودی به
        پنجره‌ی درست برسد. اگر هدفی شناخته نشده باشد، دست نمی‌زنیم (تا پنجره‌ی
        کلاینتِ کنترل به‌اشتباه به‌عنوان هدف ثبت نشود) و ورودی به پنجره‌ی فعال
        فعلی ارسال می‌شود.
        """
        h = self._target_hwnd
        if not h or not windows.is_window(h):
            self._target_hwnd = 0
            return False
        if windows.is_foreground(h):
            return True
        ok = windows.bring_to_front(h)
        if ok:
            # فرصت کوتاه برای اعمال فوکوس قبل از ارسال کلید/متن
            time.sleep(0.03)
        return ok

    def do_mouse(self, data: dict) -> None:
        action = str(data.get("action", ""))
        x, y = data.get("x"), data.get("y")
        # مکث کمتر از حالت تلگرام: در رابط وب فرمان‌ها تعاملی و پشت‌سرهم‌اند
        pause = config.REMOTE_ACTION_PAUSE
        if action == "scroll":
            d = data.get("dir")
            if d not in ("up", "down"):
                raise ValueError("dir نامعتبر")
            # مقدار از مرورگر (wheel delta تقریبی)؛ اگر نبود پیش‌فرض
            mouse.scroll(
                str(d),
                int(data.get("amount") or config.MOUSE_SCROLL_STEP_DEFAULT),
                pause=pause,
            )
            return
        if action in ("move", "down", "up", "click"):
            if x is not None and y is not None:
                mouse.move_abs(int(x), int(y))
            if action == "move":
                return
            if action == "down":
                mouse.hold(pause=pause)
                # مکث اضافی تا پنجره‌ی زیر نشانگر فرصت فعال شدن داشته باشد
                self._maybe_remember_target(settle=0.12)
                return
            if action == "up":
                mouse.release(pause=pause)
                self._maybe_remember_target(settle=0.12)
                return
            kind = str(data.get("kind") or "left")
            if kind not in mouse.CLICKS:
                raise ValueError("نوع کلیک نامعتبر")
            mouse.click(kind, pause=pause)
            self._maybe_remember_target(settle=0.12)
            return
        raise ValueError("عملیات نامعتبر")

    def do_key(self, data: dict) -> None:
        self._refocus_target()
        if "combo" in data:
            c = str(data["combo"])
            if c not in mouse.COMBOS:
                raise ValueError("ترکیب نامعتبر")
            mouse.combo(c, pause=config.REMOTE_ACTION_PAUSE)
            return
        k = str(data.get("key", ""))
        if k not in _REMOTE_KEYS:
            raise ValueError("کلید نامعتبر")
        mouse.press(k, pause=config.REMOTE_ACTION_PAUSE)

    def do_text(self, data: dict) -> None:
        t = data.get("text")
        if not isinstance(t, str) or not t:
            raise ValueError("متن نامعتبر")
        self._refocus_target()
        mouse.set_text(t[:10000])


# نمونه‌ی سراسری؛ بات فقط همین را صدا می‌زند
console = RemoteConsole()


def start(timeout: Optional[float] = None, provider: str = "cloudflare") -> str:
    return console.start(timeout=timeout, provider=provider)


def stop() -> bool:
    return console.stop()


def status() -> dict:
    return console.status()
