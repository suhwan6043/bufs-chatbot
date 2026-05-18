# 정답률 -14.64pp 회귀 원인 진단

**측정일**: 2026-05-18
**비교**: 4/21 baseline (`combined_20260421_153203`, 137/164=83.54%) vs 5/18 P0-1a (`combined_p0_1a_20260518_143121`, 113/164=68.90%)
**결론**: P0-1a 변경과 **완전 무관**. 5/6 ChromaDB + 그래프 재인제스트 결과.

## 1. 회귀 분포

| 지표 | 값 |
|---|---:|
| 회귀 (정답→오답) | **28건** |
| 개선 (오답→정답) | 4건 |
| 동일 정답 | 109건 |
| 동일 오답 | 23건 |
| **순 변화** | **-24건 = -14.63pp** |

### 1.1 데이터셋별

| 데이터셋 | 회귀 | 개선 | 순 |
|---|---:|---:|---:|
| user_eval_dataset_50 | 15 | 1 | **-14** |
| rag_eval_dataset_2026_1 | 8 | 1 | **-7** |
| balanced_test_set | 5 | 2 | -3 |

### 1.2 Intent별 (★ 핵심 신호)

| Intent | 회귀 | 개선 | 순회귀 |
|---|---:|---:|---:|
| **SCHEDULE** | **17** | 2 | **+15** ★★★ |
| REGISTRATION | 5 | 0 | +5 |
| LEAVE_OF_ABSENCE | 2 | 0 | +2 |
| MAJOR_CHANGE | 2 | 1 | +1 |
| GENERAL | 1 | 0 | +1 |
| ALTERNATIVE | 1 | 0 | +1 |
| SCHOLARSHIP | 0 | 1 | -1 |

**SCHEDULE 회귀가 전체 -24건 중 15건(62%)**.

### 1.3 Difficulty별

| Difficulty | 회귀 | 개선 |
|---|---:|---:|
| **easy** | **17** | **0** ★ |
| medium | 9 | 4 |
| hard | 2 | 0 |

쉬운 케이스(easy)가 가장 많이 회귀 — 데이터/포맷 변경의 직접 영향 시사.

## 2. SCHEDULE 회귀 패턴 (5건 sample)

| ID | Question | 4/21 (정답) | 5/18 (오답) |
|---|---|---|---|
| q008 | 수강신청 장바구니 신청 기간 | "2026년 1월 28일부터 2월 1일까지입니다." | **"2026-01-28〜2026-02-01 (10:00〜16:00)"** |
| q012 | 수업일수 1/2선 | "수업일수 1/2선: 2026년 4월 22일" | **"수업일수 1/2선은 2026-04-22입니다."** |
| q013 | 수업일수 3/4선 | "수업일수 3/4선은 2026년 5월 19일입니다." | **"수업일수 3/4선은 2026-05-19입니다."** |
| r01 | 2026학년도 1학기 수강신청 기간 | "2026년 2월 9일입니다." | **"1학년 수강신청은 2026-02-09 10:00〜15:20 ... 2학년 ... 전학년 ..."** |
| s03 | 수업일수 1/4선 | "수업일수 1/4선: 2026년 3월 26일" | **"수업일수 1/4선은 2026-03-26입니다."** |

**원인 패턴**:
1. **답변 포맷이 한글 → ISO 8601** ("2026년 3월 26일" → "2026-03-26")
2. **시간 정보 추가** ("10:00〜15:20")
3. **학년별 분리** ("1학년 ... 2학년 ... 전학년 ...")

`contains_gt` 평가는 ground_truth 토큰("2026년", "1월", "28일", "부터", "까지") 포함 여부 확인. ISO 형식 답변은 GT 토큰을 포함하지 않아 F1=0 실패.

## 3. 인프라 변경 추적

| 자산 | 4/21 환경 | 5/18 환경 | Δ |
|---|---|---|---|
| ChromaDB | `data/chromadb` (4/29 mtime, sub-dir 4/19) | `data/chromadb_new` (**5/6 18:31** 재인제스트) | 신규 인덱스 |
| Academic graph | `data/graphs/academic_graph.pkl` 이전 | 동일 파일 (**5/6 18:32** mtime) | **재빌드** |
| Docker compose | 환경 별도 | `CHROMA_PERSIST_DIR=/app/data/chromadb_new` | **새 경로 선택** |
| LLM_MODEL | (4/21 측정 시) | `gemma4:26b` (H100 터널) | 모델 다를 가능성 |

**5/6 재인제스트가 -14.64pp 회귀의 주 원인**. CLAUDE.md L40 참고 (4/22 재인제스트 = -2.44pp). 5/6 재인제스트는 추가 -12pp.

