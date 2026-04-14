# RAGAS Evaluation Analysis Report
## Korean University Chatbot (EXAONE Judge)

**Report Date:** 2026-04-04  
**Evaluation Model:** EXAONE 3.0 (7.8B Instruct)  
**Generation Model:** Qwen 3.5 (9B)  
**Embedding Model:** BAAI/bge-m3  
**Dataset:** user_eval_dataset_50.jsonl (50 questions)

---

## Executive Summary

**Overall Performance:**
- Faithfulness: 0.952 ⭐
- Answer Relevancy: 0.840 ✓
- Context Precision: 0.696 ⚠️ (Target: 0.90)
- Context Recall: 0.788 ⚠️ (Target: 0.90)
- Answer Correctness: 0.818 ✓
- **Average: 0.819** (Gap to target: -0.081)

**Key Bottleneck:** Context retrieval quality
- 15 questions (30%) score below 0.7 on CP or CR
- Context Precision is the weakest metric (0.696)
- Context Recall lags target despite being higher (0.788)

---

## Critical Issues by Category

### 1. Context Precision < 0.5 (10 questions) - HIGH PRIORITY
**Problem:** Retrieved context contains excessive noise/irrelevant information

| Question ID | Intent | CP | CR | Issue |
|-------------|--------|----|----|-------|
| q021 | SCHOLARSHIP | 0.100 | 0.500 | Scholarship eligibility: context has many unrelated scholarship details |
| q032 | REGISTRATION | 0.000 | 0.500 | Registration deadline: no relevant context retrieved |
| q034 | SCHEDULE | 0.200 | 0.800 | OCU course date: context mentions multiple scheduling periods |
| q040 | REGISTRATION | 0.400 | 0.500 | OCU payment: multiple payment scenarios mixed |
| q044 | MAJOR_CHANGE | 0.400 | 0.800 | Curriculum: irrelevant requirements from other years |
| q049 | REGISTRATION | 0.200 | 1.000 | GPA requirement: multiple GPA criteria conflated |
| q050 | MAJOR_CHANGE | 0.400 | 0.800 | Multi-year curriculum: context spans many years |
| q019 | REGISTRATION | 0.400 | 0.800 | GPA threshold: context mixes eligibility criteria |
| q022 | REGISTRATION | 0.300 | 0.900 | Transfer policy: context has multiple policy dates |
| q027 | REGISTRATION | 0.400 | 0.800 | Grade range: context mentions different grading systems |

**Root Cause:** Context retrieval system pulls related documents but lacks precise filtering.

---

### 2. Context Recall < 0.6 (8 questions) - CRITICAL
**Problem:** Context insufficient to fully verify/support answer

| Question ID | Intent | CP | CR | Issue |
|-------------|--------|----|----|-------|
| q001 | SCHEDULE | 0.900 | 0.500 | Semester start: date present but lacks semester designation |
| q009 | SCHEDULE | 0.700 | 0.500 | Course add period: context shows different course types |
| q012 | SCHEDULE | 0.900 | 0.500 | Midterm exam: specific date without supporting context |
| q013 | SCHEDULE | 0.900 | 0.500 | Final exam: isolated date without context |
| q021 | SCHOLARSHIP | 0.100 | 0.500 | Scholarship minimum: requirements incomplete |
| q032 | REGISTRATION | 0.000 | 0.500 | Late registration: policy exists but vague |
| q040 | REGISTRATION | 0.400 | 0.500 | OCU fee: amount stated but payment options unclear |
| q048 | SCHEDULE | 0.900 | 0.500 | Evening class time: lacks complete schedule table |

**Root Cause:** Point-in-time values retrieved without surrounding context.

---

### 3. Accuracy Issues (Faithfulness < 0.8 or Correctness < 0.5)

| Question ID | Intent | Faith | Correct | Problem |
|-------------|--------|-------|---------|---------|
| q009 | SCHEDULE | 0.300 | 0.100 | Generated dates dont match context |
| q021 | SCHOLARSHIP | 1.000 | 0.000 | Answer correct but context incomplete |
| q032 | REGISTRATION | 1.000 | 0.300 | Generic answer, context unclear |
| q034 | SCHEDULE | 0.700 | 0.100 | Date format inconsistent |
| q040 | REGISTRATION | 1.000 | 0.200 | Multiple payment options create ambiguity |
| q050 | MAJOR_CHANGE | 0.500 | 0.400 | Multi-year comparison partial |

---

## Intent-Based Pattern Analysis

### SCHEDULE (6 questions) - CR < 0.6 PATTERN
- Avg Metrics: CP=0.750, CR=0.550
- Problem: Isolated dates without context
- Questions: q001, q009, q012, q013, q034, q048

### REGISTRATION (6 questions) - CP < 0.3 PATTERN
- Avg Metrics: CP=0.283, CR=0.750
- Problem: Multiple overlapping rules in context
- Questions: q019, q022, q027, q032, q040, q049

### MAJOR_CHANGE (2 questions) - CONTEXT SCATTER
- Avg Metrics: CP=0.400, CR=0.800
- Problem: Too many curriculum versions in context
- Questions: q044, q050

### SCHOLARSHIP (1 question) - CRITICAL
- Metrics: CP=0.100, CR=0.500
- Problem: q021 - Data may not be in training PDFs

---

## Key Insights

### What Works Well
1. Faithfulness (0.952): Model grounds answers in retrieved context
2. Answer Relevancy (0.840): Model understands questions
3. Easy questions: Straightforward schedule queries mostly pass

### What Needs Improvement

**Primary Issue: Context Retrieval Precision**
- Root cause: RAG retrieves semantically related but imprecise content
- Impact: Noisy context confuses judge
- Solutions:
  1. Improve chunking strategy (reduce chunk size, add boundaries)
  2. Add reranking layer after initial retrieval
  3. Filter multi-version content when querying specific years
  4. Extract structured data separately

**Secondary Issue: Incomplete Context Recall**
- Root cause: Point values retrieved without explanatory context
- Impact: Judge cannot verify completeness
- Solutions:
  1. Include surrounding context (headers, titles)
  2. For schedules, retrieve full table not just cell
  3. For eligibility, retrieve complete rule + exceptions

**Tertiary Issue: Multi-Domain Complexity**
- Root cause: Queries spanning multiple policy areas
- Impact: Context retrieval creates overlap confusion
- Solutions:
  1. Implement query decomposition
  2. Add domain-specific retrieval pipelines
  3. Identify and separate compound queries

---

## Recommendations

### Immediate (Sprint 1)
1. Check scholarship data availability in source PDFs (q021)
2. Implement query rewriting for registration queries
3. Add context reranking using BGE

### Short-term (Sprint 2-3)
1. Improve chunking: extract headers, separate structured data
2. Add query-specific logic for SCHEDULE/REGISTRATION/MAJOR_CHANGE
3. Implement context post-processing (deduplication, version filtering)

### Medium-term (Sprint 4+)
1. Build domain-aware retrieval pipeline
2. Extract structured knowledge base for dates/policies
3. Expand evaluation dataset with edge cases

---

## Target Achievement Roadmap

| Metric | Current | Q2 Target | Q3 Target |
|--------|---------|-----------|-----------|
| Context Precision | 0.696 | 0.75 | 0.85 |
| Context Recall | 0.788 | 0.82 | 0.88 |
| Average Score | 0.819 | 0.835 | 0.88+ |

