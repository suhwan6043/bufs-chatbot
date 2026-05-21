"""핫패스 골든 회귀 테스트.

전제: 백엔드 + Ollama가 떠 있어야 함. CI 환경에선 별도 셋업 필요.

`tests/golden/{label}.golden.json`을 기준으로 시그너처 비교.
시그너처 형식은 reports/llm_audit/STRUCTURE.md + tests/golden/README.md 참조.

사용:
  pytest tests/test_golden_paths.py -v
  pytest tests/test_golden_paths.py::test_ko_direct_answer -v
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

# capture_golden 의 시그너처 추출 로직 재사용
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.capture_golden import capture_one, extract_signatures  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
BASE_URL = os.getenv("GOLDEN_BASE_URL", "http://localhost:8000")


# ── 헬퍼 ──────────────────────────────────────────────────────────────

def _load_golden(label: str) -> dict:
    path = GOLDEN_DIR / f"{label}.golden.json"
    if not path.exists():
        pytest.fail(f"골든 파일 없음: {path}. scripts/capture_golden.py 먼저 실행 후 굳히기.")
    return json.loads(path.read_text(encoding="utf-8"))


def _capture(label: str, query: str) -> dict:
    """현재 응답 캡처 → 시그너처 추출."""
    result = capture_one(BASE_URL, query, label, run=1)
    if result.error:
        pytest.fail(f"캡처 실패 [{label}]: {result.error}")
    return {
        "signatures": extract_signatures(result),
        "answer": (result.done_payload or {}).get("answer", ""),
    }


def _backend_alive() -> bool:
    import httpx
    try:
        with httpx.Client(timeout=3.0) as c:
            r = c.get(f"{BASE_URL}/api/health")
            r.raise_for_status()
            return r.json().get("pipeline_ready", False)
    except Exception:
        return False


# 전체 테스트가 백엔드에 의존 — 미실행 시 skip
pytestmark = pytest.mark.skipif(
    not _backend_alive(), reason=f"백엔드 미실행: {BASE_URL}"
)


# ── 골든 시그너처 비교 헬퍼 ─────────────────────────────────────────────

def _assert_path_in(actual: str, expected_any: list[str], label: str):
    assert actual in expected_any, (
        f"[{label}] path 회귀: actual={actual!r}, expected one of {expected_any}"
    )


def _assert_intent_in(actual: str, expected_any: list[str], label: str):
    assert actual in expected_any, (
        f"[{label}] intent 회귀: actual={actual!r}, expected one of {expected_any}"
    )


def _assert_contains_all(text: str, phrases: list[str], label: str):
    missing = [p for p in phrases if p not in text]
    assert not missing, (
        f"[{label}] 핵심 키워드 누락: {missing}\n응답: {text[:300]}..."
    )


def _assert_not_contains_any(text: str, patterns: list[str], label: str):
    """학사 fact 토큰 정규식 0회 확인 (Path #4 환각 방지)."""
    hits = []
    for p in patterns:
        matches = re.findall(p, text)
        if matches:
            hits.append((p, matches))
    assert not hits, (
        f"[{label}] 환각 시그너처 검출: {hits}\n응답: {text[:300]}"
    )


# ── 핫패스별 테스트 ────────────────────────────────────────────────────

def test_ko_direct_answer():
    """Path #1: KO direct_answer 우회 검증."""
    golden = _load_golden("ko_direct_answer")
    actual = _capture("ko_direct_answer", golden["query"])
    sigs = actual["signatures"]

    _assert_path_in(sigs["inferred_path"], golden["expected_path_any"], "ko_direct_answer")
    _assert_intent_in(sigs["intent"], golden["expected_intent_any"], "ko_direct_answer")
    _assert_contains_all(actual["answer"], golden["must_contain"], "ko_direct_answer")
    assert sigs["fact_token_hits"] >= golden["min_fact_token_hits"], (
        f"[ko_direct_answer] fact_hits={sigs['fact_token_hits']} < "
        f"{golden['min_fact_token_hits']} — 회귀 의심: {actual['answer']!r}"
    )


def test_ko_llm_generate():
    """Path #2: KO LLM generate (또는 cached) 검증."""
    golden = _load_golden("ko_llm_generate")
    actual = _capture("ko_llm_generate", golden["query"])
    sigs = actual["signatures"]

    _assert_path_in(sigs["inferred_path"], golden["expected_path_any"], "ko_llm_generate")
    _assert_intent_in(sigs["intent"], golden["expected_intent_any"], "ko_llm_generate")
    assert sigs["answer_chars"] >= golden["min_answer_chars"], (
        f"[ko_llm_generate] 답변 너무 짧음 ({sigs['answer_chars']}자 < "
        f"{golden['min_answer_chars']}) — 거절 회귀 의심"
    )
    found_any = any(kw in actual["answer"] for kw in golden["must_contain_any"])
    assert found_any, (
        f"[ko_llm_generate] 핵심 키워드 모두 누락 ({golden['must_contain_any']})\n"
        f"응답: {actual['answer'][:300]}"
    )


def test_ko_domain_out():
    """Path #4: 도메인 밖 → 환각 방지 검증."""
    golden = _load_golden("ko_domain_out")
    actual = _capture("ko_domain_out", golden["query"])
    sigs = actual["signatures"]

    _assert_intent_in(sigs["intent"], golden["expected_intent_any"], "ko_domain_out")
    assert sigs["inferred_path"] != golden["must_not_be_path"], (
        f"[ko_domain_out] path={sigs['inferred_path']} = 환각 회귀 (graph 오매칭):\n"
        f"응답: {actual['answer'][:200]}"
    )
    assert sigs["fact_token_hits"] <= golden["max_fact_token_hits"], (
        f"[ko_domain_out] 환각 fact 토큰 {sigs['fact_token_hits']}건 검출 "
        f"(허용 {golden['max_fact_token_hits']}):\n응답: {actual['answer']!r}"
    )
    assert sigs["answer_chars"] <= golden["max_answer_chars"], (
        f"[ko_domain_out] 응답 너무 김 ({sigs['answer_chars']}자 > "
        f"{golden['max_answer_chars']}) — 장황한 환각 의심"
    )
