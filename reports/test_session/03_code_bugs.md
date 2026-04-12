# 코드/알고리즘 버그 심층 진단 — 7건

> 실패 24건을 역추적한 결과 **파이프라인 코드 레벨에서 7가지 구조적 버그**가 식별되었다. 각 버그는 단일 문항이 아닌 **여러 실패를 설명**하는 공통 원인이다.

---

## 버그 #1 — `direct_answer` 의미 필터 부재 (영향: 5건)

### 증상
그래프 노드의 `direct_answer` 메타데이터가 질문의 세부 의미와 다름에도 수락되어 출력됨.

### 영향 문항

| qid | 질문 | 그래프 DA | 문제 |
|---|---|---|---|
| e02 | 조기졸업 **자격 요건**은? | "조기졸업 **신청기간**은 2026년 5월 20일~5월 26일" | 기간 ≠ 자격 |
| m01 | 전과 **언제 신청 가능**? | "2024~2025학번 제1·2전공 신청 및 변경(전과) **기간은 2026년 5월 18일~29일**" | 주기적 안내 질문에 구체 일정으로 답 |
| r05 | 2019학번 이후 재수강 **제한 기준**은? | "C+ **이하**의 과목만 가능합니다" | 제한 ≠ 가능 조건 |
| s04 | 2025학년도 **전기** 학위수여식은? | "2025학년도 **후기** 학위수여식은 2026-08-14" | 전기 ≠ 후기 |
| u07 | **2027학년도** 수강신청은? | "수강신청_1학년: **2026**-02-09" | 2027 ≠ 2026 |

### 현재 코드
`app/pipeline/context_merger.py:297-326` 주변 `merge()` 안의 direct_answer 수락 로직과, 최종 `_answer_unit_aligns(question, direct_answer)` 게이트(L408-416):
```python
if direct_answer and question and not _answer_unit_aligns(question, direct_answer):
    logger.info("direct_answer final-gate rejected: ...")
    direct_answer = ""
```

### 왜 막지 못했나

`AnswerUnit.aligns()`는 "질문의 **단위(unit)** 중 하나가 답변에 존재하는가"만 검사한다:
- e02 질문 `{date, time, credit}` (rule_list), DA에 `date` 있음 → **통과**
- s04 질문 `{date, time}`, DA에 `date` 있음 → **통과**
- r05 질문 `{credit}`(단위 기대), DA에 `grade`(C+) → 현재 `grade`가 필수 단위라 거부 — 하지만 **r05는 실제로는 거부되지 않았다**. 확인 필요.
- u07 질문 `{date}`, DA에 `date` 있음 → **통과** (연도 상관없이)

즉 **단위 체크는 통과했지만 질문의 핵심 의미(전기/후기, 2026/2027, 자격/기간, 제한/가능)가 다름**을 감지하지 못한다.

### 근본 원인

`AnswerUnit`은 "숫자·날짜·URL 같은 **형식**이 있는가"는 잘 보지만, **질문의 key term이 답변에도 등장하는가**는 검증하지 않는다.

- "2027" 같은 **고유 구별자**가 질문에 있으면 답변에도 있어야 한다
- "전기"/"후기" 같은 **이진 속성**이 질문에 있으면 답변에도 있어야 한다
- "자격"/"기간" 같은 **의도 명사**가 달라지면 거부해야 한다

### 제안 수정 방향

`AnswerUnit` 옆에 **Keyword Anchor Gate**를 추가한다:
1. 질문에서 **"구별자 명사"**(연도, 기수, 전기/후기, 학번 코호트, 제한/가능 같은 이분법 어휘)를 추출
2. direct_answer에 해당 구별자가 **문자열 그대로** 있는지 확인
3. 없으면 거부

이건 예외 분기가 아니라 **"단위 정합" 개념을 "키워드 앵커 정합"으로 확장**하는 동일 패러다임.

---

## 버그 #2 — 그래프 졸업요건 노드 학번 라벨 오류 (영향: 1건, 잠재적 전체 왜곡)

