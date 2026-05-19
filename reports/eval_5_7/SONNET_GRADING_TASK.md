# Sonnet 채점 작업 핸드오프 — 137문 H100 답변 (어제와 동일 방식)

> **이 문서를 Sonnet 세션 진입 후 그대로 따라 실행하면 됩니다.**
> 어제(5/11) Sonnet 세션이 만든 `reports/eval_5_7/graded.jsonl` 과 **완전히 동일한 형식·기준·절차**로
> H100 환경 답변(`reports/eval_5_7/responses_h100.jsonl`)을 채점합니다.
>
> **단지 다른 점은 환경뿐**: 어제는 로컬 CPU + 추정 `gemma4:e4b` (4B effective),
> 오늘은 **H100 GPU + `gemma4:26b` (27B effective)**.
> RAG 파이프라인·retrieval·merging·FAQ 모두 동일.

---

## 1. 작업 목표

5/7 137 unique 질문에 대해 **H100 답변**을 직접 fact-check 채점.
출력 형식·verdict 체계·근거 출처는 어제 `graded.jsonl` 과 100% 동일.
LLM judge·외부 LLM API 호출 절대 금지 — Sonnet 본인이 직접 답변을 보고 PDF·JSON·학사일정으로 검증.

---

## 2. 입력 파일

| 경로 | 내용 |
|---|---|
| `reports/eval_5_7/questions_unique.jsonl` | 137 unique 질문 (5/7 chat log 기반) |
| `reports/eval_5_7/responses_h100.jsonl` | 137개 H100 답변 (오늘 측정) |
| `reports/eval_5_7/responses_new.jsonl` | 어제 답변 (참고용) |
| `reports/eval_5_7/graded.jsonl` | 어제 채점 결과 — **GT 그대로 재사용 가능** |
| `reports/eval_5_7/graded_old.jsonl` | 어제 OLD 답변 채점 (참고) |
| `reports/eval_5_7/scored_137_h100.jsonl` | 자동 휴리스틱 1차 채점 (참고용, 신뢰 X) |
| `reports/eval_5_7/review_manual_verdicts.json` | review 20건 Opus 직접 보정 결과 (참고) |

### GT(ground_truth) 출처 — 어제와 동일하게 사용

- `data/contacts/departments.json` — 부서 연락처 (P0-2 fix로 학생복지팀 신규 추가됨)
- `data/scholarships.json` — 장학금 부서/자격/금액 (학생복지팀 051-509-5164)
- `data/faq_academic.json` — FAQ 57건 (P0-3 fix로 FAQ-0056 노동절, 0057 장학금 추가)
- `data/early_graduation.json` — 조기졸업 자격·기준
- `data/eval/rag_eval_dataset_2026_1.jsonl` — 학사 RAG 평가 GT 셋
- `data/pdfs/2026학년도1학기학사안내.pdf` — 학사규정 세부 (Read 도구로 페이지 단위 읽기 가능)
- `data/pdfs/2026학년도 1학기 수업시간표.pdf` — 시간표
- 공식 학사일정 PDF: `https://www.bufs.ac.kr/data/file/gjb_schedule/540811773_WGCTeBlA_72fa8303d643d2322eacaf2e569c00f5f938dee1.pdf`
  - 핵심 사실: **5월 1일(금) = 임시휴업일(근로자의날)**, 4.22(수) = 수업일수 1/2선, 5.5(화) = 어린이날, 5.25(월) = 석가탄신일 대체휴일

### 어제 GT 오류 정정 (반드시 반영)

어제 `graded.jsonl` 의 **idx 27 GT가 잘못됨**:
- 어제 GT: "노동절 = 5월 1일(금). 학사일정에 '수업일수 1/2선'으로 표기 — 수업 진행. 4.29는 부처님오신날"
- 실제 (공식 학사일정 PDF 검증): "5/1 = 임시휴업일(근로자의날) — 수업 없음. 4/22 = 수업일수 1/2선. 4/29는 평일. 부처님오신날은 5/24~25"
- → idx 27 채점 시 **정정된 GT** 사용. H100 답변 "5/1(금)은 임시휴업일(노동절)"은 **correct**.

다른 사례에서도 어제 GT가 의심되면 PDF/공식 자료로 재확인.

---

## 3. 출력 형식 — 어제 `graded.jsonl` 과 완전히 동일

`reports/eval_5_7/graded_h100.jsonl` 에 137개 객체를 concat JSON으로 저장 (JSON Lines 아님, 어제 형식 그대로).

각 객체 필드 (어제 graded.jsonl 스키마):

```json
{
  "idx": 0,
  "question": "졸업요건 중 전공과 교양 기준 알려줘",
  "new_answer": "H100 답변 본문 (responses_h100.jsonl 의 h100_answer)",
  "ground_truth": "직접 검증한 사실. 핵심 숫자·날짜·부서명·조건 포함.",
  "sources": ["data/pdfs/2026학년도1학기학사안내.pdf", "data/scholarships.json", ...],
  "verdict": "correct | partial | wrong | refusal_acceptable | refusal_unacceptable",
  "reason": "verdict 사유 한 줄 — 무엇이 맞고 무엇이 틀렸는지."
}
```

