"""
선언적 Key-Value 추출기 (원칙 1: 스키마 자동 진화)

정규식 패턴을 코드가 아닌 데이터(규칙 리스트)로 관리합니다.
새 필드 추가 = 규칙 1줄 추가 (코드 변경 불필요).

규칙 미매칭 시 fallback: "레이블: 값" 패턴을 자동으로 추출합니다.

사용법:
    extractor = KeyValueExtractor()
    result = extractor.extract(text, REGISTRATION_RULES)
    # {"최대신청학점": 19, "장바구니최대학점": 30, ...}
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── 추출 규칙 타입 ─────────────────────────────────────────────
# field: 결과 dict의 키 이름
# pattern: 정규식 (첫 번째 캡처 그룹이 값)
# type: "int" | "float" | "currency" | "str" (기본값: "str")
# multi: True이면 findall로 모든 매칭 (기본: False = 첫 번째만)

ExtractionRule = Dict[str, Any]


class KeyValueExtractor:
    """선언적 규칙 기반 Key-Value 추출기."""

    def extract(
        self,
        text: str,
        rules: List[ExtractionRule],
    ) -> Dict[str, Any]:
        """규칙 리스트에 따라 텍스트에서 키-값 쌍을 추출합니다.

        Args:
            text: 원본 텍스트
            rules: 추출 규칙 리스트

        Returns:
            {field_name: extracted_value, ...}
        """
        result: Dict[str, Any] = {}

        for rule in rules:
            field = rule["field"]
            pattern = rule["pattern"]
            value_type = rule.get("type", "str")
            multi = rule.get("multi", False)

            try:
                if multi:
                    matches = re.findall(pattern, text)
                    if matches:
                        result[field] = [self._cast(m, value_type) for m in matches]
                else:
                    m = re.search(pattern, text)
                    if m:
                        raw = m.group(1) if m.lastindex else m.group(0)
                        result[field] = self._cast(raw, value_type)
            except (re.error, ValueError, IndexError) as e:
                logger.debug("규칙 '%s' 추출 실패: %s", field, e)

        return result

    def extract_fallback(
        self,
        text: str,
        label_pattern: str = None,
    ) -> Dict[str, str]:
        """규칙 미매칭 시 자동 레이블-값 추출.

        "레이블 : 값" 또는 "가. 레이블" 패턴을 자동으로 감지합니다.

        Args:
            text: 원본 텍스트
            label_pattern: 커스텀 레이블 패턴 (기본: 한국어 레이블 자동 감지)

        Returns:
            {label: value, ...}
        """
        if not label_pattern:
            # "가. 항목명" | "① 항목명" | "항목명 : 값" | "항목명 ："
            label_pattern = (
                r"(?:^|\n)\s*"
                r"(?:[가-힣]\.|[①-⑳]|[0-9]+[\.\)])\s*"
                r"([가-힣a-zA-Z\s]{2,20})\s*[:：]\s*(.+?)(?=\n|$)"
            )

        result: Dict[str, str] = {}
        for m in re.finditer(label_pattern, text):
            label = m.group(1).strip()
            value = m.group(2).strip()
            if label and value:
                result[label] = value

        return result

    @staticmethod
    def _cast(value: str, value_type: str) -> Any:
        """문자열을 지정된 타입으로 변환합니다."""
        value = value.strip()
        if value_type == "int":
            # "19학점" → 19, "24,000원" → 24000
            cleaned = re.sub(r"[^\d]", "", value)
            return int(cleaned) if cleaned else 0
        elif value_type == "float":
            cleaned = re.sub(r"[^\d.]", "", value)
            return float(cleaned) if cleaned else 0.0
        elif value_type == "currency":
            # "24,000원" → 24000, "120000원" → 120000
            cleaned = re.sub(r"[^\d]", "", value)
            return int(cleaned) if cleaned else 0
        return value
