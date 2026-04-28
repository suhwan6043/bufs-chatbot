#!/bin/bash
# 팀원 서버(bufs-server)에서 데이터 증분 동기화.
# 전제: Tailscale 접속, 팀원 Windows에 OpenSSH Server 설치, 공개키 등록 완료.
# 민감 파일(사용자 DB / 암호화키 / 로그)은 제외.
#
# 사용법:
#   SERVER_USER=<팀원계정> SERVER_PATH=<서버측 data 경로> ./scripts/sync_from_server.sh
# 예:
#   SERVER_USER=suhwan SERVER_PATH="/c/Users/suhwan/bufs-chatbot/data" \
#     ./scripts/sync_from_server.sh

set -euo pipefail

SERVER_USER="${SERVER_USER:?SERVER_USER 환경변수 필요}"
SERVER_HOST="${SERVER_HOST:-bufs-server}"
SERVER_PATH="${SERVER_PATH:?SERVER_PATH 환경변수 필요 (예: /c/Users/suhwan/bufs-chatbot/data)}"
LOCAL_DATA="$(cd "$(dirname "$0")/.." && pwd)/data"

# 제외 패턴 — 민감 / 환경별 파일
EXCLUDES=(
  --exclude "users.db*"
  --exclude ".transcript_enc.key"
  --exclude "logs/*"
  --exclude "chromadb_backup_*"
  --exclude ".DS_Store"
)

echo "[sync] $SERVER_USER@$SERVER_HOST:$SERVER_PATH/ → $LOCAL_DATA/"

# dry-run 먼저 — 무엇이 변경될지 확인
echo "[dry-run]"
rsync -avn "${EXCLUDES[@]}" \
  "$SERVER_USER@$SERVER_HOST:$SERVER_PATH/" \
  "$LOCAL_DATA/" | tail -20

read -rp "실행할까요? [y/N] " yn
[[ "$yn" =~ ^[Yy]$ ]] || { echo "중단"; exit 0; }

# 실제 동기 (--delete는 의도적으로 생략 — 서버에 없는 로컬 파일은 유지)
rsync -av --progress "${EXCLUDES[@]}" \
  "$SERVER_USER@$SERVER_HOST:$SERVER_PATH/" \
  "$LOCAL_DATA/"

echo "[sync] 완료. 지문 확인:"
python3 "$(cd "$(dirname "$0")/.." && pwd)/scripts/data_fingerprint.py" | head -30
