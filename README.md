# Srius Mine

Mining bot untuk **SRTsirius (Sirius Network)** — akun auto-generate tanpa email/password, direct-hit JWE API, Turnstile auto-solve, dan token yang **tidak bisa hangus**.

```
┌──────────────┐   ┌───────────┐   ┌─────────────────┐
│  Turnstile   │──▶│  Register │──▶│  account.json   │
│ (Playwright  │   │ (JWE POST)│   │  + backup.json  │
│   + WARP)    │   └───────────┘   └─────────────────┘
└──────────────┘         │                  │
                         ▼                  ▼
┌──────────────┐   ┌───────────┐   ┌─────────────────┐
│  Task claim  │◀──│API client │◀──│ accessToken     │
│  5 task      │   │ (JWE env) │   │ (Bearer)        │
└──────────────┘   └───────────┘   └─────────────────┘
┌──────────────┐   ┌───────────┐   ┌─────────────────┐
│ Mining loop  │──▶│ensure_token──▶│ Telegram notif  │
│ 5 menit      │   │(proaktif) │   │ (max 3/hari)    │
└──────────────┘   └───────────┘   └─────────────────┘
        │
        ▼
  cron */5 menit → watchdog.sh (restart kalau mati ATAU hang)
```

## Kenapa repo ini ada

Dua akun pertama mati permanen. Bukan karena expired — karena **refresh token sekali-pakai**. Sekali kepakai, token lama terbakar (`AUTH_REFRESH_REUSED`) dan akun hilang selamanya.

Repo ini adalah jawaban atas pertanyaan: *bisa gak akun ini biar gak hangus?*

## Tiga lapis pengaman

| # | Lapis | Cara kerja |
|---|---|---|
| 1 | **Refresh proaktif** | Access token expired tiap 15 menit. Bot refresh **5 detik sebelum expired** — jadi tidak pernah kena 401. |
| 2 | **`flock`** | Cuma satu proses boleh refresh. Proses kedua baca token yang sudah baru, tidak refresh lagi. Ini yang cegah `AUTH_REFRESH_REUSED`. |
| 3 | **`/auth/login`** | Endpoint dead-code di APK yang tidak dipakai UI. Kirim `identityId` + `installationId`, dapat token baru — **akun hidup lagi tanpa register ulang, tanpa Turnstile.** |

Lapis ketiga sudah terbukti: `dc7ygf` yang refresh token-nya sudah terbakar berhasil dihidupkan kembali, mining RUNNING, 25 SST utuh.

Kalau lapis 1 dan 2 gagal, lapis 3 masih bisa. Yang tidak bisa diselamatkan cuma kalau `identityId` + `installationId` hilang — makanya keduanya disimpan di `.env` (gitignored) dan di-backup ke Telegram sekali sehari.

## Yang membuat ini jalan

Beberapa temuan yang tidak bisa ditebak dari luar:

**Transport split.** GET pakai header `X-Api-Envelope`, POST pakai raw body dengan `Content-Type: application/jose`. Kalau tertukar, server balas `INVALID_ENVELOPE`. Ini bug utama yang bikin debugging lama.

**Task claim tanpa buka website.** Cukup `POST /tasks/{taskId}/participations` dengan body `null`. Server langsung kasih reward — verifikasi murni trust-based. 5 task × 5 SST = 25 SST.

**Turnstile butuh origin match.** Sitekey domain-bound ke `api.siriussrt.com/turnstile-test/`. IP VPS polos kena `AUTH_HUMAN_VERIFICATION_FAILED`, jadi butuh WARP proxy. Token harus di-register dari session yang sama.

**Refresh butuh `Idempotency-Key`.** Tanpa itu, 400.

## Isi repo

| File | Fungsi |
|---|---|
| `srius_mine.py` | Bot utama — mining loop, task claim, token management, notif |
| `Srius_Mine_Bot.md` | Dokumentasi lengkap 15 bagian |
| `Srius_Mine_API.md` | API reference 10 endpoint + contoh Python |
| `run.sh` | Wrapper PM2 (load `.env` → jalankan python) |
| `status.sh` | Cek status read-only |
| `watchdog.sh` | Restart kalau bot mati **atau** hang (log stale > 15 menit) |
| `.gitignore` | `.env`, `account.json`, `*.pem`, `*.log` |

## Setup

Butuh `.env`:

```bash
SRT_TG_TOKEN=<token bot telegram>
SRT_TG_CHAT=<chat id>
SRT_INVITER=<your-inviter-code>
SRT_IDENTITYID=<64 hex chars>
SRT_INSTALLATIONID=<uuid4>
```

`identityId` + `installationId` wajib — itu kunci `/auth/login`. Tanpa keduanya, akun mati permanen.

Jalankan:

```bash
./run.sh              # foreground
pm2 start ./run.sh --name srius-mine --time
pm2 save
```

Watchdog cron:

```bash
*/5 * * * * <repo-dir>/watchdog.sh
```

## Notifikasi

Dibatasi **3/hari** — dikirim saat mining start lagi, ada error, atau ringkasan harian 07:00 WIB. Plus backup kredensial harian (force, tidak kena rate limit).

```
SRTsirius mining ✅

🤖 User: dc7ygf
✅ Status: RUNNING
📊 Rate: 0.4411 $SST/h
📈 My $STT: 25.0000

Waktu: 25 Oct 2026 01:06 WIB
```

## Status

RUNNING via PM2 + systemd startup + cron watchdog. Mining 24 cycle per 60 menit, rate 0.0069 SST/menit.

---

Dokumentasi lengkap ada di [`Srius_Mine_Bot.md`](Srius_Mine_Bot.md) dan [`Srius_Mine_API.md`](Srius_Mine_API.md).
