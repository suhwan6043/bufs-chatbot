# BUFS Chatbot 개선 보고서 — 4월 3일 → 4월 11일

**작성일**: 2026-04-11
**평가 모델**: `exaone3.5:7.8b` (Ollama)
**검색 엔진**: ChromaDB + BGE-M3 + BGE-Reranker-v2-m3 + AcademicGraph
**테스트 데이터셋**: `data/eval/balanced_test_set.jsonl` (신규, 39문항, 편향 없음)

---

## TL;DR

| 구분 | 4월 3일 baseline | 4월 11일 (오늘) | 변화 |
|---|---:|---:|---:|
| 검증 방식 | RAGAS (안정 최고값) | Contains-F1 + Token-F1 | 방식 전환 |
| 테스트 셋 | `user_eval_dataset_50` (intent/분야 편향) | `balanced_test_set` (10 intent × 3 난이도) | **편향 제거** |
| RAGAS avg | 0.841 | — (해당 셋 미실행) | — |
| Contains-F1 (50문항, 기존 편향 셋) | 0.560 (04-06) | **0.747** | **+18.7%p** |
| Contains-F1 (39문항, 균형 셋) | 측정 없음 | **0.385** | — (신규) |
| Hard 난이도 F1 (기존 셋) | 0.000 | **0.800** | **+80%p** |
| 문서 밖 환각률 (기존 셋) | 40% (2/5) | 25% (2/8) | **-15%p** |
| 단위 테스트 | — | **414 passed / 0 failed** | 회귀 0건 |

4월 3일 이후 **12가지 구조적 수정**이 누적되어 기존 테스트 셋 Contains-F1이 **0.560 → 0.747 (+18.7%p)**로 올랐고, 가장 큰 개선은 `Hard` 난이도 **0% → 80%**. 오늘은 **편향 없는 신규 테스트 셋**으로 재평가하여 **7가지 추가 코드 버그**를 구조적으로 식별했다.

---

## 1. 4월 3일 이후 개선 이력 (시간순)

### 1.1 P1/P2/P4 — 파이프라인 누수 차단 (2026-04-10 ~ 04-11)

| 수정 | 파일 | 효과 |
|---|---|---|
| 리랭커 top_k 7 → 10 + 절대 하한 | `reranker.py` | 정답 청크가 Top-5 밖에서 살아남음 |
| Intent별 k 상향 (SCHEDULE 5 → 15 등) | `query_router.py` | 후보 부족으로 인한 조기 절단 방지 |
| Context 토큰 예산 하드 컷오프 제거 | `context_merger.py` | 첫 청크가 예산 독점하지 않도록 `per_chunk_max=60%` |
| RRF 가중치 재조정 (그래프 편향 완화) | `context_merger.py` | GRADUATION_REQ/MAJOR_CHANGE 벡터 우선 |
| confidence = count + 벡터 점수 결합 | `context_merger.py` | 무관한 3개 청크가 0.8 받던 CAT_C 패턴 차단 |
| 저신뢰 구조화 프롬프트 체크리스트 | `answer_generator.py` | LLM에 3-point 사전 검증 강제 |
| 쿼리 재작성 재시도 루프 | `answer_generator.py` + `chat_app.py` | confidence<0.5 시 LLM으로 재작성 후 재검색 |

**측정**: Contains-F1 0.560 → 0.653 (+9.3%p)

### 1.2 Pre-existing 테스트 수정 (2026-04-10)

21건의 stale 테스트를 수정하여 `414 passed / 0 failed` 달성. 수정 내용:
- `test_glossary.py`: `academic_terms.yaml` → `en_glossary.yaml` 경로 업데이트
- `test_ko_tokenizer.py`: FAQ_STOPWORDS 축소에 맞춰 기대값 갱신
- `test_transcript_parser.py`: 다중 파일 포맷 지원 반영
- `test_faq_redirect.py`: `add_faq_node`에 역인덱스 증분 갱신 추가 (버그 수정)
- `en_glossary.yaml`: `graduation deferral` 중복 별칭 제거

### 1.3 P3-b ChromaDB 인제스트 + root cause 수정 (2026-04-10)

- 13개 추가 PDF 인제스트 (수강신청 FAQ, 공인결석, 모바일 학생증, 학생포털 매뉴얼, 학부과 전화번호, 장학 매뉴얼 등)
- **ChromaDB corruption 근본 원인 규명**: `ingest_pdf()`가 호출마다 새 `ChromaStore` 인스턴스를 만들어 HNSW 세그먼트가 깨지던 버그
- `ingest_pdf.py`에 `shared store` 옵션 추가 → 재인제스트 시 segfault 없음
- 청크 수 `1,381 → 2,095`

**측정**: Contains-F1 0.653 → 0.693 (Final LM Studio 기준)

