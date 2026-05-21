# BUFS Chatbot — 프로젝트 지침서

부산외대 학사 RAG 챗봇. 자세한 아키텍처·실행 방법은 `README.md` 참조.

## 4대 원칙 (위반 금지)

1. **유연한 스키마 진화** — 새 문서 유형/메타데이터가 추가될 때 기존 코드를 최소 수정으로 수용. 데이터에서 스키마가 자라게 둔다.
2. **비용·지연 최적화** — 불필요한 LLM 호출, 중복 임베딩, 과도한 재랭킹 후보, 매번 전체 재크롤 등을 줄인다. 동적 커뮤니티 선택·증분 업데이트를 우선.
3. **지식 생애주기 관리** — 추가·수정·삭제가 전체 재구축 없이 반영되도록 설계. 해시 기반 변경 감지 / 증분 인덱싱.
4. **하드코딩 절대 금지** — 모델명·경로·임계치는 `.env` 또는 `app/config.py`. 문서·코드에 절대값을 박지 않는다.

## 디렉터리 SSOT

| 위치 | 역할 |
|------|------|
| `app/pipeline/` | 검색·답변 파이프라인 (전처리 → 검색·병합 → 생성·검증) |
| `app/crawler/` | gnuboard5 공지 크롤러 + 변경감지 (`change_detector`, `notice_crawler`) |
| `app/graphdb/` | 학사 규정 그래프 (NetworkX, FAQ 역인덱스) |
| `app/vectordb/` | ChromaDB 래퍼 |
| `app/ingestion/` | PDF/공지 청킹·임베딩·증분 업데이트 |
| `app/scheduler/` | APScheduler 백그라운드 크롤링 잡 |
| `backend/` | FastAPI (chat, admin, transcript, source, health, feedback) |
| `frontend/` | Next.js (다국어 ko/en, `/admin` 페이지 포함) |
| `scripts/` | 인제스트·평가·빌드 (`ingest_all.py`가 마스터 진입점) |
| `data/crawl_meta/` | `content_hashes.json` (크롤 변경감지 상태) |
| `docs/archive/` | 시점성 스냅샷 (진단 리포트) — 일반 문서 아님 |

## 검색 우선순위

| 순위 | 소스 | doc_type |
|------|------|----------|
| 1 | 공식 PDF / 학생포털 | `domestic`, `guide` |
| 2 | 그래프 직접답변 / FAQ / 고정공지 | graph direct, `faq`, `notice`(📌) — RRF 동등 경쟁 |
| 3 | 일반 공지 / 장학 | `notice`(일반), `scholarship` |

## 함정 지도 — 의도된 동작 (수정 금지)

리팩터·정리 작업 시 "버그처럼 보이지만 의도된 안전장치"인 항목들. 변경 전 반드시 이 섹션 확인 + 사용자 승인 받을 것.

