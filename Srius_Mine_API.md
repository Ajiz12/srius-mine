# SRTsirius — API Reference & Akun

**Base URL:** `https://api.siriussrt.com`

Semua request wajib pakai **JWE envelope** (RSA-OAEP-256 + A256GCM). Tanpa itu server balas `100009 COMMON_ENCRYPTION_REQUIRED`.

---

## 1. Format Envelope

Envelope 5-part dipisah titik:

```
HB . encryptedKey . iv . ciphertext . tag
```

| Part | Isi | Ukuran |
|---|---|---|
| `HB` | Header JSON, base64url tanpa padding | ~90 char |
| `encryptedKey` | Content key 32 byte, RSA-OAEP-256 | 512 byte |
| `iv` | Nonce AES-GCM | 12 byte |
| `ciphertext` | Inner payload terenkripsi | variabel |
| `tag` | Auth tag GCM | 16 byte |

### Header

```json
{"alg":"RSA-OAEP-256","enc":"A256GCM","typ":"newera-request+jwe","kid":"kex"}
```

### Inner Payload

```json
{
  "v": 1,
  "requestId": "<24 random bytes, base64url>",
  "method": "POST",
  "path": "/auth/register",
  "query": "",
  "replyKey": "<content key, base64url>",
  "body": { ... }
}
```

`replyKey` harus sama dengan content key yang dipakai enkripsi — server pakai ini buat balas dalam bentuk terenkripsi juga.

---

## 2. Transport Split (PENTING)

Ditemukan di `sn.smali`. Kalau salah, server balas `INVALID_ENVELOPE`.

| Method | Cara kirim |
|---|---|
| `GET` / `DELETE` | Header `X-Api-Envelope: <envelope>` |
| `POST` / `PUT` / `PATCH` | **Raw body** = envelope, `Content-Type: application/jose` |

---

## 3. Header Wajib

```
User-Agent: SRTsirius/1.0 (Linux; Android 14; Pixel 8) okhttp/4.12.0
Accept: application/jose
```

Per-endpoint:

| Endpoint | Header tambahan |
|---|---|
| Semua kecuali auth | `Authorization: Bearer <accessToken>` |
| `POST /auth/register` | `X-Turnstile-Token: <token>` |
| `POST /auth/refresh` | `Idempotency-Key: <uuid4>` |

---

## 4. Endpoint Reference

### 4.1 `GET /auth/registration-config`

Ambil config Turnstile. Gak butuh auth.

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "turnstile": {
      "sitekey": "0x4AAAAAAE4Yb4FqKM_qx4B1",
      "action": "register",
      "size": "compact"
    }
  }
}
```

---

### 4.2 `POST /auth/register`

Bikin akun baru. Gak butuh auth, tapi butuh Turnstile token asli.

**Header:** `X-Turnstile-Token: <token dari Cloudflare>`, `Idempotency-Key: <uuid4>`

**Body:**

```json
{
  "identityId": "<64 hex chars>",
  "installationId": "<uuid4>",
  "platform": "android",
  "avatarCode": "avatar-044"
}
```

| Field | Aturan |
|---|---|
| `identityId` | 32 random bytes → hex. **Ini identitas permanen akun** |
| `installationId` | UUID4 |
| `platform` | Huruf kecil `"android"` (dari `l10.c`) |
| `avatarCode` | `avatar-001` sampai `avatar-055` |

**Response 200:**

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "userCode": "kPg8Hb",
    "accessToken": "<43 char opaque>",
    "refreshToken": "<43 char opaque>",
    "expiresIn": 900,
    "refreshExpiresIn": 2592000,
    "sessionEpoch": 1
  }
}
```

**Error:**

| Code | Arti |
|---|---|
| `100009` | Belum pakai JWE envelope |
| `100010` | Header kurang |
| `AUTH_HUMAN_VERIFICATION_FAILED` | Turnstile token ditolak — IP datacenter kena flag |

---

### 4.3 `POST /auth/refresh`

Perbarui token. **Wajib** header `Idempotency-Key`.

**Body:**

```json
{"refreshToken": "<refreshToken lama>"}
```

