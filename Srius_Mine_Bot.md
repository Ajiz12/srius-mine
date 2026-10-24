# Srius Mine — Dokumentasi Lengkap

Bot mining otomatis untuk **SRTsirius (Sirius Network)**. Akun auto-generate tanpa email/password, mining via direct JWE API, Turnstile auto-solve, token refresh otomatis, notif Telegram.

**Lokasi:** `<repo-dir>/`
**Status:** RUNNING via PM2 (`srius-mine`) + cron watchdog

---

## 1. Arsitektur Singkat

```
┌──────────────────────────────────────────────────────────────┐
│  PM2: srius-mine  (run.sh → srius_mine.py)                         │
│                                                              │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │ Turnstile  │──▶│   Register   │──▶│  account.json    │   │
│  │  (Playwright│   │  (JWE POST)  │   │  + backup.json   │   │
│  │  + WARP)    │   └──────────────┘   └──────────────────┘   │
│  └────────────┘                            │                 │
│  ┌────────────┐   ┌──────────────┐        ▼                 │
│  │ Task claim │◀──│  API client  │◀── accessToken (Bearer)  │
│  │ 5 task     │   │  (JWE env)   │                          │
│  └────────────┘   └──────────────┘                          │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │ Mining     │──▶│ ensure_token │──▶│ Telegram notif   │   │
│  │ loop 5m    │   │ (proaktif)   │   │ (max 3/hari)     │   │
│  └────────────┘   └──────────────┘   └──────────────────┘   │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
  cron */5 menit → watchdog.sh (restart kalau mati/hang)
```

---

## 2. File & Folder

| File | Fungsi |
|---|---|
| `srius_mine.py` | Bot utama (627 baris) |
| `account.json` | Akun aktif: userCode, token, expiry absolut |
| `account.backup.json` | Backup token otomatis (penyelamat kalau token terbakar) |
| `srt_pub.pem` | Public key server buat enkripsi JWE |
| `.env` | `SRT_TG_TOKEN`, `SRT_TG_CHAT`, `SRT_INVITER` |
| `run.sh` | Wrapper PM2 (load `.env` → jalankan python) |
| `status.sh` | Cek status read-only |
| `watchdog.sh` | Restart kalau bot mati/hang |
| `bot.log` | Log aktivitas |
| `.token.lock` | File lock (`flock`) buat cegah double-refresh |

---

## 3. API Client — JWE Direct Hit

Semua request pakai envelope JWE 5-part. **Bukan polling token** seperti sible — ini direct hit seperti `itlg`.

### Format Envelope

```
HB . encryptedKey . iv . ciphertext . tag
```

| Part | Isi |
|---|---|
| `HB` | Header base64url: `{"alg":"RSA-OAEP-256","enc":"A256GCM","typ":"newera-request+jwe","kid":"kex"}` |
| `encryptedKey` | Content key 32 byte, dienkripsi RSA-OAEP-256 pakai public key server |
| `iv` | 12 byte nonce |
| `ciphertext` | Body JSON terenkripsi AES-256-GCM |
| `tag` | 16 byte auth tag |

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

### Transport Split (PENTING — ini bug utama)

Ditemukan di `sn.smali`:

| Method | Cara kirim envelope |
|---|---|
| `GET` / `DELETE` | Header `X-Api-Envelope: <envelope>` |
| `POST` / `PUT` / `PATCH` | **Raw body** = envelope, `Content-Type: application/jose` |

Kalau salah, server balas `INVALID_ENVELOPE`.

### Header Wajib

```
User-Agent: SRTsirius/1.0 (Linux; Android 14; Pixel 8) okhttp/4.12.0
Accept: application/jose
Authorization: Bearer <accessToken>   # kecuali /auth/register & /auth/refresh
Idempotency-Key: <uuid>               # WAJIB di /auth/refresh, kalau tidak 400
```

---

## 4. Endpoints yang Dipakai

