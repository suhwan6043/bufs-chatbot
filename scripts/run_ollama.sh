#!/bin/bash
# ──────────────────────────────────────────────────────
# Ollama 동시 처리 슬롯 설정 + 서버 시작
# 사용법: bash scripts/run_ollama.sh
# ──────────────────────────────────────────────────────
#
# 왜 이 값들인가 (RTX 4070 12GB 기준):
#   OLLAMA_NUM_PARALLEL=2       — 한 모델당 동시 추론 2슬롯 (KV 캐시 ~2GB 소비)
#   OLLAMA_MAX_LOADED_MODELS=2  — qwen3:8b + gemma3:4b 동시 상주 (모델 스왑 지연 회피)
#   OLLAMA_KEEP_ALIVE=5m        — 5분 유휴 시 언로드 (idle VRAM 회수)
#
# VRAM 예산 (12GB):
#   qwen3:8b(Q4)   ~5.0GB
#   gemma3:4b(Q4)  ~2.5GB
#   KV(2슬롯)      ~2.0GB
#   오버헤드       ~1.0GB
#   ─────────────────
#   합계           ~10.5GB / 12GB  (마진 ~1.5GB)
#
# 더 큰 GPU(예: 24GB+)에서는 NUM_PARALLEL=4, MAX_LOADED_MODELS=3 등으로 상향 가능.
# 셸 환경변수가 이미 설정돼 있으면 그 값을 우선 사용.

export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-2}"
export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-2}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-5m}"

echo "[run_ollama] OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL"
echo "[run_ollama] OLLAMA_MAX_LOADED_MODELS=$OLLAMA_MAX_LOADED_MODELS"
echo "[run_ollama] OLLAMA_KEEP_ALIVE=$OLLAMA_KEEP_ALIVE"
echo "[run_ollama] $(date '+%Y-%m-%d %H:%M:%S') — 'ollama serve' 시작..."

exec ollama serve
