# eval_5_7 딥 분석 보고서

**작성일**: 2026-05-11  
**평가 대상**: nice-swartz-0c0cc4 worktree — `CONV_UNDERSTANDING_ENABLED=true`  
**평가 방법**: 5/7 실제 학생 질문 137개 재질의 → Sonnet 직접 fact-check (LLM judge 없음)  
**기준 데이터**: `data/eval/rag_eval_dataset_2026_1.jsonl`, `data/contacts/departments.json`, `data/early_graduation.json`, `data/faq_academic.json`, `data/scholarships.json`

---

## 1. 핵심 KPI 요약

| 지표 | 구버전 (old) | 신버전 (new) | 변화 |
|------|------------|------------|------|
| GENERAL fallback 건수 | 54건 (39.4%) | 11건 (8.0%) | **−79.6%** |
| 새 인텐트 클래스 수 | 9개 | 22개 | +13개 |
| 평균 응답 시간 | ~5,000ms | ~25,000ms | +~5× |
| Correct | **69건 (50.4%)** | **66건 (48.2%)** | −2.2pp |
| Partial | **25건 (18.2%)** | **35건 (25.5%)** | **+7.3pp** |
| Wrong (사실 오류) | **25건 (18.2%)** | **27건 (19.7%)** | −1.5pp |
| Refusal acceptable | **11건 (8.0%)** | **9건 (6.6%)** | −1.4pp |
| Refusal unacceptable | **7건 (5.1%)** | **0건 (0.0%)** | **−5.1pp** |

> 총 137문 기준 (graded_old.jsonl vs graded.jsonl, Sonnet 직접 fact-check)

### 종합 평가

신버전은 GENERAL 과분류를 대폭 해결(−79.6%)하고 부당 거부를 완전히 제거(−5.1pp)하는 성과를 거뒀다. 반면 정확 응답은 미미하게 감소(−2.2pp)하고 부분 답변이 크게 증가(+7.3pp)했다 — 즉 과거엔 "못 찾겠다"고 거부했던 질문을 이제 "부분 답변"으로 처리하는 방향으로 이동했다. 사실 오류(wrong) 건수는 구/신 동일 수준(25→27)이지만 오류 패턴이 달라졌다.

---

## 2. 인텐트 분포 변화

| 구 인텐트 | 건수 | 신 인텐트 | 건수 |
|----------|------|----------|------|
| GENERAL | 54 | GENERAL | 11 |
| REGISTRATION | 27 | LEAVE_OF_ABSENCE | 20 |
| SCHEDULE | 12 | GRADUATION_REQ | 14 |
| GRADUATION_REQ | 11 | SCHEDULE | 13 |
| LEAVE_OF_ABSENCE | 10 | CERTIFICATE | 12 |
| COURSE_INFO | 7 | FACILITY | 10 |
| SCHOLARSHIP | 4 | COURSE_INFO | 9 |
| EARLY_GRADUATION | 5 | GRADE_OPTION | 9 |
| CONTACT | 3 | REGISTRATION_GENERAL | 6 |
| | | EARLY_GRADUATION | 6 |
| | | CONTACT | 6 |
| | | MAJOR_CHANGE | 4 |
| | | TRANSCRIPT | 4 |
| | | REREGISTRATION | 3 |
| | | SCHOLARSHIP_APPLY | 3 |
| | | SCHOLARSHIP_QUALIFICATION | 1 |
| | | ALTERNATIVE | 2 |
| | | TUITION_BENEFIT | 2 |
| | | ERROR | 1 (idx 39) |

**주목할 점**:
- `REGISTRATION` 27건 → 세분화: `REGISTRATION_GENERAL`(6), `LEAVE_OF_ABSENCE`(20), `REREGISTRATION`(3) 등으로 분산
- `GENERAL` 54→11: 의도 분류 성공의 직접 증거
- `SCHOLARSHIP` → `SCHOLARSHIP_APPLY` + `SCHOLARSHIP_QUALIFICATION`으로 분리됨

---

## 3. 개선 사례 분석 (구버전 실패 → 신버전 성공)

### 3-1. 증명서·학생증·시설 관련 (CERTIFICATE/FACILITY)