| Method | Endpoint | Body | Catatan |
|---|---|---|---|
| GET | `/auth/registration-config` | — | Config Turnstile, sitekey |
| POST | `/auth/register` | `{identityId, installationId, platform, avatarCode}` | Butuh `X-Turnstile-Token` |
| POST | `/auth/refresh` | `{refreshToken}` | Butuh `Idempotency-Key` header |
| POST | `/users/me/inviter` | `{"inviterCode": "<your-inviter-code>"}` | Field = `inviterCode`, bukan `code` |
| GET | `/tasks` | — | Daftar task |
| POST | `/tasks/{taskId}/participations` | `null` | **Body kosong**, langsung dapat reward |
| GET | `/tasks/summary` | — | Progress task |
| GET | `/mining/tasks/status` | — | Status mining |
| POST | `/mining/tasks/start` | `{}` | Start mining round |
| GET | `/overview/users/me/standard` | — | Balance, rate, referral |

### DTO Register (`sx.java` — 4 field)

```
identityId      : 64 hex chars (32 random bytes)
installationId  : UUID
platform        : "android"  (lowercase!)
avatarCode      : "avatar-0XX"
```

---

## 5. Turnstile Solver

Register butuh Cloudflare Turnstile token asli. **Tidak bisa pure requests** — IP datacenter kena flag.

### Kunci Sukses (solve6.py)

1. **WARP proxy** — `socks5://127.0.0.1:40000`. IP VPS polos kena `AUTH_HUMAN_VERIFICATION_FAILED`.
2. **Origin match** — sitekey domain-bound ke `https://api.siriussrt.com/turnstile-test/`. Kalau origin beda → error `110200`.
3. **Route fulfill** — intercept `**/turnstile-test/**`, serve HTML lokal. Jadi gak perlu server asli.
4. **Persistent context** — `launch_persistent_context` + `channel="chrome"` + profile `~/srt-ts-warp`.
5. **Klik checkbox** — poll iframe `challenges.cloudflare.com`, klik `input[type=checkbox]` maksimal 10x.
6. **Register same session** — pakai `browser.request.post` dari session yang sama. Token terpisah ditolak.

### Alur

```python
page.route("**/turnstile-test/**", fulfill dengan TS_HTML)
page.goto("https://api.siriussrt.com/turnstile-test/")
# tunggu document.title jadi "TOKEN:xxx" (len ~837)
# lalu browser.request.post ke /auth/register dengan X-Turnstile-Token
```

---

## 6. Task Claim — Lebih Simpel dari Buka Website

Ternyata **gak perlu buka website atau diam 1 menit**. Cukup:

```
POST /tasks/{taskId}/participations
Body: null
```

Server langsung kasih reward. Verification murni trust-based.

**Hasil:** 5 task × 5 SST = **25 SST** langsung masuk.

| taskId | taskCode | Reward |
|---|---|---|
| 1 | FOLLOW_TELEGRAM_2026 | 5 SST |
| 5 | FOLLOW_TELEGRAM_CHANNEL_2026 | 5 SST |
| 2 | FOLLOW_X_2026 | 5 SST |
| 3 | SUBSCRIBE_YOUTUBE_2026 | 5 SST |
| 4 | JOIN_WHATSAPP_CHANNEL_2026 | 5 SST |

---

## 7. Token Management — Anti Mati

### Fakta Penting

- `accessToken` expired **900 detik** (15 menit)
- `refreshToken` expired **2592000 detik** (30 hari)
- **Refresh token ROTATING / sekali-pakai.** Sekali kepakai, yang lama mati permanen → `AUTH_REFRESH_REUSED`

### 4 Lapis Pengaman

**1. Refresh proaktif (`ensure_token`)**

Dipanggil sebelum setiap API call. Kalau access token akan expired dalam < 2 menit, refresh **sebelum** expired — jadi gak pernah kena 401.