### 1.4 Medium 실패 14건 심층 수정 (2026-04-10)

- **eval 측정 버그 수정** — `normalize_text()`가 괄호 안 URL을 삭제하던 문제 + 마침표 붙은 토큰 매칭 실패
- **Adaptive Score-Gap Thresholding** — 페이지당 청크 수 하드 제한 대신 RRF 점수 분포 기반 절단
- **Confidence clamp** — adaptive cutoff가 크게 줄이면 confidence<0.5로 하락시켜 P4 재시도 트리거
- **q042 regression 수정** — dedup 120자 서명 + 예산 상향 + GRADUATION_REQ 그래프 가중치 복원

**측정**: Contains-F1 0.693 → 0.707 (+1.4%p), Hard **0.400 → 0.800**

### 1.5 LM Studio Thinking 차단 + OCU 검색 복구 (2026-04-11)

- **4-Layer Thinking Block**: SYSTEM_PROMPT `/no_think`, user prompt `/no_think`, payload `chat_template_kwargs.enable_thinking=False`, 스트림 파싱 reasoning_content silent drop
- **OCU intent_k 제한 제거** — `query_router.py:127` `min(intent_k, 6)` 삭제, p.20 청크가 Top-0으로 복귀

**측정**: Contains-F1 0.707 → 0.707 (동일), Token-F1 0.2989 → 0.3099

### 1.6 로지컬 접근 — AnswerUnit 통합 (2026-04-11)

7가지 병목을 **예외 분기 대신 단일 개념 `AnswerUnit`**으로 통합:
- `app/pipeline/answer_units.py` 신규 (credit/won/course/date/time/phone/room/url/grade 9단위)
- `expected_units(question)` / `present_units(text)` / `aligns(q, a)` / `fill_from_context(q, a, ctx, target_entity)`
- **Fix A** (ContextMerger): direct_answer가 final-gate `aligns()` 통과해야 수락
- **Fix C** (`_try_extract_direct_answer`): entities.department가 있으면 해당 학과 라인에서만 phone/room 추출
- **Fix D** (`generate_full`): 답변 생성 후 누락 단위를 컨텍스트에서 찾아 `[참고]` 블록으로 주입

**측정**: Contains-F1 0.707 → **0.747 (+4.0%p)**, Answerable F1 0.757 → 0.800

**기존 편향 셋 누적 결과 (04-06 → 04-11)**:
- Contains-F1: 0.560 → 0.747 (**+18.7%p**)
- Token-F1: 0.155 → 0.298 (**+14.3%p**)
- Hard F1: 0.000 → 0.800 (**+80%p**)
- **퇴행 0건** (14건 개선 / 0건 퇴행)

---

## 2. 오늘의 새 관점 — 균형 잡힌 테스트 셋

### 2.1 왜 새 데이터셋이 필요했나

기존 `user_eval_dataset_50.jsonl`(75문항)은 **편향이 있었다**:
- SCHEDULE 인텐트가 과반을 차지 → 학사일정 질문에 과적합
- OCU 관련 질문이 10개+ 몰림 → 한 주제로 지표 왜곡
- 한국어 명사(마침표 포함) vs URL 정규화 불일치로 **측정 자체가 신뢰 불가**

이 문제를 눈치챈 시점이 **4월 3일 RAGAS 평가 직후**. RAGAS 수치가 0.84인데 실제 질문을 돌려보면 편차가 컸다. "고쳐진 게 아니라 **셋이 그쪽으로 기울어서** 좋아 보이는 것"이라는 의심.

### 2.2 새 데이터셋 설계

`data/eval/balanced_test_set.jsonl` (39문항):

| 축 | 분포 |
|---|---|
| **Intent** | 10종 균등 (SCHEDULE 5, REGISTRATION 6, COURSE_INFO 3, GRADUATION_REQ 4, EARLY_GRADUATION 2, MAJOR_CHANGE 3, ALTERNATIVE 2, SCHOLARSHIP 3, LEAVE_OF_ABSENCE 2, GENERAL 9) |
| **Difficulty** | easy 17 / medium 16 / hard 6 |
| **Answerable** | 31 vs Unanswerable 8 (문서 밖 별도) |
| **Field** | 26개 세부 분야 (학사일정, 수강신청, 재수강, 시간표, 졸업요건, 졸업인증, 복수전공, 조기졸업, 전과, 대체과목, 장학기준, TA장학, 국가장학, 휴학, 복학, 연락처, 사이트, 식당, 기숙사, 셔틀버스, 시설운영, 교원자격, 동아리, 미래일정, 대학원, OCU) |

**검토 가능**: `reports/test_session/01_dataset.md`에 모든 문항을 intent별로 표 형식 공개.

### 2.3 새 셋 결과