### 증상
**g01**: "2024학번 이후 졸업 학점" → 그래프 `2024_2025학번 내국인 졸업요건 - 졸업학점: **120**`
- **GT는 130** (학사안내 2026-1학기 공식 기준)
- 현재 그래프 노드는 **120**으로 잘못 기록됨

### 영향 범위
- 2024~2025 학번의 졸업요건 질문 전반에 **잘못된 수치** 반환
- g01 외에도 학번별 졸업요건 질문 n건이 영향받을 수 있음

### 의심 지점
- `scripts/build_graph.py` — 졸업요건 추출 로직
- 또는 `scripts/pdf_to_graph.py` — PDF 표 파싱 단계
- 또는 `data/graphs/academic_graph.pkl` — 빌드 시점의 학사안내 PDF가 구버전이었을 가능성

### 제안
1. `academic_graph.py`에서 `2024_2025학번 내국인 졸업요건` 노드의 속성을 직접 조회해 값 확인
2. PDF 원본 확인
3. build_graph.py의 코호트별 졸업학점 추출 regex/파싱 검증

이건 **단일 숫자 오류**지만 "정답이 틀리면 모든 것이 무의미"하므로 가장 시급.

---

## 버그 #3 — `_try_extract_direct_answer`의 휴리스틱 과잉 매칭 (영향: 2~3건)

### 증상

**현재 코드** (`app/pipeline/context_merger.py:440~536`):
`_try_extract_direct_answer` 가 질문 키워드를 보고 **정규식으로 컨텍스트를 파싱**해 direct_answer를 합성한다. 11개의 rule이 있고 각각 조건이 느슨하다:

```python
# rule 6: OCU 개강일/시간
if "ocu" in q and any(kw in q for kw in ("개강", "시작", "언제")):
    m_date = re.search(r"개강일[:\s]*([\d\-]+)", context)
    ...
```

**문제**:
- rule이 순서대로 돌면서 **가장 먼저 매칭되는 것이 승리**
- 질문이 여러 키워드를 포함할 때 잘못된 rule이 발동 (예: rule 5 "졸업 최소 학점"이 rule 12 "학과 연락처"보다 먼저 실행)
- rule 10 "금액" 추출이 "수강료·비용·금액·얼마"로 과매칭 — 질문이 "**학점**은 얼마인가?"여도 매칭될 수 있음

### 영향 문항

- **c01** ("수업시간표 어디서 확인?") — rule 1 URL 추출 실행, 하지만 컨텍스트에 **학생포털(m.bufs.ac.kr)** URL만 있고 **수강신청(sugang.bufs.ac.kr)** URL은 top-5 밖 → 잘못된 URL 반환 후 LLM이 혼동
- **sc02** ("장학금 최소 학점") — rule 5 "졸업 학점" 패턴이 "12학점(4학년 9학점)" 대신 다른 표의 "15학점"을 먼저 매칭할 가능성

### 제안
`_try_extract_direct_answer`를 **제거**하거나, 각 rule이 반환한 결과에도 **버그 #1의 Keyword Anchor Gate를 적용**한다. 휴리스틱 11개는 관리 부담이 커서 장기적으로는 그래프 노드의 `direct_answer` 메타데이터로 일원화하는 게 낫다.

---

## 버그 #4 — Intent 오분류 (영향: 2건)

### 증상

**m02**: "2024학번 이후 학생이 전공 변경을 하려면 어떻게 해야 하는가?"
- 현재 분류: `Intent.GENERAL`
- 기대 분류: `Intent.MAJOR_CHANGE`

**s04**: "2025학년도 전기 학위수여식은 언제인가?"
- 현재 분류: `Intent.SCHEDULE` (맞음)
- 그러나 `entities`에 `year_half = "전기"` 같은 구별자 entity가 없음 → direct_answer 매칭 시 구별이 안 됨

### 원인
- `query_analyzer.py`의 Intent 키워드 테이블에 "전공 변경"이 MAJOR_CHANGE 키워드로 등록되지 않았거나 우선순위가 낮음
- entity 추출기에서 "전기/후기" 같은 학기 구분자는 추출 대상 아님

### 영향 범위
- 전과/전공 변경/학적 변동 관련 질문에서 잘못된 라우팅 → 잘못된 intent_k, 잘못된 가중치
- 학위수여식/학사일정에서 구체 구별자 매칭 불가

