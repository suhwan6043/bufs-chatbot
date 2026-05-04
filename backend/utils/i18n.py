"""백엔드 사용자 응답 메시지 다국어화.

- HTTPException detail, 거절 메시지 등 사용자에게 직접 노출되는 문자열 i18n.
- 챗봇 사용자(EN/KO)에게만 적용 — admin 라우터는 KO 고정 (실무자 전원 KO 사용).
- KO가 기본값. EN 누락 시 KO로 fallback.
"""

from typing import Optional

from fastapi import Request

# ── 언어 추출 ──

_SUPPORTED = ("ko", "en")


def get_lang_from_request(request: Request, default: str = "ko") -> str:
    """Accept-Language 헤더에서 사용자 선호 언어 추출.

    프론트엔드가 lang 상태(ko/en)를 Accept-Language로 전송 — 회원가입·로그인 등
    세션 없는 경로에서 사용. 헤더 없으면 default.
    """
    al = (request.headers.get("accept-language") or "").lower().strip()
    if not al:
        return default
    # "en", "en-US", "en-US,ko;q=0.9" 등 모두 처리 — 첫 토큰만 봄
    primary = al.split(",")[0].strip().split("-")[0]
    return primary if primary in _SUPPORTED else default


def get_lang_from_session(session_data: Optional[dict], default: str = "ko") -> str:
    """세션 데이터에서 lang 추출. 세션 없으면 default."""
    if not session_data:
        return default
    lang = session_data.get("lang", default)
    return lang if lang in _SUPPORTED else default


# ── 공통 API 메시지 ──
# 키는 의미 단위(snake_case). 새 메시지 추가 시 ko + en 모두 작성.
_API_MSG: dict[str, dict[str, str]] = {
    # 인증·로그인
    "auth_required": {
        "ko": "인증이 필요합니다.",
        "en": "Authentication required.",
    },
    "token_invalid": {
        "ko": "토큰이 만료되었거나 유효하지 않습니다.",
        "en": "Token expired or invalid.",
    },
    "rate_limit_login": {
        "ko": "로그인 시도 횟수 초과. 15분 후 재시도하세요.",
        "en": "Too many login attempts. Please try again in 15 minutes.",
    },
    "login_failed": {
        "ko": "아이디 또는 비밀번호가 잘못되었습니다.",
        "en": "Invalid username or password.",
    },
    # 회원가입·프로필
    "invalid_student_type": {
        "ko": "유효하지 않은 학생 유형입니다.",
        "en": "Invalid student type.",
    },
    "username_taken": {
        "ko": "이미 사용 중인 아이디입니다.",
        "en": "This username is already taken.",
    },
    "user_not_found": {
        "ko": "사용자를 찾을 수 없습니다.",
        "en": "User not found.",
    },
    # 세션
    "session_not_found": {
        "ko": "세션을 찾을 수 없습니다.",
        "en": "Session not found.",
    },
    # 알림
    "notification_not_found": {
        "ko": "알림을 찾을 수 없거나 이미 읽었습니다.",
        "en": "Notification not found or already read.",
    },
    # 학사 리포트
    "analysis_failed": {
        "ko": "분석 생성 실패",
        "en": "Failed to generate analysis.",
    },
}


def api_msg(key: str, lang: str = "ko") -> str:
    """메시지 키 + lang으로 사용자 노출 문자열 반환. KO fallback."""
    entry = _API_MSG.get(key, {})
    return entry.get(lang) or entry.get("ko", key)


# ── 학생 유형 정규화 ──
# DB·내부 모듈은 KO 키("내국인"/"외국인"/"편입생")를 진실원으로 사용.
# EN 사용자 입력은 KO로 정규화한 뒤 저장한다 (스키마·DB 변경 없이 EN 수용).
_STUDENT_TYPE_MAP_EN_TO_KO: dict[str, str] = {
    "domestic": "내국인",
    "korean": "내국인",
    "local": "내국인",
    "international": "외국인",
    "foreign": "외국인",
    "foreigner": "외국인",
    "exchange": "외국인",
    "transfer": "편입생",
    "transferred": "편입생",
    "transfer student": "편입생",
}

_VALID_STUDENT_TYPES_KO = ("내국인", "외국인", "편입생")


def normalize_student_type(value: Optional[str]) -> Optional[str]:
    """학생 유형 입력을 KO 표준값으로 정규화.

    - KO 정식 값("내국인"/"외국인"/"편입생")은 그대로 반환
    - EN 변형(case-insensitive)은 매핑 후 반환
    - 매핑 실패 시 None — 호출 측에서 검증 에러 발생시킬 것
    """
    if not value:
        return None
    v = value.strip()
    if v in _VALID_STUDENT_TYPES_KO:
        return v
    return _STUDENT_TYPE_MAP_EN_TO_KO.get(v.lower())