Kalau refresh token akan expired dalam < 24 jam, refresh lebih awal. Tiap refresh menghasilkan refresh token baru dengan masa berlaku 30 hari penuh, jadi rantai gak pernah putus.

**2. `flock` — cegah double refresh**

Kalau 2 proses refresh barengan pakai refresh token yang sama, yang kedua kena `AUTH_REFRESH_REUSED` dan token-nya terbakar permanen.

```python
fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
```

Kalau lock gagal (ada proses lain refresh), baca ulang file dan pakai token yang sudah baru.

**3. Backup token otomatis**

Setiap `save_account()`, token lama disalin ke `account.backup.json` dulu. Kalau kena `AUTH_REFRESH_REUSED`, bot coba refresh pakai token dari backup.

Tulis pakai `os.replace()` (atomic) — gak ada file setengah-tulis kalau proses mati di tengah.

**4. Gak pernah logout**

Bot gak pernah panggil `/auth/logout`. Cuma refresh.

### Verifikasi Nyata

```
[2026-10-25 01:39:24] [token] access expired dalam 5s — refresh proaktif
[2026-10-25 01:39:25] [refresh] token diperbarui — access 900s, refresh 2592000s, epoch 1
```

---

## 8. Notifikasi Telegram

### Format (sesuai permintaan)

```
SRTsirius mining ✅

🤖 User: kPg8Hb
✅ Status: RUNNING
📊 Rate: 0.4140 $SST/h
📈 My $STT: 25.0000

Waktu: 25 Oct 2026 01:39 WIB
```

| Field | Sumber |
|---|---|
| Rate | `ratePerMinute` × 60 |
| My $STT | `/overview/users/me/standard` → `points.totalBalance` (task + mining) |

### Frekuensi — Max 3/hari

Rate limiter in-memory (`_tg_sent`), reset tiap tanggal WIB.

| Event | Status field |
|---|---|
| Bot start/restart | `RUNNING` |
| Ringkasan harian (07:00 WIB) | `RUNNING` |
| Mining restart | `RUNNING` atau `RESTART_FAILED` |
| API error 3x berturut | `API_ERROR_<code>` |
| Script exception | `ERROR` |

Kalau sudah 3 pesan hari itu, sisanya di-log saja (`[tg] dibatasi`), gak dikirim.

**Catatan:** counter in-memory, reset kalau bot restart. Kalau mau persisten lintas restart, simpan ke file JSON.

---

## 9. Mining

- Round ID bertambah tiap start
- **24 small cycles** per round
- Settlement tiap **60 menit**
- Rate: `0.0069 SST/menit` = `0.4166 SST/cycle` = `0.4140 SST/jam`
- Mining jalan server-side — bot cuma perlu start + cek status, gak perlu request terus-terusan

Bot cek status tiap 5 menit. Kalau `miningStatus != RUNNING`, bot bind inviter lalu start mining lagi.

---

## 10. Persistensi

### PM2

```bash
pm2 start ./run.sh --name srius-mine --time
pm2 save
```

### Watchdog (cron tiap 5 menit)

```bash
*/5 * * * * <repo-dir>/watchdog.sh
```

Cek 2 kondisi:
1. PM2 process gak `online` → restart
2. `bot.log` gak update > 15 menit → restart (deteksi hang)

### Auto-start setelah reboot

```bash
pm2 startup systemd
pm2 save
```

---

## 11. Cara Pakai

### Cek status

```bash
cd <repo-dir> && ./status.sh
```

### Lihat log

```bash
tail -f <repo-dir>/bot.log
```

### Restart

```bash
pm2 restart srius-mine
```

### Register akun baru (kalau token mati permanen)

```bash
cd <turnstile-dir> && python3 solve6.py
cp account.json <repo-dir>/account.json
pm2 restart srius-mine
```

Bot akan otomatis: bind inviter → klaim 5 task → start mining.

---

## 12. Error yang Pernah Ditangani

