#!/usr/bin/env python3
"""
SRTsirius (Sirius Network) mining bot.

Akun auto-generate (private key style, tanpa email/password), mining otomatis
via direct JWE API (RSA-OAEP-256 + A256GCM), Turnstile di-solve via Playwright
+ WARP proxy, token refresh otomatis, notif Telegram.

Persistent: PM2 + cron watchdog.
"""
import base64
import json
import os
import random
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# ── config ────────────────────────────────────────────────────────────────
BASE = "https://api.siriussrt.com"
PUB = load_pem_public_key(open("/tmp/srt_pub.pem").read().encode())
HEADER = {"alg": "RSA-OAEP-256", "enc": "A256GCM", "typ": "newera-request+jwe", "kid": "kex"}
UA = "SRTsirius/1.0 (Linux; Android 14; Pixel 8) okhttp/4.12.0"
SITEKEY = "0x4AAAAAAE4Yb4FqKM_qx4B1"
WARP_PROXY = "socks5://127.0.0.1:40000"
TS_PROFILE = os.path.expanduser("~/srt-ts-warp")

STATE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNT_FILE = os.path.join(STATE_DIR, "account.json")
LOG_FILE = os.path.join(STATE_DIR, "bot.log")
TOKEN_FILE = os.path.join(STATE_DIR, "token.txt")

TG_TOKEN = os.environ.get("SRT_TG_TOKEN", "")
TG_CHAT = os.environ.get("SRT_TG_CHAT", "<your-telegram-chat-id>")
INVITER_CODE = os.environ.get("SRT_INVITER", "<your-inviter-code>")
TG_MAX_PER_DAY = int(os.environ.get("SRT_TG_MAX_PER_DAY", "3"))
DAILY_HOUR = int(os.environ.get("SRT_TG_DAILY_HOUR", "7"))  # ringkasan harian jam 07:00 WIB

WIB = timezone(timedelta(hours=7))


def log(msg):
    line = f"[{datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


_tg_sent = {}  # tanggal WIB -> jumlah pesan terkirim


