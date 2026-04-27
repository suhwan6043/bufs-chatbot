"""시연 — 프로필과 질문을 코드에 직접 작성하고 실행하면 검색 로그 + 답변이 출력.

전제: uvicorn 서버가 8002 포트에 떠 있어야 함 (CHROMA_COLLECTION=bufs_demo).

사용:
  - 변수 편집 후 그대로 실행
        .venv/bin/python scripts/demo_chat.py

  - 또는 인덱스를 인자로 (코드 수정 없이)
        .venv/bin/python scripts/demo_chat.py 2 5     # PROFILES[2] × QUESTIONS[5]

  - 자유 질문도 가능
        .venv/bin/python scripts/demo_chat.py 1 "수강신청 매뉴얼 알려줘"

  - 모든 조합 한 번에 (시연 매트릭스)
        .venv/bin/python scripts/demo_chat.py --all

환경변수:
  DEMO_BASE_URL  백엔드 주소 (기본 http://127.0.0.1:8002)
"""
from __future__ import annotations

import os
import re
import sys
import time
import textwrap
import requests


# 답변에서 검증 경고 블록 제거 — 시연 출력 정리용
# 백엔드가 답변 본문에 추가하는 "*검증 경고:*" ~ 다음 "---" 사이 블록을 통째로 제거
_VALIDATION_WARNING_RE = re.compile(
    r"\n*---\s*\n+\*검증 경고:\*.*?(?=\n+---|\Z)",
    re.DOTALL,
)


def _strip_validation_warnings(text: str) -> str:
    """답변에서 검증 경고 섹션 제거 (LLM 답변 품질엔 영향 없음, 시연 노이즈 정리)."""
    if not text:
        return text
    return _VALIDATION_WARNING_RE.sub("", text).rstrip()


# ============================================================
# 1) 프로필 후보 — 시연 시 자유롭게 추가/수정
#    student_type: 내국인 | 외국인 | 편입생  (또는 빈 문자열로 미설정)
# ============================================================
PROFILES = [
    {"label": "미설정",
     "student_id": "", "department": "", "student_type": ""},

    {"label": "24학번 영어전공 내국인",
     "student_id": "2024", "department": "영어전공", "student_type": "내국인"},

    {"label": "23학번 중국학부 외국인",
     "student_id": "2023", "department": "중국학부", "student_type": "외국인"},

    {"label": "24학번 글로벌비즈니스 편입생",
     "student_id": "2024", "department": "글로벌비즈니스대학", "student_type": "편입생"},

    {"label": "20학번 (구학번) 영어통번역",
     "student_id": "2020", "department": "영어통번역전공", "student_type": "내국인"},
]


# ============================================================
# 2) 질문 후보 — 시연 시 자유롭게 추가/수정
# ============================================================
QUESTIONS = [
    "2026학년도 1학기 학사일정 알려줘",
    "비교과 활동 유형별 역량 지수",
    "100% 자유전공제가 뭐야",
    "수업연한초과자 등록 안내",
    "수강신청 일정 언제부터?",
    "공인결석 신청 매뉴얼",
    "학사경고 기준 알려줘",
    "BUFS 건학이념",
    "분할납부 안내",
    "출석점수 환산표",
    "졸업 요건 알려줘",
    "수강신청 학점 제한이 어떻게 돼?",
]


# ============================================================
# 3) 어떤 조합을 실행할지 — 시연 시 숫자만 변경
# ============================================================
PROFILE_INDEX = 1     # PROFILES[1] 사용
QUESTION_INDEX = 0    # QUESTIONS[0] 사용


# ============================================================
# 아래는 실행 로직 — 수정 불필요
# ============================================================
BASE_URL = os.getenv("DEMO_BASE_URL", "http://127.0.0.1:8002")
TIMEOUT = 180


def _create_session(lang: str = "ko") -> str:
    r = requests.post(f"{BASE_URL}/api/session", json={"lang": lang}, timeout=15)
    r.raise_for_status()
    return r.json()["session_id"]


def _set_profile(sid: str, profile: dict) -> None:
    body = {
        "student_id": profile.get("student_id") or "",
        "department": profile.get("department") or "",
        "student_type": profile.get("student_type") or "내국인",
    }
    r = requests.put(f"{BASE_URL}/api/session/{sid}/profile", json=body, timeout=15)
    r.raise_for_status()