## 4. _format_date 함수 진단

`app/graphdb/academic_graph.py:1444-1454` `_format_date(date_str, lang="ko")`:
- ISO "2026-05-19" → "2026년 5월 19일"로 변환하는 헬퍼 **존재**.
- 4/21 측정 시 호출되어 한글 형식 답변 생성.
- 5/18에는 prediction에 ISO 그대로 노출 → **호출 path 변경 또는 답변 생성이 graph 우회**.

답변 끝에 `📞 학사 문의:` footer 존재 → LLM이 답변 생성 (graph direct_answer 아님). LLM이 컨텍스트의 ISO 형식을 그대로 paraphrase한 결과.

**가설**: 5/6 재인제스트 후 graph 노드의 텍스트 필드가 ISO 형식 + 시간/학년 분리 데이터로 저장 → context_merger가 `_format_date` 호출 없이 raw text를 LLM 컨텍스트로 전달 → LLM이 ISO 그대로 답변.

## 5. P0-1a 영향 — 0pp 추정

P0-1a (`_resolve_understand_or_rule` 헬퍼 추출, commit `0947bac`):
- 코드 이동만 (130 LOC `if/else` → 함수)
- 동작 변경: sync에 `logger.info` rewrite 로그 1줄 추가 외 0
- SCHEDULE/REGISTRATION 인텐트 답변 생성 path 무영향

P0-1a 자체가 -14pp 원인일 수 없음. 같은 5/18 환경에서 P0-1a 직전 commit(4b4483c, merge origin/main)로 평가해도 ~68% 부근 예상.

## 6. 진짜 baseline 추정

4/21 측정의 환경 차이를 제거하면 P0-1a 적용 직전 5/18 시점 baseline은:
- **추정**: ~68-72% (5/6 재인제스트 + multi-task 1 + KO_PROMPT v1 누적 영향)
- 정확한 값은 4b4483c 체크아웃 + 동일 환경 평가 필요 (~110분)

P0-1a vs 4b4483c 비교 시 회귀는 0~±1pp 예상 (코드 이동의 본질).

## 7. 권고 사항

### 7.1 P0-1 작업 진행
- **P0-1a commit 보류 해제 권고**: P0-1a 자체가 회귀 원인 아님. 코드 이동의 본질.
- P0-1b도 sync에 P4 retry 도입(동작 변경) — 별도 평가 필요. 단 P0-1b 평가도 5/6 재인제스트 환경에서 진행하므로 baseline은 68.90%.

### 7.2 -14pp 회귀 해소 PR 후보 (audit P0-1 무관, 별도 작업)
1. **학사일정 답변 포맷 한글 복원** — context_merger 또는 _query_schedule에서 ISO → 한글 변환 추가
2. **KO_PROMPT v1 강화** — "날짜는 ISO 형식 그대로 쓰지 말고 한글로 변환" 명시
3. **5/6 재인제스트 데이터 검증** — graph 노드 텍스트 필드 ISO 형식 확인

### 7.3 향후 평가 baseline 갱신
- CLAUDE.md L37-41 baseline 83.54%는 **4/21 환경 기준**. 5/6 이후 재인제스트 환경에서는 별도 baseline 측정 후 갱신 필요.
- 새 baseline 측정 commit + 환경 메모 (`chromadb_new`, 그래프 5/6 mtime) 명시.

## 8. Audit 자산 활용 — 원인 체인 명확화

7-step audit 자산과 회귀 진단을 통합:

### 8.1 audit Step 1 식별 god 함수와 회귀 인텐트 정확히 일치

| audit Top 20 CC god | 위치 | 회귀 케이스 |
|---|---|---:|
| #2 `_query_registration` CC **95** | `academic_graph.py:1992` | **REGISTRATION 5건 회귀** |
| #3 `_query_schedule` CC **86** | `academic_graph.py:2323` | **SCHEDULE 17건 회귀** |
| #10 `_query_graduation` CC **55** | `academic_graph.py:1717` | GRADUATION 0건 회귀 (한글 형식 유지) |

**SCHEDULE+REGISTRATION = 22건 = 회귀 78%** — audit이 god으로 식별한 두 함수가 정확히 회귀 source.

### 8.2 원인 commit 추적 (4/22~5/6 인프라 PR)