def tg(msg, force=False):
    """Kirim notif Telegram, dibatasi max TG_MAX_PER_DAY per hari (WIB)."""
    if not TG_TOKEN:
        return False
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    n = _tg_sent.get(today, 0)
    if not force and n >= TG_MAX_PER_DAY:
        log(f"[tg] dibatasi (sudah {n}/{TG_MAX_PER_DAY} hari ini) — pesan dilewati")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data),
            timeout=15,
        )
        _tg_sent[today] = n + 1
        return True
    except Exception as e:
        log(f"[tg] {e}")
        return False


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def get_balance(acc):
    """Total balance SST (task + mining) dari /overview/users/me/standard."""
    s, r = request("GET", "/overview/users/me/standard", token=acc["accessToken"])
    if s == 200:
        try:
            return float(r["body"]["data"]["points"]["totalBalance"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


def fmt_backup(acc):
    """Pesan backup kredensial — dikirim ke Telegram biar ada di luar VPS."""
    return (f"🔐 SRTsirius BACKUP KREDENSIAL\n\n"
            f"🤖 User: {acc.get('userCode')}\n"
            f"🆔 identityId:\n`{acc.get('identityId')}`\n"
            f"📱 installationId:\n`{acc.get('installationId')}`\n"
            f"🎨 avatarCode: {acc.get('avatarCode')}\n\n"
            f"Simpan baik-baik. Kalau VPS mati dan token expired,\n"
            f"dua ID di atas = satu-satunya cara hidupkan akun\n"
            f"(POST /auth/login). Tanpa ini, akun hilang permanen.\n\n"
            f"Waktu: {datetime.now(WIB).strftime('%d %b %Y %H:%M')} WIB")


def fmt_report(user_code, mining_status, rate_per_min, total_sst=None, acc=None):
    """Template notif SRTsirius."""
    if total_sst is None and acc is not None:
        total_sst = get_balance(acc)
    try:
        rate_h = float(rate_per_min) * 60
    except (TypeError, ValueError):
        rate_h = 0.0
    try:
        total = float(total_sst)
    except (TypeError, ValueError):
        total = 0.0
    return (f"SRTsirius mining ✅\n\n"
            f"🤖 User: {user_code}\n"
            f"✅ Status: {mining_status}\n"
            f"📊 Rate: {rate_h:.4f} $SST/h\n"
            f"📈 My $STT: {total:.4f}\n\n"
            f"Waktu: {datetime.now(WIB).strftime('%d %b %Y %H:%M')} WIB")


HB = b64u(json.dumps(HEADER, separators=(",", ":")).encode())


# ── JWE envelope ──────────────────────────────────────────────────────────
def build_env(method, path, query, body_obj):
    rk = secrets.token_bytes(32)
    iv = secrets.token_bytes(12)
    req_id = b64u(secrets.token_bytes(24))
    enc = PUB.encrypt(
        rk,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    inner = {
        "v": 1,
        "requestId": req_id,
        "method": method,
        "path": path,
        "query": query or "",
        "replyKey": b64u(rk),
        "body": body_obj,
    }
    ct_tag = AESGCM(rk).encrypt(
        iv, json.dumps(inner, separators=(",", ":"), ensure_ascii=False).encode(), HB.encode()
    )
    return f"{HB}.{b64u(enc)}.{b64u(iv)}.{b64u(ct_tag[:-16])}.{b64u(ct_tag[-16:])}", rk


def decrypt_resp(raw, rk):
    parts = raw.strip().split(".")
    if len(parts) != 5:
        return {"_raw": raw[:500]}
    try:
        ct = b64u_d(parts[3])
        tag = b64u_d(parts[4])
        pt = AESGCM(rk).decrypt(b64u_d(parts[2]), ct + tag, parts[0].encode())
        return json.loads(pt)
    except Exception as e:
        return {"_decrypt_error": str(e), "_raw": raw[:500]}


def request(method, path, query="", body=None, token=None, timeout=30, extra_headers=None):
    env, rk = build_env(method, path, query, body)
    url = BASE + path + (("?" + query) if query else "")
    hdrs = {"User-Agent": UA, "Accept": "application/jose"}
    if method in ("GET", "DELETE"):
        hdrs["X-Api-Envelope"] = env
        data = None
    else:
        hdrs["Content-Type"] = "application/jose"
        data = env.encode()
    if token:
        hdrs["Authorization"] = "Bearer " + token
    if extra_headers:
        hdrs.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, decrypt_resp(r.read().decode(errors="ignore"), rk)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            return e.code, decrypt_resp(raw, rk)
        except Exception:
            return e.code, {"_raw": raw[:800]}
    except Exception as e:
        return 0, {"_error": str(e)}


# ── turnstile ─────────────────────────────────────────────────────────────
TS_HTML = """<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';
script-src 'unsafe-inline' https://challenges.cloudflare.com;
style-src 'unsafe-inline'; frame-src https://challenges.cloudflare.com;
connect-src https://challenges.cloudflare.com; img-src data: https://challenges.cloudflare.com;">
<style>html,body{margin:0;background:#fff;}#challenge{display:flex;justify-content:center;width:100%;}</style>
</head><body><div id="challenge"></div><script>
function deliver(value) {
  if (value && value.length > 20) { document.title = "TOKEN:" + value; }
  else { document.title = "STATUS:" + value; }
}
function ready() {
  turnstile.render('#challenge', {
    sitekey:'__SITEKEY__', action:'register', language:'en', theme:'light', size:'normal',
    retry:'auto', 'retry-interval':5000, 'refresh-expired':'auto', 'refresh-timeout':'auto',
    callback:deliver,
    'error-callback':function(code){ deliver('ERRCODE:' + code); return true; },
    'expired-callback':function(){deliver('expired');},
    'timeout-callback':function(){deliver('timeout');}
  });
}
</script><script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=ready&render=explicit"
async defer onerror="deliver('failed')"></script></body></html>""".replace("__SITEKEY__", SITEKEY)


def solve_turnstile(timeout=150):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            TS_PROFILE,
            headless=True,
            channel="chrome",
            proxy={"server": WARP_PROXY},
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
            ignore_default_args=["--disable-extensions"],
            viewport={"width": 500, "height": 300},
            user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/UQ1A.240205.004; wv) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                       "Chrome/131.0.6778.260 Mobile Safari/537.36",
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.route("**/turnstile-test/**",
                   lambda r: r.fulfill(status=200, content_type="text/html", body=TS_HTML))
        page.goto("https://api.siriussrt.com/turnstile-test/")

        deadline = time.time() + timeout
        token = None
        clicks = 0
        while time.time() < deadline:
            t = page.title()
            if t.startswith("TOKEN:"):
                token = t[6:]
                break
            if clicks < 10 and int(time.time()) % 10 == 0:
                try:
                    for f in page.frames:
                        if "challenges.cloudflare.com" in (f.url or ""):
                            el = f.query_selector("input[type=checkbox]") or f.query_selector("body")
                            if el:
                                el.click(timeout=3000)
                                clicks += 1
                except Exception:
                    pass
            time.sleep(1.5)
        browser.close()
        return token


# ── account ───────────────────────────────────────────────────────────────
TOKEN_LOCK = os.path.join(STATE_DIR, ".token.lock")
TOKEN_BACKUP = os.path.join(STATE_DIR, "account.backup.json")


def save_identity_env(acc):
    """Simpan identity fields ke .env (gitignored) — ini kunci /auth/login.
    Dipanggil setelah register atau login, karena nilai sessionId/epoch bisa berubah."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    keys = {"SRT_IDENTITYID": acc.get("identityId"),
            "SRT_INSTALLATIONID": acc.get("installationId"),
            "SRT_SESSIONID": acc.get("sessionId"),
            "SRT_AVATARCODE": acc.get("avatarCode"),
            "SRT_SESSIONEPOCH": acc.get("sessionEpoch")}
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path) as f:
                lines = [l.rstrip("\n") for l in f if l.strip()]
        for k, v in keys.items():
            if v is None:
                continue
            v = str(v)
            lines = [l for l in lines if not l.startswith(k + "=")]
            lines.append(f"{k}={v}")
        with open(env_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        log(f"[env] identity disimpan ke .env ({len(keys)} field)")
    except Exception as e:
        log(f"[env] gagal simpan identity: {e}")


def load_account():
    """Baca account.json + identity fields dari .env (SRT_IDENTITYID dll)."""
    if os.path.exists(ACCOUNT_FILE):
        with open(ACCOUNT_FILE) as f:
            acc = json.load(f)
        # identity fields dipindah ke .env biar gak ikut ke-push
        for env_key, field in [("SRT_IDENTITYID", "identityId"),
                               ("SRT_INSTALLATIONID", "installationId"),
                               ("SRT_SESSIONID", "sessionId"),
                               ("SRT_AVATARCODE", "avatarCode"),
                               ("SRT_SESSIONEPOCH", "sessionEpoch")]:
            if not acc.get(field) and os.environ.get(env_key):
                acc[field] = os.environ[env_key]
        return acc
    return None


def save_account(acc):
    # backup token lama dulu — ini penyelamat kalau ada yang salah
    if os.path.exists(ACCOUNT_FILE):
        try:
            with open(ACCOUNT_FILE) as f:
                old = f.read()
            if old.strip():
                with open(TOKEN_BACKUP, "w") as f:
                    f.write(old)
        except Exception:
            pass
    # identity fields TIDAK ditulis ke account.json — sumbernya .env
    # biar file ini aman kalau repo ke-push
    persist = {k: v for k, v in acc.items()
               if k not in ("identityId", "installationId", "sessionId",
                            "avatarCode", "sessionEpoch")}
    tmp = ACCOUNT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(persist, f, indent=1)
    os.replace(tmp, ACCOUNT_FILE)  # atomic — gak ada setengah-tulis


def register():
    token = solve_turnstile()
    if not token:
        raise RuntimeError("turnstile gagal")
    payload = {
        "identityId": secrets.token_bytes(32).hex(),
        "installationId": str(uuid.uuid4()),
        "platform": "android",
        "avatarCode": "avatar-%03d" % random.randint(1, 55),
    }
    env, rk = build_env("POST", "/auth/register", "", payload)
    req = urllib.request.Request(
        BASE + "/auth/register", data=env.encode(),
        headers={
            "Content-Type": "application/jose",
            "Accept": "application/jose",
            "Idempotency-Key": str(uuid.uuid4()),
            "X-Turnstile-Token": token,
            "User-Agent": UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        res = decrypt_resp(r.read().decode(errors="ignore"), rk)
    d = res["body"]["data"]
    acc = {
        "userCode": d["userCode"],
        "accessToken": d["accessToken"],
        "refreshToken": d["refreshToken"],
        "sessionId": d["sessionId"],
        "avatarCode": d["avatarCode"],
        "identityId": payload["identityId"],
        "installationId": payload["installationId"],
        "accessExp": time.time() + int(d.get("expiresIn", 900)),
        "refreshExp": time.time() + int(d.get("refreshExpiresIn", 2592000)),
        "sessionEpoch": d.get("sessionEpoch", 0),
    }
    save_account(acc)
    save_identity_env(acc)  # identity ke .env, bukan account.json
    log(f"[register] userCode={acc['userCode']}")
    # langsung bind inviter + klaim task
    s, r = request("POST", "/users/me/inviter", body={"inviterCode": INVITER_CODE}, token=acc["accessToken"])
    log(f"[inviter] {s}")
    done, gained = run_all_tasks(acc)
    res = start_mining(acc)
    tg(f"🆕 SRTsirius — akun baru dibuat\n"
       f"User: {acc['userCode']}\n"
       f"Task: {done} diklaim (+{gained} SST)\n"
       f"Mining: {'round ' + str(res['roundId']) if res else 'gagal'}\n"
       f"Waktu: {datetime.now(WIB).strftime('%d %b %Y %H:%M')} WIB")
    return acc


def login(acc):
    """Login ulang pakai identityId + installationId. Menyelamatkan akun
    yang refresh token-nya terbakar (AUTH_REFRESH_REUSED)."""
    iid = acc.get("identityId")
    inst = acc.get("installationId")
    if not iid or not inst:
        log("[login] identityId/installationId tidak ada — gak bisa rescue")
        return None
    s, r = request("POST", "/auth/login",
                   body={"identityId": iid, "installationId": inst, "platform": "android"},
                   extra_headers={"Idempotency-Key": str(uuid.uuid4())})
    if s == 200 and r.get("body", {}).get("data"):
        d = r["body"]["data"]
        now = time.time()
        acc["accessToken"] = d["accessToken"]
        acc["refreshToken"] = d.get("refreshToken", acc.get("refreshToken"))
        acc["accessExp"] = now + int(d.get("expiresIn", 900))
        acc["refreshExp"] = now + int(d.get("refreshExpiresIn", 2592000))
        acc["sessionId"] = d.get("sessionId", acc.get("sessionId"))
        acc["sessionEpoch"] = d.get("sessionEpoch", acc.get("sessionEpoch"))
        save_account(acc)
        save_identity_env(acc)  # sessionId/epoch bisa berubah setelah login
        log(f"[login] RESCUE BERHASIL — {acc.get('userCode')} hidup lagi, "
            f"access {d.get('expiresIn')}s, refresh {d.get('refreshExpiresIn')}s")
        return acc
    log(f"[login] gagal ({s}) — {json.dumps(r.get('body', {}))[:200]}")
    return None


def refresh(acc):
    import uuid
    # flock: cuma SATU proses boleh refresh. Kalau 2 proses refresh barengan
    # pakai refresh token yang sama, yang kedua kena AUTH_REFRESH_REUSED
    # dan refresh token-nya terbakar permanen.
    lock_fd = open(TOKEN_LOCK, "w")
    try:
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        # ada proses lain sedang refresh — baca ulang file, mungkin token sudah baru
        lock_fd.close()
        fresh = load_account()
        if fresh and fresh.get("accessToken") != acc.get("accessToken"):
            log("[refresh] proses lain sudah refresh — pakai token baru")
            return fresh
        log("[refresh] proses lain sedang refresh — tunggu")
        time.sleep(3)
        return load_account() or acc

    try:
        # di dalam lock: baca ulang, mungkin sudah di-refresh proses lain
        fresh = load_account()
        if fresh and fresh.get("accessExp", 0) > time.time() - 60:
            acc = fresh
        s, r = request("POST", "/auth/refresh", body={"refreshToken": acc["refreshToken"]},
                       extra_headers={"Idempotency-Key": str(uuid.uuid4())})
        if s == 200 and r.get("body", {}).get("data"):
            d = r["body"]["data"]
            now = time.time()
            acc["accessToken"] = d["accessToken"]
            acc["refreshToken"] = d.get("refreshToken", acc["refreshToken"])
            acc["accessExp"] = now + int(d.get("expiresIn", 900))
            acc["refreshExp"] = now + int(d.get("refreshExpiresIn", 2592000))
            acc["sessionId"] = d.get("sessionId", acc.get("sessionId"))
            acc["sessionEpoch"] = d.get("sessionEpoch", acc.get("sessionEpoch"))
            save_account(acc)
            save_identity_env(acc)  # sessionEpoch naik tiap refresh
            log(f"[refresh] token diperbarui — access {d.get('expiresIn')}s, "
                f"refresh {d.get('refreshExpiresIn')}s, epoch {d.get('sessionEpoch')}")
            return acc
        err = r.get("body", {}).get("errorCode", "")
        if err == "AUTH_REFRESH_REUSED":
            # refresh token terbakar — coba pulihkan dari backup, lalu login
            log("[refresh] AUTH_REFRESH_REUSED — coba backup token")
            if os.path.exists(TOKEN_BACKUP):
                try:
                    with open(TOKEN_BACKUP) as f:
                        bak = json.load(f)
                    if bak.get("refreshToken") != acc.get("refreshToken"):
                        s2, r2 = request("POST", "/auth/refresh",
                                         body={"refreshToken": bak["refreshToken"]},
                                         extra_headers={"Idempotency-Key": str(uuid.uuid4())})
                        if s2 == 200 and r2.get("body", {}).get("data"):
                            d = r2["body"]["data"]
                            now = time.time()
                            bak["accessToken"] = d["accessToken"]
                            bak["refreshToken"] = d.get("refreshToken", bak["refreshToken"])
                            bak["accessExp"] = now + int(d.get("expiresIn", 900))
                            bak["refreshExp"] = now + int(d.get("refreshExpiresIn", 2592000))
                            save_account(bak)
                            log("[refresh] PULIH dari backup token")
                            return bak
                except Exception as e:
                    log(f"[refresh] backup gagal: {e}")
            # backup gagal — rescue via /auth/login (identityId + installationId)
            log("[refresh] backup gagal — coba rescue via /auth/login")
            rescued = login(acc)
            if rescued:
                return rescued
            log("[refresh] refresh token terbakar permanen — akun mati")
            return None
        log(f"[refresh] gagal ({s}) — {json.dumps(r.get('body', {}))[:200]}")
        return None
    finally:
        try:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()


def ensure_token(acc):
    """Refresh proaktif sebelum access token expired. Jangan pernah logout."""
    now = time.time()
    # refresh token expired = akun mati total, gak bisa dipulihin tanpa register ulang
    rexp = acc.get("refreshExp")
    if rexp and now >= rexp - 86400:  # < 24 jam lagi expired
        log(f"[token] REFRESH TOKEN HAMPIR EXPIRED ({rexp - now:.0f}s) — refresh sekarang")
        new = refresh(acc)
        if new:
            acc = new
            rexp = acc.get("refreshExp")
        if rexp and now >= rexp:
            log("[token] refresh token sudah expired — akun mati")
            return acc
    exp = acc.get("accessExp")
    if exp is None:
        # belum pernah refresh — coba sekali buat dapat expiry
        return refresh(acc) or acc
    if now >= exp - 120:  # refresh 2 menit sebelum expired
        log(f"[token] access expired dalam {exp - now:.0f}s — refresh proaktif")
        new = refresh(acc)
        if new:
            return new
        log("[token] refresh gagal — pakai token lama")
    return acc


def bind_inviter(acc):
    s, r = request("POST", "/users/me/inviter", body={"inviterCode": INVITER_CODE}, token=acc["accessToken"])
    log(f"[inviter] {s} {json.dumps(r.get('body', {}))[:200]}")
    return s == 200


def start_mining(acc):
    s, r = request("POST", "/mining/tasks/start", body={}, token=acc["accessToken"])
    if s == 200:
        d = r["body"]["data"]
        log(f"[mining] round {d['roundId']} #{d['roundNumber']} — "
            f"{d['totalSmallCycles']} cycles, settle tiap {d['settlementIntervalMinutes']}m")
        return d
    log(f"[mining] start gagal {s} {json.dumps(r.get('body', {}))[:200]}")
    return None


def status(acc):
    s, r = request("GET", "/mining/tasks/status", token=acc["accessToken"])
    return s, r


def summary(acc):
    s, r = request("GET", "/tasks/summary", token=acc["accessToken"])
    return s, r


def overview(acc):
    s, r = request("GET", "/overview/users/me/standard", token=acc["accessToken"])
    return s, r


def task_list(acc):
    s, r = request("GET", "/tasks", token=acc["accessToken"])
    if s != 200:
        return []
    return r["body"].get("data", {}).get("tasks", [])


def do_task(acc, task_id):
    s, r = request("POST", f"/tasks/{task_id}/participations", body=None, token=acc["accessToken"])
    return s, r


def run_all_tasks(acc):
    """Klaim semua task yang belum participated. Return (jumlah_baru, total_sst)."""
    tasks = task_list(acc)
    if not tasks:
        return 0, 0.0
    done = 0
    total = 0.0
    for t in tasks:
        if t.get("participated"):
            continue
        s, r = do_task(acc, t["taskId"])
        if s == 200:
            d = r["body"].get("data", {})
            amt = float(d.get("amount", 0))
            total += amt
            done += 1
            log(f"[task] {t['taskCode']} -> +{amt} SST ({d.get('rewardStatus')})")
        else:
            log(f"[task] {t['taskCode']} gagal {s} {json.dumps(r.get('body', {}))[:150]}")
        time.sleep(1.5)
    return done, total


# ── main loop ─────────────────────────────────────────────────────────────
def main():
    log("=== SRTsirius mining bot start ===")
    acc = load_account()
    if not acc:
        log("[*] belum ada akun — register")
        acc = register()
        bind_inviter(acc)
    else:
        log(f"[*] akun {acc['userCode']}")

    # klaim task yang belum
    done, gained = run_all_tasks(acc)
    if done:
        log(f"[task] {done} task baru diklaim, +{gained} SST")

    # pastikan mining jalan
    acc = ensure_token(acc)
    s, r = status(acc)
    if s != 200:
        log(f"[!] status {s} — refresh token")
        acc = refresh(acc) or acc
        s, r = status(acc)
    if s != 200:
        # masih gagal — rescue via /auth/login (identityId + installationId)
        log(f"[!] status tetap gagal {s} — coba rescue via /auth/login")
        acc = login(acc) or acc
        s, r = status(acc)
    if s != 200:
        log(f"[!] status tetap gagal {s} — {json.dumps(r)[:200]}")
        tg(fmt_report(acc["userCode"], f"API_ERROR_{s}", "0", "0"))
        return

    d = r["body"].get("data", {})
    log(f"[status] {d.get('miningStatus')} rate={d.get('ratePerMinute')}/m "
        f"total={d.get('totalMiningReward')}")
    if d.get("miningStatus") != "RUNNING":
        log("[!] mining tidak jalan — start")
        if not bind_inviter(acc):
            log("[!] inviter bind gagal")
        start_mining(acc)

    tg(fmt_report(acc["userCode"], d.get("miningStatus"), d.get("ratePerMinute"),
                  acc=acc))

    last_check = time.time()
    last_daily = None
    err_streak = 0
    while True:
        try:
            now = time.time()
            now_wib = datetime.now(WIB)

            # ringkasan harian (sekali per hari, jam DAILY_HOUR WIB)
            today = now_wib.strftime("%Y-%m-%d")
            if (last_daily != today and now_wib.hour >= DAILY_HOUR
                    and now_wib.hour < DAILY_HOUR + 2):
                last_daily = today
                s1, r1 = status(acc)
                s2, r2 = summary(acc)
                s3, r3 = overview(acc)
                st = r1["body"].get("data", {}) if s1 == 200 else {}
                ov = r3["body"].get("data", {}) if s3 == 200 else {}
                bal = ov.get("points", {}).get("totalBalance")
                tg(fmt_report(acc["userCode"], st.get("miningStatus"),
                              st.get("ratePerMinute"), total_sst=bal))
                # backup kredensial ke Telegram — force, jangan kena rate limit
                tg(fmt_backup(acc), force=True)

            # cek status tiap 5 menit
            if now - last_check >= 300:
                last_check = now
                # refresh proaktif SEBELUM token expired — biar gak pernah 401
                acc = ensure_token(acc)
                s, r = status(acc)
                if s in (401, 403):
                    log("[!] token expired — refresh")
                    acc = refresh(acc) or acc
                    s, r = status(acc)
                if s in (401, 403):
                    # refresh gagal — rescue via /auth/login
                    log("[!] masih 401 — rescue via /auth/login")
                    acc = login(acc) or acc
                    s, r = status(acc)
                if s != 200:
                    err_streak += 1
                    log(f"[!] status {s} (streak {err_streak}) — {json.dumps(r)[:200]}")
                    if err_streak >= 3:
                        tg(fmt_report(acc["userCode"], f"API_ERROR_{s}", "0", "0"))
                    if err_streak >= 6:
                        log("[!] streak 6 — refresh token paksa")
                        acc = refresh(acc) or acc
                        if s in (401, 403):
                            acc = login(acc) or acc
                        err_streak = 0
                    continue
                err_streak = 0
                d = r["body"].get("data", {})
                log(f"[status] {d.get('miningStatus')} rate={d.get('ratePerMinute')} "
                    f"total={d.get('totalMiningReward')}")
                if d.get("miningStatus") != "RUNNING":
                    log("[!] mining berhenti — restart")
                    if not bind_inviter(acc):
                        log("[!] inviter bind gagal")
                    res = start_mining(acc)
                    if res:
                        tg(fmt_report(acc["userCode"], "RUNNING",
                                      d.get("ratePerMinute"), acc=acc))
                    else:
                        tg(fmt_report(acc["userCode"], "RESTART_FAILED",
                                      d.get("ratePerMinute"), acc=acc))

            time.sleep(30)
        except KeyboardInterrupt:
            log("=== stop ===")
            break
        except Exception as e:
            log(f"[!] error: {e}")
            tg(fmt_report(acc.get("userCode", "?"), "ERROR", "0", "0"))
            time.sleep(60)


if __name__ == "__main__":
    import urllib.parse
    main()