### 제안
1. `query_analyzer.py`의 MAJOR_CHANGE 키워드에 "전공 변경", "전공변경" 명시 추가
2. `_extract_entities()`에 `year_half = "전기"/"후기"` 추출 추가

---

## 버그 #5 — `fill_from_context`의 refusal response 오탐 (영향: 2건 + 잠재적 다수)

### 증상
Fix D (`answer_generator.generate_full()` post-processing)가 **refusal 응답에도 `[참고]` 블록을 주입**한다:

**u02** (기숙사 신청 기간):
```
PRED: 해당 일정 정보를 찾을 수 없습니다. 학사지원팀(051-509-5182)에 문의하시기 바랍니다.

[참고] 시간: 10:00
```

이건 unanswerable 문항에서 본문은 올바르게 거절했는데 **시스템이 컨텍스트의 관련 없는 "10:00"을 참고로 주입**해서 사용자를 혼란스럽게 만듦.

### 원인
`app/pipeline/answer_generator.py:generate_full()` 마지막 부분:
```python
if lang != "en" and full:
    try:
        from app.pipeline.answer_units import fill_from_context
        target_entity = (entities or {}).get("department") if entities else None
        full = fill_from_context(question, full, context, target_entity=target_entity)
```

**이 호출이 refusal 여부를 체크하지 않는다**. refusal 응답의 본질은 "정보 없음"인데 참고 정보를 붙이면 모순이다.

### 제안
`fill_from_context` 호출 전에 이미 구현된 `is_refusal()` 로직(`scripts/eval_f1_score.py`의 `_REFUSAL_PATTERNS`) 또는 `response_validator.NO_CONTEXT_PHRASES`를 재사용해 **refusal이면 주입 건너뛰기**:

```python
from app.pipeline.response_validator import ResponseValidator
if not ResponseValidator()._is_no_context_response(full):
    full = fill_from_context(...)
```

---

## 버그 #6 — 사용자 질문 키워드 vs 벡터 검색 불일치 (영향: 3건)

### 증상

**a01** "대체과목과 동일과목의 차이" → 벡터 top-3가 **모두 p.0 FAQ** (재수강 FAQ). 원문 학사안내 p.9 "대체/동일과목 확인" 표는 검색 top-15 밖.

**sc03** "TA장학생 선발 기준" → 벡터 top-3에 "근로장학금", "해외봉사장학금" 등 관계없는 장학 설명만. TA장학 PDF의 "선발 기준" 문장 매칭 안 됨.

**sc01** "국가장학금 신청" → 벡터가 "[장학/복지 > 교외장학신청]" 학생포털 매뉴얼만 반환. `kosaf.go.kr` 언급 청크 부재.

### 원인 추정
- **a01/sc03**: FAQ가 vector search에서 **p.0 (메타데이터상 페이지 0)**으로 나오는데, 이것은 **FAQ JSON 청크의 page_number가 모두 0**이기 때문. 리랭커가 FAQ 청크를 편향적으로 Top-3에 몰아 넣으면서 PDF 원문이 밀려남.
- **sc01**: `data/chromadb`에 `kosaf.go.kr` 자체가 인제스트 되지 않음 (커버리지 갭)

### 영향 범위
- 모든 PDF 원문 vs FAQ가 공존하는 주제에서 FAQ가 과도하게 선호됨
- p.0 청크가 "페이지 번호 기반 retrieval 지표"에서 완전히 배제됨

### 제안
1. 리랭커 단계에서 FAQ 청크와 PDF 청크의 **최소 1:1 보장** 정책 추가
2. `_filter_by_entity`가 FAQ 과잉을 감지하면 페이지 원문을 강제 상위로
3. 커버리지 갭(sc01): 국가장학금 정보를 `notice_attachment` 카테고리로 별도 인제스트하거나 glossary에 명시

---

## 버그 #7 — LLM 숫자/개념 혼동 (영향: 5건)

### 증상

