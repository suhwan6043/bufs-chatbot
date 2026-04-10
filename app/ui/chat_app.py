"""
캠챗 - 부산외국어대학교 학사 안내 AI 챗봇
"""

import asyncio
import logging
import re
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
from app.ui.i18n import t, STYPE_EN_TO_KO

logger = logging.getLogger(__name__)

# ── Persistent event loop ─────────────────────────
# asyncio.run()을 매 호출마다 쓰면 Python 3.12에서 Runner.close() →
# shutdown_default_executor()가 새 스레드를 spawn하는데, Streamlit의
# StopException 전파 중에는 스레드 생성이 실패(RuntimeError: can't create
# new thread at interpreter shutdown)한다. 루프 1개를 캐시·비폐쇄 유지해
# 해당 경로를 회피한다.
_event_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """Streamlit 스크립트 스레드에서 coroutine 실행.

    기본 경로: 캐시된 영속 루프에 run_until_complete (30-33번 줄 주석 참고).
    재진입 방어: 루프가 이미 running이면 (Streamlit 내부 async 혹은 이전 호출이
    클린업 전에 Streamlit StopException으로 중단된 상태) 전용 워커 스레드에서
    새 루프로 실행해 "This event loop is already running" 충돌을 피한다.
    """
    global _event_loop
    if _event_loop is None or _event_loop.is_closed():
        _event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_event_loop)

    if _event_loop.is_running():
        # Fallback: 별도 스레드 + 독립 루프. 캐시 루프는 건드리지 않는다.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result()

    return _event_loop.run_until_complete(coro)


# ── Brand ──────────────────────────────────────────
APP_VERSION  = "0.2.0"
LOGO_PATH    = Path(__file__).parent / "static" / "logo.png"


def _get_quick_features() -> list:
    """언어별 빠른 기능 목록."""
    return [
        {"label": t("qf.register"),  "question": t("qf.register_q")},
        {"label": t("qf.grades"),    "question": t("qf.grades_q")},
        {"label": t("qf.schedule"),  "question": t("qf.schedule_q")},
        {"label": t("qf.faq"),       "question": t("qf.faq_q")},
    ]


def _get_personal_base() -> list:
    """언어별 개인화 빠른 기능 기본 목록."""
    return [
        {"label": t("qf.shortage"),   "question": t("qf.shortage_q")},
        {"label": t("qf.retake"),     "question": t("qf.retake_q")},
        {"label": t("qf.semester"),   "question": t("qf.semester_q")},
        {"label": t("qf.graduation"), "question": t("qf.graduation_q")},
    ]


def _build_personal_quick_features(transcript) -> list:
    """
    성적표 기반 동적 Quick Features 생성.

    원칙 1 (유연한 스키마): 학생의 실제 데이터(복수전공/부족학점/수강중 과목)에 따라
    버튼 구성이 자동으로 진화. 복수전공 없는 학생에게는 복수전공 버튼 미표시.
    """
    features = list(_get_personal_base())

    if transcript is None:
        return features

    p = transcript.profile
    c = transcript.credits

    # 복수전공 있는 학생만 복수전공 버튼 추가
    if p.복수전공:
        features.append({
            "label": t("qf.dual"),
            "question": t("qf.dual_q"),
        })

    # 부족학점 있는 학생만 수강 가능 학점 버튼 추가
    if c.총_부족학점 > 0:
        features.append({
            "label": t("qf.reg_limit"),
            "question": t("qf.reg_limit_q"),
        })

    # 최대 6개까지 (2열 × 3행)
    return features[:6]