⚠ **어제는 `new_answer` 컬럼명 그대로 사용 — 코드 호환성**. `h100_answer` 가 아니라 `new_answer` 키로.

---

## 4. Verdict 체계 (어제와 100% 동일)

| verdict | 정의 |
|---|---|
| `correct` | 핵심 사실(숫자·날짜·연락처·조건·결론) **모두 일치**, 누락·오류 없음. 부수적 추가 정보가 GT보다 더 풍부해도 OK. |
| `partial` | 일부 누락이나 모호하지만 오답 아님. 핵심 결론은 맞는데 디테일 빠짐. |
| `wrong` | 핵심 사실 오류(잘못된 숫자/날짜/부서명), 잘못된 결론(불가→가능 등), 또는 의도와 무관한 답변. |
| `refusal_acceptable` | "관련 정보 찾을 수 없습니다" 등 거부 — **실제로 정보가 모호하거나 답할 수 없는 경우**. |
| `refusal_unacceptable` | 거부 응답이지만 **실제로는 답할 수 있었던** 경우 (자료에 있었음). |

추가 분류 (어제와 동일):
- **improved** (어제 vs 오늘 비교): 어제 wrong/partial → 오늘 correct/partial 또는 어제 wrong → 오늘 refusal_acc
- **regressed**: 어제 correct → 오늘 wrong/partial/refusal_unacc
- **unchanged**: 동일 verdict

---

## 5. 작업 절차

### Step 1. 환경 확인

```bash
# H100 backend 가동 중인지
curl -sf http://localhost:8000/api/health
# 출력: {"status":"ok","version":"0.3.0","pipeline_ready":true}

# 137문 H100 답변 확인
wc -l reports/eval_5_7/responses_h100.jsonl   # → 137 줄
```

### Step 2. 137개 답변 일괄 검토

각 idx에 대해:

1. `responses_h100.jsonl` 에서 `h100_answer` 추출 (새 답변)
2. `graded.jsonl` 에서 동일 idx의 `ground_truth` 확인 (어제 GT — 대부분 그대로 사용 가능)
3. 필요시 PDF/JSON 직접 확인:
   - 부서 연락처 → `data/contacts/departments.json` + `data/scholarships.json`
   - 학사일정·휴일 → 학사일정 PDF (위 URL) 또는 `data/pdfs/2026학년도1학기학사안내.pdf`
   - 장학금 자격·금액 → `data/scholarships.json`
   - FAQ 일반 → `data/faq_academic.json` (FAQ-0056/0057 추가됨)
   - 시간표·수업 → `data/pdfs/2026학년도 1학기 수업시간표.pdf`
4. verdict + reason + sources 작성
5. 어제 `new_answer` 와 100% 동일하면 어제 verdict 그대로 사용해도 됨 (시간 절약)

### Step 3. graded_h100.jsonl 생성

어제 graded.jsonl 형식 그대로 — 137개 JSON 객체를 공백 없이 concat 저장.
JSON Lines 아닌 raw concat 주의 (어제 Sonnet이 만든 형식 그대로).

검증:
```python
import json
def load_concat(p):
    txt = open(p, encoding='utf-8').read().strip()
    dec = json.JSONDecoder(); pos, items = 0, []
    while pos < len(txt):
        while pos < len(txt) and txt[pos] in ' \t\n\r': pos += 1
        if pos >= len(txt): break
        obj, end = dec.raw_decode(txt, pos); items.append(obj); pos = end
    return items
print(len(load_concat('reports/eval_5_7/graded_h100.jsonl')))  # → 137
```

### Step 4. 비교 보고서 작성 — `reports/eval_5_7/REPORT_h100.md`

어제 `REPORT.md` 와 동일한 섹션 구조:

1. **요약 KPI**: 어제 vs 오늘 verdict 분포 + 점수 평균
2. **개선/회귀/불변 집계** (improved/regressed/unchanged)
3. **개선 사례 분석** (어제 wrong → 오늘 correct)
4. **회귀 사례 분석** (어제 correct → 오늘 wrong/partial)
5. **direct_answer bypass 분석** (있다면 — duration_ms=0 케이스 / `path=direct_answer` 케이스)
6. **응답 시간 비교** (어제 ~25s vs 오늘 ~50s)
7. **Intent 분포 변화** — 신 카테고리 분류가 H100에서 0건 발생함 명시. backend_logs_h100.txt에서 PIPELINE_TIMING의 intent= 추출.
8. **근본 원인 분류** (분류오류·검색누락·컨텍스트손실·생성환각·연락처오류)
9. **즉시 수정 가능 항목** (P0/P1/P2)
10. **결론·권고**

