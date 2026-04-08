"""
UI 다국어 지원 모듈.

t(key) → 현재 세션 언어(ui_lang)에 맞는 문자열 반환.
키가 없으면 한국어 폴백 → "[key]" 폴백.
"""

from __future__ import annotations

import streamlit as st

# ──────────────────────────────────────────────────────────
# 번역 사전  (ko = 기본)
# ──────────────────────────────────────────────────────────
STRINGS: dict[str, dict[str, str]] = {
    # =====================================================
    #  한국어
    # =====================================================
    "ko": {
        # ── brand ──
        "brand.app_name":       "캠챗",
        "brand.subtitle":       "부산외대 학사 도우미",
        "brand.page_title":     "캠챗 - 부산외대 학사챗봇",
        "brand.version":        "버전 {v}",
        "brand.logo_fallback":  "캠",

        # ── language selection ──
        "lang.hero_title":      "부산외대 학사 안내 AI",
        "lang.hero_subtitle":   "언어를 선택해주세요 / Please select your language",

        # ── onboarding ──
        "onboard.welcome":      "캠챗에 오신 것을 환영합니다",
        "onboard.desc":         "맞춤 학사 안내를 위해 기본 정보를 입력해주세요.<br>"
                                "입력한 정보는 질문에 자동으로 반영됩니다.",
        "onboard.year_label":   "📅 입학연도",
        "onboard.year_fmt":     "{y}학번",
        "onboard.dept_label":   "🏫 학과 / 전공",
        "onboard.dept_none":    "선택 안 함",
        "onboard.type_label":   "👤 학생 유형",
        "onboard.type_domestic": "내국인",
        "onboard.type_intl":    "외국인",
        "onboard.type_transfer": "편입생",
        "onboard.disclaimer":   "본 서비스의 답변은 인공지능(AI)에 의해 자동 생성되며, "
                                "사실과 다르거나 부정확한 내용을 포함할 수 있습니다. "
                                "제공된 정보는 참고용이며, 중요한 학사 사항은 학과 사무실 또는 "
                                "학사지원팀을 통해 확인하시기 바랍니다. "
                                "AI 답변의 오류·누락으로 인해 발생한 직접적·간접적 불이익에 대해 "
                                "본 서비스는 책임을 지지 않습니다.",
        "onboard.agree":        "위 내용을 확인하였으며, 이에 동의합니다.",
        "onboard.btn_start":    "시작하기",
        "onboard.btn_skip":     "나중에 설정",
        "onboard.warn_agree":   "면책 조항에 동의해야 시작할 수 있습니다.",

        # ── sidebar ──
        "sidebar.my_info":      "내 정보",
        "sidebar.dept_unset":   "학과 미설정",
        "sidebar.info_unset":   "정보 미설정",
        "sidebar.year_suffix":  "학번",
        "sidebar.edit_profile": "✏️  정보 수정",
        "sidebar.transcript":   "성적 사정표",
        "sidebar.gpa":          "평점 {v}",
        "sidebar.credits_fmt":  "{done} / {total}학점 ({pct}%)",
        "sidebar.shortage":     "⚠️ 부족 {n}학점",
        "sidebar.req_met":      "✓ 졸업요건 충족",
        "sidebar.dual_remain":  "{n}학점 남음",
        "sidebar.dual_met":     "충족",
        "sidebar.btn_refresh":  "🔄 갱신",
        "sidebar.btn_delete":   "🗑️ 삭제",
        "sidebar.auto_delete":  "⏱️ {m}분 후 자동 삭제",
        "sidebar.privacy_html": '⚠️ <b>개인정보 처리 안내</b><br>'
                                '• 세션에서만 사용, 서버에 저장하지 않습니다<br>'
                                '• 30분 후 자동 삭제됩니다<br>'
                                '• AI 답변에 이름/학번이 노출되지 않습니다<br>'
                                '• 언제든 삭제 버튼으로 즉시 삭제할 수 있습니다',
        "sidebar.consent":      "위 내용을 확인하였으며, 성적표 활용에 동의합니다",
        "sidebar.upload_label": "학업성적사정표 업로드",
        "sidebar.upload_help":  "학생포털 다운로드 파일(.xls) 또는 스크린샷(이미지)",
        "sidebar.qf_label":     "빠른 기능",
        "sidebar.qf_personal":  "맞춤 기능",
        "sidebar.chat":         "대화",
        "sidebar.all_chats":    "💬&nbsp; 전체 대화",
        "sidebar.clear_chat":   "🗑️  대화 초기화",
        "sidebar.server_warn":  "⚠️ AI 서버 미연결",
        "sidebar.server_hint":  "LM Studio를 시작해주세요",
        "sidebar.init_error":   "서비스를 준비 중입니다. 잠시 후 다시 시도해주세요.",
        "sidebar.init_spinner": "시스템 초기화 중...",

        # ── quick features ──
        "qf.register":          "수강신청",
        "qf.register_q":        "수강신청 일정과 방법을 알려줘",
        "qf.grades":            "성적조회",
        "qf.grades_q":          "성적 처리 방법과 이의신청 절차 알려줘",
        "qf.schedule":          "학사일정",
        "qf.schedule_q":        "이번 학기 주요 학사일정을 알려줘",
        "qf.faq":               "자주묻는질문",
        "qf.faq_q":             "학사 관련 자주 묻는 질문을 알려줘",
        "qf.shortage":          "🎯 부족학점",
        "qf.shortage_q":        "내 성적 기준으로 뭐가 부족한지 알려줘",
        "qf.retake":            "🔁 재수강 추천",
        "qf.retake_q":          "재수강할만한 과목 추천해줘",
        "qf.semester":          "📚 이번 학기",
        "qf.semester_q":        "이번 학기 내가 듣는 과목 알려줘",
        "qf.graduation":        "🎓 졸업 상태",
        "qf.graduation_q":      "졸업까지 얼마나 남았는지 정리해줘",
        "qf.dual":              "🎯 복수전공",
        "qf.dual_q":            "복수전공 학점 얼마나 남았어?",
        "qf.reg_limit":         "📊 수강 가능",
        "qf.reg_limit_q":       "내 평점으로 몇 학점까지 신청 가능해?",

        # ── portal links ──
        "portal.register":      "수강신청 사이트 바로가기",
        "portal.student":       "학생포털시스템",
        "portal.calendar":      "학사일정",
        "portal.notices":       "학사공지",

        # ── chat header ──
        "chat.header_title":    "캠챗 &mdash; 부산외대 학사챗봇",
        "chat.header_sub":      "수강신청 &middot; 성적 &middot; 학사일정 &middot; 학사행정 지원",
        "chat.badge_transcript": "📋 성적표 연동 중 — 개인 맞춤 답변 활성화",
        "chat.badge_default":   "📢 학사 정보는 학교 포털에서도 확인하세요",
        "chat.input_placeholder": "이번 학기 수강신청 일정 알려줘",

        # ── welcome ──
        "welcome.title":        "캠챗에 오신 것을 환영합니다",
        "welcome.desc":         "부산외국어대학교 학사 안내 AI입니다.<br>"
                                "졸업요건, 수강신청, 학사일정 등 궁금한 것을 물어보세요.<br>"
                                '<span style="color:#2563eb;font-weight:600;">'
                                "💡 왼쪽 사이드바에서 성적표를 업로드하면 맞춤 답변을 받을 수 있어요.</span>",
        "welcome.transcript_title": "성적표 연동 완료",
        "welcome.credits_short": '졸업까지 <b style="color:#b45309;">{n}학점</b>이 남아 있어요. '
                                 "어떤 정보가 필요하신가요?",
        "welcome.credits_met":  '<b style="color:#15803d;">졸업요건을 충족하셨어요!</b> '
                                "수강신청·학사일정 등 궁금한 것을 물어보세요.",
        "welcome.hint_buttons": "위 버튼을 누르거나 아래 입력창에 직접 질문하세요",
        "welcome.hint_personal": "성적표 기반 맞춤 질문을 바로 시작하세요",
        "welcome.student_label": "재학생",

        # ── loading ──
        "loading.generating":   "답변을 생성하고 있어요",
        "loading.analyzing":    "학사 자료를 분석하는 중입니다",

        # ── rating ──
        "rating.prompt":        "이 답변이 도움이 됐나요?",
        "rating.done":          "만족도:",

        # ── source panel ──
        "source.expander":      "📄 근거 문서 확인",
        "source.page":          "{n}페이지",
        "source.graph_label":   "(그래프 데이터)",
        "source.notice_title":  "공지사항",
        "source.view_original": "원문 보기 →",
        "source.related":       "📌 **관련 공지**",
        "source.notice_default": "공지 원문",

        # ── right panel ──
        "right.shortcuts":      "바로가기",
        "right.help":           "도움말",
        "right.help_tip":       "학번을 포함하면 더 정확한 답변을 받을 수 있어요<br><br>"
                                '<span style="color:#64748b;">예시</span><br>'
                                '&bull; <em>"2023학번 졸업요건"</em><br>'
                                '&bull; <em>"2024학번 수강신청 학점"</em>',
        "right.feedback":       "피드백",
        "right.feedback_ph":    "불편한 점, 개선 제안, 칭찬 등 자유롭게 작성해 주세요.",
        "right.feedback_submit": "전송",
        "right.feedback_ok":    "피드백이 전송됐습니다. 감사합니다!",
        "right.feedback_empty": "내용을 입력해 주세요.",

        # ── errors / status ──
        "error.file_validation": "파일 검증 실패: {e}",
        "error.parse_module":   "서버 설정 문제: 필수 라이브러리가 설치되지 않았습니다 ({lib}). 관리자에게 문의해 주세요.",
        "error.parse_invalid":  "성적표 형식이 올바르지 않습니다: {e}",
        "error.parse_failed":   "성적표 파싱 실패. 올바른 파일인지 확인해주세요.",
        "error.empty_response": "죄송합니다. 답변 생성에 실패했습니다. 다시 질문해 주세요.\n\n"
                                "문제가 지속되면 학사지원팀(051-509-5182)에 문의하시기 바랍니다.",
        "error.validation_warning": "검증 경고:",
        "status.upload_ok":     "✅ {name}님의 성적표 등록 완료 (30분 후 자동 삭제)",
        "status.diff_detected": "변경사항 {n}건 감지",
        "status.registered":    "등록됨",
    },

    # =====================================================
    #  English
    # =====================================================
    "en": {
        # ── brand ──
        "brand.app_name":       "CamChat",
        "brand.subtitle":       "BUFS Academic Assistant",
        "brand.page_title":     "CamChat - BUFS Academic Chatbot",
        "brand.version":        "Version {v}",
        "brand.logo_fallback":  "C",

        # ── language selection ──
        "lang.hero_title":      "BUFS Academic Guide AI",
        "lang.hero_subtitle":   "언어를 선택해주세요 / Please select your language",

        # ── onboarding ──
        "onboard.welcome":      "Welcome to CamChat",
        "onboard.desc":         "Please enter your basic information for personalized academic guidance.<br>"
                                "Your information will be automatically applied to your queries.",
        "onboard.year_label":   "📅 Admission Year",
        "onboard.year_fmt":     "Class of {y}",
        "onboard.dept_label":   "🏫 Department / Major",
        "onboard.dept_none":    "Not selected",
        "onboard.type_label":   "👤 Student Type",
        "onboard.type_domestic": "Domestic",
        "onboard.type_intl":    "International",
        "onboard.type_transfer": "Transfer",
        "onboard.disclaimer":   "Responses from this service are automatically generated by AI and may "
                                "contain inaccurate or incomplete information. "
                                "The information provided is for reference only. For important academic "
                                "matters, please verify with your department office or the Academic Affairs Team. "
                                "This service is not responsible for any direct or indirect disadvantages "
                                "caused by errors or omissions in AI responses.",
        "onboard.agree":        "I have read and agree to the above.",
        "onboard.btn_start":    "Get Started",
        "onboard.btn_skip":     "Set Up Later",
        "onboard.warn_agree":   "You must agree to the disclaimer to proceed.",

        # ── sidebar ──
        "sidebar.my_info":      "My Info",
        "sidebar.dept_unset":   "Department not set",
        "sidebar.info_unset":   "Not configured",
        "sidebar.year_suffix":  "Class",
        "sidebar.edit_profile": "✏️  Edit Profile",
        "sidebar.transcript":   "Transcript",
        "sidebar.gpa":          "GPA {v}",
        "sidebar.credits_fmt":  "{done} / {total} credits ({pct}%)",
        "sidebar.shortage":     "⚠️ {n} credits short",
        "sidebar.req_met":      "✓ Requirements met",
        "sidebar.dual_remain":  "{n} credits remaining",
        "sidebar.dual_met":     "Met",
        "sidebar.btn_refresh":  "🔄 Refresh",
        "sidebar.btn_delete":   "🗑️ Delete",
        "sidebar.auto_delete":  "⏱️ Auto-delete in {m} min",
        "sidebar.privacy_html": '⚠️ <b>Privacy Notice</b><br>'
                                '• Used only during this session; not stored on server<br>'
                                '• Automatically deleted after 30 minutes<br>'
                                '• Your name/student ID will not appear in AI responses<br>'
                                '• You can delete it anytime using the delete button',
        "sidebar.consent":      "I have read the above and consent to transcript usage",
        "sidebar.upload_label": "Upload Academic Transcript",
        "sidebar.upload_help":  "Student portal file (.xls) or screenshot (image)",
        "sidebar.qf_label":     "Quick Features",
        "sidebar.qf_personal":  "Personalized",
        "sidebar.chat":         "Chat",
        "sidebar.all_chats":    "💬&nbsp; All Chats",
        "sidebar.clear_chat":   "🗑️  Clear Chat",
        "sidebar.server_warn":  "⚠️ AI server not connected",
        "sidebar.server_hint":  "Please start LM Studio",
        "sidebar.init_error":   "Service is loading. Please try again shortly.",
        "sidebar.init_spinner": "Initializing system...",

        # ── quick features ──
        "qf.register":          "Registration",
        "qf.register_q":        "Tell me about the course registration schedule and process",
        "qf.grades":            "Grades",
        "qf.grades_q":          "How does grade processing and appeals work?",
        "qf.schedule":          "Calendar",
        "qf.schedule_q":        "What are the key academic calendar dates this semester?",
        "qf.faq":               "FAQ",
        "qf.faq_q":             "What are common academic affairs questions?",
        "qf.shortage":          "🎯 Credits Gap",
        "qf.shortage_q":        "What credits am I short of based on my transcript?",
        "qf.retake":            "🔁 Retake Suggestions",
        "qf.retake_q":          "Which courses should I consider retaking?",
        "qf.semester":          "📚 This Semester",
        "qf.semester_q":        "What courses am I taking this semester?",
        "qf.graduation":        "🎓 Graduation Status",
        "qf.graduation_q":      "How close am I to graduation?",
        "qf.dual":              "🎯 Double Major",
        "qf.dual_q":            "How many double major credits do I have left?",
        "qf.reg_limit":         "📊 Credit Limit",
        "qf.reg_limit_q":       "How many credits can I register for with my GPA?",

        # ── portal links ──
        "portal.register":      "Course Registration",
        "portal.student":       "Student Portal",
        "portal.calendar":      "Academic Calendar",
        "portal.notices":       "Academic Notices",

        # ── chat header ──
        "chat.header_title":    "CamChat &mdash; BUFS Academic Chatbot",
        "chat.header_sub":      "Registration &middot; Grades &middot; Calendar &middot; Admin Support",
        "chat.badge_transcript": "📋 Transcript connected — personalized answers enabled",
        "chat.badge_default":   "📢 Also check the school portal for academic info",
        "chat.input_placeholder": "Ask about registration, grades, schedules...",

        # ── welcome ──
        "welcome.title":        "Welcome to CamChat",
        "welcome.desc":         "AI-powered academic assistant for BUFS.<br>"
                                "Ask about graduation requirements, registration, schedules, and more.<br>"
                                '<span style="color:#2563eb;font-weight:600;">'
                                "💡 Upload your transcript in the sidebar for personalized answers.</span>",
        "welcome.transcript_title": "Transcript Connected",
        "welcome.credits_short": 'You need <b style="color:#b45309;">{n} more credits</b> to graduate. '
                                 "How can I help you?",
        "welcome.credits_met":  '<b style="color:#15803d;">You\'ve met all graduation requirements!</b> '
                                "Ask about registration, schedules, or anything else.",
        "welcome.hint_buttons": "Press a button above or type your question below",
        "welcome.hint_personal": "Start with a personalized question based on your transcript",
        "welcome.student_label": "Student",

        # ── loading ──
        "loading.generating":   "Generating your answer",
        "loading.analyzing":    "Analyzing academic data",

        # ── rating ──
        "rating.prompt":        "Was this answer helpful?",
        "rating.done":          "Rating:",

        # ── source panel ──
        "source.expander":      "📄 View Source Documents",
        "source.page":          "Page {n}",
        "source.graph_label":   "(Graph Data)",
        "source.notice_title":  "Notice",
        "source.view_original": "View Original →",
        "source.related":       "📌 **Related Notices**",
        "source.notice_default": "Original Notice",

        # ── right panel ──
        "right.shortcuts":      "Quick Links",
        "right.help":           "Help",
        "right.help_tip":       "Including your admission year helps get more accurate answers<br><br>"
                                '<span style="color:#64748b;">Examples</span><br>'
                                '&bull; <em>"Class of 2023 graduation requirements"</em><br>'
                                '&bull; <em>"2024 student registration credits"</em>',
        "right.feedback":       "Feedback",
        "right.feedback_ph":    "Share suggestions, issues, or praise.",
        "right.feedback_submit": "Submit",
        "right.feedback_ok":    "Feedback submitted. Thank you!",
        "right.feedback_empty": "Please enter your feedback.",

        # ── errors / status ──
        "error.file_validation": "File validation failed: {e}",
        "error.parse_module":   "Server configuration issue: Required library not installed ({lib}). Please contact the administrator.",
        "error.parse_invalid":  "Invalid transcript format: {e}",
        "error.parse_failed":   "Transcript parsing failed. Please check if the file is valid.",
        "error.empty_response": "Sorry, answer generation failed. Please try again.\n\n"
                                "If the problem persists, contact the Academic Affairs Office (+82-51-509-5182).",
        "error.validation_warning": "Validation warning:",
        "status.upload_ok":     "✅ Transcript for {name} registered (auto-delete in 30 min)",
        "status.diff_detected": "{n} change(s) detected",
        "status.registered":    "Registered",
    },
}


# ── student_type 역매핑 (EN → KO, 파이프라인 호환용) ──
STYPE_EN_TO_KO: dict[str, str] = {
    "Domestic":      "내국인",
    "International": "외국인",
    "Transfer":      "편입생",
}


def t(key: str, **kwargs) -> str:
    """현재 세션 언어에 맞는 문자열 반환.

    폴백: 영어에 없으면 한국어 → 둘 다 없으면 "[key]".
    kwargs가 있으면 str.format(**kwargs) 적용.
    """
    lang = st.session_state.get("ui_lang", "ko")
    s = STRINGS.get(lang, STRINGS["ko"]).get(key)
    if s is None:
        s = STRINGS["ko"].get(key, f"[{key}]")
    return s.format(**kwargs) if kwargs else s
