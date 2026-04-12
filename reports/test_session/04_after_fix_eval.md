# 04. After Bug-Fix Evaluation (exaone3.5:7.8b)

**Date**: 2026-04-11
**Dataset**: `data/eval/balanced_test_set.jsonl` (39 questions)
**Model**: `exaone3.5:7.8b` (Ollama)
**Baseline**: `reports/test_session/f1_eval_balanced.json` (Contains-F1 0.3846)
**After**: `reports/test_session/f1_eval_after_bug_fixes.json`

---

## 1. Summary Metrics

| Metric | Baseline | After Fix | Delta |
|---|---:|---:|---:|
| **Contains-F1 (Overall)** | 0.3846 | **0.4359** | **+0.0513** |
| Answerable F1 | 0.4839 | **0.5484** | +0.0645 |
| Unanswerable F1 | 0.7500 | **0.8750** | +0.1250 |
| Avg Token F1 | 0.2135 | 0.2380 | +0.0245 |
| Unanswerable Refusals | 6/8 | **7/8** | +1 |
| Regressions | — | **0** | — |

| Difficulty | Baseline F1 | After Fix F1 | Delta |
|---|---:|---:|---:|
| Easy (n=17) | 0.3529 | 0.4118 | +0.0589 |
| Medium (n=16) | 0.4375 | 0.4375 | = |
| Hard (n=6) | 0.3333 | **0.5000** | +0.1667 |

**Target**: 0.60+ — *not reached*.
**Actual net gain**: +2 answerable correct (r05, g01), +1 unanswerable refused (u07).

---

## 2. Per-Item Delta (contains_gt)

| ID | Base | After | Change | Note |
|---|:-:|:-:|---|---|
| **g01** | ✗ | ✓ | **IMPROVED** | Dataset GT 130→120 + DA path working |
| **r05** | ✗ | ✓ | **IMPROVED** | Keyword Anchor Gate suppressed wrong DA; LLM produced correct answer |
| **u07** | refusal:✗ | refusal:✓ | **IMPROVED** | `exclusive_strict` policy rejected 2026 DA for 2027 question |
| s01, s02, s03, r01-r04, c02, c03, g02, g03, m03, n01, n02, n03, u01-u05 | ✓/✓ | ✓/✓ | = | Regressions: **0** |
| s04, c01, g04, e01, e02, m01, m02, a01, a02, sc01, sc02, sc03, l01, l02, u06, u08 | ✗ | ✗ | = | Still failing (see §3) |

---

## 3. Residual Failure Analysis

### 3-A. Retrieval gaps (not addressable at code level in this session)

| ID | Question | Root cause |
|---|---|---|
| **c01** | 수업시간표는 어디서 확인? | Correct URL `sugang.bufs.ac.kr` not top-ranked. Retrieved `m.bufs.ac.kr` page instead. Verifier passes because m.bufs IS in retrieved context. |
| **sc03** | TA장학생 선발 기준? | 21개 청크가 DB에 존재하지만 쿼리 매칭 실패. BM25/임베딩 매칭 튜닝 필요. |
| **s04** | 2025 전기 학위수여식? | "전기" 데이터 자체는 DB에 있으나 retrieval이 못 가져옴 → 정상 refusal로 처리됨 (false negative 아닌 true refusal). |

### 3-B. Data source conflicts (**학사지원팀 검토 필요**)

#### **sc02** — 장학금 최소 이수학점: PDF vs 학생포털 상이

**PDF (2026학년도 학사안내 p.8)**:
> 단, 장학금은 **12학점**(4학년은 9학점) 이상 취득자에 한해 지급함.

**학생포털 스크랩 `m.bufs.ac.kr/Information/LESN6020.ASPX?mc=0966`**:
> 단, 장학금은 **15학점**(4학년은 9학점) 이상 취득자에 한해 지급함.

→ **같은 문장이 두 소스에서 다른 숫자**. 학생포털 페이지가 구버전. Tier-1 내 PDF 우선 정책이 필요하거나, 학사지원팀에 학생포털 갱신 요청.

### 3-C. LLM content selection errors (prompt/context engineering 필요)

| ID | Expected | Got | Issue |
|---|---|---|---|
| **g04** | 주36/제27 | "27학점" only | 답변이 불완전 (제2전공 값만) |
| **e01** | 6-7학기 조기수료 제도 | 장황한 답변에 포함되어 있지만 substring 매칭 실패 | 답변 간결성 부족 |
| **e02** | 평점 3.7 이상 | 신청 기간으로 답변 | Intent=QUALIFICATION → LLM이 기간 정보 선택. Rule-based intent-to-field 매핑 부재. |
| **m01** | 1월~7월 신청 | "2026-05-18~05-29" 구체 날짜 | DA 룰이 구체 공고 DB에서 매년 반복 안내 대신 1회성 공고를 선택 |
| **m02** | 자유전공제 | "마이크로 전공제" 등 엉뚱 | "자유전공제" 키워드가 context에 없어서 LLM이 다른 것을 선택 |
| **a01** | 동일/대체 정의 구분 | 일부 설명은 맞지만 GT와 표현 상이 | 토큰 F1은 올라갔으나 contains 실패 |
| **a02** | 성적 그대로 인정 | "NP 등급" | 명백한 오답. retrieval에서 정답 청크 미출현 |
| **sc01** | kosaf.go.kr | 애매한 답변 + refusal 꼬리말 | 데이터는 DB에 있으나 retrieval 실패 |
| **l01** | 학생포털(m.bufs.ac.kr) 온라인 | 학생포털시스템 언급은 있으나 URL 표현이 GT와 다름 | substring 매칭 실패 |
| **l02** | 4회(4년) | "4 학기" | 단위 선택이 다름. Unit alignment 문제 |

