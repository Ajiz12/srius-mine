#!/usr/bin/env bash
# Srius mine bot — persistent wrapper (PM2)
set -e
cd "$(dirname "$0")"
set -a; [ -f .env ] && . ./.env; set +a
exec python3 -u srius_mine.py