def _truncate(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _print_profile_block(profile: dict) -> None:
    print("─" * 70)
    print(f"[프로필] {profile.get('label', '')}")
    print(f"  학번  : {profile.get('student_id') or '(미설정)'}")
    print(f"  학과  : {profile.get('department') or '(미설정)'}")
    print(f"  유형  : {profile.get('student_type') or '(미설정)'}")


def run_one(profile: dict, question: str) -> dict:
    """단일 (프로필 × 질문) 실행 — 검색 로그 + 답변 + 출처 출력."""
    sid = _create_session(lang="ko")

    _print_profile_block(profile)

    # 프로필 push (값이 있는 경우만)
    if any((profile.get("student_id"), profile.get("department"), profile.get("student_type"))):
        _set_profile(sid, profile)

    print(f"[질문] {question}")
    t0 = time.monotonic()
    # 백그라운드 요청 + 메인 스레드에서 실시간 경과 시간 표시 (같은 줄 갱신)
    import threading
    result_box: dict = {}

    def _do_request():
        try:
            r = requests.post(
                f"{BASE_URL}/api/chat",
                params={"session_id": sid, "question": question},
                headers={"X-Test-Mode": "1"},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            result_box["data"] = r.json()
        except Exception as e:
            result_box["error"] = e

    th = threading.Thread(target=_do_request, daemon=True)
    th.start()
    # 매 0.1초마다 같은 줄에 경과 시간 갱신 (carriage return)
    while th.is_alive():
        th.join(timeout=0.1)
        elapsed_now = time.monotonic() - t0
        print(f"\r  → 처리 중 … {elapsed_now:5.1f}s", end="", flush=True)
    elapsed = time.monotonic() - t0
    # 라인 클리어 후 최종 시간 표시
    print(f"\r  → 응답 수신 ({elapsed:.1f}s) {' ' * 30}")
    if "error" in result_box:
        print(f"[오류] {type(result_box['error']).__name__}: {result_box['error']}")
        return {}
    data = result_box.get("data", {})

    # ── 검색 로그 ──
    results = data.get("results") or []
    in_ctx = [it for it in results if it.get("in_context")]
    out_ctx = [it for it in results if not it.get("in_context")]
    print()
    print(f"[검색 로그]  intent={data.get('intent', '')}   소요={elapsed:.1f}s")
    print(f"  총 후보 {len(results)}개  /  in-context {len(in_ctx)}개")

    # in-context 청크 (LLM이 본 것)
    if in_ctx:
        print()
        print("  ── 컨텍스트로 사용된 청크 ──")
        for i, it in enumerate(in_ctx[:5], 1):
            src = (it.get("source") or "").split("/")[-1]
            print(f"  [{i}] {it.get('doc_type', ''):<18} "
                  f"p.{it.get('page_number', 0):<3} "
                  f"score={it.get('score', 0):.2f}  {src}")
            print(f"      {_truncate(it.get('text', ''), 110)}")

    # 추가로 본 후보 (rerank 후 컷된 것)
    if out_ctx:
        print()
        print(f"  ── 후보였지만 컷된 청크 (요약, {min(len(out_ctx), 3)}개) ──")
        for i, it in enumerate(out_ctx[:3], 1):
            src = (it.get("source") or "").split("/")[-1]
            print(f"  [{i}] {it.get('doc_type', ''):<18} "
                  f"score={it.get('score', 0):.2f}  {src}")
            print(f"      {_truncate(it.get('text', ''), 90)}")

    # ── 답변 ──
    print()
    print("[답변]")
    answer = _strip_validation_warnings(data.get("answer") or "(빈 응답)")
    for line in answer.splitlines() or [""]:
        print(f"  {line}")

    # 출처 URL
    urls = data.get("source_urls") or []
    if urls:
        print()
        print("[출처 URL]")
        for u in urls[:5]:
            print(f"  {u.get('title', '')}: {u.get('url', '')}")

    print()
    return data


def run_all() -> None:
    """모든 시나리오 × 모든 질문을 차례로 실행 (시연 매트릭스)."""
    for p_idx, profile in enumerate(PROFILES):
        for q_idx, question in enumerate(QUESTIONS):
            print(f"\n{'=' * 70}")
            print(f" Scenario {p_idx} × Question {q_idx}")
            print(f"{'=' * 70}")
            run_one(profile, question)


def _print_help() -> None:
    print(textwrap.dedent(f"""\
        BUFS Chatbot 시연 도구
        ==========================================
        백엔드: {BASE_URL}

        프로필 후보 ({len(PROFILES)}개):
        {chr(10).join(f'  [{i}] {p["label"]}' for i, p in enumerate(PROFILES))}

        질문 후보 ({len(QUESTIONS)}개):
        {chr(10).join(f'  [{i}] {q}' for i, q in enumerate(QUESTIONS))}

        사용법:
          python3 scripts/demo_chat.py                       (PROFILE_INDEX={PROFILE_INDEX}, QUESTION_INDEX={QUESTION_INDEX} 사용)
          python3 scripts/demo_chat.py 2 5                   (PROFILES[2] × QUESTIONS[5])
          python3 scripts/demo_chat.py 1 "직접 입력한 질문"
          python3 scripts/demo_chat.py --all                 (모든 조합)
          python scripts/demo_chat.py --list                (이 도움말)
    """))


def main() -> None:
    args = sys.argv[1:]

    # 도움말
    if args and args[0] in ("--list", "--help", "-h"):
        _print_help()
        return

    # 매트릭스
    if args and args[0] == "--all":
        run_all()
        return

    # 백엔드 핑 (서버가 다른 요청 처리 중이면 응답 늦을 수 있어 timeout 30초)
    try:
        h = requests.get(f"{BASE_URL}/api/health", timeout=30).json()
        if not h.get("pipeline_ready"):
            print("[경고] 백엔드 pipeline_ready=false. 모델 로딩 중일 수 있음.")
    except Exception as e:
        print(f"[오류] 백엔드 헬스체크 실패: {e}")
        print(f"   uvicorn 서버를 먼저 띄워주세요:")
        print(f"     CHROMA_COLLECTION=bufs_demo .venv/bin/uvicorn "
              f"backend.main:app --host 127.0.0.1 --port 8002")
        sys.exit(1)

    # 인자 파싱
    p_idx = PROFILE_INDEX
    q_text = QUESTIONS[QUESTION_INDEX] if 0 <= QUESTION_INDEX < len(QUESTIONS) else QUESTIONS[0]
    if len(args) >= 1:
        try:
            p_idx = int(args[0])
        except ValueError:
            print(f"[오류] 첫 인자는 PROFILE 인덱스 (0~{len(PROFILES) - 1})")
            sys.exit(1)
    if len(args) >= 2:
        try:
            q_idx = int(args[1])
            q_text = QUESTIONS[q_idx]
        except ValueError:
            q_text = args[1]  # 자유 질문

    if not (0 <= p_idx < len(PROFILES)):
        print(f"[오류] PROFILE 인덱스 범위 0~{len(PROFILES) - 1}")
        sys.exit(1)

    run_one(PROFILES[p_idx], q_text)


if __name__ == "__main__":
    main()