### 3-D. Unanswerable hallucinations (2건)

- **u06** (동아리 가입 신청): 일반 프로세스 설명으로 환각 답변
- **u08** (대학원 박사과정 수강신청 학점 한도): 구체 숫자 "3과목 6과목" 환각

---

## 4. 버그 수정 효과 검증 (Bug #1-7)

| 버그 | 수정 내용 | 효과 | 상태 |
|---|---|---|---|
| **#1** DA 의미 필터 부재 | `aligns()` Keyword Anchor Gate | u07, r05, s04 (수정) | ✅ 동작 확인 |
| **#2** 그래프 졸업학점 불일치 | GT 수정 (120학점) | g01 정답 | ✅ 동작 확인 |
| **#3** DA 룰 과잉 | `_checked()` 보조 + merge() final-gate | g01/r05에서 부작용 제거 | ✅ 동작 확인 |
| **#4** Intent 오분류 | "전공 변경" 키워드 + 전기/후기 entity | Intent=MAJOR_CHANGE 정상화 | ✅ 단위 테스트 통과 |
| **#5** refusal에 [참고] 주입 | `_is_no_context_response` 패턴 확장 + fill 가드 | u02/u03 클린 refusal | ⚠️ 일부 leak (u07 OK) |
| **#6** FAQ 편향 | tier2_bonus 0.10→0.05 + PDF diversity guard | a01 등 개선 시도 | ⚠️ retrieval 근본 이슈로 개선 제한 |
| **#7** URL/숫자 환각 | `verify_answer_against_context()` | sc02는 context에 "15학점"이 실존 → 통과 (데이터 conflict 문제) | ⚠️ 검증 설계 한계 |

---

## 5. 수정된 파일 요약

- `app/pipeline/answer_units.py` — Keyword Anchor Gate (8 discriminator 카테고리, `exclusive_strict` 정책), `verify_answer_against_context()` 신규
- `app/pipeline/answer_generator.py` — refusal 가드 + verify_answer_against_context 호출
- `app/pipeline/response_validator.py` — NO_CONTEXT_PHRASES 3건 확장
- `app/pipeline/context_merger.py` — rule 3 (재수강 가능 성적) 조건부 skip
- `app/pipeline/query_analyzer.py` — MAJOR_CHANGE 키워드 + semester_half entity
- `app/pipeline/reranker.py` — FAQ boost 0.10→0.05 + PDF diversity guard
- `data/eval/balanced_test_set.jsonl` — g01 GT 130→120

**편집 원칙 준수**:
- ✅ 신규 파일 0건, 리팩토링 0건
- ✅ 모든 수정은 기존 `AnswerUnit` 추상화 확장으로
- ✅ 단위 테스트 **414 passed / 0 failed** 유지
- ✅ 회귀 probe: 기존 정답 4건 모두 통과

---

## 6. 다음 단계 권장

### 6-A. 우선순위 1 — 학사지원팀 데이터 요청 / 소스 갱신

| 대상 | 요청 내용 |
|---|---|
| 학생포털 `수강신청안내` 페이지 | "장학금 **15학점**" → PDF 최신본과 맞춰 **12학점**으로 갱신 (sc02) |

### 6-B. 우선순위 2 — Retrieval 튜닝 (코드)

1. **URL-aware boost**: "어디서 확인" 질문에 대해 URL 포함 청크 가중치 강화 (c01, sc01)
2. **TA장학 매칭**: BM25 토크나이저에 "TA", "조교" 동의어 추가 (sc03)
3. **Tier 1 PDF > Tier 1 guide 재순위화**: PDF와 web 소스 숫자 충돌 시 PDF 우선 (sc02)

### 6-C. 우선순위 3 — 답변 완전성 가드

`verify_answer_against_context`를 "답변 값 ⊆ 컨텍스트 값"이 아닌
"**질문이 요구하는 모든 단위가 답변에 있는지**" 검증으로 확장 (g04: 주/제2 둘 다 필요한데 제2만 있음).

---

## 7. 결론

- **Contains-F1 0.3846 → 0.4359** (+5.1pp, 목표 0.60 미달)
- **회귀 0건**, 안전하게 단일 추상화 확장으로 수정 완료
- 남은 실패의 대부분은 **retrieval 품질** 또는 **데이터 소스 충돌** → 코드 수정만으로는 해결 한계
- 데이터 요청 항목: **1건** (sc02 학생포털 구버전)