| Error | Sebab | Solusi |
|---|---|---|
| `100009 COMMON_ENCRYPTION_REQUIRED` | Belum pakai JWE envelope | Implementasi JWE 5-part |
| `100010` | Header kurang | Tambah `X-Api-Envelope` / `application/jose` |
| `INVALID_ENVELOPE` | Transport split salah | GET → header, POST → raw body |
| `110200` | Sitekey domain mismatch | Origin `https://api.siriussrt.com/turnstile-test/` |
| `AUTH_HUMAN_VERIFICATION_FAILED` | IP datacenter kena flag | WARP proxy + register same session |
| `MINING_INVITER_REQUIRED` | Belum bind inviter | `POST /users/me/inviter` body `{"inviterCode": "..."}` |
| `400 COMMON_INVALID_REQUEST` (refresh) | `Idempotency-Key` header hilang | Tambah header UUID |
| `AUTH_REFRESH_REUSED` | Refresh token sekali-pakai, dobel pakai | `flock` + backup token |
| `AUTH_TOKEN_INVALID` | Access token expired | Refresh proaktif sebelum expired |

---

## 13. Akun Saat Ini

| Field | Value |
|---|---|
| userCode | `dc7ygf` |
| identityId | `<identityId>` |
| installationId | `<installationId>` |
| sessionId | `77305844-4ccb-4771-aa60-e1fc2a224440` |
| avatarCode | `avatar-050` |
| sessionEpoch | `1` |
| accessExp | 900s (auto-refresh) |
| refreshExp | 30 hari (auto-refresh) |
| Mining | RUNNING, round 1485214, 24 cycles |
| Balance | 25.0000 SST (5 task) |

### Akun Cadangan

| userCode | identityId | installationId |
|---|---|---|
| `kPg8Hb` | `<identityId>` | `<installationId>` |

### Akun Mati (tidak bisa dipulihin)

- `J9MEmQ` — refresh token terbakar saat testing, dan `installationId` aslinya tidak tersimpan jadi `/auth/login` tidak bisa rescue
- `dc7ygf` — **sudah direscue** lewat `/auth/login`, sekarang jadi akun aktif

---

## 14. Auto-Rescue — `/auth/login`

Ini alasan kenapa akun sekarang praktis **tidak bisa hangus**.

Endpoint `/auth/login` ditemukan di `uc3.java` — dead code, tidak dipakai UI, tapi server tetap menerima. Body-nya 3 field (dari serializer `vw.java`):

```json
{"identityId": "...", "installationId": "...", "platform": "android"}
```

Kalau berhasil, server mengembalikan `accessToken` + `refreshToken` baru — **akun hidup lagi tanpa register ulang, tanpa Turnstile**.

Bot memanggilnya otomatis di 4 tempat:

| Lokasi | Kondisi |
|---|---|
| `refresh()` | `AUTH_REFRESH_REUSED` dan backup token gagal |
| `main()` start | Status 401 setelah refresh gagal |
| Loop 5-menit | Masih 401 setelah refresh |
| Loop 5-menit | `err_streak >= 6` dan masih 401 |

**Syarat:** `identityId` + `installationId` harus tersimpan. Bot menyimpannya di `account.json` dan mem-backup-nya tiap save. Jangan pernah hapus file itu.

---

## 15. Catatan Jujur

- **Turnstile wajib browser asli.** Gak bisa pure requests. Butuh Playwright + Chrome + WARP. Kalau WARP mati, register akun baru gagal.
- **Mining reward baru masuk setelah settlement pertama** (~1 jam). Jadi `totalMiningReward` masih `0.0000` di awal — normal, bukan error.
- **Rate limiter notif in-memory.** Reset kalau bot restart.
- **`pm2 startup systemd` exit 1** di sesi sebelumnya, tapi `pm2-ubuntu` sudah enabled dari sible-bot, jadi auto-start sebenarnya sudah aktif.
