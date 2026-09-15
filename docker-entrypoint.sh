#!/bin/bash
# Single-container entrypoint: uvicorn (FastAPI) + Node QQ bot.
# Bot is restarted if it exits; if uvicorn dies, the container exits.
set -euo pipefail

APP_PID=""
BOT_PID=""

cleanup() {
  echo "[entrypoint] shutting down…"
  if [[ -n "${BOT_PID}" ]] && kill -0 "${BOT_PID}" 2>/dev/null; then
    kill "${BOT_PID}" 2>/dev/null || true
  fi
  if [[ -n "${APP_PID}" ]] && kill -0 "${APP_PID}" 2>/dev/null; then
    kill "${APP_PID}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM

echo "[entrypoint] starting uvicorn on 0.0.0.0:8080"
uvicorn app.main:app --host 0.0.0.0 --port 8080 &
APP_PID=$!

# Give the API a moment before the bot starts calling it
sleep 2

start_bot() {
  echo "[entrypoint] starting QQ bot (notify :8091, API ${APP_API_BASE:-http://127.0.0.1:8080})"
  node /app/bot/dist/index.js &
  BOT_PID=$!
}

start_bot

while true; do
  if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "[entrypoint] uvicorn exited; shutting down"
    if [[ -n "${BOT_PID}" ]]; then
      kill "${BOT_PID}" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "${BOT_PID}" 2>/dev/null; then
    echo "[entrypoint] bot exited; restarting in 5s…"
    sleep 5
    start_bot
  fi
  sleep 2
done
