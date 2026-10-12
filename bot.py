#!/usr/bin/env python3
"""ARK Telegram Bot"""
import os, sys, time, json, threading, datetime
from pathlib import Path

import requests
from requests.exceptions import ReadTimeout, RequestException

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    print("no token")
    sys.exit(1)

BASE = f"https://api.telegram.org/bot{TOKEN}"
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "10"))
STATE_PATH = Path(os.environ.get("STATE_PATH", "/tmp/ark_bot_state.json"))

RPC_LIST = [
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
]
RV = "0x8366a748E02F730911Cb5AB4fd049d2E1e0414b7"
SEL = "0xc3f909d4"
DASHBOARD = os.environ.get("DASHBOARD_URL", "https://aquamarine-kitten-94e7d3.netlify.app")


def load_subs():
    try:
        return set(json.loads(STATE_PATH.read_text()))
    except Exception:
        return set()


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


def read_config():
    last = None
    for rpc in RPC_LIST:
        try:
            p = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                 "params": [{"to": RV, "data": SEL}, "latest"]}
            r = requests.post(rpc, json=p, timeout=20)
            raw = r.json()["result"][2:]
            w = [raw[i:i + 64] for i in range(0, len(raw), 64)]
            v = [int(x, 16) for x in w[:6]]
            return {"minRatio": v[4] / 1e4, "maxRatio": v[5] / 1e4,
                    "totalVestingAmount": str(v[1]), "totalClaimAmount": str(v[2]),
                    "globalMaxClaimAllowanceLimit": str(v[3])}
        except Exception as e:
            last = e
    raise last or RuntimeError("all rpc failed")


def f(x):
    return f"{x:.4f}"


def now_text(rec):
    if not rec:
        return "currently unavailable"
    return ("ARK Consensus Coefficient\n"
            f"min: {f(rec['minRatio'])}\n"
            f"max: {f(rec['maxRatio'])}\n"
            f"dashboard: {DASHBOARD}")


HELP = ("ARK Turbine Monitor\n"
        "Commands:\n"
        "/now - current\n"
        "/test - test push\n"
        "/status - status\n"
        "/stop - unsubscribe\n"
        "/start - subscribe\n")

LAST = {"rec": None, "min": None, "max": None}


def monitor_loop():
    while True:
        try:
            rec = read_config()
            LAST["rec"] = rec
            mn, mx = rec["minRatio"], rec["maxRatio"]
            if LAST["min"] is None:
                LAST["min"], LAST["max"] = mn, mx
            elif mn != LAST["min"] or mx != LAST["max"]:
                msg = "ARK Coefficient Changed!\n"
                if mn != LAST["min"]:
                    msg += f"min: {f(LAST['min'])} -> {f(mn)}\n"
                if mx != LAST["max"]:
                    msg += f"max: {f(LAST['max'])} -> {f(mx)}\n"
                ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                msg += f"time: {ts}"
                print("[change]", LAST["min"], "->", mn, flush=True)
                for cid in list(SUBS):
                    send(cid, msg)
                LAST["min"], LAST["max"] = mn, mx
        except Exception as e:
            print("[monitor err]", e, flush=True)
        time.sleep(POLL_INTERVAL)


def main():
    call("deleteWebhook", {"drop_pending_updates": False})
    me = call("getMe")
    print(f"Bot @{me['username']} started", flush=True)
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