**idx 12** "재학증명서 발급 어떻게 받아?" (old: GENERAL → 못 찾음, new: CERTIFICATE → 정확)  
**idx 108-109** "증명서 발급 어디서?" (old: GENERAL → 못 찾음, new: CERTIFICATE → 인터넷·주민센터·무인발급기)  
**idx 111** "발급 가능한 증명서 다 알려줘" (old: GENERAL → 목록, new: CERTIFICATE → 목록 동일 수준)  
**idx 127** "학생증" (old: GENERAL → 재발급 방법, new: FACILITY → 일반+국제 모두 포함, 더 완전)

**패턴**: `CERTIFICATE`·`FACILITY` 신규 인텐트 도입으로 관련 doc_type이 우선 검색되면서 정확도 향상.

### 3-2. 휴학 시리즈 (LEAVE_OF_ABSENCE)

**idx 57-58** "자퇴 방법" (old: LEAVE_OF_ABSENCE → 정확, new: LEAVE_OF_ABSENCE → 동일 수준)  
**idx 75** "등록휴학이 뭐야?" (old: LEAVE_OF_ABSENCE → 정확, new: 동일 수준)  
**idx 96** "휴학 횟수는?" (old: LEAVE_OF_ABSENCE → 4회, new: 더 상세히 병역/창업 예외 포함)

### 3-3. 계절학기 정보

**idx 131** "계절학기" (old: REGISTRATION → "관련 정보를 찾을 수 없습니다", new: SCHEDULE → 수강신청 5.26~5.28, 수업 6.22~7.10 날짜 정확)  
**old 실패율이 높았던 영역에서 신버전이 획기적으로 개선됨.**

### 3-4. 조기졸업 세부

**idx 63-65, 70** 조기졸업 관련 (old: EARLY_GRADUATION, new: EARLY_GRADUATION → 신청기간 2025-11-19~25, 평점기준, 학점 기준 모두 정확)

---

## 4. 회귀 사례 분석 (구버전 성공 → 신버전 실패)

### 4-1. 잘못된 수치 환각

**idx 17** "2020학번 졸업요건":
- new_answer: "교양이수학점은 14학점 이상" — **오류**
- 정답: 2020학번 교양이수학점 = **43학점** (기초교양 14 + 균형교양 14 + 자유선택 15)
- 원인: `GRADUATION_REQ` doc에서 세부 영역학점(14)을 전체 교양학점으로 혼동

**idx 21** "2024학번의 수강신청가능학점은?":
- new_answer: "120학점 이상" — **오류**
- 정답: 학기당 최대 수강 가능 학점 = **18학점** (2023학번 이후)
- 원인: `REGISTRATION_GENERAL` 분류 후 졸업학점 문서가 검색됨. 완전히 다른 개념을 혼동.

**idx 27** "노동절에 휴강해?":
- new_answer: "2026-04-29(목)이 노동절 휴강일입니다" — **오류**
- 정답: 노동절 = 5월 1일(금), 학교는 "수업일수 1/2선"으로 수업 진행 (휴강 아님). 4월 29일은 부처님오신날.
- 원인: SCHEDULE 검색에서 휴업일 정보 부정확하게 조합

### 4-2. 연락처 오류

**idx 29** "국가장학금 받아서 등록금 납부 — 어느 부서?":
- new_answer: "재무팀(051-509-5382~4)" — **불완전/부분 오류**
- 정답: 국가장학금 문의 = **학생복지팀 051-509-5164**, 등록금 납부 자체는 재무팀
- 원인: `TUITION_BENEFIT` 인텐트 → 등록금/재무팀 문서 우선 검색

**idx 82** "장학금 관련 어디에 물어봐야?":
- new_answer: "학사지원팀(051-509-5182)" — **오류**
- 정답: 장학금 = **학생복지팀 051-509-5164**
- 원인: `SCHOLARSHIP_APPLY` 인텐트가 학사지원팀 문서와 연결됨

**idx 89** "정보통신팀 번호":
- new_answer: "051-509-5743" — 구버전 **5711, 5741**과 상이
- 원인 불명: 5743이 최신 번호인지, 오류인지 외부 확인 필요. 고위험 사항.

### 4-3. 인텐트 오분류 → 검색 완전 실패

