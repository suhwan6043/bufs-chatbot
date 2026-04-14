"""
RAGAS 기반 RAG 평가 스크립트 (ragas 0.4.x)
Claude (haiku/sonnet)를 평가 LLM으로 사용

평가 지표:
  - Faithfulness        : 답변이 컨텍스트에 근거하는가 (환각 탐지)
  - AnswerRelevancy     : 답변이 질문에 관련 있는가
  - ContextRecall       : ground_truth를 커버하는 컨텍스트가 검색됐는가
  - ContextPrecision    : 검색된 컨텍스트 중 관련 있는 비율

입력:
  eval_multilingual.py 로 생성한 JSON (retrieved_contexts 필드 필요)

사용법:
  # KO 평가 (haiku, 기본)
  python scripts/eval_ragas.py --input evaluation/results/eval_multilingual_ko_YYYYMMDD.json

  # EN 평가
  python scripts/eval_ragas.py --input evaluation/results/eval_multilingual_en_YYYYMMDD.json

  # 모델 변경
  python scripts/eval_ragas.py --input ... --model claude-sonnet-4-6

  # 일부 항목만 (비용 절감)
  python scripts/eval_ragas.py --input ... --limit 10

비용 추정 (102문항 4지표):
  haiku-4-5:  ~$0.48
  sonnet-4-6: ~$3~5
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


def load_dataset(json_path: str, lang_filter: str | None, limit: int | None):
    """eval_multilingual 결과 JSON → RAGAS Dataset 형식으로 변환."""
    from datasets import Dataset

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    skipped_no_ctx = 0
    skipped_na = 0

    for r in data["results"]:
        if r.get("not_answerable"):
            skipped_na += 1
            continue
        if lang_filter and r.get("lang") != lang_filter:
            continue

        ctx_list = r.get("retrieved_contexts")
        if not ctx_list or not any(c.strip() for c in ctx_list):
            skipped_no_ctx += 1
            continue

        rows.append({
            "question":     r["question"],
            "answer":       r.get("pred", ""),
            "contexts":     [c for c in ctx_list if c.strip()],
            "ground_truth": r.get("ground_truth", ""),
            "_id":          r.get("id", ""),
            "_category":    r.get("category", ""),
            "_lang":        r.get("lang", ""),
            "_f1":          r.get("f1"),
        })

    if skipped_no_ctx:
        print(f"  ⚠ retrieved_contexts 없음으로 제외: {skipped_no_ctx}건")
        print("    → eval_multilingual.py 재실행 필요")
    if skipped_na:
        print(f"  ℹ not_answerable 항목 제외: {skipped_na}건")

    if limit:
        rows = rows[:limit]

    print(f"  평가 대상: {len(rows)}건")
    return Dataset.from_list(rows)


def build_evaluator(model: str, api_key: str):
    """Claude LLM + BGE-M3 임베딩으로 RAGAS evaluator 구성."""
    import anthropic
    from ragas.llms import llm_factory
    from ragas.embeddings import HuggingFaceEmbeddings  # ragas 내장 HF wrapper

    client = anthropic.Anthropic(api_key=api_key)
    llm = llm_factory(model=model, provider="anthropic", client=client)
    # Anthropic API는 temperature + top_p 동시 지정 불가 → top_p 제거
    llm.model_args.pop("top_p", None)

    # 로컬 BGE-M3 재사용 (API 비용 없음)
    # answer_relevancy가 내부적으로 embed_query를 호출 → 메서드 추가
    hfe = HuggingFaceEmbeddings(model="BAAI/bge-m3")
    hfe.embed_query = lambda text: hfe.embed_text(text)
    hfe.embed_documents = lambda texts: hfe.embed_texts(texts)

    return llm, hfe


def run_evaluation(dataset, llm, embeddings):
    """RAGAS 4개 지표 평가 실행."""
    from ragas import evaluate
    # ragas 0.4.x: 모듈 레벨 인스턴스를 사용하고 evaluate()에 llm/embeddings 전달
    from ragas.metrics import (
        faithfulness,
        answer_relevancy,
        context_recall,
        context_precision,
    )

    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_recall, context_precision],
        llm=llm,
        embeddings=embeddings,
        raise_exceptions=False,
    )
    return result


def print_results(result, dataset):
    """결과 출력: 전체 + 카테고리별."""
    import collections

    df = result.to_pandas()

    metrics = ["faithfulness", "answer_relevancy", "context_recall", "context_precision"]

    print()
    print("=" * 60)
    print("RAGAS 평가 결과")
    print("=" * 60)
    for m in metrics:
        col = next((c for c in df.columns if c.lower().replace(" ", "_") == m), None)
        if col:
            val = df[col].mean()
            print(f"  {m:25s}: {val:.4f}")

    # 카테고리별 집계
    print()
    print("카테고리별 Faithfulness / AnswerRelevancy")
    print("-" * 60)
    faith_col = next((c for c in df.columns if "faithful" in c.lower()), None)
    rel_col   = next((c for c in df.columns if "relevancy" in c.lower() or "relevance" in c.lower()), None)

    cat_data = collections.defaultdict(list)
    for i in range(len(df)):
        cat = dataset[i]["_category"]
        f = df.iloc[i][faith_col] if faith_col else None
        a = df.iloc[i][rel_col]   if rel_col   else None
        cat_data[cat].append((f, a))

    for cat, vals in sorted(cat_data.items()):
        fs  = [v[0] for v in vals if v[0] is not None]
        as_ = [v[1] for v in vals if v[1] is not None]
        fa  = sum(fs)  / len(fs)  if fs  else float("nan")
        aa  = sum(as_) / len(as_) if as_ else float("nan")
        print(f"  {cat:25s}: faith={fa:.3f}  rel={aa:.3f}  (n={len(vals)})")

    # faithfulness 낮은 케이스
    if faith_col:
        low = df[df[faith_col] < 0.5]
        if len(low):
            print()
            print(f"Faithfulness < 0.5 케이스 ({len(low)}건):")
            for i in low.index:
                qid   = dataset[i]["_id"]
                faith = low.loc[i, faith_col]
                q     = dataset[i]["question"][:60]
                ans   = dataset[i]["answer"][:80]
                print(f"  [{qid}] faith={faith:.2f}")
                print(f"    Q: {q}")
                print(f"    A: {ans}")

    return df


def save_results(result, dataset, model: str):
    """결과를 JSON으로 저장."""
    df = result.to_pandas()
    out_dir = ROOT / "evaluation" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_slug = model.replace(":", "-").replace(".", "")
    out_path = out_dir / f"eval_ragas_{model_slug}_{ts}.json"

    faith_col = next((c for c in df.columns if "faithful" in c.lower()), None)
    rel_col   = next((c for c in df.columns if "relevancy" in c.lower() or "relevance" in c.lower()), None)
    recall_col = next((c for c in df.columns if "recall" in c.lower()), None)
    prec_col   = next((c for c in df.columns if "precision" in c.lower()), None)

    rows = []
    for i in range(len(df)):
        rows.append({
            "id":               dataset[i]["_id"],
            "category":         dataset[i]["_category"],
            "lang":             dataset[i]["_lang"],
            "question":         dataset[i]["question"],
            "ground_truth":     dataset[i]["ground_truth"],
            "answer":           dataset[i]["answer"],
            "token_f1":         dataset[i]["_f1"],
            "faithfulness":     df.iloc[i][faith_col]  if faith_col  else None,
            "answer_relevancy": df.iloc[i][rel_col]    if rel_col    else None,
            "context_recall":   df.iloc[i][recall_col] if recall_col else None,
            "context_precision":df.iloc[i][prec_col]   if prec_col   else None,
        })

    summary = {}
    for label, col in [("faithfulness", faith_col), ("answer_relevancy", rel_col),
                       ("context_recall", recall_col), ("context_precision", prec_col)]:
        if col:
            summary[label] = round(df[col].mean(), 4)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump({"model": model, "summary": summary, "results": rows},
                  f, ensure_ascii=False, indent=2)

    print(f"\n결과 저장: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="RAGAS 기반 RAG 평가 (ragas 0.4.x)")
    parser.add_argument(
        "--input", required=True,
        help="eval_multilingual 결과 JSON 경로 (retrieved_contexts 필드 필요)",
    )
    parser.add_argument(
        "--model", default="claude-haiku-4-5-20251001",
        help="Claude 모델 ID (기본: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--lang", choices=["ko", "en"], default=None,
        help="평가 언어 필터 (기본: JSON 내 모든 언어)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="평가할 최대 항목 수 (비용 절감용)",
    )
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("오류: ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  .env 파일에 ANTHROPIC_API_KEY=sk-ant-... 추가")
        sys.exit(1)

    print(f"입력 파일: {args.input}")
    print(f"평가 모델: {args.model}")
    print(f"언어 필터: {args.lang or '전체'}")

    print("\n[1/3] 데이터셋 로드...")
    dataset = load_dataset(args.input, args.lang, args.limit)
    if len(dataset) == 0:
        print("평가할 데이터가 없습니다.")
        sys.exit(1)

    print("[2/3] Evaluator 초기화 (BGE-M3 로드 중)...")
    llm, embeddings = build_evaluator(args.model, api_key)

    print("[3/3] RAGAS 평가 실행 중...")
    result = run_evaluation(dataset, llm, embeddings)

    print_results(result, dataset)
    save_results(result, dataset, args.model)


if __name__ == "__main__":
    main()