**Response 200:**

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "accessToken": "<baru>",
    "refreshToken": "<baru>",
    "expiresIn": 900,
    "refreshExpiresIn": 2592000,
    "sessionEpoch": 1
  }
}
```

**Error:**

| Code | Arti |
|---|---|
| `400` / `COMMON_INVALID_REQUEST` | `Idempotency-Key` header hilang |
| `200005` / `AUTH_REFRESH_REUSED` | Refresh token sudah pernah dipakai — **mati permanen** |
| `AUTH_TOKEN_INVALID` | Access token invalid |

> **Refresh token ROTATING / sekali-pakai.** Setiap refresh menghasilkan refresh token baru. Yang lama langsung mati. Kalau dua proses refresh barengan pakai token sama, yang kedua kena `AUTH_REFRESH_REUSED` dan akun hilang permanen. Wajib pakai `flock`.

---

### 4.4 `POST /auth/login`

Login ulang pakai `identityId` + `installationId`. **Ini jalan keluar darurat** — bisa memulihkan akun yang refresh token-nya terbakar (`AUTH_REFRESH_REUSED`) tanpa perlu register ulang.

Endpoint ini ada di APK (`uc3.java`) tapi tidak dipakai UI — dead code. Ternyata server tetap menerimanya.

**Header:** `Idempotency-Key: <uuid4>`

**Body** (dari serializer `vw.java` — 3 field):

```json
{
  "identityId": "<identityId>",
  "installationId": "<installationId>",
  "platform": "android"
}
```

**Response 200:** sama seperti `/auth/register` — `userCode`, `accessToken`, `refreshToken`, `expiresIn`, `refreshExpiresIn`, `sessionEpoch`.

> **WAJIB simpan `identityId` + `installationId` selamanya.** Tanpa keduanya, akun yang token-nya mati tidak bisa dipulihin. Bot menyimpannya di `account.json` dan mem-backup-nya.

---

### 4.5 `POST /users/me/inviter`

Bind kode inviter. Wajib sebelum mining bisa start.

**Body:**

```json
{"inviterCode": "<your-inviter-code>"}
```

> Field = `inviterCode`, bukan `code`. Salah field → mining balas `MINING_INVITER_REQUIRED`.

**Response 200:**

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "inviterCode": "<your-inviter-code>",
    "invitationBound": true
  }
}
```

---

### 4.6 `GET /tasks`

Daftar semua task.

**Response 200:**

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "tasks": [
      {
        "taskId": 1,
        "taskCode": "FOLLOW_TELEGRAM_2026",
        "amount": 5,
        "participated": false
      }
    ]
  }
}
```

---

### 4.7 `POST /tasks/{taskId}/participations`

Klaim reward task. **Body `null`** — bukan `{}`.

```
POST /tasks/1/participations
Content-Type: application/jose
Body: <envelope dengan body: null>
```

**Response 200:**

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "amount": 5,
    "rewardStatus": "PENDING"
  }
}
```

> Server trust-based. Gak perlu buka website atau diam 1 menit. Langsung dapat reward.

---

### 4.8 `GET /tasks/summary`

Progress task.

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "completedTaskCount": 5,
    "totalTaskCount": 5,
    "totalPointsEarned": "25.0000"
  }
}
```

---

### 4.9 `GET /mining/tasks/status`

Status mining.

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "miningStatus": "RUNNING",
    "startedAt": "2026-10-24T18:24:48.593336",
    "ratePerMinute": "0.0069",
    "projectedRewardPerCycle": "0.4166",
    "settlementIntervalSeconds": 3600,
    "largeCycleEndsAt": "2026-10-25T18:24:48.593336",
    "totalMiningReward": "0.0000"
  }
}
```

| Field | Arti |
|---|---|
| `miningStatus` | `RUNNING` / `NOT_STARTED` |
| `ratePerMinute` | SST per menit |
| `projectedRewardPerCycle` | SST per small cycle |
| `settlementIntervalSeconds` | 3600 = 1 jam |
| `largeCycleEndsAt` | Waktu settlement berikutnya |
| `totalMiningReward` | Total SST dari mining (bukan task) |

---

### 4.10 `POST /mining/tasks/start`

Start mining round.

**Body:** `{}`

**Response 200:**

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "roundId": 1488739,
    "roundNumber": 1,
    "totalSmallCycles": 24,
    "settlementIntervalMinutes": 60
  }
}
```

**Error:** `MINING_INVITER_REQUIRED` — bind inviter dulu.

---

### 4.11 `GET /overview/users/me/standard`

Balance + referral + statistik.

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "points": {
      "totalBalance": "25.0000",
      "miningBalance": "0.0000",
      "taskBalance": "25.0000"
    }
  }
}
```

> `points.totalBalance` = task + mining. Ini yang dipakai buat field `My $STT` di notif.

---

## 5. Response Envelope

Semua response juga terenkripsi JWE 5-part. Decrypt pakai content key (`replyKey`) yang sama:

```
plaintext = AESGCM(contentKey).decrypt(iv, ciphertext + tag, HB)
```

Struktur setelah decrypt:

```json
{
  "code": 200,
  "msg": "success",
  "data": { ... }
}
```

| Code | Arti |
|---|---|
| `200` | Sukses |
| `100009` | `COMMON_ENCRYPTION_REQUIRED` |
| `100010` | Header envelope kurang |
| `200005` | Auth error (lihat `errorCode`) |

