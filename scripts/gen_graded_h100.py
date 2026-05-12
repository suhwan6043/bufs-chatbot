#!/usr/bin/env python3
"""Generate graded_h100.jsonl from Sonnet's direct fact-check verdicts."""
import json, pathlib

BASE = pathlib.Path(__file__).parent.parent / "reports" / "eval_5_7"

# Verdicts: (verdict, reason)
# Scoring: correct=3, partial=2, refusal_acceptable=1, wrong/refusal_unacceptable=0
VERDICTS = {
    0: ("partial", "핵심 학점(120, 30)은 맞으나 전공·교양 영역별 세부이수학점 미제공"),
    1: ("wrong", "대화 맥락(졸업 전공이수학점) 무시, 전공 종류 목록 나열로 응답"),
    2: ("partial", "절대평가·P/NP는 맞으나 OCU 컨소시엄 상대평가 누락"),
    3: ("partial", "총학점·교양총량은 맞으나 영역별 세부 이수학점(기초14+균형14+자유15 등) 미제공"),
    4: ("correct", "130학점·교양 43학점(균형교양14 포함)·취업커뮤니티 정확"),
    5: ("partial", "2학년 날짜 2.10(화)인데 2.9~2.12 범위로 표기 — 1학년 날짜 혼용, 부분 개선"),
    6: ("refusal_unacceptable", "장바구니·수강신청 전체 일정 정보 존재하나 '확인 못함'으로 거부"),
    7: ("partial", "1학년 2.9 날짜만 언급, 2·3·4학년 각 날짜(2.10·2.11) 누락"),
    8: ("partial", "1학년·전학년 날짜만 언급, 2학년·3·4학년 개별 날짜 누락, 방법 미흡"),
    9: ("partial", "일반교과목 절대평가·P/NP 맞으나 OCU 컨소시엄 상대평가 누락"),
    10: ("correct", "2005/2006/2007학번 이후 평점 기준 3단계 모두 정확"),
    11: ("correct", "학기당 6학점·졸업까지 24학점·직전학기 수강중 과목 불가 정확"),
    12: ("correct", "인터넷·주민센터 팩스민원·F동 무인발급기 3가지 방법 정확"),
    13: ("partial", "수수료(국문500·영문1000) 언급했으나 발급 방법(인터넷·무인발급기 등) 미제공"),
    14: ("correct", "일반학생증(앱설치)·국제학생증(영문명 등록 후 이메일) 방법 모두 정확"),
    15: ("correct", "중간고사 기간 2026-04-20~04-24 정확"),
    16: ("correct", "등록금 문의 재무팀(051-509-5382~4) 정확"),
    17: ("correct", "2020학번 교양이수학점 43학점 정확(어제 신버전 14학점 오기재 개선)"),
    18: ("partial", "자유학기제 일부 설명(진로탐색·취업역량 강화 등)했으나 학년별 프로그램 상세 누락"),
    19: ("correct", "휴학신청·학점이월·재수강 FAQ 주요 항목 정확"),
    20: ("partial", "취업역량학기제 P/NP 개념과 혼용, 성적평가선택제 신청 가능학점(학기당 6학점) 명시 미흡"),
    21: ("refusal_unacceptable", "2024학번 최대수강학점(18학점) 정보 존재하나 '찾을 수 없음' 거부"),
    22: ("correct", "2024학번 학점이월제 불가(2023학번 이후 폐지) 정확"),
    23: ("correct", "커리어블라썸 개강전·개강후 신청 절차 4단계 정확"),
    24: ("correct", "취업커뮤니티 담당 학사지원팀 정확(부서 정답)"),
    25: ("correct", "사회봉사 사회기여센터(scc.bufs.ac.kr) 문의처 정확"),
    26: ("correct", "2026-1학기 주요 학사일정 전체(개강~학위수여식) 정확"),
    27: ("correct", "5월 1일(금) 임시휴업일(노동절) 정확 — 정정 GT 기준 개선"),
    28: ("correct", "영어전공 5552·영어학부 5551 direct_answer bypass 정확"),
    29: ("correct", "국가장학금 문의 학생복지팀(509-5163) 정확 — 어제 partial 개선"),
    30: ("refusal_acceptable", "운동장 대여 정보 없어 문의처 안내 — 적절한 거부"),
    31: ("wrong", "주차(parking) 질문에 현장실습 학점인정 기준 반환 — 동음이의어 오인"),
    32: ("refusal_acceptable", "셔틀버스 시간표 정보 없어 — 적절한 거부"),
    33: ("correct", "성적포기제 C+이하/NP·복구불가·W표기·조건 전부 정확, 신청일정(5.7~5.19) 포함"),
    34: ("correct", "2024학년도 기본이수표 채플2·PSC세미나2·인성체험2·글쓰기2 등 상세 정확"),
    35: ("refusal_acceptable", "학사지원팀 운영시간 시스템에 없어 — 적절한 거부(아이러니하나 정확)"),
    36: ("refusal_acceptable", "외부인 와이파이 정보 없어 거부 — 어제 기숙사 입사신청 오안내보다 개선"),
    37: ("correct", "2026-1학기 주요 학사일정 전체 정확"),
    38: ("wrong", "대화 맥락(학사일정 표 요청)에 출석점수환산표 환각 생성"),
    39: ("partial", "1학년(2.9)·2학년(2.10)·전학년(3.6) 날짜 제공, 3·4학년(2.11) 누락"),
    40: ("correct", "성적 이의신청 기간 내 학생포털 신청·집중이수 별도 일정 안내 정확"),
    41: ("correct", "성적포기제 C+이하/NP·복구불가·W표기·학적변동시 취소·2026.5.7~5.19 일정 정확"),
    42: ("correct", "재수강 기준 C+ 이하 명시 — B0는 대상 외임을 암시, 어제 partial 개선"),
    43: ("refusal_unacceptable", "금요일 오후 수업(10~14교시) 정보 존재하나 '찾을 수 없음' 거부"),
    44: ("refusal_unacceptable", "채플 2분반 수업시간 정보 존재하나 '찾을 수 없음' 거부"),
    45: ("refusal_acceptable", "특정 과목코드 분반 데이터 없어 — 적절한 거부"),
    46: ("correct", "경찰학총론 목요일 5·6교시 정확"),
    47: ("partial", "26-1학기 휴업일 정보 일부(3.2 개강일 겸 공휴일 등) 학사일정 내 존재하나 미제공"),
    48: ("correct", "성적평가선택제 P/NP 신청조건·학점제한·이수구분 변경(8·2월)·신청일정 상세 정확"),
    49: ("partial", "식단표 URL 제공(https://www.bufs.ac.kr/...) — 어제 refusal 대비 개선"),
    50: ("correct", "부분적 성적포기제 C+이하/NP·복구불가·졸업요건 확인 필수·W표기 정확"),
    51: ("correct", "성적포기제 조건·결과·주의사항 전부 정확"),
    52: ("correct", "재수강 기준 C+ 이하 명시 — B0 해당 없음 암시, 어제 partial 개선"),
    53: ("partial", "2019학번 질문자에게 2023학번 졸업요건(120학점) 제공 — 학번 메타데이터 미반영"),
    54: ("refusal_unacceptable", "졸업장 수령·증명서 발급 경로 정보 존재하나 '찾을 수 없음' 거부"),
    55: ("refusal_unacceptable", "복수전공이수증명서 발급 방법 정보 존재하나 '찾을 수 없음' 거부"),
    56: ("correct", "졸업유예(학사학위취득유예) 조건·횟수·증명서·조기졸업자 신청불가 정확"),
    57: ("correct", "자퇴 방문/비방문 절차, 위임장, 등록금 반환 서류 모두 정확"),
    58: ("correct", "자퇴 절차 방문·비방문·서류 정확, 성인이더라도 부모님 통장 필요 안내 포함"),
    59: ("wrong", "졸업직전 계절학기 불가 원칙 미안내 — 일반 학점 제한만 언급, 핵심 규칙 누락"),
    60: ("wrong", "대화 맥락(졸업직전 계절학기 불가)에서 수강신청 제한 정보 반환 — 맥락 오해"),
    61: ("partial", "계절학기로 3학점 보충 후 졸업 가능 언급, 졸업직전 학기 계절학기 제한 조건 미설명"),
    62: ("refusal_unacceptable", "편입(재입학 5·11월 공지) 정보 존재하나 '찾을 수 없음' 거부"),
    63: ("correct", "조기졸업 평점기준·신청시기(5·11월)·방법·편입생불가 정확"),
    64: ("correct", "학번별 평점 기준 3단계·6학기 이상 이수 조건 정확"),
    65: ("correct", "2023학번 조기졸업 신청조건·4.3이상·120학점·6·7학기 등록자 정확"),
    66: ("partial", "'신입생만 해당' 오기재 포함 — 병적증명서 대체는 전체 학생 가능"),
    67: ("wrong", "군입대 절차 안내에 집중, 두 서류 모두 없는 경우 일반휴학 안내 누락"),
    68: ("refusal_unacceptable", "일반→병역휴학 변경 관련 정보 존재하나 '찾을 수 없음' 거부"),
    69: ("correct", "3/4선 기준 등록금 전액 인정(군휴학만)·학업성적인정신청서 제출 필요 정확"),
    70: ("wrong", "2024학번 조기졸업 질문에 2026학년도 학사일정+계절학기 안내 반환"),
    71: ("wrong", "학업성적인정신청서(3/4선 이후 군휴학 제출 서류) 정보 있음에도 거부"),
    72: ("wrong", "군휴학 관련 대화 맥락에서 수강신청 로그인 불가 안내 — 맥락 완전 오해"),
    73: ("partial", "학생이 '수강신청이 아니라 휴학'이라 정정 → 수강신청 불가+복학 필요 설명, 휴학 방법 미흡"),
    74: ("correct", "3/4선 이후 군휴학 가능, 복학 시 등록금 인정 조건 정확"),
    75: ("partial", "'신입생만 해당' 오기재 — 등록휴학은 전체 재학생 대상"),
    76: ("correct", "휴학 횟수 4회·병역/창업/임신육아 예외·연장 1회·1회 최대 2학기 정확"),
    77: ("correct", "미등록휴학 방학 중 신청·등록금 불요·재학생만·초과학기자 서면 정확"),
    78: ("wrong", "등록휴학 질문에 미등록휴학(방학 중 신청·등록금 불요) 내용 반환 — 정반대"),
    79: ("partial", "대화 맥락(등록휴학 장학금 반환 여부) 무시, 일반 장학금 종류 나열"),
    80: ("correct", "국가장학금 종류(I·II유형 등)·이중지원 예외·지원구간 산정자 우선 감면 정확"),
    81: ("partial", "장학금 문의 맥락에서 학과사무실 번호 나열 — 맥락 오류(정답: 학생복지팀 5164)"),
    82: ("wrong", "장학금 담당 학생복지팀(5164) 대신 한국장학재단(1599-2000) 안내"),
    83: ("correct", "교환학생·산업체 실습 학기 중 취업커뮤니티 과목 면제 + 조건 정확 — 어제 wrong 개선"),
    84: ("partial", "교내장학금 신청 절차 일부 설명, '어디 문의' 핵심 질문(학생복지팀) 미답변"),
    85: ("correct", "취업커뮤니티 면제 조건(나머지 3과목 이수 후 신청) 구체 설명 — 어제 partial 개선"),
    86: ("refusal_unacceptable", "2019년 1학기 졸업년도(일반 2023.2 or 조기 2022.2) 정보 존재하나 거부"),
    87: ("refusal_acceptable", "마라탕 맛집 — 학사 범위 외 적절한 거부"),
    88: ("partial", "학사지원팀 팀장 개인번호 없어 대표번호(5182~5183) 제공 — 부분 대응"),
    89: ("refusal_unacceptable", "정보통신팀 번호(5711·5741) 정보 존재하나 '찾을 수 없음' 거부"),
    90: ("refusal_unacceptable", "해외교류 이수 과목 일반 안내 정보 있음에도 거부"),
    91: ("partial", "취업전략과 경력관리Ⅰ·Ⅱ로 안내 — 2023학번 기준 진로탐색·진로설정·취커Ⅰ·Ⅱ와 과목명 불일치"),
    92: ("partial", "기숙사 식당 번호(070-8220-7480) 제공 — 어제 refusal_acceptable 대비 개선"),
    93: ("refusal_acceptable", "식당 메뉴 정보 없어 거부 — 어제 wrong(전공·도서관 환각) 대비 개선"),
    94: ("partial", "대학원 성적조회 편중 안내, 일반 학부 성적조회(학생포털) 방법 미제공"),
    95: ("refusal_unacceptable", "계절학기 일정(하계 5.26~5.28 수강신청, 6.22~7.10 수업) 정보 있음에도 거부"),
    96: ("correct", "휴학 총 4회·병역/창업/임신육아 예외·연장 포함·신입생 첫학기 제한 정확"),
    97: ("correct", "공인결석 취소 3단계(처리전·처리중·승인후) 방법 모두 정확"),
    98: ("wrong", "소년원 출소 공인결석 불인정 정보 있음에도 '찾을 수 없음' 거부"),
    99: ("partial", "공인결석 신청 방법 설명, 회사 행사 불인정 여부 명확히 안내하지 않음"),
    100: ("correct", "조기취업·훈련·현장교육·행사 참여 인정 사유 목록, 체육특기자 1/2 제한 정확"),
    101: ("correct", "생리결석 매달 1회 정확"),
    102: ("correct", "입원치료 공인결석 가능, 학기 내 최대일수 이내·1개월 신청 조건 정확"),
    103: ("partial", "최대 인정일수 구체 숫자 없음 — '학기 내 최대일수 이내'만 반복, 질문에 직접 답 못함"),
    104: ("correct", "본인·배우자의 (외)조부모 사망 시 공인결석 3일 정확"),
    105: ("correct", "온라인 휴/복학 신청 기간 2026-07-06~2026-08-30 정확"),
    106: ("wrong", "자동차 주차등록 질문에 LMS 화상강의 주차 안내 — 동음이의어 오인"),
    107: ("refusal_acceptable", "자동차 주차등록 정보 없어 — 적절한 거부"),
    108: ("partial", "인터넷·주민센터·무인발급기 3가지 방법 제공, EMS 해외우편 방법 누락"),
    109: ("correct", "①인터넷 ②주민센터 ③무인발급기 3가지 발급 경로 상세 설명 정확"),
    110: ("refusal_unacceptable", "편입(재입학 5·11월 공지) 정보 있음에도 거부"),
    111: ("correct", "발급 가능 증명서 13종 목록 + 수기발급 2종(장학금지급확인서·비수혜증명서) 정확"),
    112: ("refusal_acceptable", "타 학교 편입 정보 없어 — 적절한 거부"),
    113: ("correct", "재입학 1학기는 11월 공지, 2학기는 5월 공지 정확"),
    114: ("correct", "복수전공 재적·수료증명서 발급 가능, 제2전공 졸업 전 표기 불가 정확"),
    115: ("wrong", "복수전공이수증명서 발급 방법 대신 '졸업 전 표기 불가' 내용만 반환"),
    116: ("wrong", "발급 가능 여부 정보 있음에도 '찾을 수 없음' 거부"),
    117: ("wrong", "복수전공이수증명서 발급 가능 정보 있음에도 '확인 못함' 거부"),
    118: ("correct", "수료증명서 발급 기준(학칙 제42조)·학적구분별 발급 안내 정확"),
    119: ("refusal_unacceptable", "대화 맥락(수료증명서 학칙 42조 내용) 관련 정보 있음에도 거부"),
    120: ("refusal_acceptable", "학칙 제42조 구체 내용 시스템에 없어 거부 — 어제 교직이수 환각보다 개선"),
    121: ("correct", "성적 확인 및 정정기간 2026-06-15~2026-06-19 정확 — 어제 partial(방법만) 개선"),
    122: ("correct", "계절학기 후 졸업 가능, 6학점/24학점 한도, 조기졸업 조건 포함 정확"),
    123: ("correct", "증명서 12종 목록·발급 방법·개명 처리·영문명 등록 포괄 정확"),
    124: ("correct", "성적평가선택제 P/NP 조건·이수구분 변경(8·2월)·등급제 변경 불가 상세 정확"),
    125: ("correct", "이수구분 변경 신청 필요 이유(제2전공·전부과 승인 학점 인정) 정확"),
    126: ("refusal_acceptable", "무의미 입력에 거부 — 어제 학교 홍보 정보 환각보다 개선"),
    127: ("correct", "일반학생증·국제학생증 발급·재발급·사진변경·ISIC·모바일열람증 상세 정확"),
    128: ("correct", "수수료(국문500·영문1000)·발급 목록·수기발급·방법 모두 정확"),
    129: ("correct", "신입생 국제학생증 발급 절차(영문명 등록~3/9, 이메일 신청) 정확"),
    130: ("correct", "재학생 학생증 재발급 F동1층 학사지원팀(104), 1회1000원·2회이상2000원 정확"),
    131: ("partial", "하계 수강신청 기간(5.26~5.28) 제공, 수업 기간(6.22~7.10) 누락"),
    132: ("refusal_unacceptable", "계절학기 정의 정보 있음에도 '찾을 수 없음' 거부"),
    133: ("partial", "졸업시험 조회 방법·P/NP 설명 제공, 시험 일정(중간 4.20~4.24, 기말 6.8~6.12) 누락"),
    134: ("partial", "중간고사 기간 제공(4.20~4.24), 기말고사 기간(6.8~6.12) 누락"),
    135: ("partial", "신입생 특별강좌 일정 제공, 2026년도 적용 여부 불명확(2024학년도 도입 내용)"),
    136: ("correct", "개명 학적부기재사항변경 신청서·초본(원본)·우편/방문 신청·포털 절차 정확"),
}

