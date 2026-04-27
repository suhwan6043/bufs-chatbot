# 검색 아키텍처 — KO vs EN 차이 매핑

**대상 브랜치**: `feat/en-support-optimization` @ `a0068eb`
**주요 파일**: [app/pipeline/](app/pipeline/), [app/vectordb/](app/vectordb/), [app/graphdb/](app/graphdb/)

---

## 1. 전체 파이프라인 (공통)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         사용자 질문 (Q)                                    │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │  LanguageDetector     │  한국어 문자 ≥30% → 'ko'
                    │  language_detector.py │  미만 → 'en'                 <1ms
                    └───────────┬───────────┘
                                │
               ┌────────────────┴────────────────┐
               │                                 │
        ┌──────▼──────┐                  ┌───────▼───────┐
        │  KO 경로     │                  │   EN 경로     │
        │  analyze()   │                  │  _analyze_en() │  <3ms (FlashText)
        │  L430-509    │                  │  L511-747     │
        └──────┬──────┘                  └───────┬───────┘
               │                                 │
               └────────────────┬────────────────┘
                                │
                     ┌──────────▼──────────┐
                     │   QueryAnalysis     │
                     │   (intent, entities,│
                     │    student_id,      │
                     │    requires_vector, │
                     │    matched_terms)   │
                     └──────────┬──────────┘
                                │
                   ┌────────────▼────────────┐
                   │   QueryRouter           │
                   │   route_and_search()    │
                   │   query_router.py:49    │
                   └──────┬──────────────┬───┘
                          │              │
                  병렬 실행 ───────────┐ │
                          │              │ │
                ┌─────────▼───┐    ┌────▼─▼───────┐
                │ ChromaStore │    │ AcademicGraph│
                │ BGE-M3 1024d│    │ NetworkX pkl │
                │ cosine 검색 │    │ FAQ 역인덱스  │
                │ L155-318    │    │ L321-...     │
                └─────────┬───┘    └──────┬───────┘
                          │               │
                          └───────┬───────┘
                                  │
                     ┌────────────▼────────────┐
                     │   Reranker              │
                     │   BGE-Reranker-v2-m3    │
                     │   CrossEncoder, Top-K   │
                     └────────────┬────────────┘
                                  │
                     ┌────────────▼────────────┐
                     │   ContextMerger         │
                     │   RRF + intent 가중치   │
                     │   direct_answer 판정    │
                     └────────────┬────────────┘
                                  │
                     ┌────────────▼────────────┐
                     │   AnswerGenerator       │
                     │   Ollama LLM (qwen3:8b) │
                     └────────────┬────────────┘
                                  │
                     ┌────────────▼────────────┐
                     │   ResponseValidator     │
                     │   + ChatLogger          │
                     └─────────────────────────┘