def _get_portal_links() -> list:
    """언어별 포탈 바로가기 링크."""
    return [
        {"icon": "🖥️", "label": t("portal.register"), "url": "https://sugang.bufs.ac.kr/Login.aspx"},
        {"icon": "📊", "label": t("portal.student"),   "url": "https://m.bufs.ac.kr/default.aspx?ReturnUrl=%2f"},
        {"icon": "📅", "label": t("portal.calendar"),  "url": "https://m.bufs.ac.kr/popup/Haksa_Iljeong.aspx?gbn="},
        {"icon": "📢", "label": t("portal.notices"),   "url": "https://www.bufs.ac.kr/bbs/board.php?bo_table=notice_aca"},
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
_THINKING_TEMPLATE = """
<style>
@keyframes _bkFlt {{
    0%,100% {{ transform: translateY(0px) rotate(-4deg); }}
    25%     {{ transform: translateY(-8px) rotate(0deg); }}
    50%     {{ transform: translateY(-12px) rotate(4deg); }}
    75%     {{ transform: translateY(-6px) rotate(0deg); }}
}}
@keyframes _pgTrn {{
    0%,100% {{ transform: scaleX(1);  opacity: 1;   }}
    45%,55% {{ transform: scaleX(0);  opacity: 0.3; }}
}}
@keyframes _dtPop {{
    0%,80%,100% {{ transform: scale(0.5); opacity: 0.25; }}
    40%         {{ transform: scale(1.1); opacity: 1;    }}
}}
._cam-ld {{ display:flex; align-items:center; gap:16px; padding:8px 2px; }}
._cam-book-wrap {{
    position: relative; width: 44px; height: 44px;
    display: flex; align-items: center; justify-content: center;
}}
._cam-book {{
    font-size: 2.4rem; display: inline-block;
    animation: _bkFlt 1.8s ease-in-out infinite;
    filter: drop-shadow(0 4px 8px rgba(79,70,229,0.25));
}}
._cam-page {{
    position: absolute; right: 4px; top: 10px;
    width: 10px; height: 20px;
    background: rgba(79,70,229,0.18);
    border-radius: 0 3px 3px 0;
    animation: _pgTrn 1.8s ease-in-out infinite;
    transform-origin: left center;
}}
._cam-info {{ display:flex; flex-direction:column; gap:7px; }}
._cam-lbl {{
    font-size: 0.86rem; color: #374151;
    font-family: 'Noto Sans KR', sans-serif;
    font-weight: 500; letter-spacing: -0.01em;
}}
._cam-sub {{
    font-size: 0.74rem; color: #9ca3af;
    font-family: 'Noto Sans KR', sans-serif;
    margin-top: -4px;
}}
._cam-dots {{ display:flex; gap:5px; align-items:center; }}
._cam-dot {{
    width: 7px; height: 7px; border-radius: 50%;
    background: #4f46e5;
    animation: _dtPop 1.2s infinite ease-in-out;
}}
._cam-dot:nth-child(2) {{ animation-delay: 0.22s; }}
._cam-dot:nth-child(3) {{ animation-delay: 0.44s; }}
</style>
<div class="_cam-ld">
  <div class="_cam-book-wrap">
    <span class="_cam-book">📖</span>
    <div class="_cam-page"></div>
  </div>
  <div class="_cam-info">
    <div class="_cam-lbl">{lbl}</div>
    <div class="_cam-sub">{sub}</div>
    <div class="_cam-dots">
      <div class="_cam-dot"></div>
      <div class="_cam-dot"></div>
      <div class="_cam-dot"></div>
    </div>
  </div>
</div>
"""


def _thinking_html() -> str:
    return _THINKING_TEMPLATE.format(
        lbl=t("loading.generating"),
        sub=t("loading.analyzing"),
    )


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


# ── Language selection ─────────────────────────────
def render_language_selection() -> None:
    """언어 선택 히어로 페이지 — 온보딩 이전에 1회 표시."""
    inject_custom_css()
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.markdown(
            '<div style="text-align:center;padding:4rem 0 1.5rem;">'
            '<div style="font-size:3.2rem;">🎓</div>'
            '<div style="font-size:1.35rem;font-weight:700;color:#1e3a5f;margin:0.7rem 0 0.4rem;">'
            'BUFS Academic Guide AI</div>'
            '<div style="font-size:0.88rem;color:#64748b;line-height:1.6;">'
            '언어를 선택해주세요 / Please select your language</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height:0.5rem;"></div>', unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("🇰🇷  한국어로 시작하기", key="lang_ko", use_container_width=True, type="primary"):
                st.session_state.ui_lang = "ko"
                st.rerun()
        with c2:
            if st.button("🇺🇸  Start in English", key="lang_en", use_container_width=True, type="primary"):
                st.session_state.ui_lang = "en"
                st.rerun()


# ── Onboarding ─────────────────────────────────────
def render_onboarding() -> None:
    """최초 접속 시 표시하는 사용자 프로필 설정 화면."""
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.markdown(
            '<div style="text-align:center;padding:2.5rem 0 1.2rem;">'
            '<div style="font-size:2.8rem;">🎓</div>'
            '<div style="font-size:1.25rem;font-weight:700;color:#1e3a5f;margin:0.5rem 0 0.3rem;">'
            f'{t("onboard.welcome")}</div>'
            '<div style="font-size:0.84rem;color:#64748b;line-height:1.6;">'
            f'{t("onboard.desc")}</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        stype_options = [
            t("onboard.type_domestic"),
            t("onboard.type_intl"),
            t("onboard.type_transfer"),
        ]

        with st.form("onboarding_form", border=True):
            year = st.selectbox(
                t("onboard.year_label"),
                options=list(range(2026, 2015, -1)),
                format_func=lambda y: t("onboard.year_fmt", y=y),
                index=3,
            )
            dept = st.selectbox(
                t("onboard.dept_label"),
                options=[t("onboard.dept_none")] + sorted(DEPARTMENTS),
            )
            stype = st.radio(
                t("onboard.type_label"),
                options=stype_options,
                horizontal=True,
            )

            st.divider()
            st.markdown(
                '<p style="font-size:0.78rem;color:#64748b;line-height:1.65;margin:0 0 0.3rem;">'
                f'{t("onboard.disclaimer")}</p>',
                unsafe_allow_html=True,
            )
            disclaimer_checked = st.checkbox(
                t("onboard.agree"),
                value=False,
                key="disclaimer_check",
            )

            c_start, c_skip = st.columns([3, 2])
            with c_start:
                submitted = st.form_submit_button(
                    t("onboard.btn_start"), use_container_width=True, type="primary"
                )
            with c_skip:
                skipped = st.form_submit_button(
                    t("onboard.btn_skip"), use_container_width=True
                )

        if submitted or skipped:
            if not disclaimer_checked:
                st.warning(t("onboard.warn_agree"), icon="⚠️")
            elif submitted:
                # student_type은 항상 한국어로 저장 (파이프라인 호환)
                stype_ko = STYPE_EN_TO_KO.get(stype, stype)
                st.session_state.user_profile = {
                    "student_id": str(year),
                    "department": dept if dept != t("onboard.dept_none") else "",
                    "student_type": stype_ko,
                }
                st.rerun()
            else:  # skipped
                st.session_state.user_profile = {}
                st.rerun()


# ── Transcript upload (보안 중심) ─────────────────────
def _render_transcript_upload() -> None:
    """사이드바: 성적 사정표 업로드 (개인정보 동의 기반)."""
    from app.transcript.security import SecureTranscriptStore, PIIRedactor

    st.markdown(
        '<p style="font-size:0.7rem;font-weight:700;color:#94a3b8;'
        'text-transform:uppercase;letter-spacing:0.5px;margin:0.2rem 0 0.4rem;">'
        f'{t("sidebar.transcript")}</p>',
        unsafe_allow_html=True,
    )

    transcript = SecureTranscriptStore.retrieve(st.session_state)

    if transcript:
        # 등록된 성적표 상태 표시 (이름은 store() 시점에 삭제됨, _masked_name 사용)
        masked = getattr(transcript, "_masked_name", t("status.registered"))
        c = transcript.credits
        p = transcript.profile

        # 진행률 계산 (0~100%)
        progress_pct = 0
        if c.총_졸업기준 > 0:
            progress_pct = min(100, int((c.총_취득학점 / c.총_졸업기준) * 100))

        # 부족학점 강조 (있으면 경고색, 없으면 녹색)
        shortage_block = ""
        if c.총_부족학점 > 0:
            shortage_block = (
                f'<div style="font-size:0.72rem;color:#b45309;margin-top:0.3rem;'
                f'padding:0.2rem 0.4rem;background:#fef3c7;border-radius:4px;">'
                f'{t("sidebar.shortage", n=c.총_부족학점)}</div>'
            )
        else:
            shortage_block = (
                f'<div style="font-size:0.72rem;color:#15803d;margin-top:0.3rem;'
                f'padding:0.2rem 0.4rem;background:#dcfce7;border-radius:4px;">'
                f'{t("sidebar.req_met")}</div>'
            )

        # 복수전공 정보 (있을 때만)
        dual_block = ""
        if p.복수전공:
            # 복수전공 부족학점 추출
            dual_shortage = 0
            for cat in c.categories:
                if "복수전공" in cat.name or "다전공" in cat.name:
                    dual_shortage = cat.부족학점
                    break
            dual_status = t("sidebar.dual_remain", n=dual_shortage) if dual_shortage > 0 else t("sidebar.dual_met")
            dual_block = (
                f'<div style="font-size:0.7rem;color:#475569;margin-top:0.2rem;">'
                f'🎯 복수전공: {dual_status}</div>'
            )

        st.markdown(
            f'<div style="padding:0.55rem 0.65rem;border-radius:8px;'
            f'background:#f0fdf4;border:1px solid #bbf7d0;margin-bottom:0.3rem;">'
            f'<div style="display:flex;align-items:baseline;justify-content:space-between;">'
            f'<span style="font-size:0.85rem;font-weight:700;color:#166534;">{masked}</span>'
            f'<span style="font-size:0.72rem;color:#22c55e;">{t("sidebar.gpa", v=c.평점평균)}</span>'
            f'</div>'
            # 진행률 바
            f'<div style="margin-top:0.35rem;height:6px;background:#dcfce7;border-radius:3px;overflow:hidden;">'
            f'<div style="height:100%;width:{progress_pct}%;background:linear-gradient(90deg,#22c55e,#16a34a);"></div>'
            f'</div>'
            f'<div style="font-size:0.7rem;color:#15803d;margin-top:0.2rem;">'
            f'{t("sidebar.credits_fmt", done=c.총_취득학점, total=c.총_졸업기준, pct=progress_pct)}</div>'
            f'{shortage_block}'
            f'{dual_block}'
            f'</div>',
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button(t("sidebar.btn_refresh"), key="refresh_transcript", use_container_width=True):
                SecureTranscriptStore.destroy(st.session_state)
                st.rerun()
        with c2:
            if st.button(t("sidebar.btn_delete"), key="delete_transcript", use_container_width=True):
                SecureTranscriptStore.destroy(st.session_state)
                st.rerun()

        remaining = SecureTranscriptStore.remaining_seconds(st.session_state)
        st.caption(t("sidebar.auto_delete", m=remaining // 60))
        return

    # ── 업로드 전 개인정보 동의 ──
    st.markdown(
        '<div style="font-size:0.72rem;color:#64748b;line-height:1.5;'
        'padding:0.45rem;background:#fef3c7;border-radius:6px;border:1px solid #fbbf24;">'
        f'{t("sidebar.privacy_html")}</div>',
        unsafe_allow_html=True,
    )

    consent = st.checkbox(
        t("sidebar.consent"),
        key="transcript_consent_cb",
    )

    if consent:
        # 동의 상태를 SecureTranscriptStore에 기록
        if not SecureTranscriptStore.has_consent(st.session_state):
            session_id = st.session_state.get("session_id", "")
            SecureTranscriptStore.grant_consent(st.session_state, session_id)

        uploaded = st.file_uploader(
            t("sidebar.upload_label"),
            type=["xls", "pdf", "doc", "docx", "ppt", "pptx", "hwp",
                  "png", "jpg", "jpeg", "bmp", "gif"],
            key="transcript_upload",
            label_visibility="collapsed",
            help=t("sidebar.upload_help"),
        )
        if uploaded:
            _handle_transcript_upload(uploaded)
    else:
        # 동의 해제 시 데이터 즉시 파기
        if SecureTranscriptStore.has_consent(st.session_state):
            SecureTranscriptStore.revoke_consent(st.session_state)
            st.rerun()


def _handle_transcript_upload(uploaded_file) -> None:
    """성적표 보안 업로드 핸들러."""
    from app.transcript import TranscriptParser, TranscriptVersionManager
    from app.transcript.security import (
        SecureTranscriptStore,
        UploadValidator,
        PIIRedactor,
        audit_log,
    )

    file_bytes = uploaded_file.read()
    session_id = st.session_state.get("session_id", "")

    # 1) 파일 보안 검증 (원본 파일명으로 경로 순회 차단)
    ok, err = UploadValidator.validate(file_bytes, uploaded_file.name)
    safe_filename = UploadValidator.sanitize_filename(uploaded_file.name)
    if not ok:
        audit_log("UPLOAD_REJECTED", session_id, err)
        st.error(t("error.file_validation", e=err))
        return

    # 2) 파싱 (성명은 여기서만 사용 후 store()에서 삭제됨)
    try:
        parser = TranscriptParser()
        profile = parser.parse(file_bytes, safe_filename)
    except ModuleNotFoundError as e:
        # 서버 설정 문제: 의존성 누락 (예: xlrd). 사용자 파일 문제 아님.
        audit_log("PARSE_MODULE_MISSING", session_id, e.name or "")
        st.error(t("error.parse_module", lib=e.name or ""))
        logger.error("성적표 파싱 의존성 누락: %s", e)
        return
    except ValueError as e:
        # 파일 포맷/구조 문제 (예: HTML에 <table> 없음, 데이터 부족)
        audit_log("PARSE_INVALID", session_id, type(e).__name__)
        st.error(t("error.parse_invalid", e=e))
        logger.warning("성적표 포맷 오류: %s", e)
        return
    except Exception as e:
        audit_log("PARSE_FAILED", session_id, type(e).__name__)
        st.error(t("error.parse_failed"))
        logger.exception("성적표 파싱 실패")
        return
    finally:
        del file_bytes  # 원본 바이트 즉시 폐기

    # 3) 마스킹된 이름 먼저 추출 (store()에서 원본 삭제되기 전에)
    masked = PIIRedactor.mask_name(profile.profile.성명)

    # 4) 버전 비교
    old = SecureTranscriptStore.retrieve(st.session_state)
    if old:
        diff = TranscriptVersionManager.detect_diff(old, profile)
        if diff:
            st.info(t("status.diff_detected", n=len(diff)))

    # 5) 보안 저장 (⚠️ store()에서 성명/학번 원본 즉시 삭제됨)
    SecureTranscriptStore.store(st.session_state, profile, session_id)
    TranscriptVersionManager.store_snapshot(profile, st.session_state)

    # 6) user_profile 자동 갱신 (입학연도만 사용, 학번 미포함)
    st.session_state.user_profile = {
        "student_id": profile.profile.입학연도,
        "department": profile.profile.전공 or profile.profile.학부과,
        "student_type": profile.profile.student_type or "내국인",
    }

    st.success(t("status.upload_ok", name=masked))
    st.rerun()


# ── Profile sidebar card ────────────────────────────
def _render_profile_sidebar() -> None:
    """사이드바 내 현재 사용자 프로필 표시 + 수정 버튼."""
    profile = st.session_state.get("user_profile") or {}

    st.markdown(
        '<p style="font-size:0.7rem;font-weight:700;color:#94a3b8;'
        f'text-transform:uppercase;letter-spacing:0.5px;margin:0.2rem 0 0.4rem;">{t("sidebar.my_info")}</p>',
        unsafe_allow_html=True,
    )

    if profile.get("student_id"):
        dept_txt  = profile.get("department") or t("sidebar.dept_unset")
        stype_txt = profile.get("student_type", "내국인")
        year_display = f'{profile["student_id"]}{t("sidebar.year_suffix")}'
        st.markdown(
            f'<div style="padding:0.5rem 0.65rem;border-radius:7px;'
            f'background:#eef2ff;border:1px solid #c7d2fe;margin-bottom:0.3rem;">'
            f'<span style="font-size:0.85rem;font-weight:700;color:#3730a3;">'
            f'{year_display}</span>'
            f'<span style="font-size:0.77rem;color:#4f46e5;margin-left:0.45rem;">'
            f'{dept_txt}</span>'
            f'<span style="font-size:0.73rem;color:#818cf8;margin-left:0.3rem;">· {stype_txt}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="font-size:0.79rem;color:#94a3b8;padding:0.2rem 0 0.3rem;">'
            f'{t("sidebar.info_unset")}</div>',
            unsafe_allow_html=True,
        )

    if st.button(t("sidebar.edit_profile"), key="edit_profile_btn", use_container_width=True):
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
                f'font-size:1.5rem;color:white;font-weight:700;margin-bottom:0.1rem;">{t("brand.logo_fallback")}</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div style="margin:0.45rem 0 0.2rem;">'
            f'  <span style="font-size:1.15rem;font-weight:700;color:#1e293b;">{t("brand.app_name")}</span>'
            f'</div>'
            f'<div style="font-size:0.76rem;color:#64748b;margin-bottom:0.3rem;">{t("brand.subtitle")}</div>',
            unsafe_allow_html=True,
        )

        st.divider()

        # ── Silent init ───────────────────────────
        try:
            init_components()
        except Exception:
            st.error(t("sidebar.init_error"))
            return False

        # ── 내 정보 ────────────────────────────────
        _render_profile_sidebar()

        st.divider()

        # ── 성적 사정표 ──────────────────────────────
        _render_transcript_upload()

        st.divider()

        # ── 빠른 기능 (성적표 있으면 개인화 버튼으로 전환) ──
        from app.transcript.security import SecureTranscriptStore
        _tx = SecureTranscriptStore.retrieve(st.session_state)
        _features = _build_personal_quick_features(_tx) if _tx else _get_quick_features()
        _label = t("sidebar.qf_personal") if _tx else t("sidebar.qf_label")

        st.markdown(
            f'<p style="font-size:0.7rem;font-weight:700;color:#94a3b8;'
            f'text-transform:uppercase;letter-spacing:0.5px;margin:0.2rem 0 0.5rem;">{_label}</p>',
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        for i, feat in enumerate(_features):
            with (c1 if i % 2 == 0 else c2):
                if st.button(feat["label"], key=f"qf_{i}", use_container_width=True):
                    st.session_state.pending_question = feat["question"]
                    st.rerun()

        st.divider()

        # ── 대화 ──────────────────────────────────
        st.markdown(
            '<p style="font-size:0.7rem;font-weight:700;color:#94a3b8;'
            f'text-transform:uppercase;letter-spacing:0.5px;margin:0.2rem 0 0.4rem;">{t("sidebar.chat")}</p>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="padding:0.45rem 0.6rem;border-radius:7px;'
            'background:#eef2ff;color:#4f46e5 !important;'
            f'font-size:0.85rem;font-weight:600;margin-bottom:0.2rem;">{t("sidebar.all_chats")}</div>',
            unsafe_allow_html=True,
        )

        if st.button(t("sidebar.clear_chat"), key="clr", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        # ── LLM 서버 경고 (에러 시에만) ────────────
        # generator를 메인 스레드에서 먼저 캡처 (스레드 내 session_state 접근 불가)
        _gen = st.session_state.get("generator")
        if _gen is not None:
            async def _chk():
                return await _gen.health_check()

            if not _run_async(_chk()):
                st.markdown(
                    '<div style="margin-top:0.6rem;padding:0.5rem 0.6rem;border-radius:7px;'
                    'background:#fef3c7;border:1px solid #fcd34d;font-size:0.78rem;color:#92400e;">'
                    f'{t("sidebar.server_warn")}<br><span style="font-size:0.72rem;">{t("sidebar.server_hint")}</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )

        # ── Version footer ────────────────────────
        st.markdown(
            f'<div style="position:absolute;bottom:0.75rem;left:1rem;right:1rem;'
            f'font-size:0.7rem;color:#cbd5e1;border-top:1px solid #e2e8f0;padding-top:0.6rem;">'
            f'{t("brand.version", v=APP_VERSION)}</div>',
            unsafe_allow_html=True,
        )

    return True


# ── Chat header ────────────────────────────────────
def render_chat_header():
    """Chat 헤더. 성적표 업로드 상태에 따라 배지가 전환됨."""
    from app.transcript.security import SecureTranscriptStore
    transcript = SecureTranscriptStore.retrieve(st.session_state)

    if transcript:
        badge_html = (
            '<div class="chat-hdr-badge" style="background:#ecfdf5;color:#047857;'
            f'border:1px solid #a7f3d0;">{t("chat.badge_transcript")}</div>'
        )
    else:
        badge_html = (
            f'<div class="chat-hdr-badge">{t("chat.badge_default")}</div>'
        )

    st.markdown(
        '<div class="chat-hdr">'
        '  <div>'
        f'    <h2>{t("chat.header_title")}</h2>'
        f'    <p>{t("chat.header_sub")}</p>'
        '  </div>'
        f'  {badge_html}'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Welcome screen ─────────────────────────────────
def render_welcome_screen():
    """Welcome 화면. 성적표 업로드 여부에 따라 개인화 메시지/버튼 표시."""
    from app.transcript.security import SecureTranscriptStore
    transcript = SecureTranscriptStore.retrieve(st.session_state)

    if transcript:
        # 성적표 있음 — 개인화 인사 (PII 없음)
        c = transcript.credits
        p = transcript.profile

        # 상태 요약 문구 (PII 없이 학점 수치만)
        if c.총_부족학점 > 0:
            status_msg = t("welcome.credits_short", n=c.총_부족학점)
        else:
            status_msg = t("welcome.credits_met")

        major_display = p.전공 or p.학부과 or t("welcome.student_label")

        st.markdown(
            f'<div class="wc-wrap">'
            f'  <div class="wc-icon">🎓</div>'
            f'  <div class="wc-title">{t("welcome.transcript_title")}</div>'
            f'  <div class="wc-sub">'
            f'    {major_display} {p.입학연도}{t("sidebar.year_suffix")} · {t("sidebar.gpa", v=c.평점평균)}<br>'
            f'    {status_msg}'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        features = _build_personal_quick_features(transcript)
        hint_text = t("welcome.hint_personal")
    else:
        # 성적표 없음 — 기본 인사 + 업로드 유도
        st.markdown(
            '<div class="wc-wrap">'
            '  <div class="wc-icon">🎓</div>'
            f'  <div class="wc-title">{t("welcome.title")}</div>'
            f'  <div class="wc-sub">{t("welcome.desc")}'
            '  </div>'
            '</div>',
            unsafe_allow_html=True,
        )
        features = _get_quick_features()
        hint_text = t("welcome.hint_buttons")

    c1, c2 = st.columns(2)
    for i, feat in enumerate(features):
        with (c1 if i % 2 == 0 else c2):
            if st.button(
                f"{feat['label']}\n{feat['question']}",
                key=f"wc_{i}",
                use_container_width=True,
            ):
                st.session_state.pending_question = feat["question"]
                st.rerun()
    st.markdown(
        f'<div class="wc-hint">{hint_text}</div>',
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
            f'{t("rating.done")} <span style="color:#f59e0b;letter-spacing:2px;">{stars}</span>'
            f'&nbsp;({rating_value}/5)</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div style="font-size:0.78rem;color:#64748b;margin:0.3rem 0 0.4rem 0.2rem;">'
        f'{t("rating.prompt")}</div>',
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
    st.markdown(f'<div class="rp-section">{t("right.shortcuts")}</div>', unsafe_allow_html=True)
    for lnk in _get_portal_links():
        st.markdown(
            f'<a href="{lnk["url"]}" target="_blank" class="rp-link">'
            f'{lnk["icon"]} {lnk["label"]}</a>',
            unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="margin-top:0.9rem;" class="rp-section">{t("right.help")}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="rp-tip">{t("right.help_tip")}</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div style="margin-top:1.2rem;" class="rp-section">{t("right.feedback")}</div>',
        unsafe_allow_html=True,
    )
    with st.form("feedback_form", clear_on_submit=True, border=False):
        fb_text = st.text_area(
            t("right.feedback"),
            placeholder=t("right.feedback_ph"),
            height=110,
            label_visibility="collapsed",
        )
        if st.form_submit_button(t("right.feedback_submit"), use_container_width=True):
            if fb_text.strip():
                _save_feedback(fb_text.strip())
                st.success(t("right.feedback_ok"))
            else:
                st.warning(t("right.feedback_empty"))


# ── Pipeline (UNCHANGED) ───────────────────────────
def init_components():
    if "initialized" not in st.session_state:
        with st.spinner(t("sidebar.init_spinner")):
            chroma_store   = get_chroma_store()   # 공유 싱글톤 (스케줄러와 동일 인스턴스)
            academic_graph = AcademicGraph()

            from app.shared_resources import get_embedder
            st.session_state.analyzer  = QueryAnalyzer(embedder=get_embedder())
            from app.shared_resources import get_bm25_index
            bm25_index = get_bm25_index()

            st.session_state.router    = QueryRouter(
                chroma_store=chroma_store,
                academic_graph=academic_graph,
                bm25_index=bm25_index,
            )
            st.session_state.merger    = ContextMerger()
            st.session_state.generator = AnswerGenerator()
            st.session_state.validator = ResponseValidator()

            # Reranker 백그라운드 warmup — 첫 질문 cold-start(~46초) 방지
            # session_state는 스레드 접근 불가이므로 router를 local로 캡처
            import threading as _threading
            _router_ref = st.session_state.router
            def _warmup_reranker(_r=_router_ref):
                try:
                    if _r and _r.reranker:
                        _ = _r.reranker.model
                except Exception:
                    pass
            _threading.Thread(target=_warmup_reranker, name="reranker-warmup", daemon=True).start()
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

    # in_context 결과 우선, 스코어 내림차순 정렬
    in_ctx = [r for r in results if r.metadata.get("in_context")]
    source_pool = in_ctx if in_ctx else results
    source_pool = sorted(source_pool, key=lambda r: r.score, reverse=True)

    # 출처별로 그룹화 (PDF: source:page, 공지: source_url, 그래프: node_type), 최대 5개
    seen: set = set()
    items: list = []
    for r in source_pool:
        doc_type = r.metadata.get("doc_type", "")
        if doc_type in ("notice", "notice_attachment"):
            key = r.metadata.get("source_url", "") or r.metadata.get("source_notice_url", "")
            if key and key not in seen:
                seen.add(key)
                items.append(("notice", r))
        elif doc_type == "faq":
            faq_id = r.metadata.get("faq_id", "")
            key = f"faq:{faq_id}" if faq_id else f"faq:{r.text[:40]}"
            if key not in seen:
                seen.add(key)
                items.append(("faq", r))
        elif r.source and r.source != "graph" and r.page_number:
            key = f"{r.source}:{r.page_number}"
            if key not in seen:
                seen.add(key)
                items.append(("pdf", r))
        elif r.metadata.get("source_type") == "graph":
            # 그래프 결과여도 원본 PDF 출처가 있으면 PDF로 표시
            if r.source and r.source != "graph" and Path(r.source).suffix == ".pdf":
                pg = r.page_number or 1  # 페이지 없으면 1페이지 표시
                key = f"{r.source}:{pg}"
                if key not in seen:
                    seen.add(key)
                    items.append(("pdf", r))
            else:
                key = f"graph:{r.metadata.get('node_type', 'data')}:{r.text[:40]}"
                if key not in seen:
                    seen.add(key)
                    items.append(("graph", r))
        if len(items) >= 5:
            break

    if not items:
        return

    with st.expander(t("source.expander"), expanded=False):
        for kind, r in items:
            if kind == "pdf":
                page_img = _render_pdf_page(r.source, r.page_number, r.text)
                fname = Path(r.source).name
                st.caption(f"📑 **{fname}** — {t('source.page', n=r.page_number)}")
                if page_img:
                    st.image(page_img, use_container_width=True)
                else:
                    st.markdown(r.text[:300])
            elif kind == "faq":
                category = r.metadata.get("카테고리", "")
                faq_id = r.metadata.get("faq_id", "")
                q_text = r.metadata.get("faq_question", "")
                a_text = r.metadata.get("faq_answer", "")
                if not q_text and "Q:" in r.text:
                    parts = r.text.split("A:", 1)
                    q_text = parts[0].split("Q:", 1)[-1].strip()
                    a_text = parts[1].strip() if len(parts) > 1 else ""
                header = "FAQ"
                if category:
                    header += f" — {category}"
                if faq_id:
                    header += f" ({faq_id})"
                st.caption(f"📋 **{header}**")
                if q_text:
                    st.markdown(f"**Q.** {q_text}")
                if a_text:
                    st.markdown(f"**A.** {a_text}")
                st.divider()
            elif kind == "graph":
                node_type = r.metadata.get("node_type", "학사 데이터")
                st.caption(f"📊 **{node_type}** {t('source.graph_label')}")
                st.markdown(r.text[:500])
                st.divider()
            else:
                title = r.metadata.get("title", t("source.notice_title"))
                url   = r.metadata.get("source_url", "") or r.metadata.get("source_notice_url", "")
                date  = r.metadata.get("post_date", "")
                st.markdown(
                    f'**{title}**'
                    + (f' <span style="color:#64748b;font-size:0.85em">({date})</span>' if date else ""),
                    unsafe_allow_html=True,
                )
                st.caption(r.text[:200] + ("..." if len(r.text) > 200 else ""))
                if url:
                    st.markdown(f"[{t('source.view_original')}]({url})")
                st.divider()


def _render_source_urls(source_urls: list) -> None:
    """
    답변 아래에 관련 공지사항 출처 링크를 표시합니다.

    공지 doc_type 청크가 컨텍스트에 포함된 경우에만 호출됩니다.
    """
    lines = []
    for item in source_urls:
        title = item.get("title", t("source.notice_default"))
        url   = item.get("url", "")
        if url:
            lines.append(f"- [{title}]({url})")
    if lines:
        st.caption(t("source.related") + "\n" + "\n".join(lines))


def _enrich_analysis(question: str, analysis, router) -> tuple:
    """프로필 폴백 + 성적표 컨텍스트 생성 (공통 헬퍼).

    Returns:
        (analysis, transcript_context, student_context)
    """
    # ── 사용자 프로필 폴백 주입 ──
    _profile = st.session_state.get("user_profile") or {}
    if analysis.student_id is None and _profile.get("student_id"):
        analysis.student_id = _profile["student_id"]
        if "student_id" in analysis.missing_info:
            analysis.missing_info.remove("student_id")
    if not analysis.entities.get("department") and _profile.get("department"):
        analysis.entities["department"] = _profile["department"]
    if _profile.get("student_type") and _profile["student_type"] != "내국인":
        analysis.student_type = _profile["student_type"]

    # ── 성적표 기반 컨텍스트 (보안: PII 제거, lazy 계산) ──
    from app.transcript.security import SecureTranscriptStore
    transcript = SecureTranscriptStore.retrieve(st.session_state)
    transcript_context = ""
    student_context = ""

    if transcript:
        from app.transcript.analyzer import TranscriptAnalyzer
        from app.models import Intent

        tp = transcript.profile
        if analysis.student_id is None and tp.입학연도:
            analysis.student_id = tp.입학연도
        if not analysis.entities.get("department") and tp.전공:
            analysis.entities["department"] = tp.전공
        analysis.entities["has_transcript"] = True

        _TX_INTENTS = {Intent.GRADUATION_REQ, Intent.REGISTRATION, Intent.TRANSCRIPT}
        _TX_KW = ("부족", "재수강", "평점", "이번 학기", "수강 가능", "몇 학점", "내 성적", "내 학점", "졸업")

        if analysis.intent in _TX_INTENTS or any(kw in question for kw in _TX_KW):
            tx = TranscriptAnalyzer(transcript, router.academic_graph)

            if "부족" in question or "졸업" in question or analysis.intent == Intent.GRADUATION_REQ:
                transcript_context = tx.format_gap_context_safe()
            elif "재수강" in question or "평점 올" in question:
                transcript_context = tx.format_courses_context_safe(tx.retake_candidates())
            elif "이번 학기" in question or "현재 수강" in question:
                transcript_context = tx.format_courses_context_safe(tx.current_semester_courses())
            elif "수강 가능" in question or "몇 학점" in question:
                reg = tx.registration_limit()
                transcript_context = f"[수강신청 학점 한도]\n- 기본 최대: {reg.get('기본_최대학점', '미확인')}\n- 현재 평점: {reg.get('현재_평점', 0)}"
            else:
                transcript_context = tx.format_profile_summary_safe()

            student_context = tx.format_profile_summary_safe()

    return analysis, transcript_context, student_context


async def generate_response(question: str) -> str:
    analyzer  = st.session_state.analyzer
    router    = st.session_state.router
    merger    = st.session_state.merger
    generator = st.session_state.generator
    validator = st.session_state.validator

    analysis = analyzer.analyze(question)
    if st.session_state.get("ui_lang") == "en":
        analysis.lang = "en"
    analysis, transcript_context, student_context = _enrich_analysis(question, analysis, router)

    search_results = router.route_and_search(question, analysis)
    merged         = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
        question=question,
        intent=analysis.intent,
        entities=analysis.entities,
        transcript_context=transcript_context,
        question_type=analysis.question_type,
    )

    if not merged.formatted_context.strip():
        if analysis.lang == "en":
            return (
                "I'm sorry, but I couldn't find any relevant information in the academic regulations.\n\n"
                "Please contact the Academic Affairs Office at +82-51-509-5182."
            )
        return (
            "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.\n\n"
            "다음을 확인해 주세요:\n"
            "- PDF 학사 안내 자료가 등록되어 있는지\n"
            "- 질문에 학번을 포함했는지 (예: 2023학번)"
        )

    if merged.direct_answer and analysis.lang != "en":
        return merged.direct_answer

    answer = await generator.generate_full(
        question=question,
        context=merged.formatted_context,
        student_id=analysis.student_id,
        question_focus=analysis.entities.get("question_focus"),
        lang=analysis.lang,
        matched_terms=analysis.matched_terms,
        student_context=student_context,
        context_confidence=merged.context_confidence,
        question_type=analysis.question_type.value if analysis.question_type else None,
        intent=analysis.intent.value,
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
    placeholder.markdown(_thinking_html(), unsafe_allow_html=True)
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

    _t_analyze = time.monotonic()
    analysis = analyzer.analyze(question)
    # EN UI 사용자는 항상 영어 답변
    if st.session_state.get("ui_lang") == "en":
        analysis.lang = "en"
    analysis, transcript_context, student_context = _enrich_analysis(question, analysis, router)
    _ms_analyze = int((time.monotonic() - _t_analyze) * 1000)

    _t_search = time.monotonic()
    search_results = router.route_and_search(question, analysis)
    _ms_search = int((time.monotonic() - _t_search) * 1000)

    _t_merge = time.monotonic()
    merged         = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
        question=question,
        intent=analysis.intent,
        entities=analysis.entities,
        transcript_context=transcript_context,
        question_type=analysis.question_type,
    )
    _ms_merge = int((time.monotonic() - _t_merge) * 1000)

    def _log(answer: str) -> None:
        """Q&A 쌍을 로그 파일에 기록 (실패해도 메인 기능에 영향 없음)"""
        from app.transcript.security import PIIRedactor
        try:
            st.session_state.chat_logger.log(
                question=PIIRedactor.redact_for_log(question),  # PII 산화
                answer=PIIRedactor.redact_for_log(answer),  # PII 산화
                session_id=st.session_state.get("session_id", ""),
                intent=analysis.intent.name if analysis.intent else "",
                student_id=analysis.student_id,
                duration_ms=int((time.monotonic() - _t0) * 1000),
            )
        except Exception:
            pass

    if not merged.formatted_context.strip():
        if analysis.lang == "en":
            msg = (
                "I'm sorry, but I couldn't find any relevant information in the academic regulations.\n\n"
                "Please contact the Academic Affairs Office at +82-51-509-5182."
            )
        else:
            msg = (
                "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.\n\n"
                "다음을 확인해 주세요:\n"
                "- PDF 학사 안내 자료가 등록되어 있는지\n"
                "- 질문에 학번을 포함했는지 (예: 2023학번)"
            )
        placeholder.markdown(msg)
        _log(msg)
        return msg, [], []

    if merged.direct_answer and analysis.lang != "en":
        placeholder.markdown(merged.direct_answer)
        _log(merged.direct_answer)
        return merged.direct_answer, merged.source_urls, merged.vector_results + merged.graph_results

    _t_gen = time.monotonic()
    full_answer = ""
    async for token in generator.generate(
        question=question,
        context=merged.formatted_context,
        student_id=analysis.student_id,
        question_focus=analysis.entities.get("question_focus"),
        lang=analysis.lang,
        matched_terms=analysis.matched_terms,
        student_context=student_context,
        context_confidence=merged.context_confidence,
        question_type=analysis.question_type.value if analysis.question_type else None,
        intent=analysis.intent.value,
    ):
        if token == "\x00CLEAR\x00":
            full_answer = ""
            continue
        full_answer += token
        _preview = re.sub(r'(?<!~)~(?!~)', r'\~', full_answer)
        placeholder.markdown(_preview + "▌")
    _ms_gen = int((time.monotonic() - _t_gen) * 1000)

    # 방어: LLM이 빈 응답을 반환한 경우
    if not full_answer.strip():
        full_answer = t("error.empty_response")
        logger.warning("LLM 빈 응답: question='%s'", question[:50])

    # ~ 이스케이프: 시간 범위 표기(10:00~15:20)가 마크다운 취소선으로 렌더링되는 현상 방지
    full_answer = re.sub(r'(?<!~)~(?!~)', r'\~', full_answer)

    placeholder.markdown(full_answer)

    _t_val = time.monotonic()
    all_results = search_results["vector_results"] + search_results["graph_results"]
    passed, warnings = validator.validate(
        answer=full_answer,
        context=merged.formatted_context,
        search_results=all_results,
    )
    _ms_val = int((time.monotonic() - _t_val) * 1000)
    if warnings:
        warning_text = "\n".join(f"- {w}" for w in warnings)
        full_answer += f"\n\n---\n*{t('error.validation_warning')}*\n{warning_text}"
        placeholder.markdown(full_answer)

    # 연락처 꼬리말 추가 (학사 질문 → 학사지원팀 / 학과 졸업시험·과행사 → 학과 사무실)
    footer = _get_contact_footer(analysis.intent, analysis.entities, question)
    if footer:
        full_answer += footer
        placeholder.markdown(full_answer)

    # 원칙 2: 파이프라인 단계별 타이밍 로그
    _ms_total = int((time.monotonic() - _t0) * 1000)
    logger.info(
        "PIPELINE_TIMING total=%dms analyze=%dms search=%dms merge=%dms "
        "generate=%dms validate=%dms intent=%s qt=%s",
        _ms_total, _ms_analyze, _ms_search, _ms_merge,
        _ms_gen, _ms_val, analysis.intent.value,
        analysis.question_type.value if analysis.question_type else "?",
    )

    _log(full_answer)
    return full_answer, merged.source_urls, merged.vector_results + merged.graph_results


# ── Main ───────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="CamChat",
        page_icon="🎓",
        layout="wide",
    )

    # 크롤링 스케줄러 싱글톤 시작 (CRAWLER_ENABLED=false면 no-op)
    get_scheduler()

    # ── Gate 0: 언어 선택 (최초 1회) ────────────────────
    if "ui_lang" not in st.session_state:
        render_language_selection()
        return

    inject_custom_css()

    if not render_sidebar():
        return

    # ── Gate 1: 온보딩 (프로필 미설정) ─────────────────
    if "user_profile" not in st.session_state:
        render_onboarding()
        return

    pending    = st.session_state.pop("pending_question", None)
    user_input = st.chat_input(t("chat.input_placeholder"))
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
                answer, source_urls, results = _run_async(
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