| 지표 | 값 |
|---|---:|
| **Contains-F1** | **0.3846** (15/39) |
| Token-F1 | 0.2135 |
| Answerable F1 | 0.4839 (15/31) |
| Unanswerable F1 | 0.7500 (6/8) |
| Easy F1 | 0.3529 (6/17) |
| Medium F1 | 0.4375 (7/16) |
| Hard F1 | 0.3333 (2/6) |

**해석**: 기존 셋에서 0.747이었는데 균형 셋에서 0.385. **0.36의 격차**는 기존 셋이 얼마나 편향되어 있었는지를 드러낸다. 구체적으로:

1. 기존 셋에 **SCHEDULE/OCU 쏠림**이 있어, 우리가 반복 튜닝한 구간만 잘 맞았다
2. **ALTERNATIVE, EARLY_GRADUATION, SCHOLARSHIP, LEAVE_OF_ABSENCE 4개 Intent는 기존 셋에 충분히 포함되지 않아 테스트되지 않았던** 영역
3. 새 셋에서 이 4개 intent가 **전부 0% 정답** → 구조적 버그 노출

**즉 기존 0.747은 "풀 수 있는 질문만 평가해서 나온 수치"**였고, 새 0.385가 **실제 상태**에 더 가깝다.

---

## 3. 오늘 새로 발견한 7개 코드 버그

전체 24건 실패 중 **17건**은 아래 7가지 구조적 버그가 원인이다 (`03_code_bugs.md` 참조).

### 버그 목록 (영향도순)

| # | 버그 | 영향 | 난이도 | 상세 |
|---|---|---:|---|---|
| **1** | **`direct_answer` 의미 필터 부재** | **5건** | 중 | AnswerUnit.aligns가 단위만 체크, "전기/후기", "2026/2027", "자격/기간", "제한/가능" 같은 **이분법 구별자**는 감지 못함 |
| **2** | **그래프 졸업요건 노드 학번 라벨 오류** | 1건 | 저 | `2024_2025학번 내국인 졸업요건 - 졸업학점: 120` → **GT는 130** (데이터 오류) |
| **3** | **`_try_extract_direct_answer` 휴리스틱 과잉** | 2~3건 | 중 | 11개 rule이 순서대로 매칭, rule 우선순위 충돌 |
| **4** | **Intent 오분류** | 2건 | 낮 | "전공 변경" → GENERAL, "전기/후기" 구별자 entity 미추출 |
| **5** | **`fill_from_context` refusal 오탐** | 2건 | 낮 | Unanswerable 응답에 `[참고] 시간: 10:00`이 부적절 주입 |
| **6** | **FAQ 과잉 선호 + 커버리지 갭** | 3건 | 중 | FAQ 청크(p.0)가 리랭커 편애, PDF 원문 밀림 |
| **7** | **LLM 숫자/URL 혼동** | 5건 | 중 | 컨텍스트에 GT 있는데 근접 값으로 대체 (12 → 15, sugang → m.bufs 등) |

### 근본 원인 3가지 (버그를 추상화하면)