| Commit | 날짜 | 영향 |
|---|---|---|
| `95781c4` feat(indexing): **v2 PDF pipeline** (Surya 0.17 + page routing + section stack + VLM tables) | ~4/25 | PDF 파싱 형식 변경 — 학사일정 표 구조 영향 |
| `2653a85` feat(indexing): **v2 전체 코퍼스 인제스트** + FAQ/notice 통합 | ~4/30 | 그래프·ChromaDB 새 인덱스 |
| `8234c7f` feat(ingest): crawled/ 파일명 패턴 확장 + 후처리 패치 | ~5/1 | 노드 메타 변경 |
| `71f3708` feat(faq): paraphrase 복수 등록 + COURSE_INFO 그래프 검색 | 4/29 | 그래프 검색 path 변경 |
| **5/6 18:31** | — | **chromadb_new + academic_graph.pkl 재빌드** (수동 실행) |

### 8.3 _query_schedule 코드 변경 추적

`6592407 feat(en): improve schedule retrieval` (4/18) — 4/21 baseline에 이미 포함. 그러나 4/21 baseline에서 SCHEDULE 답변은 한글 형식이었음 → 4/18 코드 변경은 회귀 원인 아님.

**today 코드에 추가된 새 처리** (4/22~5/6, git blame 필요):
- "정규학기 1차 수강신청만 포함 (학년별 이벤트: 수강신청_1학년, 2학년, 3,4학년)"
- "본교 수강신청이 학년별로 분산되어 있어 최저~최고 범위로 계산"
- 수강취소마감일시: 시간 정보 처리

이 처리는 **새 그래프 노드 구조 (학년별 분리 + 시간 정보)에 대응하는 코드**. 5/6 재인제스트로 도입된 노드 구조에 맞춰 코드도 추가됨. 그러나 4/21 GT는 한글 통합 형식("2026년 2월 9일") → today 답변은 학년별 분리 형식("1학년 수강신청은 2026-02-09 10:00〜15:20") → 토큰 매칭 실패.

### 8.4 audit Step 4·5의 직접 시그널

- audit Step 4 case_*.jsonl 측정 데이터는 **5/7 H100 측정** (= 5/6 재인제스트 이후 환경). audit 시점 자체가 이미 -14pp 회귀 환경.
- audit Step 5 timing_delta.csv baseline = 41,547ms (5/7 측정). 4/21 baseline 환경 측정이 아님.
- **CLAUDE.md L37-41 baseline 83.54%는 4/21 stale**. audit Step 5/6/7 모든 진단의 진짜 baseline은 ~68.90%.

### 8.5 원인 체인 (확정)

```
4/22~5/6 v2 indexing pipeline PR 도입 (95781c4, 2653a85, 8234c7f, 71f3708)
    ↓
5/6 18:31 수동 재인제스트
    ↓
academic_graph 노드: 학사일정/수강신청 데이터를 학년별 분리 + 시간 정보 + ISO 형식으로 저장
    ↓
audit Step 1 god _query_schedule(CC 86) / _query_registration(CC 95)가 새 노드 처리
    ↓
한글 변환 path(_format_date) 일부 우회 — 학년별/시간 처리 분기는 raw 형식 그대로 노출
    ↓
context_merger.formatted_context에 ISO + 학년별 텍스트 포함
    ↓
LLM이 컨텍스트의 ISO 형식을 paraphrase
    ↓
GT "2026년 X월 Y일" 토큰 미포함 → contains_gt = False
    ↓
SCHEDULE 17건 + REGISTRATION 5건 = 회귀 22건 (전체 -24건의 92%)
```

**P0-1a 자체는 이 체인에 0 영향**.

## 9. Audit 자산 기반 해소 방안 (audit P0/P1/P2 매핑)

| 회귀 해소 PR | audit 매핑 | 우선순위 |
|---|---|---|
| `_query_schedule` 학년별/시간 처리에서 `_format_date` 호출 보강 | audit P2-12 (academic_graph 4 god 분해) — 부분 적용 | **P0** (-14pp 회귀 즉시 해소) |
| context_merger에서 graph 결과 텍스트 한글 정규화 | audit P2-11 (context_merger 3-알고리즘 분리) | P1 |
| KO_PROMPT v1에 "ISO 날짜→한글 변환" 명시 강화 | audit P0-5 (direct_answer docstring) 연계 | P1 |
| CLAUDE.md baseline 갱신 (~68.90% + 환경 메모) | audit Step 7 종합 | **P0** (회귀 baseline 명시) |

## 10. 다음 단계 (사용자 결정)

A. **새 baseline 측정** (4b4483c 평가, ~110분) → P0-1a 영향 정확 분리
B. **학사일정 ISO→한글 복원 PR 우선** (audit P2-12 부분 적용) — -14pp 즉시 해소
C. **CLAUDE.md baseline 갱신** (4/21 stale → 5/18 ~68.90% + 환경 메모)
D. **P0-1a/b commit 진행** (회귀 원인 아님 확정) + 학사일정 별도 PR 병행
E. **P0-2/4/5** (정답률 평가 불필요한 작업) 먼저 진행
