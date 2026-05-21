"""4 핫패스 골든 캡처 스크립트.

목적:
  4 대표 쿼리를 N회 반복 호출하여 응답을 raw 형태로 캡처.
  안정성·flaky 조건 확인 후 별도 단계에서 골든 JSON 굳히기.

캡처 대상:
  1. KO direct_answer (졸업학점 알려줘) — path=direct 안정성 검증
  2. KO LLM generate  (졸업요건 중 전공과 교양 기준 알려줘) — source_urls 채워지는지
  3. EN generate      (How can I apply for early graduation?) — 영어 출력 + 검수
  4. 도메인 밖        (마라탕 맛집 알려줘) — 환각 방지

path 추론 규칙:
  - token 이벤트 0건 + done 1건만 → direct
  - token 이벤트 ≥1건 + done 1건 → stream
  - clear 이벤트 수도 메타에 기록 (LLM thinking 우회 동작 모니터링)

사용:
  python scripts/capture_golden.py --base-url http://localhost:8000 \
      --runs 3 --output tests/golden/_raw/

  python scripts/capture_golden.py --query "사용자 정의 쿼리" --label custom
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx


# ── 4 핫패스 정의 ──────────────────────────────────────────────────────

@dataclass
class GoldenPath:
    """캡처 대상 1개를 정의."""
    label: str
    query: str
    expected_path: str          # "direct" or "stream"
    expected_intent_any: list[str]  # 분류 결과가 이 중 하나여야 함 (정보용)
    description: str

PATHS: list[GoldenPath] = [
    GoldenPath(
        label="ko_direct_answer",
        query="졸업학점 알려줘",
        expected_path="direct",
        expected_intent_any=["GRADUATION_REQ"],
        description="KO direct_answer 우회 — PR #25 rerank gate 통과 후 direct 송출",
    ),
    GoldenPath(
        label="ko_llm_generate",
        query="휴학신청은 어떻게 해요?",
        expected_path="stream",
        expected_intent_any=["LEAVE_OF_ABSENCE", "GENERAL"],
        description="KO LLM generate — procedural 질문, LLM 경유 검증",
    ),
    # Path #3 (EN) 보류: app.log에 실 EN 트래픽 0건 → 현재 동작이 올바른 동작
    # 인지 미검증. 별도 EN UX 검증 후 추가 (KO 3 path만 안전망에 포함).
    GoldenPath(
        label="ko_domain_out",
        query="마라탕 맛집 알려줘",
        expected_path="?",     # direct·stream·cached 중 어느 거든 받음 (환각 방지가 본질)
        expected_intent_any=["GENERAL", "FACILITY"],
        description="도메인 밖 — 환각 방지 검증, 학사 fact 토큰 0회",
    ),
]


# ── SSE 클라이언트 ─────────────────────────────────────────────────────

@dataclass
class CaptureResult:
    label: str
    query: str
    run: int
    timestamp: str
    # SSE 이벤트 카운터
    token_events: int
    clear_events: int
    done_events: int
    error_events: int
    # 추론된 path
    inferred_path: str  # "direct" / "stream" / "unknown"
    # done payload (전체 보존)
    done_payload: Optional[dict] = None
    # 누적 토큰 (stream 경로에서 검증용)
    streamed_answer_chars: int = 0
    # 메타
    total_duration_ms: int = 0
    error: Optional[str] = None


def _parse_sse_line(line: str) -> tuple[str, str]:
    """단일 SSE 줄을 (field, value)로 파싱. 빈 줄·주석 무시."""
    if not line or line.startswith(":"):
        return ("", "")
    if ":" in line:
        field, _, value = line.partition(":")
        return (field.strip(), value.strip())
    return ("", "")


def capture_one(base_url: str, query: str, label: str, run: int) -> CaptureResult:
    """SSE 1회 호출 + 이벤트 분류."""
    session_id = f"golden-{label}-{uuid.uuid4().hex[:8]}"
    url = f"{base_url.rstrip('/')}/api/chat/stream"
    params = {"session_id": session_id, "question": query}

    result = CaptureResult(
        label=label,
        query=query,
        run=run,
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        token_events=0,
        clear_events=0,
        done_events=0,
        error_events=0,
        inferred_path="unknown",
    )

    t0 = time.monotonic()
    streamed = []

    try:
        with httpx.Client(timeout=120.0) as client:
            with client.stream("GET", url, params=params) as response:
                response.raise_for_status()
                current_event: Optional[str] = None
                current_data_lines: list[str] = []

                def _flush_event():
                    nonlocal current_event, current_data_lines
                    if not current_event:
                        current_event = None
                        current_data_lines = []
                        return
                    data_str = "\n".join(current_data_lines)
                    if current_event == "token":
                        result.token_events += 1
                        try:
                            tok = json.loads(data_str).get("token", "")
                            streamed.append(tok)
                        except json.JSONDecodeError:
                            pass
                    elif current_event == "clear":
                        result.clear_events += 1
                        streamed.clear()
                    elif current_event == "done":
                        result.done_events += 1
                        try:
                            result.done_payload = json.loads(data_str)
                        except json.JSONDecodeError as e:
                            result.error = f"done payload parse failed: {e}"
                    elif current_event == "error":
                        result.error_events += 1
                        result.error = data_str[:200]
                    current_event = None
                    current_data_lines = []

                for raw_line in response.iter_lines():
                    if not raw_line:
                        _flush_event()
                        continue
                    field, value = _parse_sse_line(raw_line)
                    if field == "event":
                        current_event = value
                    elif field == "data":
                        current_data_lines.append(value)
    except httpx.HTTPError as e:
        result.error = f"HTTP error: {e}"
    except Exception as e:
        result.error = f"unexpected: {e}"

    result.total_duration_ms = int((time.monotonic() - t0) * 1000)
    result.streamed_answer_chars = sum(len(t) for t in streamed)

    # path 식별 — payload.path 필드 우선, 없으면 token 이벤트로 추론 (구버전 호환)
    payload_path = (result.done_payload or {}).get("path")
    if result.error_events > 0:
        result.inferred_path = "error"
    elif payload_path:
        result.inferred_path = payload_path
    elif result.token_events == 0 and result.done_events == 1:
        result.inferred_path = "direct_or_cached"  # 구버전 백엔드: 구분 불가
    elif result.token_events >= 1 and result.done_events == 1:
        result.inferred_path = "stream"
    else:
        result.inferred_path = "unknown"

    return result


# ── 진단 시그너처 추출 ──────────────────────────────────────────────────

# 환각 방지 검증용 정규식 (Path #4)
_FACT_TOKEN_PATTERNS = [
    re.compile(r"\d+\s*학점"),          # "120학점", "30 학점"
    re.compile(r"\d{3}[-\s]?\d{3,4}"),   # "051-509-5182", "0515095182"
    re.compile(r"20\d{2}\s*학번"),       # "2023학번"
    re.compile(r"\d+\s*학년"),           # "1학년"
    re.compile(r"\d+/\d+"),              # "2/9", "7/6"
]

_KO_CHAR = re.compile(r"[가-힣]")
_EN_CHAR = re.compile(r"[A-Za-z]")


def extract_signatures(result: CaptureResult) -> dict:
    """캡처 1회 결과에서 검증 시그너처 추출."""
    payload = result.done_payload or {}
    answer = (payload.get("answer") or "").strip()
    sigs = {
        "inferred_path": result.inferred_path,
        "intent": payload.get("intent", ""),
        "answer_chars": len(answer),
        "source_urls_count": len(payload.get("source_urls") or []),
        "results_count": len(payload.get("results") or []),
        "duration_ms": payload.get("duration_ms", result.total_duration_ms),
        "has_timing": "timing" in payload,  # stream 경로 보조 시그너처
        "token_events": result.token_events,
        "clear_events": result.clear_events,
        # 환각 검증
        "fact_token_hits": sum(1 for p in _FACT_TOKEN_PATTERNS for _ in p.finditer(answer)),
        # 언어
        "ko_chars": len(_KO_CHAR.findall(answer)),
        "en_chars": len(_EN_CHAR.findall(answer)),
    }
    if sigs["ko_chars"] + sigs["en_chars"] > 0:
        sigs["en_ratio"] = round(
            sigs["en_chars"] / (sigs["ko_chars"] + sigs["en_chars"]), 3
        )
    else:
        sigs["en_ratio"] = 0.0
    return sigs


# ── 메인 ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--runs", type=int, default=3, help="path별 반복 캡처 횟수")
    parser.add_argument("--output", default="tests/golden/_raw/",
                        help="raw 캡처 저장 디렉터리")
    parser.add_argument("--query", default=None, help="단일 쿼리만 캡처 (디버깅용)")
    parser.add_argument("--label", default="custom", help="--query 와 함께 사용")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 백엔드 헬스체크
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{args.base_url}/api/health")
            r.raise_for_status()
            print(f"[health] {r.json()}")
    except Exception as e:
        print(f"[ERROR] 백엔드 헬스체크 실패: {e}", file=sys.stderr)
        sys.exit(1)

    # 캡처 대상 결정
    if args.query:
        targets = [GoldenPath(
            label=args.label,
            query=args.query,
            expected_path="?",
            expected_intent_any=[],
            description="(단일 쿼리)",
        )]
    else:
        targets = PATHS

    summary = []
    for path in targets:
        print(f"\n=== [{path.label}] {path.description}")
        print(f"    Query: {path.query}")
        runs_data = []
        for run in range(1, args.runs + 1):
            print(f"    Run {run}/{args.runs} ... ", end="", flush=True)
            result = capture_one(args.base_url, path.query, path.label, run)
            sigs = extract_signatures(result)
            print(f"path={result.inferred_path} intent={sigs['intent']} "
                  f"tokens={result.token_events} dur={sigs['duration_ms']}ms "
                  f"fact_hits={sigs['fact_token_hits']} en_ratio={sigs.get('en_ratio',0)}")
            runs_data.append({
                "run": run,
                "signatures": sigs,
                "done_payload": result.done_payload,
                "error": result.error,
            })

        # 안정성 진단
        paths = [r["signatures"]["inferred_path"] for r in runs_data]
        intents = [r["signatures"]["intent"] for r in runs_data]
        stable_path = len(set(paths)) == 1
        stable_intent = len(set(intents)) == 1

        out_file = out_dir / f"{path.label}.json"
        out_file.write_text(json.dumps({
            "label": path.label,
            "query": path.query,
            "description": path.description,
            "expected_path": path.expected_path,
            "expected_intent_any": path.expected_intent_any,
            "runs": runs_data,
            "stability": {
                "path_stable": stable_path,
                "path_values": paths,
                "intent_stable": stable_intent,
                "intent_values": intents,
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    → {out_file}")
        summary.append({
            "label": path.label,
            "stable_path": stable_path,
            "stable_intent": stable_intent,
            "paths": paths,
            "intents": intents,
        })

    print("\n" + "=" * 72)
    print("STABILITY SUMMARY")
    print("=" * 72)
    for s in summary:
        flag = "✅" if s["stable_path"] and s["stable_intent"] else "⚠️"
        print(f"{flag} {s['label']:<22s} path={s['paths']}  intent={s['intents']}")


if __name__ == "__main__":
    main()
