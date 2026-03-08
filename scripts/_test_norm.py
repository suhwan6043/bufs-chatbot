"""정규화 테스트 스크립트"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))

# evaluate.py 에서 직접 임포트
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "evaluate",
    str(pathlib.Path(__file__).parent / "evaluate.py")
)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

check_answer_match = ev.check_answer_match
_extract_key_tokens = ev._extract_key_tokens
_normalize = ev._normalize
_strip_weekday = ev._strip_weekday
_extract_year = ev._extract_year

import re

cases = [
    # (gen, gt, expected_contains, label)
    # --- 기존 OK ---
    ("개강일은 2025년 9월 1일입니다.", "2025.09.01.(월)", True, "q001 한국어→서양날짜"),
    ("수강신청 기간은 2025년 8월 18일부터 8월 21일까지입니다.", "2025.08.18.(월) ~ 2025.08.21.(목)", True, "q002 범위"),
    ("9월 3일부터 9월 5일까지입니다.", "2025.09.03.(수) ~ 2025.09.05.(금)", True, "q003 연도폴백"),
    # --- 개선 대상 ---
    ("로그인 가능한 시간은 **09:45**입니다.", "수강신청 시작일의 09:45부터", True, "q022 시간토큰"),
    ("취소-신청 지연제 가능 시간은 **15:30 ~ 16:00** 입니다.", "매일 15:30 ~ 16:00", True, "q025 시간범위"),
    # q044: 2022학번 (괄호 내 2023학번은 제외돼야)
    ("2022학번 이전 학번 학생에게만 해당됩니다.", "학점이월 제도는 2022학번 이전 학생에게만 적용된다 (2023학번부터 폐지)", True, "q044 학번+괄호제외"),
    ("기준은 **200회**입니다.", "1일 200회 이상 클릭 시 보안문자 요구", True, "q046 200회"),
    # q048: 45분제 (2017은 괄호 안이 아니므로 여전히 2차 파트에 포함)
    # GT: "45분제(야간수업은 45분제, 2017학년도부터 시행)"
    # 쉼표로 분리: ["45분제(야간수업은 45분제", " 2017학년도부터 시행)"]
    # 1파트 main (괄호 제거): "45분제" → token "45분제" ← gen에 있음 OK
    # 2파트 원문: " 2017학년도부터 시행)" → main (괄호 제거): "2017학년도부터 시행" → tokens: []
    # (2017은 HHMM 패턴 미적용 — 06~19 범위만; "2017" → "20" + "17" 이제 제외됨)
    # 2파트 token 없음 → 직접포함 시도: "2017학년도부터시행" in gen_loose? NO → False
    # 그래서 q048은 여전히 NG: 2파트가 매칭 안 됨
    ("야간수업은 45분제로 운영됩니다.", "45분제(야간수업은 45분제, 2017학년도부터 시행)", True, "q048 45분제"),
    ("한 학기 최대 **6학점**이며, 졸업까지 최대 **24학점**입니다.", "한 학기 최대 6학점, 졸업 시까지 최대 24학점", True, "q055 쉼표분리"),
    ("재수강 가능한 성적 기준은 **C+ 이하**입니다.", "C+ 이하의 과목만 재수강 가능", True, "q056 성적코드"),
    ("최고 성적은 **A**입니다.", "재수강 후에는 최대 A까지 취득 가능", True, "q057 A성적"),
    # q058: 괄호 제거 후 main에 19학점,3학점만 — gen에 19학점 없음
    ("최대 **3학점**까지 이월이 가능합니다.", "직전학기 신청학점이 19학점에 미달했을 경우 최대 3학점까지 이월 가능(단 2022학번 이전에만 적용)", True, "q058 3학점(19학점조건NG예상)"),
    ("약 **3시간**입니다.", "각 주차별 약 3시간 학습 분량", True, "q087 3시간"),
    ("이수학점은 **3학점**입니다.", "3학점 (수준별 배치 후 수강)", True, "q097 3학점괄호"),
    # q062: 24,000원 — 쉼표가 숫자 안에 있어 분리 안 돼야 함
    ("시스템 사용료는 과목당 **24,000원**입니다.", "과목당 24,000원", True, "q062 24000원(쉼표분리방지)"),
    ("부전공 신청은 2023학번까지만 가능합니다.", "2023학번까지만 신청 가능", True, "q071 2023학번"),
    # 진짜 오답 — 변하면 안 됨
    ("수강신청일은 2025년 9월 3일부터 5일까지입니다.", "2025.08.21.(목)", False, "q007_wrong"),
    ("OCU 개강일은 2025년 9월 8일입니다.", "2025.09.01.(월) 오전 10시", False, "q008_wrong"),
    # 8월 월 버그 방지
    ("2025년 8월 21일에 수강신청합니다.", "2025.08.21.(목)", True, "8월_weekday버그"),
]

print("개선된 check_answer_match 테스트:")
pass_cnt = 0
for gen, gt, expected, label in cases:
    result = check_answer_match(gen, gt)
    ok = result["contains_gt"] == expected
    if ok:
        pass_cnt += 1
    status = "OK" if ok else "FAIL"
    arrow = "[+]" if result["contains_gt"] else "[-]"
    print(f"  [{status}] {label}: {arrow} (expected={expected})")
    if not ok:
        # Debug
        gt_year = _extract_year(gt)
        gt_raw_parts = re.split(r"~|,\s+(?!\d{3})", _strip_weekday(gt))
        for raw in gt_raw_parts:
            if not raw.strip():
                continue
            norm = _normalize(raw, fallback_year=gt_year)
            toks = _extract_key_tokens(raw.strip(), norm)
            print(f"       GT part: {raw.strip()!r}")
            print(f"       norm   : {norm!r}")
            print(f"       tokens : {toks}")
        gen_loose = _normalize(_strip_weekday(gen), fallback_year=gt_year)
        print(f"       gen_loose: {gen_loose!r}")

print()
print(f"결과: {pass_cnt}/{len(cases)} pass")
