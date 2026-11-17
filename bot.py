#!/usr/bin/env python3
"""ARK Telegram Bot —— 支持每天 12:00 / 00:00 定时推送"""
import os, sys, time, json, threading, datetime
from pathlib import Path
import http.server

import requests
from requests.exceptions import ReadTimeout, RequestException

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    print("no token")
    sys.exit(1)

BASE = f"https://api.telegram.org/bot{TOKEN}"
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "10"))
STATE_PATH = Path(os.environ.get("STATE_PATH", "/tmp/ark_bot_state.json"))

# 固定推送对象（你 + 群 + 4位），环境变量可覆盖
DEFAULT_SUBS = "6181300497,-5567259487,810215667,8081808952,8099061382,8239955906"

RPC_LIST = [
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
]
RV = "0x8366a748E02F730911Cb5AB4fd049d2E1e0414b7"
LP = "0xCAaF3c41a40103a23Eeaa4BbA468AF3cF5b0e0D8"
SEL = "0xc3f909d4"
SEL_RESERVES = "0x0902f1ac"
DASHBOARD = os.environ.get("DASHBOARD_URL", "https://aquamarine-kitten-94e7d3.netlify.app")

TZ8 = datetime.timezone(datetime.timedelta(hours=8))


def load_subs():
    """订阅者 = 文件里存的 + 环境变量里固定的"""
    subs = set()
    for s in os.environ.get("BROADCAST_IDS", DEFAULT_SUBS).split(","):
        s = s.strip()
        if s:
            try:
                subs.add(int(s))
            except ValueError:
                pass
    try:
        for x in json.loads(STATE_PATH.read_text()):
            subs.add(int(x))
    except Exception:
        pass
    return subs


def save_subs(s):
    try:
        STATE_PATH.write_text(json.dumps(sorted(s)))
    except Exception:
        pass


SUBS = load_subs()


def call(method, payload=None, timeout=20):
    r = requests.post(f"{BASE}/{method}", json=payload or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()["result"]


def send(chat_id, text):
    try:
        return call("sendMessage", {"chat_id": chat_id, "text": text,
                    "parse_mode": "HTML", "disable_web_page_preview": True})
    except RequestException as e:
        print("[send err]", e, flush=True)


def rpc_call(data):
    last = None
    for rpc in RPC_LIST:
        try:
            p = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                 "params": [{"to": data["to"], "data": data["sel"]}, "latest"]}
            r = requests.post(rpc, json=p, timeout=20)
            return r.json()["result"]
        except Exception as e:
            last = e
    raise last or RuntimeError("all rpc failed")


def read_config():
    raw = rpc_call({"to": RV, "sel": SEL})[2:]
    w = [raw[i:i + 64] for i in range(0, len(raw), 64)]
    v = [int(x, 16) for x in w[:6]]
    return {"minRatio": v[4] / 1e4, "maxRatio": v[5] / 1e4,
            "totalVestingAmount": str(v[1]), "totalClaimAmount": str(v[2]),
            "globalMaxClaimAllowanceLimit": str(v[3])}


def ark_price():
    try:
        raw = rpc_call({"to": LP, "sel": SEL_RESERVES})[2:]
        w = [raw[i:i + 64] for i in range(0, len(raw), 64)]
        r0 = int(w[0], 16) / 1e18
        r1 = int(w[1], 16) / 1e18
        return r0 / r1 if r1 > 0 else None
    except Exception:
        return None


def f(x):
    return f"{x:.4f}"


def now_text(rec):
    if not rec:
        return "currently unavailable"
    return ("ARK Consensus Coefficient\n"
            f"min: {f(rec['minRatio'])}\n"
            f"max: {f(rec['maxRatio'])}\n"
            f"dashboard: {DASHBOARD}")


def coef_text(rec):
    """标准 4 行推送格式"""
    if not rec:
        return "ARK 系數暫時讀不到"
    Y = rec["minRatio"]
    pr = 0.78 * Y - 0.02
    p = ark_price()
    usd = f"${pr * 100 * p:,.0f}" if p else "$—"
    return ("🔔 ARK 渦輪系數\n\n"
            f"Y：{Y:.4f} （{rec['minRatio']:.4f} ~ {rec['maxRatio']:.4f}）\n\n"
            f"到手比例：{pr*100:.2f}%\n\n"
            f"100 ARK 到手：{pr*100:.2f} · 折算 {usd}")


HELP = ("ARK Turbine Monitor\n"
        "Commands:\n"
        "/now - current\n"
        "/test - test push\n"
        "/status - status\n"
        "/stop - unsubscribe\n"
        "/start - subscribe\n")

LAST = {"rec": None, "min": None, "max": None}


def broadcast_all(rec):
    for cid in list(SUBS):
        send(cid, coef_text(rec))


def monitor_loop():
    last_noon = None
    last_midnight = None
    while True:
        try:
            rec = read_config()
            LAST["rec"] = rec
            mn, mx = rec["minRatio"], rec["maxRatio"]

            # 规则1：系数变化
            if LAST["min"] is None:
                LAST["min"], LAST["max"] = mn, mx
            elif mn != LAST["min"] or mx != LAST["max"]:
                print("[change]", LAST["min"], "->", mn, flush=True)
                broadcast_all(rec)
                LAST["min"], LAST["max"] = mn, mx

            # 规则2：每日 12:00 / 00:00（北京时间）
            now = datetime.datetime.now(TZ8)
            today = now.strftime("%Y-%m-%d")
            hhmm = now.hour * 60 + now.minute

            if 12 * 60 <= hhmm <= 12 * 60 + 30 and last_noon != today:
                print("[daily 12:00]", today, flush=True)
                broadcast_all(rec)
                last_noon = today

            if hhmm <= 30 and last_midnight != today:
                print("[daily 00:00]", today, flush=True)
                broadcast_all(rec)
                last_midnight = today
        except Exception as e:
            print("[monitor err]", e, flush=True)
        time.sleep(POLL_INTERVAL)


def health_server():
    """假网站端口，让 Fly.io 认为服务健康，不会被自动停"""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *a):
            pass
    try:
        srv = http.server.HTTPServer(("0.0.0.0", 8080), H)
        srv.serve_forever()
    except Exception as e:
        print("[health err]", e, flush=True)


def main():
    threading.Thread(target=health_server, daemon=True).start()
    call("deleteWebhook", {"drop_pending_updates": False})
    me = call("getMe")
    print(f"Bot @{me['username']} started", flush=True)
    print(f"subs: {sorted(SUBS)}", flush=True)
    threading.Thread(target=monitor_loop, daemon=True).start()

    offset = None
    while True:
        try:
            updates = call("getUpdates", {"offset": offset, "timeout": 10}, timeout=20)
        except ReadTimeout:
            continue
        except RequestException as e:
            print("[poll err]", e, flush=True)
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            text = (msg.get("text") or "").strip()
            if not cid:
                continue
            if text.startswith("/start"):
                SUBS.add(cid); save_subs(SUBS)
                send(cid, HELP); send(cid, now_text(LAST["rec"]))
            elif text.startswith("/stop"):
                SUBS.discard(cid); save_subs(SUBS)
                send(cid, "unsubscribed")
            elif text.startswith("/test"):
                send(cid, "test push OK")
            elif text.startswith("/now"):
                send(cid, now_text(LAST["rec"]))
            elif text.startswith("/status"):
                send(cid, f"status: interval {POLL_INTERVAL}s, subs {len(SUBS)}")
            else:
                send(cid, HELP)


if __name__ == "__main__":
    main()