```

---

## 2. KO vs EN 경로 차이 — 상세

### 2.1 분기 지점 (QueryAnalyzer)

```
                    QueryAnalyzer.analyze(question)
                             │
                   ┌─────────┴─────────┐
                   │ lang = detect()   │
                   └─────────┬─────────┘
                             │
          ┌──────────────────┴──────────────────┐
          │                                      │
     ▼ KO 경로 (L430-509)                 ▼ EN 경로 (L511-747)
     ┌─────────────────────┐              ┌─────────────────────────────┐
     │ 1. Glossary 정규화  │              │ 1. FlashText 매핑            │
     │    TERM_MAP 80+     │              │    en_glossary.yaml 175종    │
     │    은어·약어 → 정식 │              │    aliases_en → ko 추출     │
     │    glossary.py      │              │    EnTermMapper.extract()   │
     └──────────┬──────────┘              └──────────┬──────────────────┘
                │                                    │
     ┌──────────▼──────────┐              ┌──────────▼──────────────────┐
     │ 2. 학번 추출        │              │ 2. 학번 추출 (EN 표현)      │
     │    4자리/2자리/범위 │              │    4자리 + "class of 2020"  │
     │    L141-155         │              │    "cohort" / "student"     │
     │                     │              │    L528-540                 │
     └──────────┬──────────┘              └──────────┬──────────────────┘
                │                                    │
     ┌──────────▼──────────┐              ┌──────────▼──────────────────┐
     │ 3. 학생유형 매칭    │              │ 3. 학생유형 매칭 (EN)       │
     │    STUDENT_TYPE_KW  │              │    EN_STUDENT_TYPE_PATTERNS │
     └──────────┬──────────┘              └──────────┬──────────────────┘
                │                                    │
     ┌──────────▼──────────┐              ┌──────────▼──────────────────┐
     │ 4. Intent 분류      │              │ 4. Intent 분류              │
     │    키워드 매칭       │              │    matched_terms에서 KO 용어│
     │    9개 Intent       │              │    → KO 키워드 매칭 재사용  │
     │    INTENT_KEYWORDS  │              │    미탐 시 GENERAL          │
     └──────────┬──────────┘              └──────────┬──────────────────┘
                │                                    │
     ┌──────────▼──────────┐              ┌──────────▼──────────────────┐
     │ 5. 엔티티 추출      │              │ 5. 엔티티 추출 (+5종)       │
     │    12종             │              │    17종 (asks_url,          │
     │                     │              │    semester_half, 등)       │
     │    L970-1066        │              │    L551-649                 │
     └──────────┬──────────┘              └──────────┬──────────────────┘
                │                                    │
     ┌──────────▼──────────┐              ┌──────────▼──────────────────┐
     │ 6. requires_vector  │              │ 6. requires_vector          │
     │    Intent별 선택적  │              │    **항상 True** (L716)     │
     │    SCHEDULE는 False │              │                              │
     │    가능             │              │                              │
     └──────────┬──────────┘              └──────────┬──────────────────┘
                │                                    │
     ┌──────────▼──────────┐              ┌──────────▼──────────────────┐
     │ 7. 기능어 보강      │              │ 7. EN 기능어 보강           │
     │    (불필요)         │              │    if-else 12개 하드코딩    │
     │                     │              │    'application period' →   │
     │                     │              │    신청기간 추가            │
     │                     │              │    L658-688                 │
     └──────────┬──────────┘              └──────────┬──────────────────┘
                │                                    │
                └────────────────┬───────────────────┘
                                 │
                        ┌────────▼────────┐
                        │ QueryAnalysis   │
                        │ (공통 스키마)   │
                        │ + matched_terms │  ← EN만 채워짐
                        └─────────────────┘
```

### 2.2 검색 실행 (QueryRouter)

```
                   route_and_search(analysis)
                            │
             ┌──────────────┴──────────────┐
             │ need_vector = analysis       │
             │   .requires_vector &&        │
             │   self.chroma_store          │
             │                              │
             │ need_graph = analysis        │
             │   .requires_graph &&         │
             │   self.academic_graph        │
             └──────────────┬──────────────┘
                            │
          ┌─────────────────┴─────────────────┐
          │ ▼ KO                    ▼ EN       │
          │                                    │
┌─────────▼──────────┐      ┌──────────────────▼──────────┐
│  Vector Search     │      │  Vector Search               │
│  (intent 따라 스킵) │      │  (항상 실행 — 20-30% 더 씀) │
│                    │      │                              │
│  query_text =      │      │  query_text =                │
│    analysis.normal │      │    analysis.ko_query         │
│    ized_query     │      │    (FlashText가 재작성한 KO) │
│                    │      │                              │
│  department 필터  │       │  department 필터 동일        │
│  chroma_store      │      │  chroma_store                │
└─────────┬──────────┘      └──────────────┬───────────────┘
          │                                │
          └────────────────┬───────────────┘
                           │
                  ┌────────▼────────┐
                  │  Graph Search   │  양쪽 공통
                  │  FAQ 역인덱스   │  academic_graph
                  │  direct_answer  │
                  │  후보 반환       │
                  └────────┬────────┘
                           │
                  ┌────────▼────────┐
                  │  merge          │  RRF + intent 가중치
                  └─────────────────┘
```

### 2.3 답변 생성 (AnswerGenerator)

```
                  generate(question, context, lang, matched_terms, ...)
                            │
              ┌─────────────┴─────────────┐
              │ if lang == "en":           │
              └─────────────┬─────────────┘
                            │
         ┌──────────────────┴──────────────────┐
         │ ▼ KO 경로                           │ ▼ EN 경로
         │                                     │
