# 5월 7일 137문 재질의·채점·딥 분석 작업 가이드

> 이 파일은 **Sonnet 세션 재진입 후 바로 이어서 작업할 수 있도록** 준비해둔
> 핸드오프 문서입니다. 직전 세션은 Opus 4.7로 실행됐고, 사용자 요구에 따라
> Sonnet 세션에서 채점·딥분석을 직접 수행해야 합니다 (LLM judge·외부 API 호출 금지).

## 직전 세션 산출물

- worktree branch: `claude/nice-swartz-0c0cc4`
- main 대비 6 커밋:
  ```
  ee2183a chore(config): add direct_answer_bypass_llm to PipelineConfig (hotfix)
  00a64d7 chore(conv): raise understanding LLM timeouts based on host sanity check
  63c307b feat(chat): wire understanding behind flag (dual-write, default OFF)
  00b985a feat: register 9 new Intent child keys in _INTENT_* dicts (additive)
  0e719c3 feat(pipeline): add query_understanding module behind flag (default OFF)
  b3136ec feat(models): add 9 new Intent categories + LegacyIntent + bidirectional mapping
  ```
- 핸드오프 파일:
  - `reports/eval_5_7/questions_unique.jsonl` — **137 unique 질문** + 5/7 시점 옛 응답/intent/duration_ms 포함
  - `reports/eval_5_7/WORK_GUIDE.md` — 이 문서

## 작업 절차 (Sonnet 세션에서 진행)

### 1. 재빌드 + understanding 활성화

```bash
# .env에 명시
echo "CONV_UNDERSTANDING_ENABLED=true" >> .env

# 컨테이너 재빌드 (worktree의 코드가 컨테이너로)
docker compose -f docker/docker-compose.yml up --build -d
# 또는 프로젝트 루트의 docker-compose.yml 위치 확인 후

# 헬스체크
curl -sf http://localhost:8000/health
```

> hotfix `ee2183a`로 `direct_answer_bypass_llm` 필드가 PipelineConfig에 추가되어
> 재빌드 시 backend가 AttributeError로 죽지 않습니다.

### 2. 137문 자동 재질의

`reports/eval_5_7/questions_unique.jsonl` 의 각 질문을 `/chat_sync`(논스트림)에
보내고 응답을 `reports/eval_5_7/responses_new.jsonl` 로 저장.

스크립트 예시:

```python
# scripts/rerun_5_7.py (작성 필요)
import json, requests, time

API = "http://localhost:8000"
with open("reports/eval_5_7/questions_unique.jsonl", encoding="utf-8") as f:
    qs = [json.loads(l) for l in f if l.strip()]

out = open("reports/eval_5_7/responses_new.jsonl", "w", encoding="utf-8")
for q in qs:
    t = time.monotonic()
    # session 별도 발급 — follow-up 컨텍스트 격리 (각 질문 독립 평가)
    sess = requests.post(f"{API}/session/create").json()["session_id"]
    r = requests.post(f"{API}/chat_sync", json={
        "session_id": sess, "question": q["question"], "lang": "ko",
    }, timeout=120)
    data = r.json()
    elapsed = int((time.monotonic() - t) * 1000)
    out.write(json.dumps({
        **q,
        "new_answer": data.get("answer",""),
        "new_intent": data.get("intent",""),
        "new_duration_ms": data.get("duration_ms", elapsed),
        "http_status": r.status_code,
    }, ensure_ascii=False) + "\n")
    out.flush()
    print(f"{q['idx']+1}/137 [{q.get('old_intent','')}] → [{data.get('intent','')}]")
out.close()
```

예상 소요: 137 × (understand 1차/2차 LLM ~3s + 답변 LLM 5s) ≈ 15-20분.

### 3. 백엔드 로직 로그 수집

```bash
# 최근 24h (재질의 직후 추출)
docker logs --since 24h docker-backend-1 > reports/eval_5_7/backend_logs.txt 2>&1
# PIPELINE_TIMING / understand[...] 로그 필터
grep -E "PIPELINE_TIMING|understand\[" reports/eval_5_7/backend_logs.txt > reports/eval_5_7/backend_pipeline.txt
```

### 4. Sonnet이 직접 fact-check + 채점 (137개)

각 질문에 대해 다음 절차:

