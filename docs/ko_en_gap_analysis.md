# 한국어(KO) vs 영어(EN) 지원 Gap 분석

**작성일**: 2026-04-23
**대상 브랜치**: `feat/en-support-optimization` (HEAD 1518093) + `origin/main` (HEAD 8643be0)
**범위**: 파이프라인·UI·데이터·평가 전반

---

## 0. 한눈에 보기

| 축 | KO | EN | 격차 |
|----|----|----|------|
| Intent 분류 | 9종 규칙 기반 | 9종 (KO 로직 재사용 + FlashText) | 동등 |
| 엔티티 추출 | 12종 | **17종** (EN이 더 많음: asks_url/semester_half 등) | EN+5 |
| 용어 매핑 | TERM_MAP 80+ (은어·약어 전용) | `en_glossary.yaml` 82종 (학사 공식용어 전용) | **불일치** |
| 성적표 업로드 | ✅ 지원 | ✅ i18n 지원 ([TranscriptUpload.tsx:107-219](frontend/src/components/layout/TranscriptUpload.tsx#L107-L219)) | 동등 |
| One-Pass 스트리밍 | N/A (직접 생성) | ✅ 구현 | EN 고유 |
| 평가셋 | `eval_ko.jsonl` 50 / `eval_ko_unified.jsonl` 88 | `eval_en.jsonl` 52 / `eval_en_unified.jsonl` 83 | 동등 |
| 어드민 페이지 | ✅ 정식 | ❌ EN UI 없음 | **High** |

---

## 1. 언어 감지·라우팅

| 항목 | KO | EN |
|------|----|----|
| 감지 | [language_detector.py:16-23](app/pipeline/language_detector.py#L16-L23) — 한국어 문자 비율 ≥30% → `ko` | 동일 함수에서 `en` 반환 |
| 분석 함수 | `QueryAnalyzer.analyze()` [query_analyzer.py:430-509](app/pipeline/query_analyzer.py#L430-L509) — 글로서리 정규화 → 학번/학생유형/의도 규칙 분류 | `QueryAnalyzer._analyze_en()` [query_analyzer.py:511-747](app/pipeline/query_analyzer.py#L511-L747) — FlashText로 `aliases_en→ko` 매핑 → KO 규칙 재사용 → 미탐 시 `Intent.GENERAL` + BGE-M3 fallback |
| 반환 필드 차이 | `matched_terms` 없음 | `matched_terms` 리스트 제공 (검색 힌트) |
| 학번 파서 | 4자리/2자리/범위(2024~2025) | 4자리/2자리/`class of 2020`/`2020 student` 등 EN 표현 (line 528-540) |
| EN→KO 쿼리 재작성 | N/A | `ko_query` 필드로 재작성해 그래프에 전달 |

**Gap**: EN은 2단계 처리라 경로가 길다. KO 파이프라인은 단일 경로 직관적.

---

## 2. 검색 경로

| 항목 | KO | EN |
|------|----|----|
| 벡터 검색 | Intent별 **선택적** (SCHEDULE은 `requires_vector=False` 가능) | **항상 True** (query_analyzer.py:716) → 검색 비용 20-30% 상승 |
| 그래프 검색 | FAQ 역인덱스 + direct_answer 게이트 정상 | 동일 (최근 1518093/4812d2a 커밋에서 EN 매핑 강화) |
| 리랭커 편향 | 기본 | `asks_url` 엔티티 감지 시 URL boost (EN 고유) |
| 기능어 보강 | 불필요 | line 658-688: 'application period'/'where to apply' → `신청기간`/`신청` 키워드 수동 주입 (if-else 12개 하드코딩) |
| fallback 용어 | N/A | `_INTENT_FALLBACK_TERM` — matched_terms=[] 시 intent별 기본 학사용어 1개 강제 ([answer_generator.py:31-42](app/pipeline/answer_generator.py#L31-L42)) |

**Gap**: EN 기능어 보강이 수동 if-else (`if 'application period' in q:...`) 구조 → 확장성 나쁨. 새 EN 표현마다 코드 수정 필요.

---

## 3. 번역·용어 매핑

| 자원 | KO 커버리지 | EN 커버리지 |
|------|-------------|-------------|
| `app/pipeline/glossary.py` `TERM_MAP` | **80+** (복전→복수전공, 조기졸신청→조기졸업 신청 등 은어·약어) | 해당 entries의 EN 동의어 없음 |
| `config/en_glossary.yaml` | 해당 항목의 KO aliases 포함 | **82종 공식 학사용어** (ko/en/aliases_en/aliases_ko) |
| `config/academic_terms.yaml` | aliases_ko | aliases_en |
| FlashText EnTermMapper | N/A | 싱글톤, O(N) 매칭 ([query_analyzer.py:50-131](app/pipeline/query_analyzer.py#L50-L131)) |

### 누락된 EN 용어 (High 우선순위 예시)
- 수강정정 (EN alias 없음)
- 학사경고 (EN alias 없음)
- 공인결석 (EN alias 없음)
- 수업연한초과자 (EN alias 없음)
- 성적표 (transcript-related EN 매핑 미흡)

---

## 4. 답변 생성·번역

| 항목 | KO | EN |
|------|----|----|
| 시스템 프롬프트 | `SYSTEM_PROMPT` ([answer_generator.py:75-98](app/pipeline/answer_generator.py#L75-L98)) — 직결론 + 예외·조건 + 학번 분기 | `EN_SKIP_TRANSLATE_SYSTEM_PROMPT` ([answer_generator.py:45-72](app/pipeline/answer_generator.py#L45-L72)) — KO 테이블 직독 + KO 약자 변환 + `[Term Guide]` 주입 |
| **`term_guide_section` 플레이스홀더** | N/A | ⚠️ **미구현** — 프롬프트에 `{term_guide_section}` 변수 있지만 matched_terms 실시간 삽입 로직 없음 |
| 스트리밍 | 일반 | One-Pass Rolling Buffer: `<ko_draft>` → "분석 중..." → `<final_answer>` → CLEAR 후 EN 출력 |
| 지연시간 | LLM ~200-500ms + retrieval ~30-50ms | 거의 동일 (FlashText +1-2ms) |

**Gap**: `{term_guide_section}` 주입이 안 돼서 EN 답변에 **학사용어 영문명이 일관되게 들어가지 않음**. 이건 EN 답변 품질에 직접 영향.

---

## 5. 기능 완성도 차이표

| 기능 | KO | EN | 심각도 | 근거 |
|------|----|----|--------|------|
| 9종 Intent 분류 | ✅ | ✅ | - | query_analyzer.py:299-394 |
| 학번 추출 | ✅ (4자리/2자리/범위) | ✅ (+ EN 표현 class of/cohort) | - | line 141-155 / 528-540 |
| 학생유형(국제·편입) | ✅ | ✅ | - | STUDENT_TYPE_PATTERNS |
| 엔티티 추출 | 12종 | **17종** (+asks_url/semester_half/registration_deadline 등) | EN 우위 | line 970-1066 / 551-649 |
| 성적표 업로드 | ✅ | ✅ i18n 지원 | - | [TranscriptUpload.tsx:107-219](frontend/src/components/layout/TranscriptUpload.tsx#L107-L219) — lang 분기 완비 |
| 고정공지 연동 | ✅ | ✅ | - | context_merger.py:333-361 |
| 학사일정 표 추출 | ✅ | ✅ | - | 동등 |
| 장학금 FAQ | ✅ | ✅ (최근 개선) | - | query_router.py:145 |
| 어드민 페이지 | ✅ | ❌ **EN UI 없음** | **High** | [admin/layout.tsx](frontend/src/app/admin/layout.tsx) 메뉴 레이블 한국어 하드코딩 ("대시보드", "대화 로그" 등), i18n 없음 |
| `term_guide` 실시간 주입 | N/A | ✅ 구현됨 | - | [answer_generator.py:268-305](app/pipeline/answer_generator.py#L268-L305) `_build_en_system_prompt` — query matched_terms + `EnTermMapper.extract_from_ko_context(context)` 병합해 `[Term Guide]` 섹션 주입 |
| TRANSCRIPT Intent | 있음 + UI 연동 | 있음 + **UI 연동 O** (EN i18n 지원) | - | query_analyzer.py:375-379, TranscriptUpload.tsx |
| 단턴 리라이터 (rewriter) | 규칙 기반 (cde2df4) | 동일 규칙 적용 추정 — **EN 실험 결과 미기록** | Mid | |
| FAQ 7개 신규(학사지원팀) | ✅ main에 반영 (eee6b40) | EN 쪽 대응 미확인 | Mid | |

---

## 6. 평가 데이터·성능

### 평가셋 통계

| 파일 | 문항 수 | 주요 카테고리 | 난이도 |
|------|---------|---------------|--------|
| `eval_ko.jsonl` | **50** | 학사일정 14 / 수강신청 16 / 졸업 7 / OCU 9 | easy 31 / med 16 / hard 3 |
| `eval_ko_unified.jsonl` | **88** | 통합 확장 (unified) | - |
| `eval_en.jsonl` | **52** | 수강신청 10 / 졸업 10 / 부전공 5 / 학사일정 7 / 기타 16 (국제학생·편입·언어일관성) | easy 26 / med 19 / hard 7 |
| `eval_en_unified.jsonl` | **83** | 통합 확장 | - |

### 최신 공식 지표 (reports/ANALYSIS_REPORT.md, 2026-04-04)

| 메트릭 | KO 값 | EN 값 | 비고 |
|--------|-------|-------|------|
| Faithfulness | 0.952 | (미기록) | |
| Answer Relevancy | 0.840 | (미기록) | |
| Context Precision | **0.696** ⚠️ | (미기록) | 목표 0.90 |
| Context Recall | **0.788** ⚠️ | (미기록) | |
| Answer Correctness | 0.818 | (미기록) | |
| 평균 | **0.819** | (미기록) | |

**주목**: `reports/`에 **EN 전용 RAGAS 리포트가 없음**. KO 중심으로만 기록됨. EN 개선 commit(1518093) 이후 공식 평가 결과 미기록 상태.

### 메모리 기준선 (점검 필요)

| 메트릭 | 값 | 출처 |
|--------|-----|------|
| KO F1 (50문항, qwen2.5:7b) | 0.4119 | project_baselines memory (20일 전 기록) |
| EN F1 (52문항, qwen2.5:7b) | **0.3261** | 동일 메모리 |
| KO Recall@5 | 0.96 | |
| EN Recall@5 | **0.80** | |

→ **EN 기준선이 KO보다 체계적으로 낮음** (F1 -0.086, Recall@5 -0.16). 현재 팀 기준선(78%)과 맞춰 재측정 필요.

---

## 7. 알려진 오류·이슈 리스트 (심각도별)

### High (EN 사용자 실제 저해)

- [ ] **EN 어드민 페이지 없음** — [admin/layout.tsx](frontend/src/app/admin/layout.tsx) 메뉴 레이블("대시보드"·"대화 로그"·"FAQ 관리" 등) 한국어 하드코딩, `t(lang,...)` i18n 호출 부재. EN 언어 선택으로 진입해도 한국어 UI 노출.
- [ ] **`en_glossary.yaml` 커버리지 부족** — 수강정정/학사경고/공인결석/수업연한초과자 등 핵심 학사 용어 EN 매핑 누락. KO `glossary.py` TERM_MAP 80+ 대비 단절. **term_guide 인프라가 이미 작동 중이라 용어만 추가하면 EN 답변에 즉시 반영**.

### Mid (사용자 영향 있으나 우회 가능)

- [ ] **EN 조기졸업 신청기간 과잉 거절** — `academic_graph.py` (a3b7694 수정 이력) — 재발 확인 필요
- [ ] **EN TRANSCRIPT 학번 미추출** — `_analyze_en()` [query_analyzer.py:511-747](app/pipeline/query_analyzer.py#L511-L747) 에서 학번 추출 후 student_groups 미설정 (KO line 441-442와 불일치)
- [ ] **EN 벡터 검색 always-on** — KO처럼 Intent별 선택이 아니라 매번 실행 → 지연·비용 증가
- [ ] **학사지원팀 7개 신규 FAQ EN 대응** — main의 eee6b40 커밋으로 FAQ 추가됐으나 EN 쿼리에서 검색·매칭 검증 미기록

### Low (기술 부채)

- [ ] **EN 기능어 보강 하드코딩** — [query_analyzer.py:658-688](app/pipeline/query_analyzer.py#L658-L688) if-else 12개. 확장 시 매번 코드 수정
- [ ] **단턴 리라이터 EN 실험 미기록** — cde2df4에서 KO 기준 규칙 기반 전환, EN 영향도 평가 없음
- [ ] **평가셋 카테고리 불균형** — KO는 OCU 9개 편중, EN은 국제학생·편입생 집중. 직접 비교 어려움

---

## 8. 우선순위 권장 작업 (EN 지원 개선)

| 순위 | 작업 | 기대 효과 | 예상 소요 |
|------|------|-----------|-----------|
| P1 | **`en_glossary.yaml` 고빈도 누락 용어 10~20개 추가** (수강정정·학사경고·공인결석·수업연한초과·성적표 등) | matched_terms 탐지율↑ + term_guide 자동 주입 경로 작동 → EN 답변 용어 일관성 및 검색 품질 동시 개선 | 1일 |
| P2 | **EN 공식 RAGAS/Contains-F1 최신 기준선 측정** | 개선 효과 정량화 가능 | 반일 |
| P2 | **EN 어드민 페이지 i18n** (메뉴 레이블부터 각 페이지 라벨 번역) | EN 관리자 접근성 확보 | 1~2일 |
| P3 | **EN 기능어 보강 하드코딩 → yaml 기반으로 이전** | 유지보수성 개선 | 반일 |
| P3 | **EN 벡터 검색 Intent별 선택적 토글 검토** | 지연·비용 감소 | 반일 + 평가 |

---

## 9. 한계·불확실성

- `reports/` 디렉터리에 **EN 전용 최신 RAGAS 결과가 없음** → 현재 EN 품질의 정확한 수치 미기록 상태
- 현재 메모리의 20일 전 기준선 외 최신 EN 측정값 부재
- 서버 운영 ChromaDB(`chromadb_new`, 3,377 embeddings)는 이 보고서 작성 직전에 받아서 반영됨. 이 DB 기준 EN 평가 재측정 권장
- `frontend/src/app/[lang]/` 구조 직접 파일 레벨 확인은 본 리포트에서 수행하지 않음. EN UI 유무는 코드 검색 기반 추론