┌────────▼────────────┐              ┌────────▼────────────────────────┐
│ SYSTEM_PROMPT        │              │ EN_SKIP_TRANSLATE_SYSTEM_PROMPT  │
│ L75-98               │              │ L45-72                           │
│                      │              │                                  │
│ 규칙:                │              │ 규칙:                            │
│ - 추론 과정 금지     │              │ - KO 원문 테이블/일정 직독      │
│ - 번역 금지          │              │ - KO 약자 → 영어 (월→Month)     │
│ - KO만               │              │ - [Term Guide] 주입             │
└────────┬────────────┘              └────────┬────────────────────────┘
         │                                    │
         │                           ┌────────▼────────────────────────┐
         │                           │ _build_en_system_prompt()       │
         │                           │ L268-305                         │
         │                           │                                  │
         │                           │ term_section 구성:               │
         │                           │ 1. matched_terms (쿼리 추출)    │
         │                           │ 2. context_terms                │
         │                           │    (KO 컨텍스트에서 추출,       │
         │                           │     EnTermMapper               │
         │                           │     .extract_from_ko_context)  │
         │                           │ 3. intent fallback              │
         │                           │    (_INTENT_FALLBACK_TERM)      │
         │                           │                                  │
         │                           │ → "[Term Guide]\n- KO → EN\n"   │
         │                           └────────┬────────────────────────┘
         │                                    │
         │                           ┌────────▼────────────────────────┐
         │                           │ One-Pass Streaming              │
         │                           │ <ko_draft>...</ko_draft>        │
         │                           │ <final_answer>...</final_answer>│
         │                           │                                  │
         │                           │ Rolling Buffer로 태그 쪼개짐    │
         │                           │ 방어                            │
         │                           └────────┬────────────────────────┘
         │                                    │
         └────────────────────┬───────────────┘
                              │
                     ┌────────▼────────┐
                     │ Ollama LLM      │  qwen3:8b
                     └─────────────────┘
