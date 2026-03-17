"""
캠챗 대화 로그 뷰어
사용자 질문·답변 기록을 조회하고 CSV/JSONL로 다운로드합니다.
접근: http://localhost:8501/logs  (관리자 비밀번호 필요)
"""

import hashlib
import hmac
import io
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from app.config import settings, _ADMIN_PW_DEFAULT
from app.logging import ChatLogger

# ── 페이지 설정 ─────────────────────────────────────
st.set_page_config(
    page_title="캠챗 로그",
    page_icon="📊",
    layout="wide",
)

# ── CSS (메인과 동일 느낌) ───────────────────────────
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] {
        font-family: 'Noto Sans KR', -apple-system, sans-serif !important;
    }
    .stApp { background: #f1f5f9; }
    footer  { display: none !important; }
    [data-testid="stSidebarNav"]     { display: none !important; }
    [data-testid="stToolbarActions"] { display: none !important; }
    [data-testid="stMainMenu"]       { display: none !important; }
    [data-testid="stAppDeployButton"]{ display: none !important; }
    header[data-testid="stHeader"] {
        background: transparent !important;
        height: 0 !important; overflow: visible !important;
    }
    .log-hdr {
        background: linear-gradient(135deg, #1e3a5f 0%, #2d5a9e 100%);
        border-radius: 12px; padding: 1rem 1.5rem; margin-bottom: 1.5rem;
    }
    .log-hdr h2 { color: #fff; margin: 0; font-size: 1.05rem; font-weight: 700; }
    .log-hdr p  { color: rgba(255,255,255,0.65); margin: 0.2rem 0 0; font-size: 0.76rem; }
    .metric-card {
        background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
        padding: 0.85rem 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .metric-val { font-size: 1.6rem; font-weight: 700; color: #1e3a5f; line-height: 1; }
    .metric-lbl { font-size: 0.74rem; color: #64748b; margin-top: 0.3rem; }
    .back-btn a {
        font-size: 0.82rem; color: #4f46e5; text-decoration: none;
        padding: 0.35rem 0.75rem; border: 1px solid #c7d2fe;
        border-radius: 6px; background: #eef2ff;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ════════════════════════════════════════════════════
# 인증 (어드민과 동일한 보안 수칙 적용)
# ════════════════════════════════════════════════════
_MAX_ATTEMPTS = settings.admin.max_login_attempts
_LOCKOUT_SECS = settings.admin.lockout_minutes * 60
_TIMEOUT_SECS = settings.admin.session_timeout_minutes * 60


def _verify_password(input_pw: str) -> bool:
    a = hashlib.sha256(input_pw.encode("utf-8")).digest()
    b = hashlib.sha256(settings.admin.password.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def _check_session_timeout() -> None:
    if not st.session_state.get("is_admin"):
        return
    last_active = st.session_state.get("admin_last_active", 0.0)
    if time.time() - last_active > _TIMEOUT_SECS:
        for key in ("is_admin", "admin_login_time", "admin_last_active"):
            st.session_state.pop(key, None)
    st.session_state.admin_last_active = time.time()


def _check_auth() -> None:
    _check_session_timeout()

    if st.session_state.get("is_admin"):
        return  # 이미 인증됨 (어드민 페이지 로그인 공유)

    # 기본 비밀번호 미설정 시 차단
    if settings.admin.password == _ADMIN_PW_DEFAULT:
        st.error(
            "**로그 페이지 접근 불가**\n\n"
            "`.env` 파일에 `ADMIN_PASSWORD` 환경변수가 설정되지 않았습니다.\n\n"
            "```\n# .env\nADMIN_PASSWORD=강력한비밀번호를여기에입력\n```",
            icon="🚫",
        )
        st.stop()

    st.title("🔐 로그 뷰어 로그인")
    st.markdown("---")

    # 잠금 상태 확인
    lockout_until = st.session_state.get("admin_lockout_until", 0.0)
    remaining = lockout_until - time.time()
    if remaining > 0:
        mins = int(remaining // 60) + 1
        st.warning(
            f"로그인 시도 횟수를 초과했습니다.  \n약 **{mins}분** 후에 다시 시도하세요.",
            icon="🔒",
        )
        st.stop()

    attempts_left = _MAX_ATTEMPTS - st.session_state.get("admin_failed_attempts", 0)

    with st.form("logs_login_form"):
        pw = st.text_input(
            "비밀번호",
            type="password",
            placeholder="관리자 비밀번호 입력",
        )
        submitted = st.form_submit_button("로그인", use_container_width=True)

    if submitted:
        if _verify_password(pw):
            st.session_state.is_admin          = True
            st.session_state.admin_login_time  = time.time()
            st.session_state.admin_last_active = time.time()
            st.session_state.pop("admin_failed_attempts", None)
            st.session_state.pop("admin_lockout_until",   None)
            st.rerun()
        else:
            failed = st.session_state.get("admin_failed_attempts", 0) + 1
            st.session_state.admin_failed_attempts = failed
            if failed >= _MAX_ATTEMPTS:
                st.session_state.admin_lockout_until   = time.time() + _LOCKOUT_SECS
                st.session_state.admin_failed_attempts = 0
                st.error(
                    f"로그인 시도 횟수 {_MAX_ATTEMPTS}회를 초과했습니다.  \n"
                    f"**{settings.admin.lockout_minutes}분** 동안 잠깁니다.",
                    icon="🔒",
                )
            else:
                st.error(
                    f"비밀번호가 올바르지 않습니다. (남은 시도: {_MAX_ATTEMPTS - failed}회)",
                    icon="❌",
                )
        st.stop()

    st.stop()


_check_auth()

# ── 헤더 ────────────────────────────────────────────
st.markdown(
    '<div class="log-hdr">'
    '  <h2>📊 캠챗 대화 로그</h2>'
    '  <p>사용자 질문 &middot; 답변 기록 조회 및 다운로드</p>'
    '</div>',
    unsafe_allow_html=True,
)

# ← 메인으로 돌아가기
st.markdown(
    '<div class="back-btn"><a href="/" target="_self">← 캠챗으로 돌아가기</a></div>',
    unsafe_allow_html=True,
)
st.markdown("<br>", unsafe_allow_html=True)

# ── 데이터 로드 ─────────────────────────────────────
chat_logger = ChatLogger()
dates = chat_logger.list_dates()

if not dates:
    st.info("📭 아직 저장된 대화 로그가 없습니다. 챗봇으로 질문해보세요!")
    st.stop()

# ── 필터 컨트롤 ─────────────────────────────────────
fc1, fc2, fc3 = st.columns([2, 2, 2])

with fc1:
    show_all = st.checkbox("📅 전체 기간 보기", value=False)

with fc2:
    sel_date = None
    if not show_all:
        sel_date = st.selectbox(
            "날짜 선택",
            options=dates,
            format_func=lambda d: d.strftime("%Y년 %m월 %d일"),
        )

INTENT_LABELS = {
    "전체": "전체",
    "GRADUATION_REQ": "졸업요건",
    "REGISTRATION": "수강신청",
    "SCHEDULE": "학사일정",
    "COURSE_INFO": "교과목",
    "MAJOR_CHANGE": "전과",
    "ALTERNATIVE": "대안/선택",
    "GENERAL": "일반",
}

with fc3:
    filter_intent = st.selectbox(
        "인텐트 필터",
        options=list(INTENT_LABELS.keys()),
        format_func=lambda k: INTENT_LABELS[k],
    )

# 데이터 필터링
entries = chat_logger.read_all() if show_all else chat_logger.read(sel_date)
if filter_intent != "전체":
    entries = [e for e in entries if e.get("intent") == filter_intent]

# ── 요약 지표 ───────────────────────────────────────
today_cnt = len(chat_logger.read(date.today()))
avg_ms    = (sum(e.get("duration_ms", 0) for e in entries) / len(entries)) if entries else 0
intents   = [e.get("intent", "") for e in entries if e.get("intent")]
top_intent_raw = Counter(intents).most_common(1)
top_intent = INTENT_LABELS.get(top_intent_raw[0][0], top_intent_raw[0][0]) if top_intent_raw else "-"

m1, m2, m3, m4 = st.columns(4)
for col, val, lbl in [
    (m1, len(entries),          "조회된 대화 수"),
    (m2, today_cnt,             "오늘 대화 수"),
    (m3, f"{avg_ms/1000:.1f}초", "평균 응답 시간"),
    (m4, top_intent,            "최다 인텐트"),
]:
    col.markdown(
        f'<div class="metric-card">'
        f'  <div class="metric-val">{val}</div>'
        f'  <div class="metric-lbl">{lbl}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)

if not entries:
    st.info("해당 조건의 로그가 없습니다.")
    st.stop()

# ── 대화 목록 테이블 ────────────────────────────────
df = pd.DataFrame(entries)
df["시간"]      = pd.to_datetime(df["timestamp"]).dt.strftime("%m/%d %H:%M")
df["학번"]      = df["student_id"].fillna("").astype(str)
df["인텐트"]    = df["intent"].map(lambda x: INTENT_LABELS.get(x, x))
df["질문"]      = df["question"]
df["답변 미리보기"] = df["answer"].str[:60] + "…"
df["응답(ms)"]  = df["duration_ms"].fillna(0).astype(int)
if "rating" not in df.columns:
    df["rating"] = None
df["별점"] = df["rating"].apply(
    lambda r: ("★" * int(r) + "☆" * (5 - int(r))) if pd.notna(r) and r else "-"
)

st.markdown("##### 💬 대화 목록")
st.dataframe(
    df[["시간", "학번", "인텐트", "질문", "답변 미리보기", "응답(ms)", "별점"]],
    use_container_width=True,
    hide_index=True,
    height=340,
)

# ── 상세 보기 ───────────────────────────────────────
st.markdown("---")
st.markdown("##### 🔍 대화 상세 (최근 20건)")

recent = list(reversed(entries))[:20]
for entry in recent:
    ts      = entry.get("timestamp", "")[:16].replace("T", " ")
    q       = entry.get("question", "")
    a       = entry.get("answer", "")
    intent  = INTENT_LABELS.get(entry.get("intent", ""), entry.get("intent", ""))
    sid     = entry.get("student_id", "") or "미기재"
    dur     = entry.get("duration_ms", 0)

    rating     = entry.get("rating")
    stars_disp = ("★" * int(rating) + "☆" * (5 - int(rating))) if rating else "미평가"
    label = f"[{ts}]  {q[:50]}{'…' if len(q) > 50 else ''}  —  {intent}  {'⭐' * int(rating) if rating else ''}"
    with st.expander(label):
        col_q, col_a = st.columns([1, 2])
        with col_q:
            st.markdown("**🙋 질문**")
            st.info(q)
            st.caption(f"학번: {sid} · 응답: {dur}ms · 만족도: {stars_disp}")
        with col_a:
            st.markdown("**🎓 답변**")
            st.markdown(a)

# ── 다운로드 ────────────────────────────────────────
st.markdown("---")
st.markdown("##### 📥 로그 다운로드")

dl1, dl2 = st.columns(2)

with dl1:
    buf = io.StringIO()
    dl_df = pd.DataFrame(entries)
    available_cols = ["timestamp", "session_id", "student_id", "intent", "question", "answer", "duration_ms", "rating"]
    dl_df = dl_df[[c for c in available_cols if c in dl_df.columns]]
    dl_df.to_csv(buf, index=False, encoding="utf-8-sig")
    fname = f"캠챗_로그_{date.today().isoformat()}.csv"
    st.download_button(
        label="📊 CSV 다운로드 (Excel에서 열기)",
        data=buf.getvalue().encode("utf-8-sig"),
        file_name=fname,
        mime="text/csv",
        use_container_width=True,
    )

with dl2:
    jsonl_data = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
    fname_j = f"캠챗_로그_{date.today().isoformat()}.jsonl"
    st.download_button(
        label="📄 JSONL 다운로드 (원본)",
        data=jsonl_data.encode("utf-8"),
        file_name=fname_j,
        mime="application/json",
        use_container_width=True,
    )