근거 자료 첨부: backend 로그(`backend_logs_h100.txt`), 측정 raw(`responses_h100.jsonl`), 채점 raw(`graded_h100.jsonl`).

---

## 6. 절대 지킬 것

1. **LLM judge 호출 금지** — Sonnet 본인이 직접 PDF·JSON·답변 읽고 채점. Anthropic API·OpenAI API·다른 LLM 호출 절대 금지.
2. **어제 graded.jsonl 형식 100% 유지** — 키 이름(`new_answer`), 객체 concat 형식, verdict 명칭 모두 동일.
3. **GT 일관성** — 어제 GT 그대로 활용. 단 어제 GT가 PDF/공식 자료와 모순되면 정정 후 채점 (idx 27 노동절처럼).
4. **direct_answer 트리거 코드 무수정** — 채점 작업이므로 코드 변경 없음. backend 컨테이너도 안 건드림.
5. **모든 137개 채점** — 표본 추출 금지. 어제 137 전체 채점한 것과 동일.

---

## 7. 환경 차이 (보고서에 명시)

| 항목 | 어제 (2026-05-11) | 오늘 (2026-05-12) |
|---|---|---|
| 답변 LLM | `gemma4:e4b` (로컬 ollama 추정) | `gemma4:26b` (H100, 17GB) |
| 분류 LLM | `gemma3:4b` (로컬) | `gemma3:4b` (SSH 터널 경유 H100) |
| 추론 환경 | 호스트 CPU | H100 NVL MIG 47GB GPU |
| 평균 응답시간 | 25.7s | 49.7s |
| 거부 응답 비율 | 8.8% | 19.7% (자동 휴리스틱 기준) |
| 신 카테고리 분류 작동 | 일부 (CERTIFICATE/FACILITY 등) | **0건** (SSH 터널 latency로 매번 timeout → 룰 폴백) |
| FAQ | 55개 | 57개 (FAQ-0056 노동절·0057 장학금 추가) |

→ **이 환경 차이가 정답률에 어떻게 반영됐는지**가 보고서의 핵심.

---

## 8. 직전 세션(Opus) 발견 사항 (참고용)

Opus가 만든 30 사례 직접 검토 결과 (`LLM_PERF_VERIFICATION.md`):
- improved: 11/30 (37%)
- improved-safe (잘못된 답 → 거부): 6/30 (20%)
- regressed: 1/30 (3%, idx 7)
- unchanged: 12/30 (40%)

Opus의 137 자동 휴리스틱 채점(`TABLE_137_h100.md`):
- correct 24, partial 35, wrong 48, refusal_unacc 27, refusal_acc 3
- correct+partial = 43.1% (어제 73.7% 대비 -30.6pp)
- 자동이 보수적이라 신뢰 X — Sonnet 직접 채점이 진짜 결과

Sonnet 직접 채점 결과가 30 사례 직접 검토(+57% 우위)에 가까울 가능성 높음.
또는 137 전체 평균이 자동 결과(-30.6pp)에 가까울 수도. **직접 확인 필요**.

---

## 9. 최종 산출물 (Sonnet이 만들 것)

| 파일 | 내용 |
|---|---|
| `reports/eval_5_7/graded_h100.jsonl` | 137 사례 채점 (어제 graded.jsonl 동일 스키마) |
| `reports/eval_5_7/REPORT_h100.md` | 어제 REPORT.md 동일 구조 비교 보고서 |
| (선택) `reports/eval_5_7/CHANGES_h100_vs_yesterday.md` | 사례별 변화 표 (improved/regressed/unchanged) |

작업 끝나면 다음 한 줄로 commit:
```
git add reports/eval_5_7/graded_h100.jsonl reports/eval_5_7/REPORT_h100.md
git commit -m "eval(5/7): Sonnet 직접 137문 H100 채점 + 어제 vs 오늘 비교 보고서"
```

---

## 10. 작업 후 사용자 응답 형식

```
✅ Sonnet 직접 채점 137 완료

| verdict | 어제 | H100 | Δ |
|---|---:|---:|---:|
| correct | 66 | XX | ±X |
| partial | 35 | XX | ±X |
| wrong | 27 | XX | ±X |
| refusal_acc | X | XX | ±X |
| refusal_unacc | X | XX | ±X |

correct+partial: 어제 73.7% vs H100 XX.X% (Δ ±X.Xpp)

핵심 발견:
- improved XX건, regressed XX건, unchanged XX건
- P0 fix(FAQ-0056/0057) 효과: idx 27, 82 — correct로 전환됨
- 환경 차이(SSH 터널)로 분류기 신 카테고리 미작동 — 답변 LLM만 효과
- ...

산출물:
- reports/eval_5_7/graded_h100.jsonl (137 사례)
- reports/eval_5_7/REPORT_h100.md (비교 분석)
- commit XXXXXXX
```
