#!/bin/bash
# ──────────────────────────────────────────────────────
# CAMCHAT 백엔드 자동 재시작 스크립트
# 크래시 감지 시 5초 대기 후 자동 재시작 (최대 10회 연속)
# 사용법: bash scripts/run_backend.sh
# ──────────────────────────────────────────────────────

MAX_CRASHES=10
CRASH_COUNT=0
COOLDOWN=5

cd "$(dirname "$0")/.." || exit 1

echo "[run_backend] CAMCHAT 백엔드 시작 (자동 재시작 활성)"
echo "[run_backend] 최대 연속 크래시: $MAX_CRASHES"

while [ $CRASH_COUNT -lt $MAX_CRASHES ]; do
    echo "[run_backend] $(date '+%Y-%m-%d %H:%M:%S') — uvicorn 시작 (시도 $((CRASH_COUNT + 1))/$MAX_CRASHES)"

    python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "[run_backend] 정상 종료 (exit 0). 재시작하지 않습니다."
        break
    fi

    CRASH_COUNT=$((CRASH_COUNT + 1))
    echo "[run_backend] 비정상 종료 (exit $EXIT_CODE). ${COOLDOWN}초 후 재시작... ($CRASH_COUNT/$MAX_CRASHES)"
    sleep $COOLDOWN
done

if [ $CRASH_COUNT -ge $MAX_CRASHES ]; then
    echo "[run_backend] 최대 연속 크래시 횟수 도달 ($MAX_CRASHES). 중단합니다."
    exit 1
fi