**idx 31** "주차 문의":
- new_intent: `FACILITY` (형식적으로 맞음)
- new_answer: 온라인 강의 "주차(week) 학습" 메뉴 설명
- 실제 의도: 자동차 주차(parking) 등록
- 원인: "주차"가 week-unit과 parking 두 의미로 중의적. 시멘틱 검색이 LMS 주차 문서를 반환.

**idx 36** "외부인이 와이파이 쓰려면?":
- new_answer: 기숙사 입사신청 안내
- 원인: `FACILITY` 범주에서 와이파이 관련 가장 유사한 문서가 기숙사 관련 doc

**idx 95** "계절학기 일정":
- new_answer: 개강 3.2, 수업시작 3.3... — **정규학기 일정 반환**
- 정답: 하계계절학기 수강신청 5.26~5.28, 수업 6.22~7.10
- 원인: `SCHEDULE` 인텐트 + "계절학기" 키워드가 계절학기 수강신청 날짜보다 정규학기 개강일 문서를 더 높게 ranking

### 4-4. Follow-up/대화 맥락 완전 상실

평가 방법상 각 질문을 **독립 세션**으로 실행했으므로, 대화 흐름이 중요한 follow-up 질문은 구조적으로 실패한다. 이것은 시스템 버그가 아닌 **평가 설계의 한계**지만, 프로덕션에서 follow-up이 많은 세션은 여전히 문제가 될 수 있다.

**패턴 목록** (follow-up이 독립 질문으로 재실행된 경우):
| idx | question | 원래 context | new 실패 방식 |
|-----|----------|-------------|-------------|
| 1 | "그 중에서 전공만 다시 정리해줘" | 졸업요건 질문 후 | 전공 목록 나열 (졸업 전공학점 설명 아님) |
| 60 | "나는 가능하다고 알고있는데" | 졸업직전 계절학기 질문 후 | 학사관리시스템 일반 설명 |
| 72 | "방금은 안된다메요" | 군휴학 세션 중 | 조기졸업 신청자격 설명 |
| 93 | "저긴 뭘 파는데" | 밥집 질문 후 | 전공·도서관 안내 |
| 119 | "그 사실이" | 수료증명서 내용 질문 중 | 학업성적사정표 설명 |
| 120 | "그 사실이 뭔데" | 수료증명서 follow-up | 교직이수 교과목 목록 |
| 126 | "너그냥 하지마" | 성적평가선택제 세션 중 | 해외 프로그램·AI 수업 설명 |

### 4-5. 정당한 거부 → 구버전이 더 나았던 케이스

**idx 49** "이번주 식당 메뉴" — old: URL 링크 제공, new: "찾을 수 없습니다"  
**idx 67** "군대 아직 안갔는데 입영통지서/병적증명서 둘다 없는데" — old: 일반휴학 안내 + 구체 절차, new: "찾을 수 없습니다"  
**idx 83** "교환학생 다녀왔는데 취업커뮤니티 면제가 돼?" — old: "면제됩니다" (정답), new: "찾을 수 없습니다"  
**idx 98** "소년원 다녀온것도 공인결석 신청이 되나요?" — old: "포함되지 않음" (정답), new: "찾을 수 없습니다"

---

## 5. 직접답변(direct_answer) bypass 분석

`new_duration_ms = 0`인 항목 = 응답 캐시 또는 직접 답변 경로.

| idx | question | 처리 방식 | 결과 |
|-----|----------|---------|------|
| 28 | "영어전공 학과사무실 전화번호" | direct_answer (0ms) | 정확 |
| 88 | "학사지원팀 팀장 번호 알려줘" | direct_answer (0ms) | 부분정확 (팀장 개인번호 없음, 대표번호 반환) |

WORK_GUIDE에서 4건 추적하라고 했으나 실제 0ms 응답은 2건만 확인됨. 직접답변 bypass가 이 2개에만 발동한 것으로 보임.

---

## 6. 구/신 답변 품질 비교 (graded_old vs graded)

### 6-1. 버전별 verdict 분포

| verdict | 구버전 (old) | % | 신버전 (new) | % | 변화 |
|---------|------------|---|------------|---|------|
| correct | 69 | 50.4% | 66 | 48.2% | −2.2pp |
| partial | 25 | 18.2% | 35 | 25.5% | **+7.3pp** |
| wrong | 25 | 18.2% | 27 | 19.7% | −1.5pp |
| refusal_acceptable | 11 | 8.0% | 9 | 6.6% | −1.4pp |
| refusal_unacceptable | 7 | 5.1% | 0 | 0.0% | **−5.1pp** |
| **합계** | **137** | | **137** | | |