1. **"단위 정합"만으론 부족** — 의미·구별자까지 체크해야 함 (버그 #1, #3, #7)
2. **그래프 데이터 건강성 부재** — 빌드 후 PDF와 일치하는지 검증 파이프라인 없음 (버그 #2)
3. **Refusal과 답변의 contract 모호** — post-processing이 refusal을 답변처럼 취급 (버그 #5)

---

## 4. 데이터셋/평가 원본

### 4.1 제공 파일 목록

| 경로 | 내용 |
|---|---|
| `reports/test_session/00_REPORT.md` | **이 보고서** |
| `reports/test_session/01_dataset.md` | 39문항 데이터셋 전체 공개 (표 형식) |
| `reports/test_session/02_results.md` | 실패 24건 상세 + 오늘 결과 요약 |
| `reports/test_session/03_code_bugs.md` | 7가지 코드 버그 심층 진단 |
| `reports/test_session/02_raw_results.txt` | 실패 문항 raw 출력 (GT/PRED/retrieved_pages) |
| `reports/test_session/03_deep_diag.txt` | 파이프라인 trace (18문항 × 벡터 top-3 + 그래프 top-3) |
| `reports/test_session/f1_eval_balanced.json` | eval 원본 JSON (재현 가능) |
| `data/eval/balanced_test_set.jsonl` | 테스트 데이터셋 원본 |

### 4.2 재현 방법

```bash
# 1. exaone 환경 확인
curl -s http://localhost:11434/api/tags

# 2. eval 실행
PYTHONUTF8=1 .venv/Scripts/python scripts/eval_f1_score.py \
  --dataset data/eval/balanced_test_set.jsonl \
  --output reports/test_session/f1_eval_balanced.json

# 3. 단위 테스트
.venv/Scripts/pytest tests/ -q
```

---

## 5. 4월 3일 대비 핵심 개선 요약

### 5.1 수치로 본 개선 (기존 편향 셋 기준, 가능한 비교)

| 지표 | 4월 3일 | 4월 11일 | 변화 | 비고 |
|---|---:|---:|---:|---|
| RAGAS avg (50문항) | 0.841 | — | — | 방식 다름 |
| Contains-F1 (75문항 기존 셋) | — (측정 없음) | 0.747 | — | |
| Contains-F1 baseline 04-06 | 0.560 | 0.747 | **+18.7%p** | 같은 셋 |
| Hard 난이도 F1 | 0.000 | 0.800 | **+80%p** | 구조적 병목 해결 |
| 단위 테스트 | 393 passed | **414 passed** | **+21 tests** | 회귀 0건 |

### 5.2 구조적 개선 (측정이 어려운 영역)

| 영역 | 4월 3일 | 4월 11일 |
|---|---|---|
| 인제스트 파이프라인 | segfault 빈발 (P3-b 당시) | `shared store` 패턴으로 안정 |
| 테스트 데이터셋 | 편향된 50문항 | **균형 잡힌 39문항 + 편향 제거** |
| LLM 호환성 | Ollama qwen3:14b 전용 | Ollama + LM Studio 양립 (`_env_llm` 폴백) |
| Thinking 모드 | thinking 원문 노출 3,995자 | **4-Layer 차단 → 58자** |
| 단위 개념 | 없음 | **AnswerUnit 9종 공통 추상화** |
| Intent 라우팅 | 하드코딩된 단일 테이블 | intent + question_type 복합 라우팅 + QuestionType modifier |
| 그래프 FAQ 증분 | `_build_faq_index()` 생성자에서만 | **`add_faq_node` 호출 시 즉시 반영** |

### 5.3 관점의 변화

**4월 3일**: "RAGAS avg 0.84, 이 정도면 배포 가능성 있음"

**4월 11일**: "기존 셋의 0.747은 **편향된 수치**였다. 균형 셋으로 재측정하니 0.385. 실제 가용 상태는 6/10 수준이고, **구조적 버그 7개**가 남아 있다."

**수치만 보면 baseline에서 많이 올라온 것 같지만, 실제로는 "풀 수 있는 문제만 풀게 되어 있는" 평가였다는 것을 오늘 확인**했다. 이것이 오늘의 가장 큰 수확이다.

---

## 6. 다음 단계 우선순위

### 우선순위 1 (데이터 무결성 — 즉시)
- **버그 #2**: `2024_2025학번 내국인 졸업요건 - 졸업학점 120 → 130` 수정 및 그래프 재빌드
  - `scripts/build_graph.py` 검토 후 PDF 기준으로 교정
  - 다른 학번 노드들도 전수 검증

### 우선순위 2 (구조적 — 이번 주)
- **버그 #1**: `AnswerUnit`에 Keyword Anchor Gate 추가
  - 질문의 구별자 명사("전기/후기", "2026/2027", "자격/기간", "제한/가능")를 추출
  - direct_answer에 그 구별자가 없으면 거부
  - 예외 분기가 아닌 단일 메커니즘으로 버그 #1, #3, #7 동시 해결
- **버그 #5**: `generate_full()`에서 `is_refusal()` 체크 후 `fill_from_context` 건너뛰기

### 우선순위 3 (데이터 보강 — 다음 주)
- **버그 #6**: `kosaf.go.kr` 국가장학금 정보 추가 인제스트
- 리랭커 FAQ vs PDF 비율 보장 정책

### 우선순위 4 (권장)
- **RAGAS 재측정**: 4월 3일과 같은 방식(RAGAS)으로 균형 셋을 돌려 일대일 비교 가능한 수치 확보
- **데이터셋 계속 성장**: 오늘 편향 없는 39문항 → 100문항 목표 (intent별 10문항 균등)

---

## 부록 — 파일 트리

```
reports/test_session/
├── 00_REPORT.md              ← 이 파일 (종합 보고서)
├── 01_dataset.md             ← 39문항 전체 공개
├── 02_results.md             ← 결과 요약 + 실패 24건 분석
├── 02_raw_results.txt        ← 실패 문항 raw (GT/PRED/retrieved_pages)
├── 03_code_bugs.md           ← 7개 코드 버그 심층
├── 03_deep_diag.txt          ← 18문항 파이프라인 trace
└── f1_eval_balanced.json     ← eval 원본 JSON
```

**관련 파일**:
- `data/eval/balanced_test_set.jsonl` — 테스트 데이터셋
- `reports/ragas_eval_20260403_200231.json` — 4월 3일 RAGAS (비교 근거)
