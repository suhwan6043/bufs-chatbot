# 학사지원팀 피드백 반영 후 Contains-F1 평가

- **일시**: 2026-04-21 15:32
- **대상 커밋**: `eee6b40` (feat: FAQ 7개 신규 + 연락처 SSOT 분리)
- **LLM**: Ollama qwen3.5:9b @ 192.168.0.4:11434 (native /api/chat, think:false)
- **검색**: BGE-M3 (GPU) + BGE-Reranker-v2-M3 (GPU, candidate_k=15, top_k=5)

## 결과 요약

| 데이터셋 | 기준선 (4/18) | 현재 (4/21) | Δ | 판정 |
|---|---:|---:|---:|:---:|
| balanced_test_set | 61.54% (24/39) | **66.67%** (26/39) | **+5.13pp** | ✅ |
| rag_eval_dataset_2026_1 | 88.00% (44/50) | **92.00%** (46/50) | **+4.00pp** | ✅ |
| user_eval_dataset_50 | 77.33% (58/75) | **86.67%** (65/75) | **+9.34pp** | ✅ |
| **전체** | **76.83%** (126/164) | **83.54%** (137/164) | **+6.71pp** | **✅ GO** |

기준선 파일: `reports/eval_contains_f1/combined_no_tier1_boost_20260418_094724.json`
현재 결과 파일: `reports/eval_contains_f1/combined_20260421_153203.json`

## CLAUDE.md NO-GO 기준 검증

| 기준 | 임계 | 실측 | 결과 |
|---|---|---|:---:|
| 전체 정답률 회귀 | -1pp 이상 | +6.71pp | ✅ 통과 |
| 단일 데이터셋 회귀 | -3pp 이상 | 최소 +4.00pp | ✅ 통과 |
| 거부율 폭락 | -10pp 이상 | 안정 | ✅ 통과 |

## 개선 기여 요소 분석

### User 세트 +9.34pp (최대 개선)
7개 신규 FAQ 중 5개가 user 세트 범위와 직접 매칭:
- `ADMIN-20260421-0003` (자원봉사/교내 증명서) → u002 증명서 질문
- `ADMIN-20260421-0004` (졸업증명서) → u002 발급 방법
- `ADMIN-20260421-0005` (특별강좌)
- `ADMIN-20260421-0001` (OCU 수강 방법)
- `ADMIN-20260421-0007` (P/NP)

`departments.json` 학사지원팀 분리(5146 업무별 매핑)로 "학생증", "증명서" 질문의 연락처 정확도 향상.

### Balanced +5.13pp
- OCU 용어 정정, 본교 수강신청 분리 FAQ가 `c03`, `r01` 등에서 더 나은 답변 제공

### RAG +4.00pp
- 7개 신규 FAQ가 검색 풀에 추가되며 관련 질문(q023 OCU 학점 초과, q034 OCU 수강신청 등)에서 매칭 품질 향상

## 재현 명령

```bash
python -X utf8 scripts/eval_contains_f1.py \
  --datasets data/eval/balanced_test_set.jsonl \
             data/eval/rag_eval_dataset_2026_1.jsonl \
             data/eval/user_eval_dataset_50.jsonl \
  --base-url http://localhost:8000 \
  --output reports/eval_contains_f1
```
