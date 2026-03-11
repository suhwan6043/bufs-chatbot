"""
캠챗 - 부산외국어대학교 학사 안내 AI 챗봇
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path

import streamlit as st

from app.config import settings
from app.logging import ChatLogger
from app.embedding import Embedder
from app.vectordb import ChromaStore
from app.graphdb import AcademicGraph
from app.pipeline import (
    QueryAnalyzer,
    QueryRouter,
    ContextMerger,
    AnswerGenerator,
    ResponseValidator,
)

logger = logging.getLogger(__name__)

# ── Brand ──────────────────────────────────────────
APP_NAME    = "캠챗"
APP_SUBTITLE = "부산외대 학사 도우미"
APP_VERSION  = "0.1.0"
LOGO_PATH    = Path(__file__).parent / "static" / "logo.png"

QUICK_FEATURES = [
    {"label": "수강신청",     "question": "수강신청 일정과 방법을 알려줘"},
    {"label": "성적조회",     "question": "성적 처리 방법과 이의신청 절차 알려줘"},
    {"label": "학사일정",     "question": "이번 학기 주요 학사일정을 알려줘"},
    {"label": "자주묻는질문", "question": "학사 관련 자주 묻는 질문을 알려줘"},
]

PORTAL_LINKS = [
    {"icon": "🖥️", "label": "수강신청 포털",   "url": "https://sugang.bufs.ac.kr/Login.aspx"},
    {"icon": "📊", "label": "학사정보시스템",    "url": "https://m.bufs.ac.kr/default.aspx?ReturnUrl=%2f"},
    {"icon": "📅", "label": "학사일정 달력",     "url": "https://m.bufs.ac.kr/popup/Haksa_Iljeong.aspx?gbn="},
]

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
        </style>
        """,
        unsafe_allow_html=True,
    )


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

        # ── Ollama 경고 (에러 시에만) ────────────
        async def _chk():
            return await st.session_state.generator.health_check()

        if not asyncio.run(_chk()):
            st.markdown(
                '<div style="margin-top:0.6rem;padding:0.5rem 0.6rem;border-radius:7px;'
                'background:#fef3c7;border:1px solid #fcd34d;font-size:0.78rem;color:#92400e;">'
                '⚠️ AI 서버 미연결<br><span style="font-size:0.72rem;">Ollama를 시작해주세요</span>'
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


# ── Right panel ────────────────────────────────────
def render_right_panel():
    st.markdown('<div class="rp-section">유용한 도구</div>', unsafe_allow_html=True)
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


# ── Pipeline (UNCHANGED) ───────────────────────────
def init_components():
    if "initialized" not in st.session_state:
        with st.spinner("시스템 초기화 중..."):
            embedder    = Embedder()
            chroma_store = ChromaStore(embedder=embedder)
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
        return msg

    if merged.direct_answer:
        placeholder.markdown(merged.direct_answer)
        _log(merged.direct_answer)
        return merged.direct_answer

    full_answer = ""
    async for token in generator.generate(
        question=question,
        context=merged.formatted_context,
        student_id=analysis.student_id,
    ):
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

    _log(full_answer)
    return full_answer


# ── Main ───────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="캠챗 - 부산외대 학사챗봇",
        page_icon="🎓",
        layout="wide",
    )

    inject_custom_css()

    if not render_sidebar():
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

            # 마지막 어시스턴트 메시지 아래 별점 UI 표시
            if (
                msg["role"] == "assistant"
                and idx == len(messages) - 1
            ):
                _render_rating(idx, msg)

        if not messages and prompt is None:
            render_welcome_screen()

        if prompt:
            messages.append({"role": "user", "content": prompt})
            with st.chat_message("user", avatar="👤"):
                st.markdown(prompt)
            with st.chat_message("assistant", avatar="🎓"):
                placeholder = st.empty()
                answer = asyncio.run(generate_response_stream(prompt, placeholder))
                messages.append(
                    {"role": "assistant", "content": answer, "rated": False}
                )

    with right_col:
        render_right_panel()


if __name__ == "__main__":
    main()
