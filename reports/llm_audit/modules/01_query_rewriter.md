# 모듈 1 — `app/pipeline/query_rewriter.py` deep dive

**파일**: [app/pipeline/query_rewriter.py](app/pipeline/query_rewriter.py) (363 LoC)
**역할**: 멀티턴 follow-up 쿼리를 자립적 검색 쿼리로 변환
**LLM 호출**: Stage 3에서 `gemma3:4b` (또는 env로 override) 단발 호출
**의존성**: `app.config.settings`, `app.pipeline.ko_tokenizer`, `httpx`

---

## 1. 구조 요약

```
rewrite(query, history)                ← 외부 진입점
├─ rewrite_enabled 체크
├─ standalone-question skip (휴리스틱)
├─ Stage 2: rule_based_rewrite()       ← <5ms, 동기
│  └─ _extract_last_assistant_entity() ← bold > bullet > 빈도 토큰
└─ Stage 3: llm_rewrite()              ← LLM 호출, 0.8s timeout
   ├─ _format_history_for_prompt()
   ├─ httpx POST (api_type별 분기)
   └─ 4단계 응답 검증 (길이, 형식, prior copy, 동일)
```

호출 위치: [chat.py:552](backend/routers/chat.py#L552) (stream), [chat.py:969](backend/routers/chat.py#L969) (sync). 발동 조건은 `follow_up_signal.is_follow_up = True`.

---

## 2. 함수별 문제 카드

### `_extract_last_assistant_entity(history)` (line 39-85)

**의도**: 직전 assistant 응답에서 entity 후보 1개 추출.

| # | 등급 | 문제 | 위치 |
|---|---|---|---|
| P1 | 🟡 | 첫 매치 즉시 반환 — 후보 검증 없음. 의미 없는 굵은 글씨도 그대로 통과 | [line 60-64](app/pipeline/query_rewriter.py#L60-L64) |
| P2 | 🔴 | 빈도 기반은 잘못된 entity 선택 위험. "졸업학점은 120학점입니다" → entity="학점" (정작 사용자가 묻는 토픽은 "졸업"). 도메인에서 가장 자주 실패하는 패턴 | [line 73-83](app/pipeline/query_rewriter.py#L73-L83) |
| P3 | 🟡 | `_STOPWORDS`가 너무 빈약 — "학점·신청·방법" 같은 도메인 빈출어 누락. 도메인 stopword 보강 필요 | [line 33-36](app/pipeline/query_rewriter.py#L33-L36) |
| P4 | 🟢 | `bold.split(":")[0]`이 ":" 없는 경우 처리 미명 (실제론 동일 문자열 반환이라 무해) | [line 62](app/pipeline/query_rewriter.py#L62) |
| P5 | 🟡 | bullet 케이스 `b.strip().split(" ")[0]` — 한국어는 단어+조사 붙어 있어 첫 토큰이 조사 포함됨 ("졸업이"→"졸업이"). 의도는 형태소이지만 공백 분리로 부정확 | [line 67-71](app/pipeline/query_rewriter.py#L67-L71) |
| P6 | 🟡 | 빈도 카운트 시 형태 normalization 없음 — "학점이/학점은/학점" 따로 카운트. ko_tokenizer가 lemma까지 주지 않음 | [line 78-82](app/pipeline/query_rewriter.py#L78-L82) |

### `rule_based_rewrite(query, history)` (line 88-127)

**의도**: 단일 대명사를 entity로 치환.

| # | 등급 | 문제 | 위치 |
|---|---|---|---|
| P7 | 🟡 | 대명사 토큰 8개(KO) + 3개(EN)만 하드코딩. "걔/걔네/걔들/얘/얘들"·"they/them/those" 누락. 누락이 많아 효과 제한적 | [line 30](app/pipeline/query_rewriter.py#L30), [line 112-116](app/pipeline/query_rewriter.py#L112-L116) |
| P8 | 🟢 | `replace(p, entity, 1)`로 첫 매치만 치환. 두 대명사 있으면 둘째 누락. 실용적 영향 낮음 | [line 106](app/pipeline/query_rewriter.py#L106) |
| P9 | 🟢 | 한국어 매치 시 영어 검사 skip (`break`). 한 쿼리에 둘 다 있는 경우 드물어 무해 | [line 107-108](app/pipeline/query_rewriter.py#L107-L108) |
| P10 | 🔴 | **단어 경계 검사 없음** — "그거" → "졸업" 치환 시 "그거짓말" → "졸업짓말"처럼 부분 매치로 잘못 치환됨. **버그 가능성** | [line 106](app/pipeline/query_rewriter.py#L106) |

### `llm_rewrite(query, history, lang)` (line 185-307)

**의도**: gemma3:4b로 follow-up 쿼리 재작성.

| # | 등급 | 문제 | 위치 |
|---|---|---|---|
| P11 | 🟢 | `if not history: return None` — caller가 follow-up 체크 후 부르므로 이중 가드. 무해. | [line 196](app/pipeline/query_rewriter.py#L196) |
| P12 | 🔴 | **timeout 0.8s가 CPU에서 거의 항상 발동** — gemma3:4b가 CPU에서 ~4초. 운영 GPU에선 OK, dev/CPU에선 100% rule_fallback. **환경별 자동 조정 없음** | [line 249](app/pipeline/query_rewriter.py#L249), config rewrite_timeout_sec |
| P13 | 🟡 | `content.split("\n")[0]` — 첫 줄만. multi-line 정상 답변도 손실. think 토큰 우회 의도이나 정상 답변 손상 가능 | [line 271](app/pipeline/query_rewriter.py#L271) |
| P14 | 🟡 | 접두사 제거 리스트 6개 하드코딩. LLM이 다른 접두사 쓰면 통과 못함 ("재작성:", "Result:" 등 누락) | [line 272-277](app/pipeline/query_rewriter.py#L272-L277) |
| P15 | 🟡 | `rewritten == query: return None` — LLM이 원본 그대로 반환 시 reject. line 343의 standalone skip이 먼저 걸러 무해할 수도. **검증 필요** | [line 285-286](app/pipeline/query_rewriter.py#L285-L286) |
| P16 | 🔴 | **`_Q_KEYWORDS` 가드가 너무 엄격** — 의문문 어미·키워드 강제. LLM이 자연스러운 명사구 답해도 거부. False negative 위험 | [line 291-297](app/pipeline/query_rewriter.py#L291-L297) |
| P17 | 🔴 | **prior copy 차단 `rewritten[:30] in asst`** — 30자 substring 매칭은 너무 엄격. 정상 rewrite가 단어 공유만 해도 거부. False positive 위험 | [line 299-305](app/pipeline/query_rewriter.py#L299-L305) |
| P18 | 🟢 | `history[-4:]`로 최근 2턴 가정. user+assistant=1턴이라 4는 맞음 | [line 300](app/pipeline/query_rewriter.py#L300) |
| P19 | 🟡 | OpenAI 경로에도 `think: false` 필드 추가. OpenAI 스펙엔 없음 — 무시되거나 거부될 수 있음 | [line 245](app/pipeline/query_rewriter.py#L245) |
| P20 | 🟡 | `httpx.AsyncClient`를 매 호출마다 생성. connection pool 미활용. 동접 환경에서 비효율 | [line 249](app/pipeline/query_rewriter.py#L249) |
| P21 | 🟢 | `stream: false` 단발 호출. 80토큰이라 OK | [line 225, 241](app/pipeline/query_rewriter.py#L225) |

### `rewrite(query, history, *, skip_rule_stage, lang)` 통합 엔트리 (line 312-363)

| # | 등급 | 문제 | 위치 |
|---|---|---|---|
| P22 | 🟡 | standalone-question 가드 임계치 `len(query.replace(" ", "")) >= 8` — **매직 넘버 8**. 4원칙 위반 (env 미설정) | [line 343](app/pipeline/query_rewriter.py#L343) |
| P23 | 🟡 | Stage 2 `except Exception as e` — 너무 광범위. 구체 예외 분리 필요 | [line 354](app/pipeline/query_rewriter.py#L354) |
| P24 | 🟢 | Stage 3 실패 시 원본 반환 — graceful fallback. OK | [line 363](app/pipeline/query_rewriter.py#L363) |
| P25 | 🟢 | `skip_rule_stage=True` 분기 — follow_up_detector의 분배/순서 대명사 감지 시 사용 (chat.py에서 검증 예정) | [line 348](app/pipeline/query_rewriter.py#L348) |

---

## 3. 모듈 횡단 문제

### 3.1 캐싱 부재

- LLM 호출 캐싱 없음. history가 매번 달라 캐시 효율은 낮으나, history 안정한 경우 (예: 같은 세션 반복 follow-up)도 매번 호출.
- **개선 ROI 낮음** (해시 키 안정성 어려움).

### 3.2 관찰성 부족

- `logger.debug` 다수 — 운영 INFO 레벨에서 안 보임.
- 실패 사유 분류 없음:
  - 타임아웃 → "쿼리 재작성 타임아웃"
  - 길이/형식 reject → 분류 안 됨, 모두 `None` 반환만
  - prior copy reject → 분류 안 됨
- "97% rule_fallback" 디버깅이 main 코드론 불가능 — 메트릭 추가 필요.

### 3.3 4원칙(하드코딩 금지) 위반

| 위치 | 값 | 등급 |
|---|---|---|
| [line 30](app/pipeline/query_rewriter.py#L30) `_PRONOUN_TOKENS_KO` 8개 | 도메인 사전이 아닌 일반 사전이라 OK 경계 | 🟢 |
| [line 33-36](app/pipeline/query_rewriter.py#L33-L36) `_STOPWORDS` | OK (일반 stopword) | 🟢 |
| [line 132-155](app/pipeline/query_rewriter.py#L132-L155) `_SYSTEM_PROMPT_KO/EN` | **프롬프트가 코드 안 하드코딩** — 프롬프트 변경마다 코드 수정. config 분리 권장 | 🟡 |
| [line 291-293](app/pipeline/query_rewriter.py#L291-L293) `_Q_KEYWORDS` | 가드 임계값 하드코딩 (의문어 사전). 정책 일부라 환경변수보다 코드가 맞을 수 있음 | 🟢 |
| [line 343](app/pipeline/query_rewriter.py#L343) 매직 넘버 8 | **`MIN_STANDALONE_QUERY_CHARS = 8` 환경변수화 권장** | 🟡 |
| [line 283](app/pipeline/query_rewriter.py#L283) `len(query) * 5` 팽창 한도 | 매직 5. env 또는 상수 분리 | 🟢 |
| [line 303](app/pipeline/query_rewriter.py#L303) `rewritten[:30]` substring 길이 | 매직 30. env 또는 상수 분리 | 🟡 |
| [line 300](app/pipeline/query_rewriter.py#L300) `history[-4:]` prior copy 검사 범위 | 매직 4. env 또는 max_turns 연동 | 🟢 |

### 3.4 외부 의존 (cross-cutting)

| 사용 | 위치 | 비고 |
|---|---|---|
| `settings.conversation.rewrite_enabled` | line 325 | 전역 토글 |
| `settings.conversation.rewrite_model` | line 220, 236 | 모델명 |
| `settings.conversation.rewrite_max_input_turns` | line 200 | 컨텍스트 턴 수 |
| `settings.conversation.rewrite_max_tokens` | line 228, 242 | output 한도 |
| `settings.conversation.rewrite_timeout_sec` | line 249 | 타임아웃 |
| `settings.conversation.rewrite_base_url` | line 215 | 별도 LLM URL |
| `settings.llm.api_type` | line 216 | ollama / openai |
| `settings.llm.base_url` | line 215 | fallback URL |
| `ko_tokenizer.tokenize` | line 75 | 한국어 토큰화 |

---

## 4. 의문점 (검증 필요)

| # | 의문 | 검증 방법 |
|---|---|---|
| Q1 | **bold 추출이 실제 답변에 일관되게 적용되나?** answer_generator의 system prompt가 bold 출력을 강제하는지 확인 필요 | [answer_generator.py](app/pipeline/answer_generator.py) 프롬프트 정독 |
| Q2 | **빈도 기반 entity가 잘 작동?** "졸업학점 120학점" 케이스에서 잘못된 entity? | 실 운영 로그에서 `_extract_last_assistant_entity` 출력 샘플 |
| Q3 | **0.8초 타임아웃은 어떻게 결정?** GPU에서 측정한 값? CPU에선 항상 fallback이면 의미 없음 | git blame line 249, settings.conversation.rewrite_timeout_sec 도입 시점 |
| Q4 | **`max_input_turns=2`로 충분?** 4-5턴 대화에서 효과 감소? | 운영 로그에서 평균 turn 수 측정 |
| Q5 | **prior copy substring=30이 정상 rewrite도 false positive로 reject?** | 모킹 테스트 + 실 LLM 출력 샘플 |

---

## 5. 우선순위 진단 (모듈 1 한정)

### 🔴 P1 — 잠재 버그 / 기능 손실

- **P2** 빈도 기반 entity 선택의 잘못된 토픽 → rewrite 실패 → follow-up UX 손상
- **P10** 단어 경계 미검사 → 잘못된 치환 가능 ("그거" → "졸업"이 부분 매치 사고)
- **P12** CPU 환경 0.8초 타임아웃 → 100% rule_fallback → LLM 분기 무효화
- **P16** 의문문 강제로 자연스러운 명사구 답변 reject (false negative)
- **P17** prior copy 30자 substring → 정상 rewrite reject (false positive)

### 🟡 P2 — 정책 정밀화 / 관찰성

- **P1·P3·P5·P6** entity 추출 휴리스틱 정밀화
- **P7** 대명사 토큰 누락 보강
- **P13·P14** 응답 파싱 견고성 (multi-line, 접두사)
- **P19** OpenAI 경로의 `think: false` 정리
- **P22** 매직 넘버 8 등 env 추출
- **3.2** 실패 사유별 메트릭 분류 (rule fallback / timeout / 형식 reject / prior copy reject)

### 🟢 P3 — 마이너 정리

- **P4·P8·P9·P11·P18·P21·P24·P25** — 무해하거나 개선 ROI 낮음
- **P20** httpx Client 재사용 (성능)
- **P23** 광범위 예외 catch 정리

---

## 6. 리팩터 가설 (Phase C 입력용)

1. **Stage 2 entity 추출 재설계**: 빈도 기반 last resort → 도메인 entity 사전 기반 매칭 (학과명·학번·키워드)으로 변경 검토. 또는 LLM analyzer(`query_analyzer`)의 entities 출력을 직접 활용.
2. **Stage 2 단어 경계 정규식 치환**: `\b그거\b` 한국어 단어 경계 (조사 패턴 고려).
3. **Stage 3 폴백 정책 변경**: 0.8초 타임아웃은 GPU 기준. CPU 환경에선 자동 우회 (또는 환경변수로 dev/prod 분리).
4. **응답 검증 완화**: 의문문 강제 / prior copy 30자 substring을 더 정교한 의미 기준으로 (예: 사용자 질문의 의문어 보존 여부).
5. **메트릭 추가**: rewrite[rule|llm|timeout|reject:length|reject:format|reject:copy|fail] 분류 로깅.
6. **프롬프트 외부화**: `_SYSTEM_PROMPT_KO/EN`을 `prompts/rewriter_ko.txt` 등 파일로 분리.

---

## 7. 다음 단계

- 의문점 Q1~Q5는 모듈 2~6 작업하면서 자연스럽게 답이 나오는 것들 (Q1 answer_generator, Q4 chat.py 등). 일단 메모만.
- 모듈 1 fix 자체는 Phase C 청사진에서 다른 모듈과 함께 우선순위 결정 후 Phase D 실행.
- 다음 모듈: **`translator.py` (254 LoC, 독립)** 진행.