---

## 6. Contoh Implementasi Python

```python
import base64, json, secrets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PUB = serialization.load_pem_public_key(open("srt_pub.pem","rb").read())
HEADER = {"alg":"RSA-OAEP-256","enc":"A256GCM","typ":"newera-request+jwe","kid":"kex"}
BASE = "https://api.siriussrt.com"
UA = "SRTsirius/1.0 (Linux; Android 14; Pixel 8) okhttp/4.12.0"

def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def build_env(method, path, query, body_obj):
    HB = b64u(json.dumps(HEADER, separators=(",",":")).encode())
    rk = secrets.token_bytes(32)
    iv = secrets.token_bytes(12)
    inner = {"v":1,"requestId":b64u(secrets.token_bytes(24)),
             "method":method,"path":path,"query":query or "",
             "replyKey":b64u(rk),"body":body_obj}
    enc = PUB.encrypt(rk, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(), label=None))
    ct_tag = AESGCM(rk).encrypt(
        iv, json.dumps(inner,separators=(",",":"),ensure_ascii=False).encode(), HB.encode())
    return f"{HB}.{b64u(enc)}.{b64u(iv)}.{b64u(ct_tag[:-16])}.{b64u(ct_tag[-16:])}", rk

def decrypt(raw, rk):
    p = raw.strip().split(".")
    pad = lambda s: s + "="*(-len(s)%4)
    ct = base64.urlsafe_b64decode(pad(p[3]))
    tag = base64.urlsafe_b64decode(pad(p[4]))
    iv = base64.urlsafe_b64decode(pad(p[2]))
    return json.loads(AESGCM(rk).decrypt(iv, ct+tag, p[0].encode()))
```

---

## 7. Akun Aktif

| Field | Value |
|---|---|
| userCode | `dc7ygf` |
| identityId | `<identityId>` |
| installationId | `<installationId>` |
| sessionId | `77305844-4ccb-4771-aa60-e1fc2a224440` |
| avatarCode | `avatar-050` |
| sessionEpoch | `1` |
| accessToken | tersimpan di `account.json` (expired 900s, auto-refresh) |
| refreshToken | tersimpan di `account.json` (expired 30 hari, auto-refresh) |

### Akun Cadangan

| Field | Value |
|---|---|
| userCode | `kPg8Hb` |
| identityId | `<identityId>` |
| installationId | `<installationId>` |
| sessionId | `8db02b4d-8964-4207-98a6-76bf716df84b` |
| avatarCode | `avatar-044` |

> Simpan kedua akun ini. Kalau `dc7ygf` mati permanen, `kPg8Hb` bisa langsung dipakai lewat `/auth/login`.

### Status Live (25 Okt 2026, 02:00 WIB)

| Metrik | Value |
|---|---|
| Mining | `RUNNING` |
| Round | 1485214, cycle #1 dari 24 |
| Rate | 0.0069 SST/menit = 0.4166 SST/cycle = 0.4140 SST/jam |
| Settlement | 2026-10-25T18:24:48 UTC |
| Task | 5/5 cleared, 25.0000 SST |
| Balance total | 25.0000 SST |
| Mining reward | 0.0000 SST (baru masuk setelah settlement pertama) |

### Akun Mati (tidak bisa dipulihin)

| userCode | identityId | Sebab |
|---|---|---|
| `J9MEmQ` | `<identityId>` | Refresh token terbakar saat testing. `installationId` asli tidak tersimpan, jadi `/auth/login` tidak bisa rescue |

### Akun yang Sudah Direscue

| userCode | identityId | Status |
|---|---|---|
| `dc7ygf` | `<identityId>` | Hidup kembali via `/auth/login`, mining RUNNING, balance 25 SST |

> **`/auth/login` = jalan keluar darurat.** Ditemukan di `uc3.java` (dead code, tidak dipakai UI). Body: `{"identityId", "installationId", "platform"}` (dari serializer `vw.java`). Kalau refresh token terbakar, akun bisa dipulihin tanpa register ulang — selama `identityId` + `installationId` tersimpan. **Simpan keduanya selamanya.**

---

## 8. Turnstile (kalau mau register akun baru)

Register butuh Cloudflare Turnstile token asli. **Gak bisa pure requests** dari IP datacenter.

| Parameter | Value |
|---|---|
| Sitekey | `0x4AAAAAAE4Yb4FqKM_qx4B1` |
| Action | `register` |
| Origin | `https://api.siriussrt.com/turnstile-test/` |
| Proxy | WARP `socks5://127.0.0.1:40000` |

Alur: Playwright + Chrome persistent context → intercept `**/turnstile-test/**` → serve HTML lokal → tunggu token → register same session.

Script jadi: `<turnstile-dir>/solve6.py`