```

---

## 3. 차이점 요약 표

| 단계 | KO | EN | 주요 파일·라인 |
|------|----|----|----------------|
| **언어 감지** | 한국어 문자 ≥30% | 미만 | [language_detector.py:16-23](app/pipeline/language_detector.py#L16-L23) |
| **용어 정규화** | `glossary.py` TERM_MAP 80+ (은어/약어) | `en_glossary.yaml` 175종 (공식용어 ko↔en↔aliases) | [glossary.py:19-180](app/pipeline/glossary.py#L19-L180) / [en_glossary.yaml](config/en_glossary.yaml) |
| **매칭 엔진** | dict lookup | FlashText (Aho-Corasick, O(N)) | [query_analyzer.py:50-131](app/pipeline/query_analyzer.py#L50-L131) |
| **학번 추출** | 4자리/2자리/범위 | 4자리 + `class of 2020`, `cohort` | [query_analyzer.py:141-155](app/pipeline/query_analyzer.py#L141-L155) / [:528-540](app/pipeline/query_analyzer.py#L528-L540) |
| **Intent 분류** | 키워드 직접 매칭 | matched_terms → KO 규칙 재사용 | [query_analyzer.py:299-394](app/pipeline/query_analyzer.py#L299-L394) / [:511-747](app/pipeline/query_analyzer.py#L511-L747) |
| **엔티티 수** | 12종 | **17종** (+asks_url, semester_half, registration_deadline, payment_period, graduation_cert) | [query_analyzer.py:970-1066](app/pipeline/query_analyzer.py#L970-L1066) / [:551-649](app/pipeline/query_analyzer.py#L551-L649) |
| **기능어 보강** | 불필요 | if-else 12개 하드코딩 (`application period`→신청기간 등) | [query_analyzer.py:658-688](app/pipeline/query_analyzer.py#L658-L688) |
| **`requires_vector`** | Intent별 선택적 (SCHEDULE은 False 가능) | **항상 True** | [query_analyzer.py:456](app/pipeline/query_analyzer.py#L456) vs [:716](app/pipeline/query_analyzer.py#L716) |
| **벡터 쿼리 문자열** | `analysis.normalized_query` | `analysis.ko_query` (FlashText 재작성판) | [query_router.py:155-318](app/pipeline/query_router.py#L155-L318) |
| **그래프 검색** | 공통 (academic_graph) | 공통, 다만 `matched_terms`로 FAQ 매칭 후보 확장 | [query_router.py:321+](app/pipeline/query_router.py#L321) |
| **리랭커 편향** | 기본 | `asks_url` 엔티티 감지 시 URL 청크 boost | [reranker.py](app/pipeline/reranker.py) |
| **시스템 프롬프트** | `SYSTEM_PROMPT` — KO 직생성 | `EN_SKIP_TRANSLATE_SYSTEM_PROMPT` — KO 컨텍스트 + KO 약자 변환 + Term Guide | [answer_generator.py:75-98](app/pipeline/answer_generator.py#L75-L98) / [:45-72](app/pipeline/answer_generator.py#L45-L72) |
| **Term Guide 주입** | N/A | ✅ query matched + context 추출 병합 | [answer_generator.py:268-305](app/pipeline/answer_generator.py#L268-L305) |
| **스트리밍** | 일반 | One-Pass Rolling Buffer (`<ko_draft>` → `<final_answer>`) | [answer_generator.py:825+](app/pipeline/answer_generator.py#L825) |

---

## 4. 실측 성능 대비 (오늘 측정)

| 지표 | KO (baseline, 81.7%) | EN (2026-04-23, 83Q) | EN 기준 갭 |
|------|---------------------|---------------------|-----------|
| Overall F1 | 0.4119 (메모리, 50Q qwen2.5:7b) | **0.267** (qwen3:8b 7개 용어 추가 후) | -0.145 |
| Contains F1 | 0.817 | **0.473** | -0.344 |
| Recall@5 | 0.96 | **0.862** | -0.098 |
| MRR@5 | - | 0.746 | - |
| 평균 검색 지연 | - | 15.9s | - |
| 평균 생성 지연 | - | 7.8s | - |

EN이 여전히 Contains-F1 기준 KO 대비 -34%p. 주요 원인:
1. **벡터 always-on으로 인한 노이즈** (KO는 SCHEDULE intent에서 스킵 가능)
2. **EN→KO 쿼리 재작성**에서 매칭 손실 — matched_terms가 부족하면 ko_query가 원문 그대로 전달
3. **학사용어 커버리지** (glossary 175종 중에도 OPS 11 영역 빈 곳: 증명서 경로 6종, 학생증 3종 구분, 개명 절차 등)

---

## 5. 병목 식별 포인트 (내일 테스트 초점)

```
┌──────────────────────────────────────────────────────────────────────┐
│                     EN 쿼리 처리 병목 후보                             │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│ ① FlashText 매칭 실패                                                │
│    → matched_terms = []                                              │
│    → ko_query = 원문 EN 그대로                                       │
│    → BGE-M3 크로스링구얼 fallback                                    │
│    → noise 높음, Recall 저하                                         │
│                                                                      │
│ ② Intent 오분류                                                      │
│    → matched_terms 있어도 Intent.GENERAL로 떨어짐                    │
│    → direct_answer 경로 스킵                                         │
│    → 일반 벡터 검색만 실행                                           │
│                                                                      │
│ ③ Vector Search 노이즈                                               │
│    → requires_vector=True 고정                                       │
│    → SCHEDULE/REGISTRATION도 벡터 검색                               │
│    → 다른 연도·학번 청크 혼입                                        │
│                                                                      │
│ ④ Context Merger 가중치 부적합                                       │
│    → EN은 항상 hybrid (vector+graph)                                 │
│    → KO의 graph-only direct_answer 경로 미활용                       │
│                                                                      │
│ ⑤ Term Guide 부족                                                    │
│    → matched_terms=[] → fallback 1개 용어만                          │
│    → EN 출력 시 KO 용어 그대로 노출 위험                             │
│                                                                      │
│ ⑥ 프롬프트에서 KO 원문 직독 실패                                     │
│    → LLM이 KO 청크 읽고 EN으로 옮기는 과정에서                       │
│      숫자/날짜 환각 가능                                             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 6. 다음 검증 로드맵 (병목 식별용)

1. **matched_terms 로그 기록** — 각 EN 쿼리에서 FlashText 추출 결과, 길이 0인 경우 비율 측정
2. **Intent 분포 로그** — EN 쿼리 중 GENERAL 비율, 실제 intent 정답 라벨과 비교
3. **Vector vs Graph 기여도 로그** — merger가 최종 컨텍스트에 각각 몇 청크 포함했는지
4. **카테고리별 Recall 상세** — `cat:grade`, `cat:leave_of_absence` 등 약점 카테고리에서 구체 쿼리 → 검색 결과 트레이스