### 인프라·동시성
- **`QUERY_ROUTER_SEQUENTIAL=1`** — 일부 ChromaDB 빌드에서 동시 query가 segfault를 일으키는 환경 우회용. 병렬 검색 로직(`query_router.py:67-72`) 수정 시 이 폴백 경로 보존 필수.
- **`_LLM_SEMAPHORE = asyncio.Semaphore(settings.llm.max_concurrent)`** — `chat.py:36`. 동시 LLM 스트리밍 상한 (PR #22 베타 동접 보호). N+1번째 요청은 큐 대기 → Ollama VRAM 폭주 방어. **세마포어 제거하면 운영 OOM 위험**.
- **`_LLM_RESPONSE_CACHE` (TTL 1h, max 256)** — `answer_generator._make_cache_key()` + `chat.py:723`의 `get_cached_response()`. 캐시 hit 시 LLM 우회 즉시 반환. 캐시 키 산정 변경 시 hit rate 0으로 떨어질 수 있음 — 측정 후 변경.

### 다국어 분기
- **EN은 direct_answer를 의도적으로 스킵** — `chat.py:696` / `chat.py:1034` 의 `if merged.direct_answer and analysis.lang != "en":` 조건. EN 사용자는 항상 LLM 경로(→ translator 단계). 이유: graph/FAQ의 KO 텍스트를 EN으로 직접 보낼 수 없음. 이 조건 제거 시 EN UX 회귀.
- **`clear` SSE 이벤트는 LLM thinking 제거용** — `answer_generator.py:950` 의 `yield "\x00CLEAR\x00"`. Ollama 네이티브 경로에서 thinking 토큰 누적 후 실제 답변 시작 시 프론트 누적 텍스트 리셋. 프론트 [`useChat.ts:46-49`](frontend/src/hooks/useChat.ts) 가 이 이벤트 수신. 마커 변경 시 양쪽 동시 수정.

### 검색·랭킹
- **Phase 2.5 FAQ 최소 보장** — `query_router.py:268-289`. preferred_types에 `faq`가 있는데 FAQ 청크가 2개 미만이면 FAQ-only 추가 검색. 크롤된 일반 페이지가 상위 랭킹 차지해 FAQ가 reranker에 도달 못하는 회귀 방지용. **제거 시 a01·sc01 등 회귀 재현**.
- **OCU intent_k 제한 제거 상태** — `query_router.py:172-177`. 이전 `intent_k = min(intent_k, 6)`로 OCU 청크가 6위 밖에서 잘리는 q033/q035/q040 회귀 발생 → 제거됨. 다시 도입하지 말 것.
- **FAQ/PDF diversity guarantee** — `reranker.py:181-198`. top-k가 전부 FAQ면 최상위 PDF 1개 강제 삽입. LLM이 FAQ의 짧은 답변만 보고 환각 생성하는 a01 패턴 방어용.
- **Reranker Tier 1 부스트 제거 상태 (2026-04-18)** — `reranker.py:121-124`. 이전 `domestic +22%, guide +18%` 고정 부스트가 가이드북이 정답인 케이스 회귀 → 제거. 다시 추가하지 말 것. (보존: `tier2_bonus`=FAQ/pinned 5%, `url_*_bonus`=asks_url 4~18%)

### 답변 채택·검증
- **direct_answer 채택 시 LLM·후처리·validator 모두 우회** — `chat.py:695-715` (stream), `chat.py:1034-1046` (sync). `return` 즉시 SSE done 전송. **16% 트래픽이 이 경로**라 게이트 정확도가 사용자 신뢰에 직결.
- **PR #25 rerank_bypass_threshold = 0.20** — `context_merger.py:411`. CrossEncoder raw logit이 이 미만이면 direct_answer 거부 → LLM 경로 위임. `reranker.py:141`의 `r.metadata["raw_score"]` 보존이 게이트 입력. **reranker 점수 정규화·boost 변경 시 raw_score 보존 확인**.
- **`_answer_unit_aligns()` 게이트** — `answer_units.py:247` + `context_merger.py:413, 445, 520`. 단위(credit/won/date) + 구별자(연도/학번/이분법) 정합 검증. final-gate(`context_merger.py:520`)는 어느 경로로 들어온 direct_answer든 마지막에 한 번 더 확인. **새 direct_answer 진입점 추가 시 이 final-gate 통과 확인**.
- **L2 저신뢰 재시도 (`context_confidence < 0.3`)** — `chat.py:619-624`. 운영 트래픽에서 발동률 0% (audit Phase 2 확인). 제거 검토 가능하나 **transcript_context 첨부 트래픽에서 발동할 수 있어 측정 후 결정** (PR #15 이전 트래픽 패턴 확인).

### 대화·재작성
- **`follow_up_detector`의 `no_history` 첫 가드** — `follow_up_detector.py:97-98`. history 없으면 무조건 비-follow-up. 운영 로그에서 122/122 발동 — 단순 첫턴 트래픽 패턴이지 감지 실패 아님. session_id 발급 정책 (프론트 sessionStorage·새로고침 동작) 확인 전엔 감지 로직 수정 금지.
- **`query_rewriter.rewrite()`의 standalone-question skip** — `query_rewriter.py:328-345`. 이미 자립적 의문문(의문어 + 의문 어미 + 8자 이상)은 LLM rewrite 스킵. 이전 회귀(`langauge → langauge가 prior 토픽 합성`)의 핵심 가드. 임계치 8자 변경 시 회귀 재발 가능.
- **rewrite Stage 3의 4단계 응답 검증** — `query_rewriter.py:280-307` (길이·의문문·prior copy·동일). LLM이 답변 텍스트를 그대로 출력하는 회귀(`언제 수강신청? → 2026년 1월 28일`) 방어. 검증 완화 시 회귀 재발.

### 인제스트·데이터
- **`bufs_academic` 컬렉션은 production** — 절대 직접 수정·삭제 금지. 인제스트 실험은 별도 컬렉션(`bufs_v2`, `bufs_p0_fix_*`)에서 진행 후 `CHROMA_COLLECTION` env 전환으로 cutover.
- **`data/crawl_meta/content_hashes.json` source_id 형식 변경 = 일회성 마이그레이션 비용** — 크롤러 source_id 키 변경 시 기존 변경감지 상태 전체 무효화. 머지 전 사용자 명시 승인 필수.

## 실행 명령어

### 백엔드 시작
```bash
bash scripts/run_backend.sh
# → uvicorn backend.main:app --host 0.0.0.0 --port 8000 (자동 재시작 10회)
# 헬스체크: curl http://localhost:8000/api/health
```

### Ollama 시작
```bash
bash scripts/run_ollama.sh
# → OLLAMA_NUM_PARALLEL=2 OLLAMA_MAX_LOADED_MODELS=2 ollama serve
```

### 평가 (정답률 회귀)
```bash
python -X utf8 scripts/eval_contains_f1.py \
  --datasets data/eval/balanced_test_set.jsonl \
             data/eval/rag_eval_dataset_2026_1.jsonl \
             data/eval/user_eval_dataset_50.jsonl \
  --base-url http://localhost:8000 \
  --output reports/eval_contains_f1 \
  [--tag <태그>] [--seed 42] [--per-question-session] [--no-cache]
```
- baseline (5/18 환경): `combined_p0_1a_20260518_143121.json` = **68.90%**
- NO-GO: 전체 -1pp / 단일셋 -3pp / 거부율 -10pp 폭락

### 골든 회귀 테스트
```bash
# 골든 캡처 (현 동작을 골든 파일에 저장)
python scripts/capture_golden.py --base-url http://localhost:8000 --output tests/golden/
# 골든 재실행 검증
pytest tests/test_golden_paths.py -v
```

### Reranker 임계치 측정 (PR #25 후속)
```bash
python scripts/measure_rerank_bypass_threshold.py
# 운영 로그 23쌍 라벨 데이터에서 (cross logit, Q-Q cosine) 분포 측정
```

### 단위 테스트
```bash
pytest tests/ -v
# 또는 특정 모듈
pytest tests/test_follow_up_detector.py -v
```

### 인제스트 (별도 컬렉션)
```bash
export CHROMA_COLLECTION=bufs_v2     # production bufs_academic 건드리지 말 것
python scripts/ingest_all_v2.py --collection bufs_v2 --report /tmp/ingest.json
# 80-100분 GPU
```

## 작업 규칙

- 새 기능 전 `app/pipeline/`, `app/crawler/` 기존 함수 우선 재사용. 중복 구현 금지.
- 크롤러 변경 시 `data/crawl_meta/content_hashes.json` 호환성 확인 (source_id 형식 변경은 일회성 마이그레이션 비용 발생).
- 모델·임계치는 `app/config.py`의 `Settings` 클래스에 추가. 코드에 매직 넘버 금지.
- 평가는 `scripts/eval_*` 사용. 새 평가 도입 시 기존 리포트 양식과 키 호환.

## 커밋 규칙 (필수 준수)

기능적·로직적 검증이 끝나면 **즉시 커밋**한다. 사용자가 따로 지시하지 않아도 된다.
**푸시는 절대 자동으로 하지 않는다** — 사용자가 명시적으로 "푸시" 또는 "push"라고 지시할 때만 실행.

### 커밋 전 체크리스트 (모두 통과해야 커밋)

1. **파싱·구문 검증** — `python -c "import ast; ast.parse(...)"` 또는 해당 파일 import 검증
2. **단위 동작 검증** — 변경한 함수·로직이 의도대로 동작하는지 직접 호출하거나 테스트로 확인
3. **회귀 테스트** — 기존 동작이 깨지지 않았는지 인접 케이스 점검
4. **정답률 체크 (필수, 절대 생략 금지)**:
   - **검색·생성·답변 품질에 영향을 줄 수 있는 변경**(파이프라인·그래프·프롬프트·컨텍스트·LLM 설정 등)이면 반드시 `scripts/eval_contains_f1.py`로 164문항 E2E 평가
   - **현재 기준선**: 2026-04-21 commit `99a01df`, `reports/eval_contains_f1/combined_20260421_153203.json` = **83.54%** (학사지원팀 피드백 반영 +6.71pp)
   - 참고: 4/22 ChromaDB 경로 정규화·재인제스트 후 `combined_full_crawl_rebuild_20260422` = 81.10% (신입생가이드북 OCR 미설치로 -2.44pp, 재현 가능 범위)
   - 참고: 4/18 구 기준선 76.8% (`combined_no_tier1_boost_20260418_094724.json`) — 학사지원팀 피드백 반영 전
   - 참고: 4/16 구 기준선 81.7% (`combined_slicing_off_20260416_002907.json`) — 모델 변경·리랭커 Tier 부스트 제거 등 구조 변경 반영 전
   - **NO-GO 기준**: 전체 정답률 -1pp 이상 회귀 OR 단일 데이터셋 -3pp 이상 회귀 OR 거부율 -10pp 이상 폭락
   - 회귀 발생 시 commit 보류, 원인 분석 후 사용자에게 보고
   - 검색·생성에 영향 없는 명백한 작업(UI 텍스트, 주석, 로그 메시지 등)은 정답률 체크 생략 가능 — 단 사유를 한 줄로 명시

### 커밋 흐름

```
검증 완료 → git add (선별, 스크래치·바이너리 제외)
         → git commit (HEREDOC 메시지, Co-Authored-By 포함)
         → 사용자에게 commit hash + 핵심 변경 요약 보고 (push 대기)
```

- 사용자 명시적 승인 없이도 위 검증을 모두 통과했으면 **커밋만** 진행 (검증 자체가 커밋 승인 대체).
- 검증을 한 단계라도 건너뛰었으면 커밋 금지.
- **푸시는 항상 사용자 명시 지시 필요** — origin에 올라가면 다른 사람도 보게 되니 사용자가 시점·범위 통제.
- PR 머지·force push·하드 리셋 같은 파괴적 작업은 사용자 명시적 승인 필수.

## 장기 작업 인수인계

여러 세션에 걸친 긴 작업(문서 대량 수정, 대형 리팩터링, 평가 이터레이션 등)을 수행할 때:

**세션 시작 시** — 프로젝트 루트의 `progress.txt`가 존재하면 반드시 먼저 읽는다. 이전 세션이 남긴 진행 상황·미완료 항목·주의사항을 파악한 뒤 작업을 재개한다.

**세션 종료 시** — 작업이 완료되거나 중단될 때 `progress.txt`를 아래 형식으로 덮어쓴다:

```
날짜: YYYY-MM-DD
작업: <작업명>
완료: <이번 세션에서 끝낸 것>
미완료: <다음 세션에서 이어야 할 것>
주의: <다음 세션이 알아야 할 중요 컨텍스트>
```

작업이 완전히 끝났으면 `progress.txt`를 삭제하거나 `미완료: 없음`으로 표시한다.