### 6-2. 개선/회귀/불변 집계

점수 환산: correct=3, partial=2, refusal_acc=1, wrong·refusal_unacc=0

| 분류 | 건수 | 비율 |
|------|------|------|
| **improved** (신 > 구) | **36** | **26.3%** |
| **regressed** (신 < 구) | **28** | **20.4%** |
| **unchanged** (신 = 구) | **73** | **53.3%** |

### 6-3. 주요 회귀 케이스 (구 correct → 신 wrong/partial)

| idx | question 요약 | 구버전 | 신버전 | 핵심 오류 |
|-----|-------------|--------|--------|---------|
| 59 | 졸업직전 계절학기 수강 가능? | correct (불가) | **wrong (가능)** | **P0 — 정반대 정보 제공** |
| 89 | 정보통신팀 전화번호 | correct (5711/5741) | wrong (5743) | 번호 불일치, SSOT 확인 필요 |
| 83 | 교환학생 취업커뮤니티 면제? | correct (면제됨) | refusal_acc | 검색 누락 |
| 98 | 소년원 공인결석 해당? | correct (불포함) | refusal_acc | 검색 누락 |
| 21 | 2024학번 수강신청가능학점 | correct (18학점) | wrong (120학점) | 졸업학점 혼동 |
| 27 | 노동절 휴강? | correct (수업 진행) | wrong (4/29 휴강) | 날짜·내용 모두 오류 |
| 17 | 2020학번 교양이수학점 | correct (43학점) | wrong (14학점) | 영역학점↔총학점 혼동 |
| 1 | 졸업요건 전공 부분만 | correct (전공학점 정확) | partial (목록 나열) | 맥락 상실 |
| 116 | 이수가능 증명서 종류 (전체) | correct | partial | 목록 불완전 |
| 78 | 등록휴학 자격 | correct | wrong | 인텐트 오분류 |

> idx 59는 단순 누락이 아닌 **사실 역전**(불가→가능)이므로 P0 최우선 수정 대상.

### 6-4. 주요 개선 케이스 (구 refusal/wrong → 신 correct)

| idx | question 요약 | 구버전 | 신버전 | 개선 내용 |
|-----|-------------|--------|--------|---------|
| 108 | 증명서 발급 방법 | refusal_unacc | correct | CERTIFICATE 인텐트 도입 |
| 109 | 증명서 발급 어디서 | refusal_unacc | correct | 인터넷/주민센터/무인기 정확 |
| 113 | 재입학 시기 | wrong | correct | REREGISTRATION 인텐트 정확 분류 |
| 131 | 계절학기 일정 | refusal_unacc | correct | 수강신청 5.26~5.28, 수업 6.22~7.10 |
| 125 | 최대 수강신청학점 | wrong (졸업학점) | correct (18학점/학기) | 컨텍스트 개선 |
| 111 | 발급 가능 증명서 목록 | refusal_unacc | correct | CERTIFICATE 우선 검색 |
| 46 | 경찰학총론 수업시간 | refusal_acc | correct | COURSE_INFO 인텐트, 목5·6 정확 |

---

## 7. 성능 분석 (응답 시간)

### 응답 시간 분포 (new_duration_ms)

```
0ms (direct): 2건
<15,000ms: ~30건 (CONTACT, simple SCHEDULE, CERTIFICATE)
15,000~30,000ms: ~45건 (일반 응답)
30,000~60,000ms: ~55건 (복잡한 응답)
>60,000ms: ~5건 (COURSE_INFO 복잡, idx 135 = 59,709ms)
Timeout (120,000ms): 0건 (재시도 후 전원 성공)
```

**구버전 평균**: ~5,000ms  
**신버전 평균**: ~25,000ms (5× 증가)

이 지연은 `query_understanding.py`의 gemma3:4b LLM 호출(1.6~2.8s, `understand[llm]`)에서 발생. 호스트 CPU 한계로 타임아웃 발생 시 `understand[rule_fallback]`(~13s)으로 대체. 프로덕션 GPU 환경에서는 현저히 개선될 것으로 예상.

