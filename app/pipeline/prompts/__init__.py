"""KO SYSTEM_PROMPT 버전 선택.

KO_PROMPT_VERSION 환경변수로 즉시 롤백 가능 (rebuild 불요, container restart만):
  - v0: 기존 4a/4b/4c (commit e9f41f2 시점) — 안전 baseline
  - v1: 전체 재설계 (EN STRICT PRECISION 한국어 이식, 거부 정책 단독 섹션, 예시 4개)

회귀 발생 시 `.env`에 KO_PROMPT_VERSION=v0 추가 + `docker compose restart backend`로 30초 내 복귀.
"""
import os

from .system_ko_v0 import SYSTEM_PROMPT_KO_V0
from .system_ko_v1 import SYSTEM_PROMPT_KO_V1

_VERSION = os.getenv("KO_PROMPT_VERSION", "v0").lower().strip()

if _VERSION == "v0":
    SYSTEM_PROMPT = SYSTEM_PROMPT_KO_V0
elif _VERSION == "v1":
    SYSTEM_PROMPT = SYSTEM_PROMPT_KO_V1
else:
    raise ValueError(
        f"unknown KO_PROMPT_VERSION={_VERSION!r}, expected 'v0' or 'v1'"
    )

__all__ = ["SYSTEM_PROMPT", "SYSTEM_PROMPT_KO_V0", "SYSTEM_PROMPT_KO_V1"]
