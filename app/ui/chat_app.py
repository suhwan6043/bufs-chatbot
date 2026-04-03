"""
캠챗 - 부산외국어대학교 학사 안내 AI 챗봇
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path

import streamlit as st

from app.logging import ChatLogger
from app.shared_resources import get_chroma_store
from app.graphdb import AcademicGraph
from app.pipeline import (
    QueryAnalyzer,
    QueryRouter,
    ContextMerger,
    AnswerGenerator,
    ResponseValidator,
)
from app.scheduler import get_scheduler
from app.contacts import get_dept_searcher

logger = logging.getLogger(__name__)

# ── Brand ──────────────────────────────────────────
APP_NAME    = "캠챗"
APP_SUBTITLE = "부산외대 학사 도우미"
APP_VERSION  = "0.2.0"
LOGO_PATH    = Path(__file__).parent / "static" / "logo.png"

QUICK_FEATURES = [
    {"label": "수강신청",     "question": "수강신청 일정과 방법을 알려줘"},
    {"label": "성적조회",     "question": "성적 처리 방법과 이의신청 절차 알려줘"},
    {"label": "학사일정",     "question": "이번 학기 주요 학사일정을 알려줘"},
    {"label": "자주묻는질문", "question": "학사 관련 자주 묻는 질문을 알려줘"},
]

PORTAL_LINKS = [
    {"icon": "🖥️", "label": "수강신청 사이트 바로가기", "url": "https://sugang.bufs.ac.kr/Login.aspx"},
    {"icon": "📊", "label": "학생포털시스템",            "url": "https://m.bufs.ac.kr/default.aspx?ReturnUrl=%2f"},
    {"icon": "📅", "label": "학사일정",                 "url": "https://m.bufs.ac.kr/popup/Haksa_Iljeong.aspx?gbn="},
    {"icon": "📢", "label": "학사공지",                 "url": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca"},
]

# 학과 목록 (departments.json에서 동적 로드)
def _load_departments() -> list:
    """departments.json에서 학과/전공 이름을 추출합니다."""
    try:
        import json
        from pathlib import Path
        data_path = Path(__file__).parent.parent.parent / "data" / "contacts" / "departments.json"
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        names = []
        for college in data.get("colleges", []):
            for dept in college.get("departments", []):
                # "전공" 접미사 제거
                name = dept["name"].replace("전공", "").replace("학부", "").replace("학과", "").strip()
                if name:
                    names.append(name)
                for sub in dept.get("sub_units", []):
                    sub_name = sub["name"].replace("전공", "").replace("학부", "").replace("학과", "").strip()
                    if sub_name:
                        names.append(sub_name)
        return sorted(set(names))
    except Exception:
        return []

DEPARTMENTS = _load_departments()

# ── Loading animation (답변 생성 중 표시) ───────────
THINKING_HTML = """
<style>
@keyframes _bkFlt {
    0%,100% { transform: translateY(0px) rotate(-4deg); }
    25%     { transform: translateY(-8px) rotate(0deg); }
    50%     { transform: translateY(-12px) rotate(4deg); }
    75%     { transform: translateY(-6px) rotate(0deg); }
}
@keyframes _pgTrn {
    0%,100% { transform: scaleX(1);  opacity: 1;   }
    45%,55% { transform: scaleX(0);  opacity: 0.3; }
}
@keyframes _dtPop {
    0%,80%,100% { transform: scale(0.5); opacity: 0.25; }
    40%         { transform: scale(1.1); opacity: 1;    }
}
._cam-ld { display:flex; align-items:center; gap:16px; padding:8px 2px; }
._cam-book-wrap {
    position: relative; width: 44px; height: 44px;
    display: flex; align-items: center; justify-content: center;
}
._cam-book {
    font-size: 2.4rem; display: inline-block;
    animation: _bkFlt 1.8s ease-in-out infinite;
    filter: drop-shadow(0 4px 8px rgba(79,70,229,0.25));
}
._cam-page {
    position: absolute; right: 4px; top: 10px;
    width: 10px; height: 20px;
    background: rgba(79,70,229,0.18);
    border-radius: 0 3px 3px 0;
    animation: _pgTrn 1.8s ease-in-out infinite;
    transform-origin: left center;
}
._cam-info { display:flex; flex-direction:column; gap:7px; }
._cam-lbl {
    font-size: 0.86rem; color: #374151;
    font-family: 'Noto Sans KR', sans-serif;
    font-weight: 500; letter-spacing: -0.01em;
}
._cam-sub {
    font-size: 0.74rem; color: #9ca3af;
    font-family: 'Noto Sans KR', sans-serif;
    margin-top: -4px;
}
._cam-dots { display:flex; gap:5px; align-items:center; }
._cam-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #4f46e5;
    animation: _dtPop 1.2s infinite ease-in-out;
}
._cam-dot:nth-child(2) { animation-delay: 0.22s; }
._cam-dot:nth-child(3) { animation-delay: 0.44s; }
</style>
<div class="_cam-ld">
  <div class="_cam-book-wrap">
    <span class="_cam-book">📖</span>
    <div class="_cam-page"></div>
  </div>
  <div class="_cam-info">
    <div class="_cam-lbl">답변을 생성하고 있어요</div>
    <div class="_cam-sub">학사 자료를 분석하는 중입니다</div>
    <div class="_cam-dots">
      <div class="_cam-dot"></div>
      <div class="_cam-dot"></div>
      <div class="_cam-dot"></div>
    </div>
  </div>
