"""
범용 마크다운 테이블 파서 (원칙 1: 스키마 자동 진화)

마크다운 테이블 문자열을 dict 리스트로 변환합니다.
헤더를 자동 감지하고, 빈 셀은 이전 행 값을 계승합니다 (병합 셀 처리).

사용법:
    parser = GenericTableParser()
    rows = parser.parse(table_md)
    # [{"일자": "2.9.(월)", "신청학년": "1학년", ...}, ...]
"""

import re
from typing import List, Dict, Optional


class GenericTableParser:
    """마크다운 테이블 → dict 리스트 (헤더 자동 감지, 병합 셀 처리)."""

    def parse(
        self,
        table_md: str,
        carry_forward: bool = True,
    ) -> List[Dict[str, str]]:
        """마크다운 테이블을 파싱합니다.

        Args:
            table_md: 마크다운 테이블 문자열 (| 구분)
            carry_forward: 빈 셀을 이전 행 값으로 채울지 여부 (병합 셀 처리)

        Returns:
            [{"col1": "val1", "col2": "val2"}, ...] 형태의 dict 리스트
        """
        lines = [line.strip() for line in table_md.strip().split("\n") if line.strip()]
        if not lines:
            return []

        # 파이프(|)가 있는 줄만 필터
        table_lines = [l for l in lines if "|" in l]
        if len(table_lines) < 2:
            return []

        # 헤더 추출
        headers = self._parse_row(table_lines[0])
        if not headers:
            return []

        # 구분선 건너뛰기 (--- 패턴)
        data_start = 1
        if data_start < len(table_lines) and self._is_separator(table_lines[data_start]):
            data_start = 2

        # 데이터 행 파싱
        rows: List[Dict[str, str]] = []
        prev_row: Dict[str, str] = {}

        for line in table_lines[data_start:]:
            cells = self._parse_row(line)
            if not cells:
                continue

            row: Dict[str, str] = {}
            for i, header in enumerate(headers):
                val = cells[i].strip() if i < len(cells) else ""
                if not val and carry_forward and header in prev_row:
                    val = prev_row[header]
                row[header] = val

            rows.append(row)
            # carry_forward용 이전 행 업데이트 (빈 값은 유지)
            for k, v in row.items():
                if v:
                    prev_row[k] = v

        return rows

    def detect_column_types(
        self, rows: List[Dict[str, str]]
    ) -> Dict[str, str]:
        """컬럼별 데이터 타입을 추론합니다.

        Returns:
            {"col_name": "date|credit|number|text", ...}
        """
        if not rows:
            return {}

        types: Dict[str, str] = {}
        for col in rows[0]:
            values = [r.get(col, "") for r in rows if r.get(col)]
            types[col] = self._infer_type(values)
        return types

    @staticmethod
    def _parse_row(line: str) -> List[str]:
        """파이프(|)로 구분된 행을 셀 리스트로 분리합니다."""
        # 앞뒤 | 제거 후 분리
        stripped = line.strip().strip("|")
        if not stripped:
            return []
        return [cell.strip() for cell in stripped.split("|")]

    @staticmethod
    def _is_separator(line: str) -> bool:
        """구분선 행인지 판단합니다 (--- 패턴)."""
        cleaned = line.replace("|", "").replace("-", "").replace(":", "").strip()
        return len(cleaned) == 0

    @staticmethod
    def _infer_type(values: List[str]) -> str:
        """값 리스트에서 가장 적합한 타입을 추론합니다."""
        if not values:
            return "text"

        date_pattern = re.compile(r"\d{1,4}[.\-/]\d{1,2}[.\-/]\d{1,2}")
        credit_pattern = re.compile(r"\d+\s*학점")
        number_pattern = re.compile(r"^\d+(\.\d+)?$")

        date_count = sum(1 for v in values if date_pattern.search(v))
        credit_count = sum(1 for v in values if credit_pattern.search(v))
        number_count = sum(1 for v in values if number_pattern.match(v.strip()))

        n = len(values)
        if date_count > n * 0.5:
            return "date"
        if credit_count > n * 0.3:
            return "credit"
        if number_count > n * 0.5:
            return "number"
        return "text"