def main():
    # Load H100 responses
    h100 = {}
    with open(BASE / "responses_h100.jsonl", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            h100[obj["idx"]] = obj

    # Load GT from graded.jsonl
    gt_data = {}
    with open(BASE / "graded.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                gt_data[obj["idx"]] = obj
            except json.JSONDecodeError:
                pass

    # Write graded_h100.jsonl
    out_path = BASE / "graded_h100.jsonl"
    with open(out_path, "w", encoding="utf-8") as out:
        for idx in range(137):
            h = h100.get(idx, {})
            g = gt_data.get(idx, {})
            verdict, reason = VERDICTS.get(idx, ("wrong", "채점 누락"))
            entry = {
                "idx": idx,
                "question": h.get("question", g.get("question", "")),
                "new_answer": h.get("h100_answer", ""),
                "ground_truth": g.get("ground_truth", ""),
                "sources": g.get("sources", []),
                "verdict": verdict,
                "reason": reason,
            }
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Written {out_path}")

    # Print summary
    from collections import Counter
    counts = Counter(v for v, _ in VERDICTS.values())
    total = sum(counts.values())
    score = (counts["correct"]*3 + counts["partial"]*2 + counts["refusal_acceptable"]*1) / (total*3)
    print(f"\nH100 verdict summary ({total} entries):")
    print(f"  correct:              {counts['correct']:3d} ({counts['correct']/total*100:.1f}%)")
    print(f"  partial:              {counts['partial']:3d} ({counts['partial']/total*100:.1f}%)")
    print(f"  wrong:                {counts['wrong']:3d} ({counts['wrong']/total*100:.1f}%)")
    print(f"  refusal_unacceptable: {counts['refusal_unacceptable']:3d} ({counts['refusal_unacceptable']/total*100:.1f}%)")
    print(f"  refusal_acceptable:   {counts['refusal_acceptable']:3d} ({counts['refusal_acceptable']/total*100:.1f}%)")
    print(f"  Weighted score: {score*100:.1f}%")

if __name__ == "__main__":
    main()
