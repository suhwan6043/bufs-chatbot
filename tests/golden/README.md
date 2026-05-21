# 핫패스 골든 회귀 테스트

## 목적

LLM 파이프라인 리팩터 작업 전 현재 동작을 골든 파일로 고정. 모든 수정에서 "내가 의도한 것만 바뀌고 나머지는 그대로"를 즉시 확인하기 위함.

## 4 핫패스

| Label | 쿼리 | 검증 핵심 |
|---|---|---|
| `ko_direct_answer` | "졸업학점 알려줘" | direct path 발동 + 핵심 fact 보존 |
| `ko_llm_generate` | "졸업요건 중 전공과 교양 기준 알려줘" | LLM 경유 + 핵심 키워드 포함 |
| `en_generate` | "How can I apply for early graduation?" | 영어 출력 + intent 안정성 |
| `ko_domain_out` | "마라탕 맛집 알려줘" | **환각 방지** (학사 fact 토큰 0회) |

## 파일

```
tests/golden/
├── README.md                         (본 문서)
├── _raw/                             (캡처 raw — 다회 반복 결과)
│   ├── ko_direct_answer.json
│   ├── ko_llm_generate.json
│   ├── en_generate.json
│   └── ko_domain_out.json
├── ko_direct_answer.golden.json      (최종 굳혀진 시그너처)
├── ko_llm_generate.golden.json
├── en_generate.golden.json
└── ko_domain_out.golden.json
```

`_raw/` = 캡처 즉시 산출물 (참고·재현용). `.golden.json` = pytest가 비교하는 확정 시그너처.

## 캡처·갱신 워크플로우

### 신규 캡처 (또는 의도된 변경 후 재캡처)

```bash
# 1. 백엔드 시작
bash scripts/run_backend.sh &

# 2. 4 path × 3회 반복 캡처 → tests/golden/_raw/
python scripts/capture_golden.py --runs 3 --output tests/golden/_raw/

# 3. 안정성 리포트 확인 — 출력 마지막 STABILITY SUMMARY 보기
#    ✅ = path/intent 모두 안정. golden 굳히기 가능
#    ⚠️ = 흔들림. 쿼리 교체 또는 시그너처 완화 필요

# 4. (Path #3 EN 응답은 사람이 눈으로 검수 — 영어가 의미 있는 답인가)
cat tests/golden/_raw/en_generate.json | jq '.runs[0].done_payload.answer'

# 5. 검수 통과 후 golden 굳히기 (수동)
#    _raw/<label>.json 에서 시그너처 추출 → <label>.golden.json
```

### 골든 검증 (회귀 테스트)

```bash
# 백엔드 떠 있는 상태에서
pytest tests/test_golden_paths.py -v
```

## 변경 절차

- **코드 변경 → 회귀 테스트 실패**: 의도된 변경인지 검토. 의도된 거면 재캡처 후 골든 갱신. 의도 아니면 회귀 — 수정.
- **golden 갱신 시 반드시 PR commit 메시지에 사유 명시**. 예: "골든 갱신: rerank gate threshold 0.20 → 0.25 적용 후 path #1 응답 유지 확인"
- **다회 캡처가 흔들리는 path는 시그너처를 더 느슨하게** (예: intent 단일 → intent_any). 또는 쿼리 자체를 더 안정적인 것으로 교체.

## 시그너처 항목

| 항목 | 검증 방식 | 비고 |
|---|---|---|
| `inferred_path` | 정확 일치 ("direct" / "stream") | token 이벤트 카운트로 추론 |
| `intent` | `intent in expected_intent_any` | 단일 강제 X (분류 안정성 허용) |
| `fact_token_hits` | path별 임계 (예: #4는 0) | 환각 검증 핵심 |
| `answer contains [..]` | 핵심 키워드 substring | Path #2의 "120학점" 등 |
| `en_ratio` | Path #3에서 > 0.5 | 영어 출력 보장 |
| `answer_chars` | 범위 (예: #4 < 500자) | 장황한 환각 차단 |
| `duration_ms` | **기록만, assert X** | 환경 의존 flaky |
| `source_urls_count` | 캡처 결과 보고 결정 | LLM 경로 항상 채우는지 미검증 |

## 알려진 한계

1. **LLM temperature=0.1**: 미세 변동 있음. 시그너처는 verbatim 아닌 구조 + key phrase 중심.
2. **Path #3 EN**: app.log에 실 트래픽 0건. 골든이 "현재 동작" 일 뿐 "올바른 동작" 보장 아님. 응답 텍스트는 사람 검수 필수.
3. **direct vs stream 경계**: PR #25 rerank gate(threshold 0.20) 근처에서 흔들릴 수 있음. 다회 캡처로 안정성 확인 후 굳히기.
4. **백엔드 의존**: 인-프로세스 mock 없음. CI에선 백엔드 + Ollama 둘 다 띄워야 함 (느림). 로컬 회귀 검증용 1차 도구.

## 후속 개선 (1주차 이후)

- fact 토큰 정규식 확장 ("백이십 학점" 같은 우회 환각도 잡기)
- in-process 테스트 (LLM mock으로 빠른 CI)
- golden capture의 결정성 강화 (LLM_TEMPERATURE=0 + LLM_SEED 환경변수)
- 더 많은 path (저신뢰 재시도, follow-up 멀티턴 등)
