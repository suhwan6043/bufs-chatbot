"""
detect_cohort 함수 단위 테스트
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.ingestion.chunking import detect_cohort


@pytest.mark.parametrize("text, expected", [
    # 범위 패턴
    ("2021~2023학번 학생은 다음을 적용한다",        (2021, 2023)),
    ("2021·2023학번 학생은 다음을 적용한다",        (2021, 2023)),

    # 방향 패턴 - 이후/부터
    ("2024학번 이후 학생은 A를 적용한다",           (2024, 2030)),
    ("2024학번부터 B 과목을 이수해야 한다",         (2024, 2030)),

    # 방향 패턴 - 이전/까지
    ("2023학번 이전 학생은 C를 적용한다",           (2016, 2023)),
    ("2023학번까지 D 기준을 따른다",               (2016, 2023)),

    # 복수 단일 학번 → 범위
    ("2023학번은 A, 2024학번은 B 기준을 따른다",    (2023, 2024)),

    # 단일 학번
    ("2024학번 학생의 졸업 요건",                   (2024, 2024)),
    ("2021학번 해당 사항",                          (2021, 2021)),

    # 공통 (감지 불가)
    ("모든 학생에게 공통으로 적용되는 사항",         (2016, 2030)),
    ("2025학년도 학사안내 제1장 총칙",              (2016, 2030)),  # 학년도는 무시
    ("",                                           (2016, 2030)),
])
def test_detect_cohort(text, expected):
    assert detect_cohort(text) == expected