</div>
"""


# ── CSS ────────────────────────────────────────────
def inject_custom_css():
    # 뷰포트 메타태그: iOS Safari 자동 축소 방지
    st.markdown(
        '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');

        :root {
            --navy:        #1e3a5f;
            --accent:      #4f46e5;
            --accent-lt:   #eef2ff;
            --sidebar-bg:  #f8f9fb;
            --main-bg:     #f1f5f9;
            --card:        #ffffff;
            --text:        #1e293b;
            --text-sub:    #475569;
            --text-muted:  #94a3b8;
            --border:      #e2e8f0;
            --shadow-sm:   0 1px 3px rgba(0,0,0,0.06);
            --shadow-md:   0 4px 12px rgba(0,0,0,0.08);
            --radius:      10px;
            --radius-sm:   7px;
        }

        html, body, [class*="css"] {
            font-family: 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif !important;
        }

        /* Hide Streamlit chrome */
        footer { display: none !important; }
        [data-testid="stToolbarActions"] { display: none !important; }
        [data-testid="stMainMenu"]       { display: none !important; }
        [data-testid="stAppDeployButton"]{ display: none !important; }

        /* Header: 높이 0으로 줄이되 사이드바 토글 버튼은 유지 */
        header[data-testid="stHeader"] {
            background: transparent !important;
            height: 0px !important;
            min-height: 0px !important;
            overflow: visible !important;
        }

        /* 사이드바 접기/펼치기 버튼 - 항상 좌상단에 고정 */
        [data-testid="stExpandSidebarButton"] {
            position: fixed !important;
            top: 0.5rem !important;
            left: 0.5rem !important;
            z-index: 99999 !important;
            background: var(--card) !important;
            border: 1px solid var(--border) !important;
            border-radius: 8px !important;
            box-shadow: var(--shadow-sm) !important;
            visibility: visible !important;
            display: flex !important;
        }

        /* App background */
        .stApp { background: var(--main-bg); }

        /* ── Sidebar ── */
        section[data-testid="stSidebar"] {
            background: var(--sidebar-bg) !important;
            border-right: 1px solid var(--border);
        }
        section[data-testid="stSidebar"] > div { background: var(--sidebar-bg) !important; }

        /* Force all sidebar text to dark */
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] span,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] div { color: var(--text) !important; }

        section[data-testid="stSidebar"] hr {
            border-color: var(--border) !important;
            margin: 0.6rem 0 !important;
        }

        /* Sidebar quick-feature buttons */
        section[data-testid="stSidebar"] .stButton > button {
            background: var(--card) !important;
            border: 1px solid var(--border) !important;
            border-radius: var(--radius-sm) !important;
            color: var(--text-sub) !important;
            font-size: 0.82rem !important;
            font-weight: 500 !important;
            padding: 0.45rem 0.5rem !important;
            transition: all 0.15s ease;
            box-shadow: var(--shadow-sm);
        }
        section[data-testid="stSidebar"] .stButton > button:hover {
            background: var(--accent-lt) !important;
            border-color: var(--accent) !important;
            color: var(--accent) !important;
        }

        /* ── Chat header ── */
        .chat-hdr {
            background: linear-gradient(135deg, var(--navy) 0%, #2d5a9e 100%);
            border-radius: 12px;
            padding: 0.85rem 1.1rem;
            margin-bottom: 0.9rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: var(--shadow-md);
        }
        .chat-hdr h2 {
            margin: 0;
            font-size: 0.95rem;
            font-weight: 700;
            color: #fff;
        }
        .chat-hdr p {
            margin: 0.15rem 0 0;
            font-size: 0.72rem;
            color: rgba(255,255,255,0.65);
        }
        .chat-hdr-badge {
            background: rgba(255,255,255,0.14);
            border-radius: 20px;
            padding: 0.25rem 0.65rem;
            font-size: 0.7rem;
            color: rgba(255,255,255,0.88);
            white-space: nowrap;
        }

        /* ── Chat messages ── */
        [data-testid="stChatMessage"] {
            background: transparent !important;
            border: none !important;
            padding: 0.3rem 0 !important;
        }
        /* User bubble */
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) .stMarkdown {
            background: var(--accent);
            border-radius: 16px 16px 4px 16px;
            padding: 0.65rem 0.95rem;
            max-width: 80%;
            margin-left: auto;
            box-shadow: var(--shadow-sm);
        }
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) .stMarkdown p {
            color: #fff !important;
            margin: 0;
        }
        /* Assistant bubble */
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) .stMarkdown {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px 16px 16px 4px;
            padding: 0.65rem 0.95rem;
            max-width: 88%;
            box-shadow: var(--shadow-sm);
        }

        /* ── Chat input ── */
        [data-testid="stChatInput"] {
            background: var(--card);
            border-top: 1px solid var(--border);
            padding: 0.6rem 1rem !important;
        }
        [data-testid="stChatInput"] textarea {
            border-radius: 24px !important;
            border: 1.5px solid var(--border) !important;
            font-size: 0.88rem !important;
            background: var(--main-bg) !important;
            transition: border-color 0.15s;
        }
        [data-testid="stChatInput"] textarea:focus {
            border-color: var(--accent) !important;
            box-shadow: 0 0 0 3px rgba(79,70,229,0.1) !important;
        }
        [data-testid="stChatInput"] button {
            background: var(--accent) !important;
            border-radius: 50% !important;
        }

        /* ── Welcome screen ── */
        .wc-wrap { text-align: center; padding: 3.5rem 1rem 1.5rem; }
        .wc-icon { font-size: 3rem; }
        .wc-title {
            font-size: 1.35rem; font-weight: 700;
            color: var(--navy); margin: 0.4rem 0 0.3rem;
        }
        .wc-sub {
            font-size: 0.86rem; color: var(--text-sub);
            line-height: 1.6; margin-bottom: 1.8rem;
        }
        /* Welcome + right panel main-area buttons */
        section[data-testid="stMainBlockContainer"] .stButton > button {
            background: var(--card) !important;
            border: 1px solid var(--border) !important;
            border-radius: var(--radius) !important;
            text-align: left !important;
            color: var(--text-sub) !important;
            font-size: 0.83rem !important;
            padding: 0.65rem 0.85rem !important;
            white-space: pre-line !important;
            transition: all 0.15s ease;
            box-shadow: var(--shadow-sm);
            min-height: 64px;
        }
        section[data-testid="stMainBlockContainer"] .stButton > button:hover {
            border-color: var(--accent) !important;
            color: var(--accent) !important;
            background: var(--accent-lt) !important;
            box-shadow: var(--shadow-md) !important;
        }
        .wc-hint {
            font-size: 0.75rem; color: var(--text-muted);
            margin-top: 1.5rem;
        }

        /* ── Right panel ── */
        .rp-section {
            font-size: 0.72rem; font-weight: 700;
            color: var(--text-muted); text-transform: uppercase;
            letter-spacing: 0.5px; margin-bottom: 0.5rem;
            padding-bottom: 0.35rem;
            border-bottom: 1px solid var(--border);
        }
        .rp-link {
            display: flex; align-items: center; gap: 0.4rem;
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 0.5rem 0.65rem;
            margin-bottom: 0.35rem;
            font-size: 0.8rem; color: var(--text-sub);
            text-decoration: none !important;
            transition: all 0.15s ease;
            box-shadow: var(--shadow-sm);
        }
        .rp-link:hover {
            border-color: var(--accent);
            color: var(--accent);
            background: var(--accent-lt);
        }
        .rp-tip {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 0.65rem 0.75rem;
            font-size: 0.76rem;
            color: var(--text-sub);
            line-height: 1.75;
            box-shadow: var(--shadow-sm);
        }

        /* ── Star rating buttons ── */
        .star-rating-row .stButton > button {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            color: #d1d5db !important;
            font-size: 1.15rem !important;
            padding: 0 !important;
            min-height: unset !important;
            line-height: 1 !important;
            transition: color 0.1s ease;
        }
        .star-rating-row .stButton > button:hover {
            color: #f59e0b !important;
            background: transparent !important;
            border: none !important;
        }

        /* ── Scrollbar ── */
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

        /* Streamlit 자동 페이지 내비 숨김 (커스텀 사이드바 보호) */
        [data-testid="stSidebarNav"] { display: none !important; }

        /* ═══════════════════════════════════════
           📱 MOBILE RESPONSIVE  (≤ 768 px)
           ═══════════════════════════════════════ */
        @media (max-width: 768px) {

            /* 메인 컨테이너 패딩 축소 */
            .main .block-container {
                padding-left: 0.6rem !important;
                padding-right: 0.6rem !important;
                padding-top: 0.5rem !important;
                max-width: 100% !important;
            }

            /* 오른쪽 패널(#rp-marker 포함 컬럼) 숨김 */
            [data-testid="stColumn"]:has(#rp-marker) {
                display: none !important;
            }
            /* 채팅 컬럼 전체 너비로 확장 */
            [data-testid="stHorizontalBlock"]:has(#rp-marker) {
                flex-wrap: wrap !important;
            }
            [data-testid="stHorizontalBlock"]:has(#rp-marker) > [data-testid="stColumn"]:first-child {
                min-width: 100% !important;
                width: 100% !important;
                flex: 1 1 100% !important;
            }

            /* 채팅 헤더: 배지 숨김·패딩 축소 */
            .chat-hdr {
                padding: 0.55rem 0.75rem !important;
                border-radius: 10px !important;
            }
            .chat-hdr-badge { display: none !important; }
            .chat-hdr h2   { font-size: 0.88rem !important; }
            .chat-hdr p    { font-size: 0.68rem !important; }

            /* 메시지 말풍선 너비 확대 */
            [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) .stMarkdown {
                max-width: 92% !important;
            }
            [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) .stMarkdown {
                max-width: 98% !important;
            }

            /* 채팅 입력창: iOS 자동 줌 방지 (font ≥ 16px 필수) */
            [data-testid="stChatInput"] textarea {
                font-size: 1rem !important;
            }

            /* 웰컴 화면 상단 여백 축소 */
            .wc-wrap { padding-top: 1.5rem !important; }
            .wc-title { font-size: 1.1rem !important; }

            /* 사이드바 토글 버튼 위치 조정 */
            [data-testid="stExpandSidebarButton"] {
                top: 0.3rem !important;
                left: 0.3rem !important;
            }
        }

        /* ═══════════════════════════════════════
           📱 SMALL PHONE  (≤ 480 px)
           ═══════════════════════════════════════ */
        @media (max-width: 480px) {

            .main .block-container {
                padding-left: 0.4rem !important;
                padding-right: 0.4rem !important;
            }

            /* 웰컴 버튼 2열 → 1열 (메인 영역만 대상) */
            section[data-testid="stMainBlockContainer"]
              [data-testid="stHorizontalBlock"]:not(:has(#rp-marker))
              [data-testid="stColumn"] {
                min-width: 100% !important;
                flex: 1 1 100% !important;
            }

            /* 헤더 추가 축소 */
            .chat-hdr h2 { font-size: 0.82rem !important; }

            /* 채팅 아바타 크기 */
            [data-testid="chatAvatarIcon-user"],
            [data-testid="chatAvatarIcon-assistant"] {
                width: 1.6rem !important;
                height: 1.6rem !important;
                font-size: 0.9rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ── Onboarding ─────────────────────────────────────
def render_onboarding() -> None:
    """최초 접속 시 표시하는 사용자 프로필 설정 화면."""
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.markdown(
            '<div style="text-align:center;padding:2.5rem 0 1.2rem;">'
            '<div style="font-size:2.8rem;">🎓</div>'
            '<div style="font-size:1.25rem;font-weight:700;color:#1e3a5f;margin:0.5rem 0 0.3rem;">'
            '캠챗에 오신 것을 환영합니다</div>'
            '<div style="font-size:0.84rem;color:#64748b;line-height:1.6;">'
            '맞춤 학사 안내를 위해 기본 정보를 입력해주세요.<br>'
            '입력한 정보는 질문에 자동으로 반영됩니다.</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        with st.form("onboarding_form", border=True):
            year = st.selectbox(
                "📅 입학연도",
                options=list(range(2026, 2015, -1)),
                format_func=lambda y: f"{y}학번",
                index=3,  # 기본값: 2023학번
            )
            dept = st.selectbox(
                "🏫 학과 / 전공",
                options=["선택 안 함"] + sorted(DEPARTMENTS),
            )
            stype = st.radio(
                "👤 학생 유형",
                options=["내국인", "외국인", "편입생"],
                horizontal=True,
            )

            st.divider()
            st.markdown(
                '<p style="font-size:0.78rem;color:#64748b;line-height:1.65;margin:0 0 0.3rem;">'
                '본 서비스의 답변은 인공지능(AI)에 의해 자동 생성되며, '
                '사실과 다르거나 부정확한 내용을 포함할 수 있습니다. '
                '제공된 정보는 참고용이며, 중요한 학사 사항은 학과 사무실 또는 '
                '학사지원팀을 통해 확인하시기 바랍니다. '
                'AI 답변의 오류·누락으로 인해 발생한 직접적·간접적 불이익에 대해 '
                '본 서비스는 책임을 지지 않습니다.</p>',
                unsafe_allow_html=True,
            )
            disclaimer_checked = st.checkbox(
                "위 내용을 확인하였으며, 이에 동의합니다.",
                value=False,
                key="disclaimer_check",
            )

            c_start, c_skip = st.columns([3, 2])
            with c_start:
                submitted = st.form_submit_button(
                    "시작하기", use_container_width=True, type="primary"
                )
            with c_skip:
                skipped = st.form_submit_button(
                    "나중에 설정", use_container_width=True
                )

        if submitted or skipped:
            if not disclaimer_checked:
                st.warning("면책 조항에 동의해야 시작할 수 있습니다.", icon="⚠️")
            elif submitted:
                st.session_state.user_profile = {
                    "student_id": str(year),
                    "department": dept if dept != "선택 안 함" else "",
                    "student_type": stype,
                }
                st.rerun()
            else:  # skipped
                st.session_state.user_profile = {}
                st.rerun()


# ── Profile sidebar card ────────────────────────────
def _render_profile_sidebar() -> None:
    """사이드바 내 현재 사용자 프로필 표시 + 수정 버튼."""
    profile = st.session_state.get("user_profile") or {}

    st.markdown(
        '<p style="font-size:0.7rem;font-weight:700;color:#94a3b8;'
        'text-transform:uppercase;letter-spacing:0.5px;margin:0.2rem 0 0.4rem;">내 정보</p>',
        unsafe_allow_html=True,
    )

    if profile.get("student_id"):
        dept_txt  = profile.get("department") or "학과 미설정"
        stype_txt = profile.get("student_type", "내국인")
        st.markdown(
            f'<div style="padding:0.5rem 0.65rem;border-radius:7px;'
            f'background:#eef2ff;border:1px solid #c7d2fe;margin-bottom:0.3rem;">'
            f'<span style="font-size:0.85rem;font-weight:700;color:#3730a3;">'
            f'{profile["student_id"]}학번</span>'
            f'<span style="font-size:0.77rem;color:#4f46e5;margin-left:0.45rem;">'
            f'{dept_txt}</span>'
            f'<span style="font-size:0.73rem;color:#818cf8;margin-left:0.3rem;">· {stype_txt}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="font-size:0.79rem;color:#94a3b8;padding:0.2rem 0 0.3rem;">'
            '정보 미설정</div>',
            unsafe_allow_html=True,
        )

    if st.button("✏️  정보 수정", key="edit_profile_btn", use_container_width=True):
        st.session_state.user_profile = None  # 온보딩으로 복귀
        st.rerun()


# ── Sidebar ────────────────────────────────────────
def render_sidebar() -> bool:
    with st.sidebar:
        # ── Logo + Brand ──────────────────────────
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=56)
        else:
            # CSS hex fallback until logo.png is placed
            st.markdown(
                '<div style="width:56px;height:56px;background:linear-gradient(135deg,#1e3a5f,#2d5a9e);'
                'border-radius:14px;display:flex;align-items:center;justify-content:center;'
                'font-size:1.5rem;color:white;font-weight:700;margin-bottom:0.1rem;">캠</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div style="margin:0.45rem 0 0.2rem;">'
            f'  <span style="font-size:1.15rem;font-weight:700;color:#1e293b;">{APP_NAME}</span>'
            f'</div>'
            f'<div style="font-size:0.76rem;color:#64748b;margin-bottom:0.3rem;">{APP_SUBTITLE}</div>',
            unsafe_allow_html=True,
        )

        st.divider()

        # ── Silent init ───────────────────────────
        try:
            init_components()
        except Exception:
            st.error("서비스를 준비 중입니다. 잠시 후 다시 시도해주세요.")
            return False

        # ── 내 정보 ────────────────────────────────
        _render_profile_sidebar()

        st.divider()

        # ── 빠른 기능 ──────────────────────────────
        st.markdown(
            '<p style="font-size:0.7rem;font-weight:700;color:#94a3b8;'
            'text-transform:uppercase;letter-spacing:0.5px;margin:0.2rem 0 0.5rem;">빠른 기능</p>',
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        for i, feat in enumerate(QUICK_FEATURES):
            with (c1 if i % 2 == 0 else c2):
                if st.button(feat["label"], key=f"qf_{i}", use_container_width=True):
                    st.session_state.pending_question = feat["question"]
                    st.rerun()

        st.divider()

        # ── 대화 ──────────────────────────────────
        st.markdown(
            '<p style="font-size:0.7rem;font-weight:700;color:#94a3b8;'
            'text-transform:uppercase;letter-spacing:0.5px;margin:0.2rem 0 0.4rem;">대화</p>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="padding:0.45rem 0.6rem;border-radius:7px;'
            'background:#eef2ff;color:#4f46e5 !important;'
            'font-size:0.85rem;font-weight:600;margin-bottom:0.2rem;">'
            '💬&nbsp; 전체 대화</div>',
            unsafe_allow_html=True,
        )

        if st.button("🗑️  대화 초기화", key="clr", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        # ── LLM 서버 경고 (에러 시에만) ────────────
        async def _chk():
            return await st.session_state.generator.health_check()

        if not asyncio.run(_chk()):
            st.markdown(
                '<div style="margin-top:0.6rem;padding:0.5rem 0.6rem;border-radius:7px;'
                'background:#fef3c7;border:1px solid #fcd34d;font-size:0.78rem;color:#92400e;">'
                '⚠️ AI 서버 미연결<br><span style="font-size:0.72rem;">LM Studio를 시작해주세요</span>'
                '</div>',
                unsafe_allow_html=True,
            )

        # ── Version footer ────────────────────────
        st.markdown(
            f'<div style="position:absolute;bottom:0.75rem;left:1rem;right:1rem;'
            f'font-size:0.7rem;color:#cbd5e1;border-top:1px solid #e2e8f0;padding-top:0.6rem;">'
            f'버전 {APP_VERSION}</div>',
            unsafe_allow_html=True,
        )

    return True


# ── Chat header ────────────────────────────────────
def render_chat_header():
    st.markdown(
        '<div class="chat-hdr">'
        '  <div>'
        '    <h2>캠챗 &mdash; 부산외대 학사챗봇</h2>'
        '    <p>수강신청 &middot; 성적 &middot; 학사일정 &middot; 학사행정 지원</p>'
        '  </div>'
        '  <div class="chat-hdr-badge">📢 학사 정보는 학교 포털에서도 확인하세요</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Welcome screen ─────────────────────────────────
def render_welcome_screen():
    st.markdown(
        '<div class="wc-wrap">'
        '  <div class="wc-icon">🎓</div>'
        '  <div class="wc-title">캠챗에 오신 것을 환영합니다</div>'
        '  <div class="wc-sub">부산외국어대학교 학사 안내 AI입니다.<br>'
        '  졸업요건, 수강신청, 학사일정 등 궁금한 것을 물어보세요.</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    for i, feat in enumerate(QUICK_FEATURES):
        with (c1 if i % 2 == 0 else c2):
            if st.button(
                f"{feat['label']}\n{feat['question']}",
                key=f"wc_{i}",
                use_container_width=True,
            ):
                st.session_state.pending_question = feat["question"]
                st.rerun()
    st.markdown(
        '<div class="wc-hint">위 버튼을 누르거나 아래 입력창에 직접 질문하세요</div>',
        unsafe_allow_html=True,
    )


# ── Rating UI ──────────────────────────────────────
def _render_rating(msg_idx: int, msg: dict) -> None:
    """마지막 어시스턴트 답변 아래에 1~5점 별점 UI를 표시합니다."""
    already_rated = msg.get("rated", False)
    rating_value  = msg.get("rating", 0)

    if already_rated:
        stars = "★" * rating_value + "☆" * (5 - rating_value)
        st.markdown(
            f'<div style="font-size:0.78rem;color:#94a3b8;margin:0.2rem 0 0.6rem 0.2rem;">'
            f'만족도: <span style="color:#f59e0b;letter-spacing:2px;">{stars}</span>'
            f'&nbsp;({rating_value}/5)</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div style="font-size:0.78rem;color:#64748b;margin:0.3rem 0 0.4rem 0.2rem;">'
        '이 답변이 도움이 됐나요?</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns([1, 1, 1, 1, 1, 6])
    for star in range(1, 6):
        with cols[star - 1]:
            if st.button(
                "★" * star,
                key=f"star_{msg_idx}_{star}",
                help=f"{star}점",
                use_container_width=True,
            ):
                # 메시지에 별점 기록
                st.session_state.messages[msg_idx]["rated"]  = True
                st.session_state.messages[msg_idx]["rating"] = star

                # 대응하는 질문(바로 앞 user 메시지) 찾기
                question = ""
                for i in range(msg_idx - 1, -1, -1):
                    if st.session_state.messages[i]["role"] == "user":
                        question = st.session_state.messages[i]["content"]
                        break

                # 로그 파일에 별점 업데이트
                try:
                    st.session_state.chat_logger.update_rating(
                        session_id=st.session_state.get("session_id", ""),
                        question=question,
                        rating=star,
                    )
                except Exception:
                    pass

                st.rerun()


# ── Feedback save helper ────────────────────────────
def _save_feedback(text: str) -> None:
    """사용자 자유 피드백을 data/feedback/feedback.jsonl 에 저장합니다."""
    import json
    from datetime import datetime
    feedback_dir = Path(__file__).resolve().parent.parent.parent / "data" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "text": text,
        "timestamp": datetime.now().isoformat(),
        "session_id": st.session_state.get("session_id", ""),
    }
    with open(feedback_dir / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Right panel ────────────────────────────────────
def render_right_panel():
    # 모바일에서 이 컬럼 전체를 숨기기 위한 마커 (CSS :has(#rp-marker) 대상)
    st.markdown('<div id="rp-marker"></div>', unsafe_allow_html=True)
    st.markdown('<div class="rp-section">바로가기</div>', unsafe_allow_html=True)
    for lnk in PORTAL_LINKS:
        st.markdown(
            f'<a href="{lnk["url"]}" target="_blank" class="rp-link">'
            f'{lnk["icon"]} {lnk["label"]}</a>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div style="margin-top:0.9rem;" class="rp-section">도움말</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="rp-tip">'
        '학번을 포함하면 더 정확한 답변을 받을 수 있어요<br><br>'
        '<span style="color:#64748b;">예시</span><br>'
        '&bull; <em>"2023학번 졸업요건"</em><br>'
        '&bull; <em>"2024학번 수강신청 학점"</em>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div style="margin-top:1.2rem;" class="rp-section">피드백</div>',
        unsafe_allow_html=True,
    )
    with st.form("feedback_form", clear_on_submit=True, border=False):
        fb_text = st.text_area(
            "의견",
            placeholder="불편한 점, 개선 제안, 칭찬 등 자유롭게 작성해 주세요.",
            height=110,
            label_visibility="collapsed",
        )
        if st.form_submit_button("전송", use_container_width=True):
            if fb_text.strip():
                _save_feedback(fb_text.strip())
                st.success("피드백이 전송됐습니다. 감사합니다!")
            else:
                st.warning("내용을 입력해 주세요.")


# ── Pipeline (UNCHANGED) ───────────────────────────
def init_components():
    if "initialized" not in st.session_state:
        with st.spinner("시스템 초기화 중..."):
            chroma_store   = get_chroma_store()   # 공유 싱글톤 (스케줄러와 동일 인스턴스)
            academic_graph = AcademicGraph()

            st.session_state.analyzer  = QueryAnalyzer()
            st.session_state.router    = QueryRouter(
                chroma_store=chroma_store,
                academic_graph=academic_graph,
            )
            st.session_state.merger    = ContextMerger()
            st.session_state.generator = AnswerGenerator()
            st.session_state.validator = ResponseValidator()
            st.session_state.chroma_store = chroma_store
            st.session_state.chat_logger = ChatLogger()
            st.session_state.session_id  = uuid.uuid4().hex[:12]
            st.session_state.messages    = []
            st.session_state.initialized = True


def _format_contact_answer(question: str) -> str:
    """
    연락처 쿼리를 감지하면 DeptSearcher로 전화번호를 조회하여
    LLM 없이 즉시 답변을 생성합니다.

    매칭되는 학과/부서가 없으면 빈 문자열 반환 → 일반 RAG 파이프라인으로 fallback.
    """
    searcher = get_dept_searcher()
    if not searcher.is_contact_query(question):
        return ""

    results = searcher.search(question, top_k=3)
    if not results:
        return ""

    lines = ["📞 **연락처 안내**\n"]
    for r in results:
        college_info = f" ({r.college})" if r.college else ""
        office_info = f" | 사무실: {r.office}" if r.office else ""
        lines.append(
            f"- **{r.name}**{college_info}: "
            f"`내선 {r.extension}` / {r.phone}{office_info}"
        )

    return "\n".join(lines)


def _get_contact_footer(intent, entities: dict, question: str) -> str:
    """
    답변 마지막에 붙일 연락처 꼬리말을 반환합니다.
    - 학과별 졸업시험/과 행사 질문 → 해당 학과 사무실 번호
    - 학사 일반 질문 → 학사지원팀 (051-509-5182)
    """
    from app.models import Intent

    # 학과별 졸업시험 / 과 행사 → 해당 학과 사무실
    _DEPT_KW = ("졸업시험", "과 행사", "학과 행사", "과행사", "학과행사")
    if any(kw in question for kw in _DEPT_KW):
        dept = entities.get("department", "")
        if dept:
            results = get_dept_searcher().search(dept, top_k=1)
            if results:
                r = results[0]
                return f"\n\n---\n📞 **{r.name}** 문의: `{r.phone}`"

    # 학사 일반 질문 → 학사지원팀 (departments.json에서 동적 조회)
    _ACADEMIC = {
        Intent.GRADUATION_REQ, Intent.EARLY_GRADUATION,
        Intent.REGISTRATION, Intent.SCHEDULE,
        Intent.COURSE_INFO, Intent.MAJOR_CHANGE,
        Intent.ALTERNATIVE,
    }
    if intent in _ACADEMIC:
        haksa = get_dept_searcher().search("학사지원팀", top_k=1)
        if haksa:
            return f"\n\n---\n📞 학사 문의: **{haksa[0].name}** `{haksa[0].phone}`"

    return ""


@st.cache_data(show_spinner=False)
def _render_pdf_page(source_file: str, page_num: int, chunk_text: str, _v: int = 2) -> bytes | None:
    """PDF 지정 페이지를 렌더링하고 chunk_text 위치를 노란색 하이라이트합니다."""
    try:
        import fitz  # PyMuPDF

        # 경로 resolve: __file__을 절대경로로 확정한 뒤 project root 산출
        path = Path(source_file)
        if not path.is_absolute():
            project_root = Path(__file__).resolve().parent.parent.parent
            path = (project_root / source_file).resolve()
        if not path.exists():
            logger.warning("PDF 파일 없음: %s", path)
            return None

        doc = fitz.open(str(path))
        page_idx = max(0, page_num - 1)  # 1-indexed → 0-indexed
        if page_idx >= len(doc):
            return None
        page = doc[page_idx]

        # ── 하이라이트 (실패해도 페이지 렌더링은 계속) ────────────
        # 문자 기반 슬라이딩 윈도우: 한국어 텍스트에 최적화
        try:
            import re as _re
            # 1. 메타 접두사·특수문자 제거
            clean = _re.sub(r"\[공지\]\s*", "", chunk_text)
            clean = _re.sub(r"[|│┃]", " ", clean)
            clean = _re.sub(r"\s+", " ", clean).strip()

            # 2. 문장 단위 분리 (마침표·줄바꿈 기준)
            sentences = _re.split(r"[.\n]+", clean)

            highlighted: set = set()
            for sent in sentences:
                sent = sent.strip()
                if len(sent) < 8:
                    continue
                # 짧은 문장은 그대로, 긴 문장은 15자 슬라이딩 윈도우
                if len(sent) <= 30:
                    fragments = [sent]
                else:
                    fragments = [sent[j:j + 15] for j in range(0, len(sent) - 14, 10)]

                for frag in fragments:
                    if frag in highlighted:
                        continue
                    rects = page.search_for(frag)
                    for rect in rects:
                        h = page.add_highlight_annot(rect)
                        h.set_colors(stroke=(1, 0.9, 0))  # 노란색
                        h.update()
                    if rects:
                        highlighted.add(frag)
        except Exception as e:
            logger.debug("하이라이트 실패 (렌더링 계속): %s", e)

        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        return pix.tobytes("png")
    except Exception as e:
        logger.warning("PDF 렌더링 실패 (%s p.%d): %s", source_file, page_num, e)
        return None


def _render_source_panel(results: list) -> None:
    """답변 근거 문서(PDF 페이지 또는 공지 카드)를 expander로 표시합니다."""
    if not results:
        return

    # 출처별로 그룹화 (PDF: source:page, 공지: source_url), 최대 3개
    seen: set = set()
    items: list = []
    for r in results:
        doc_type = r.metadata.get("doc_type", "")
        if doc_type in ("notice", "notice_attachment"):
            key = r.metadata.get("source_url", "") or r.metadata.get("source_notice_url", "")
            if key and key not in seen:
                seen.add(key)
                items.append(("notice", r))
        elif r.source and r.source != "graph" and r.page_number:
            key = f"{r.source}:{r.page_number}"
            if key not in seen:
                seen.add(key)
                items.append(("pdf", r))
        if len(items) >= 3:
            break

    if not items:
        return

    with st.expander("📄 근거 문서 확인", expanded=False):
        for kind, r in items:
            if kind == "pdf":
                page_img = _render_pdf_page(r.source, r.page_number, r.text)
                fname = Path(r.source).name
                st.caption(f"📑 **{fname}** — {r.page_number}페이지")
                if page_img:
                    st.image(page_img, use_container_width=True)
                else:
                    st.markdown(r.text[:300])
            else:
                title = r.metadata.get("title", "공지사항")
                url   = r.metadata.get("source_url", "") or r.metadata.get("source_notice_url", "")
                date  = r.metadata.get("post_date", "")
                st.markdown(
                    f'**{title}**'
                    + (f' <span style="color:#64748b;font-size:0.85em">({date})</span>' if date else ""),
                    unsafe_allow_html=True,
                )
                st.caption(r.text[:200] + ("..." if len(r.text) > 200 else ""))
                if url:
                    st.markdown(f"[원문 보기 →]({url})")
                st.divider()


def _render_source_urls(source_urls: list) -> None:
    """
    답변 아래에 관련 공지사항 출처 링크를 표시합니다.

    공지 doc_type 청크가 컨텍스트에 포함된 경우에만 호출됩니다.
    """
    lines = []
    for item in source_urls:
        title = item.get("title", "공지 원문")
        url   = item.get("url", "")
        if url:
            lines.append(f"- [{title}]({url})")
    if lines:
        st.caption("📌 **관련 공지**\n" + "\n".join(lines))


async def generate_response(question: str) -> str:
    analyzer  = st.session_state.analyzer
    router    = st.session_state.router
    merger    = st.session_state.merger
    generator = st.session_state.generator
    validator = st.session_state.validator

    analysis       = analyzer.analyze(question)
    search_results = router.route_and_search(question, analysis)
    merged         = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
        question=question,
        intent=analysis.intent,
    )

    if not merged.formatted_context.strip():
        return (
            "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.\n\n"
            "다음을 확인해 주세요:\n"
            "- PDF 학사 안내 자료가 등록되어 있는지\n"
            "- 질문에 학번을 포함했는지 (예: 2023학번)"
        )

    if merged.direct_answer:
        return merged.direct_answer

    answer = await generator.generate_full(
        question=question,
        context=merged.formatted_context,
        student_id=analysis.student_id,
        question_focus=analysis.entities.get("question_focus"),
    )

    all_results = search_results["vector_results"] + search_results["graph_results"]
    passed, warnings = validator.validate(
        answer=answer,
        context=merged.formatted_context,
        search_results=all_results,
    )
    if warnings:
        warning_text = "\n".join(f"- {w}" for w in warnings)
        answer += f"\n\n---\n*검증 경고:*\n{warning_text}"

    return answer


async def generate_response_stream(question: str, placeholder) -> str:
    # 처리 시작 즉시 애니메이션 표시 → 첫 토큰 도착 시 자동 대체됨
    placeholder.markdown(THINKING_HTML, unsafe_allow_html=True)
    _t0 = time.monotonic()

    # ── 연락처 쿼리 단락 처리 (LLM 없이 즉시 응답) ───────────────
    contact_answer = _format_contact_answer(question)
    if contact_answer:
        placeholder.markdown(contact_answer)
        try:
            st.session_state.chat_logger.log(
                question=question,
                answer=contact_answer,
                session_id=st.session_state.get("session_id", ""),
                intent="CONTACT",
                student_id=None,
                duration_ms=int((time.monotonic() - _t0) * 1000),
            )
        except Exception:
            pass
        return contact_answer, [], []

    analyzer  = st.session_state.analyzer
    router    = st.session_state.router
    merger    = st.session_state.merger
    generator = st.session_state.generator
    validator = st.session_state.validator

    analysis = analyzer.analyze(question)

    # ── 사용자 프로필 폴백 주입 ─────────────────────────────────────
    # 질문에 학번·학과·학생유형이 명시되지 않은 경우 프로필 값으로 보완
    _profile = st.session_state.get("user_profile") or {}
    if analysis.student_id is None and _profile.get("student_id"):
        analysis.student_id = _profile["student_id"]
        if "student_id" in analysis.missing_info:
            analysis.missing_info.remove("student_id")
    if not analysis.entities.get("department") and _profile.get("department"):
        analysis.entities["department"] = _profile["department"]
    if _profile.get("student_type") and _profile["student_type"] != "내국인":
        analysis.student_type = _profile["student_type"]

    search_results = router.route_and_search(question, analysis)
    merged         = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
        question=question,
        intent=analysis.intent,
    )

    def _log(answer: str) -> None:
        """Q&A 쌍을 로그 파일에 기록 (실패해도 메인 기능에 영향 없음)"""
        try:
            st.session_state.chat_logger.log(
                question=question,
                answer=answer,
                session_id=st.session_state.get("session_id", ""),
                intent=analysis.intent.name if analysis.intent else "",
                student_id=analysis.student_id,
                duration_ms=int((time.monotonic() - _t0) * 1000),
            )
        except Exception:
            pass

    if not merged.formatted_context.strip():
        msg = (
            "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.\n\n"
            "다음을 확인해 주세요:\n"
            "- PDF 학사 안내 자료가 등록되어 있는지\n"
            "- 질문에 학번을 포함했는지 (예: 2023학번)"
        )
        placeholder.markdown(msg)
        _log(msg)
        return msg, [], []

    if merged.direct_answer:
        placeholder.markdown(merged.direct_answer)
        _log(merged.direct_answer)
        return merged.direct_answer, merged.source_urls, merged.vector_results + merged.graph_results

    full_answer = ""
    async for token in generator.generate(
        question=question,
        context=merged.formatted_context,
        student_id=analysis.student_id,
        question_focus=analysis.entities.get("question_focus"),
    ):
        if token == "\x00CLEAR\x00":
            full_answer = ""
            continue
        full_answer += token
        placeholder.markdown(full_answer + "▌")

    placeholder.markdown(full_answer)

    all_results = search_results["vector_results"] + search_results["graph_results"]
    passed, warnings = validator.validate(
        answer=full_answer,
        context=merged.formatted_context,
        search_results=all_results,
    )
    if warnings:
        warning_text = "\n".join(f"- {w}" for w in warnings)
        full_answer += f"\n\n---\n*검증 경고:*\n{warning_text}"
        placeholder.markdown(full_answer)

    # 연락처 꼬리말 추가 (학사 질문 → 학사지원팀 / 학과 졸업시험·과행사 → 학과 사무실)
    footer = _get_contact_footer(analysis.intent, analysis.entities, question)
    if footer:
        full_answer += footer
        placeholder.markdown(full_answer)

    _log(full_answer)
    return full_answer, merged.source_urls, merged.vector_results + merged.graph_results


# ── Main ───────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="캠챗 - 부산외대 학사챗봇",
        page_icon="🎓",
        layout="wide",
    )

    # 크롤링 스케줄러 싱글톤 시작 (CRAWLER_ENABLED=false면 no-op)
    get_scheduler()

    inject_custom_css()

    if not render_sidebar():
        return

    # ── 온보딩 게이트: 프로필 미설정 시 입력 화면 표시 ────────────
    if "user_profile" not in st.session_state:
        render_onboarding()
        return

    pending    = st.session_state.pop("pending_question", None)
    user_input = st.chat_input("이번 학기 수강신청 일정 알려줘")
    prompt     = pending or user_input

    # ── 3-column: main chat | right panel ──────────
    chat_col, right_col = st.columns([4, 1.25])

    with chat_col:
        render_chat_header()

        messages = st.session_state.messages
        for idx, msg in enumerate(messages):
            with st.chat_message(
                msg["role"],
                avatar="🎓" if msg["role"] == "assistant" else "👤",
            ):
                st.markdown(msg["content"])
                if msg["role"] == "assistant":
                    if msg.get("source_urls"):
                        _render_source_urls(msg["source_urls"])
                    _render_source_panel(msg.get("results", []))

            # 모든 어시스턴트 메시지 아래 별점 UI 표시
            # (스트리밍 중에는 prompt가 있으므로 숨김 → rerun 충돌 방지)
            if msg["role"] == "assistant" and prompt is None:
                _render_rating(idx, msg)

        if not messages and prompt is None:
            render_welcome_screen()

        if prompt:
            messages.append({"role": "user", "content": prompt})
            with st.chat_message("user", avatar="👤"):
                st.markdown(prompt)
            with st.chat_message("assistant", avatar="🎓"):
                placeholder = st.empty()
                answer, source_urls, results = asyncio.run(
                    generate_response_stream(prompt, placeholder)
                )
                if source_urls:
                    _render_source_urls(source_urls)
                _render_source_panel(results)
                messages.append(
                    {"role": "assistant", "content": answer, "rated": False,
                     "source_urls": source_urls, "results": results}
                )

    with right_col:
        render_right_panel()


if __name__ == "__main__":
    main()