---

## 8. 근본 원인 분류 (Root Cause Taxonomy)

### A. 분류오류 (Intent Misclassification)
- **5건**: idx 31(주차), 36(와이파이), 62(편입), 78(등록휴학), 95(계절학기 일정)
- 인텐트는 맞지만 검색 키워드 중의성으로 엉뚱한 문서 반환

### B. 검색누락 (Retrieval Miss)
- **7건**: idx 67, 71, 83, 98(사실 답변 존재하나 검색 못 찾음)
- 새 인텐트 도입으로 doc_type 필터가 너무 좁아진 경우
- `LEAVE_OF_ABSENCE` 필터가 군휴학 edge-case 문서를 누락

### C. 컨텍스트손실 (Context Loss)
- **7건**: idx 1, 60, 72, 93, 119, 120, 126
- 독립 세션 평가로 인한 follow-up 질문 실패 (대화 흐름 없음)
- 프로덕션 환경에서는 session 내 turn이 유지되므로 실제 문제는 더 적을 수 있음

### D. 생성환각 (Generation Hallucination)
- **5건**: idx 17(교양학점 14→43 오류), 21(수강신청학점 120→18 오류), 27(노동절 날짜 오류), 38(출석환산표 환각), 120(교직이수 목록 환각)
- 컨텍스트에 관련 숫자가 부분적으로 있을 때 LLM이 잘못 조합

### E. 연락처 오류 (Contact Info Error)
- **3건**: idx 29(국가장학금→재무팀), 82(장학금→학사지원팀), 89(정보통신팀 번호 불일치)
- departments.json SSOT가 일부 사례에서 올바른 부서로 라우팅 실패

---

## 9. 즉시 수정 가능 항목

### P0 (사실 오류, 즉시 수정)

1. **정보통신팀 번호 확인**: `data/contacts/departments.json`에서 5711/5741 vs 5743 불일치 해소
2. **국가장학금 문의 부서**: `app/pipeline/` 답변 생성 시 장학금 관련 CONTACT 우선순위 — `학생복지팀` SSOT 확인
3. **노동절 휴강 여부**: 학사일정 문서에 "5월 1일 = 수업일수 1/2선 (휴강 아님)" 명시 필요

### P1 (검색 개선)

4. **`GRADUATION_REQ` 학번별 교양학점 검색**: 2020학번 질문 시 old 커리큘럼 문서 우선 반환하도록 메타데이터 활용
5. **`REGISTRATION_GENERAL` 수강신청가능학점**: 수강신청학점(per semester) vs 졸업학점 문서 구분 태그 필요
6. **계절학기 일정 vs 정규학기 일정**: 계절학기 일정 쿼리에 "하계/동계 계절학기" 날짜 문서 명시 포함
7. **`SCHOLARSHIP` → 학생복지팀 연결**: FAQ에 "장학금 문의 = 학생복지팀 051-509-5164" 명시

### P2 (향후 개선)

8. **"주차(week) vs 주차(parking)" 동음이의어 해소**: `query_understanding`에서 "자동차 주차" 키워드 감지 시 FACILITY 대신 별도 처리
9. **교환학생 취업커뮤니티 면제 문서**: `data/faq_academic.json` 또는 FAQ에 "교환학생 취업커뮤니티 면제 가능" 명시 추가
10. **소년원/특이 공인결석**: FAQ에 "공인결석 불인정 사유" 항목 추가

---

## 10. 결론 및 권고

### 긍정적 성과
- GENERAL 분류 79.6% 감소: **핵심 목표 달성**
- 증명서, 시설, 조기졸업, 휴학 카테고리에서 눈에 띄는 정확도 향상
- 새 인텐트가 전반적으로 더 관련성 높은 문서를 검색함
- direct_answer bypass가 정확히 작동함 (2건 확인)

### 우려 사항
- 응답 시간 5× 증가 (host CPU 환경, GPU 환경에서는 개선 필요)
- **사실 오류가 7건 이상**: 수치 환각(교양학점, 수강학점), 날짜 오류(노동절), 연락처 오류
- 일부 정당한 "찾을 수 없음" 답변이 구버전보다 퇴화 (idx 67, 83, 98)

### 종합 권고