1. **정답 사료** 확보 — Sonnet이 다음 도구로 직접:
   - `Read` — `data/pdfs/` 의 학사안내 PDF
   - `Grep` — `data/pdfs/`·`data/graphs/`·`app/data/faq/` 등에서 키워드 검색
   - `WebFetch` — `https://www.bufs.ac.kr` 학사 공지·학생포털·학과 페이지
   - `WebSearch` — "부산외국어대학교 OOO" 검색
2. **정답 작성** — Sonnet의 직접 판단 (LLM judge 호출 금지)
3. **채점** — `new_answer` vs 정답:
   - `correct` — 핵심 사실(숫자·날짜·URL·조건) 모두 일치, 누락·오류 없음
   - `partial` — 일부 누락이나 모호, 오답은 아님
   - `wrong` — 핵심 사실 오류 또는 잘못된 안내
   - `refusal` — "정보 없음/연락 바람" 응답이지만 실제로는 정답 가능했던 경우 등 별도 표기
4. **결과 누적** — `reports/eval_5_7/graded.jsonl` 에 한 줄씩:
   ```json
   {"idx": 0, "question": "...", "new_answer": "...", "ground_truth": "...",
    "sources": ["data/pdfs/학사안내_2026.pdf p.12", "https://www.bufs.ac.kr/..."],
    "verdict": "correct|partial|wrong|refusal",
    "reason": "근거 한 문장"}
   ```

### 5. 옛 응답(5/7) vs 새 응답 정확도 비교

- 옛 응답은 `questions_unique.jsonl` 의 `old_answer` 필드 (400자 trim)
- 옛 응답도 같은 채점 기준으로 verdict 부여 → `graded_old.jsonl`
- 옛/신 verdict 차이 (`improved` / `regressed` / `unchanged`) 집계

### 6. 딥 분석 보고서 작성

`reports/eval_5_7/REPORT.md`:

- **요약 KPI**: 옛 정확도 vs 새 정확도, intent 분포 변화, GENERAL 폴백률
- **틀린 질문 분석** (`verdict in {wrong, partial}`): 각 케이스에 대해
  - 새 응답 인용
  - 정답 인용 + 출처
  - 백엔드 로그(PIPELINE_TIMING / understand) 인용
  - 로직 트레이스:
    1. understanding이 분류한 intent가 적절했나?
    2. `_INTENT_DOC_TYPES` 필터가 정답 청크를 포함했나?
    3. retrieval 결과(vector + graph)가 정답 청크를 상위로 올렸나?
    4. context_merger budget 안에 정답 청크가 들어왔나?
    5. answer_generator가 컨텍스트를 올바르게 사용했나?
  - 근본 원인 분류: 분류오류 / 검색누락 / 컨텍스트 손실 / 생성환각
- **개선 제안** (지금 fix 가능한 것 ↔ 후속 작업으로 분리할 것)

## 직전 세션 관찰 (사전 단서)

- 호스트 CPU에서 gemma3:4b 1차 LLM ~4030ms (timeout 1.5s 부족). GPU
  컨테이너 환경에선 단축 예상. understand의 `source="rule_fallback"`이 자주
  찍히면 호스트 환경 영향이고, `source="llm"`/`llm_fallback`이 다수면 정상.
- 5/7 167개 중 GENERAL이 63개 (38%) — multi-task 1의 동기였던 "GENERAL 폴백
  40%"와 정확히 일치. 신 분류기가 이걸 얼마나 줄였는지가 핵심 지표.
- 137 unique 중 `student_id` 보유 다수 — TRANSCRIPT intent 일부 포함. session
  격리하면 transcript context는 없으므로 별도 처리 필요할 수 있음.

## 직전 세션이 손대지 않은 영역

- `backend/routers/chat.py:591-616` (스트림 direct_answer 단락 응답)
- `backend/routers/chat.py:962-989` (논스트림 direct_answer 단락 응답)
- `app/pipeline/context_merger.py:391-442` (direct_answer 채움 로직)

사용자 명시 요구: "direct_answer 트리거 로직은 별도 검토 — 건들지 말 것".
Sonnet 세션에서도 이 영역은 무수정 유지하되, 채점 결과 direct_answer 오답 4건
패턴이 어떻게 변했는지(또는 변하지 않았는지) 보고서에 따로 정리할 것.