| qid | GT | PRED | 차이 |
|---|---|---|---|
| c01 | `sugang.bufs.ac.kr` | `m.bufs.ac.kr` | 잘못된 URL |
| g04 | 주 36, 제2 27 | 주 27, 제2 27 | 주전공 학점 혼동 |
| l01 | `m.bufs.ac.kr` | "m.bufs.ac.kr" (맞지만 URL이 본문 없이 설명만) | 정답 포함 실패 |
| l02 | 4회(4년) | 4학기 | 단위 혼동 |
| sc02 | 12학점 | 15학점 | 숫자 혼동 |

### 공통 분석
전부 **컨텍스트에 GT 원문이 명시돼 있는** 상태에서 LLM이 **근접 값이나 다른 표의 값**을 가져와 답함. 이는 cross-encoder가 가져온 여러 청크 중 **GT 문장과 비슷한 맥락의 다른 수치**가 있을 때 LLM이 그것을 채택하는 패턴.

### 원인
- SYSTEM_PROMPT에 "컨텍스트 원문을 한 글자라도 바꾸지 마세요"가 있지만, exaone3.5가 이를 충분히 따르지 않음
- 여러 청크가 동일 주제의 다른 수치를 포함할 때 LLM이 "그럴듯한 값"을 합성

### 제안
**Answer-Context Consistency Check**: `generate_full()` 후 답변의 숫자·URL을 추출해, 그 값들이 **컨텍스트에 등장하는지** 검증. 등장하지 않으면 재생성 또는 refusal. 이건 버그 #1/#5와 같은 **AnswerUnit 확장**으로 가능:

```python
# answer_units.py 신규 함수
def verify_answer_against_context(answer, context):
    answer_units = present_units(answer)
    for unit_type, values in answer_units.items():
        for v in values:
            if v not in context:
                return False, f"{unit_type}:{v} not in context"
    return True, None
```

---

## 요약 — 버그 영향도 매트릭스

| # | 버그 | 영향 문항 | 난이도 | 우선순위 |
|---|---|---|---|---|
| **#1** | direct_answer 의미 필터 부재 | e02, m01, r05, s04, u07 (5건) | 중 | **🔥 최우선** |
| **#2** | 그래프 졸업요건 노드 학번 라벨 오류 | g01 (1건, 파급력 큼) | 저 | **🔥 최우선** |
| **#3** | _try_extract_direct_answer 휴리스틱 | c01 등 (2~3건) | 중 | 높음 |
| **#4** | Intent 오분류 | m02, s04 (2건) | 낮 | 중 |
| **#5** | fill_from_context refusal 오탐 | u02, u03 (2건, 부수적) | 낮 | 중 |
| **#6** | FAQ 과잉 선호 / 커버리지 갭 | a01, sc01, sc03 (3건) | 중 | 중 |
| **#7** | LLM 숫자/URL 혼동 | c01, g04, l01, l02, sc02 (5건) | 중 | 높음 |

**총 영향**: 24건 실패 중 **최소 17건**은 이 7개 버그 중 하나 이상이 원인. 나머지 7건은 커버리지 부족(sc01, sc03, m02, u01, u04 등).

---

## 원칙 점검

이 진단에서 **예외 분기로 해결해선 안 되는** 이유:

- 버그 #1 (의미 필터): 개별 질문마다 "전기/후기" "2027/2026" 체크를 넣으면 다음엔 "2학기/3학기", "국내/국외" 같은 새 구별자가 나올 때마다 코드 추가 필요 → **폭발적 복잡도**
- 버그 #3 (휴리스틱): rule이 11개 → 12개 → 15개... 점점 디버그 불가능
- 버그 #7 (LLM 혼동): "sugang vs m.bufs" "12 vs 15" 같은 개별 오답을 if-else로 막으면 영원히 끝나지 않음

**단일 추상화로 해결해야 함**:
- **Keyword Anchor** 개념 — 질문의 구별자가 답변에도 있어야 한다 (버그 #1, #3, #7 전부 한 번에 해결)
- **Refusal Gate** — refusal이면 모든 post-processing 건너뛰기 (버그 #5)
- **데이터 검증 스크립트** — 그래프 노드 값이 PDF와 일치하는지 자동 체크 (버그 #2)