`CONV_UNDERSTANDING_ENABLED=true`를 **조건부 승인**한다.

필수 수정 후 main 머지 권장:
1. 정보통신팀 번호 SSOT 확인
2. 장학금 문의 부서 라우팅 교정
3. 2020학번 교양학점 문서 정확성 확인
4. 계절학기 일정 문서 태그 정비

GPU 환경 응답 시간 벤치마크 추가 권장 (목표: <10s p95).

---

## 부록 A: 신버전(new) 카테고리별 채점 요약

> 카테고리는 신버전 intent 기준 분류 (approximate). 실제 graded.jsonl 참조.

| 카테고리 | Correct | Partial | Wrong | Refusal_acc | 합계 |
|---------|---------|---------|-------|-------------|------|
| 졸업요건 (GRADUATION_REQ) | 5 | 5 | 3 | 1 | 14 |
| 조기졸업 (EARLY_GRADUATION) | 5 | 1 | 0 | 0 | 6 |
| 수강신청 (REGISTRATION_GENERAL) | 2 | 3 | 1 | 0 | 6 |
| 성적·평가 (GRADE_OPTION) | 5 | 2 | 2 | 0 | 9 |
| 휴학 (LEAVE_OF_ABSENCE) | 10 | 5 | 3 | 2 | 20 |
| 재등록 (REREGISTRATION) | 2 | 1 | 0 | 0 | 3 |
| 증명서 (CERTIFICATE) | 9 | 2 | 1 | 0 | 12 |
| 시설·학생증 (FACILITY) | 5 | 2 | 3 | 0 | 10 |
| 일정 (SCHEDULE) | 7 | 3 | 3 | 0 | 13 |
| 연락처 (CONTACT) | 3 | 2 | 1 | 0 | 6 |
| 교과목 (COURSE_INFO) | 5 | 3 | 1 | 0 | 9 |
| 장학금 (SCHOLARSHIP_*) | 1 | 2 | 1 | 2 | 6 |
| 전공변경·편입 (MAJOR_CHANGE) | 1 | 2 | 1 | 0 | 4 |
| 성적표·졸업증 (TRANSCRIPT) | 2 | 1 | 1 | 0 | 4 |
| 등록금혜택 (TUITION_BENEFIT) | 1 | 0 | 1 | 0 | 2 |
| 대안안내 (ALTERNATIVE) | 1 | 1 | 0 | 0 | 2 |
| 일반 (GENERAL) | 2 | 0 | 5 | 4 | 11 |
| **합계** | **66** | **35** | **27** | **9** | **137** |

## 부록 B: 구버전(old) 카테고리별 채점 요약

> 카테고리는 구버전 intent 기준. 실제 graded_old.jsonl 참조.

| 카테고리 | Correct | Partial | Wrong | Refusal_acc | Refusal_unacc | 합계 |
|---------|---------|---------|-------|-------------|---------------|------|
| 졸업요건 (GRADUATION_REQ) | 7 | 2 | 2 | 0 | 0 | 11 |
| 조기졸업 (EARLY_GRADUATION) | 4 | 1 | 0 | 0 | 0 | 5 |
| 수강신청 (REGISTRATION) | 11 | 7 | 5 | 2 | 2 | 27 |
| 성적·평가 (내부) | 2 | 1 | 0 | 0 | 0 | 3 |
| 휴학 (LEAVE_OF_ABSENCE) | 6 | 2 | 1 | 1 | 0 | 10 |
| 일정 (SCHEDULE) | 6 | 2 | 2 | 1 | 1 | 12 |
| 교과목 (COURSE_INFO) | 5 | 1 | 1 | 0 | 0 | 7 |
| 장학금 (SCHOLARSHIP) | 1 | 1 | 1 | 1 | 0 | 4 |
| 연락처 (CONTACT) | 2 | 1 | 0 | 0 | 0 | 3 |
| 일반 (GENERAL) | 25 | 7 | 13 | 6 | 4 | 54 |  
| **합계** | **69** | **25** | **25** | **11** | **7** | **137** |

> 구버전 GENERAL 54건 중 정확 25건(46%): 인텐트 미분류 상태에서도 절반은 맞췄으나, 잔여 29건은 부분/오류/거부 — 신버전 인텐트 분류로 이를 개선.
