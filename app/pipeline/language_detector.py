"""
언어 감지 - 한국어(ko) / 영어(en) 판별
외부 라이브러리 없이 문자 비율 휴리스틱으로 <1ms 처리
"""

import re

_KO_RE    = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_ALPHA_RE = re.compile(r"[a-zA-Z가-힣]")


def detect_language(text: str) -> str:
    """
    텍스트 언어를 감지합니다.

    Korean 문자 비율이 30% 이상이면 'ko', 미만이면 'en' 반환.
    숫자·공백·특수문자만 있는 경우 기본값 'ko' 반환.
    """
    ko_count    = len(_KO_RE.findall(text))
    alpha_count = len(_ALPHA_RE.findall(text))
    if alpha_count == 0:
        return "ko"
    return "ko" if (ko_count / alpha_count) >= 0.3 else "en"
