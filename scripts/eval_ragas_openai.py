"""
RAGAS 기반 RAG 평가 스크립트 (ragas 0.4.x)
OpenAI (gpt-4.1-mini / gpt-4o-mini 등)를 평가 LLM으로 사용.

eval_ragas_claude.py의 OpenAI 버전. 2026-04-17 Anthropic API 한도 초과 대응.

평가 지표:
  - Faithfulness        : 답변이 컨텍스트에 근거하는가 (환각 탐지)
  - AnswerRelevancy     : 답변이 질문에 관련 있는가
  - ContextRecall       : ground_truth를 커버하는 컨텍스트가 검색됐는가
  - ContextPrecision    : 검색된 컨텍스트 중 관련 있는 비율

입력:
  eval_multilingual.py 로 생성한 JSON (retrieved_contexts 필드 필요)

사용법:
  # KO+EN 모두 (기본, gpt-4.1-mini)
  python scripts/eval_ragas_openai.py --input evaluation/results/eval_multilingual_*.json

  # 언어 필터
  python scripts/eval_ragas_openai.py --input ... --lang ko

  # 모델 변경
  python scripts/eval_ragas_openai.py --input ... --model gpt-4o-mini

  # 일부 항목만 (비용 절감)
  python scripts/eval_ragas_openai.py --input ... --limit 10

비용 추정 (171문항 4지표, 한글 포함):
  gpt-4o-mini:  ~$0.40
  gpt-4.1-mini: ~$1.10
  gpt-4o:       ~$7
  gpt-4.1:      ~$10
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

        q   = r["question"]
        ans = r.get("pred", "")
        ctx = [c for c in ctx_list if c.strip()]
        gt  = r.get("ground_truth", "")
        # 이중 키 제공: 기존 이름(legacy) + Ragas 0.5 단일턴 이름(single_turn)
        rows.append({
            # legacy (faithfulness, context_recall/precision, answer_relevancy 호환)
            "question":     q,
            "answer":       ans,
            "contexts":     ctx,
            "ground_truth": gt,
            # Ragas 0.5 SingleTurnSample 필드 (answer_correctness 등 신규 메트릭)
            "user_input":   q,
            "response":     ans,
            "retrieved_contexts": ctx,
            "reference":    gt,
            # 내부 메타
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


def build_evaluator(model: str, api_key: str, max_tokens: int = 4096):
    """OpenAI LLM + BGE-M3(MPS) 임베딩으로 RAGAS evaluator 구성.

    max_tokens: judge LLM 출력 상한. 이전 run에서 "max_tokens length limit" 일부 발생 →
                기본 4096으로 상향 (Ragas 기본은 1024 근처).

    임베딩: BGE-M3를 Apple MPS(GPU)로 가속. 평가 전용으로만 MPS 사용 —
    프로덕션(app/config.py)은 CPU 기본값 유지. CUDA 환경에선 자동으로 cuda 선택.
    """
    import openai
    import torch
    from sentence_transformers import SentenceTransformer
    from ragas.llms import llm_factory

    client = openai.OpenAI(api_key=api_key)
    llm = llm_factory(model=model, provider="openai", client=client)

    # 출력 토큰 상한 상향 — ragas faithfulness/precision 분석 시 긴 JSON 출력 방어
    if hasattr(llm, "model_args"):
        llm.model_args["max_tokens"] = max_tokens
    if hasattr(llm, "max_tokens"):
        llm.max_tokens = max_tokens

    # GPU 자동 선택: MPS(Mac) > CUDA > CPU
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    print(f"  임베딩 디바이스: {device}")

    # SentenceTransformer 직접 사용 + Ragas 인터페이스 어댑터
    # answer_relevancy, answer_correctness(similarity)에서 embed_query/embed_documents 필요
    st_model = SentenceTransformer("BAAI/bge-m3", device=device)

    class _STEmbeddings:
        """Ragas가 기대하는 임베딩 인터페이스 전체 제공.

        Ragas 메트릭별로 다른 메서드를 호출:
        - answer_relevancy: embed_query / embed_documents
        - answer_correctness(similarity): embed_text / embed_texts (구버전 스타일)
        - 모두 async 변형 있음
        """

        def embed_text(self, text: str):
            return st_model.encode(text, convert_to_tensor=False).tolist()

        def embed_texts(self, texts: list[str]):
            return st_model.encode(texts, convert_to_tensor=False).tolist()

        def embed_query(self, text: str):
            return self.embed_text(text)

        def embed_documents(self, texts: list[str]):
            return self.embed_texts(texts)

        async def aembed_text(self, text: str):
            return self.embed_text(text)

        async def aembed_texts(self, texts: list[str]):
            return self.embed_texts(texts)

        async def aembed_query(self, text: str):
            return self.embed_text(text)

        async def aembed_documents(self, texts: list[str]):
            return self.embed_texts(texts)

    return llm, _STEmbeddings()


def run_evaluation(dataset, llm, embeddings):
    """RAGAS 5개 지표 평가 실행 (Answer Correctness 포함)."""
    from ragas import evaluate
    from ragas.run_config import RunConfig
    from ragas.metrics import (
        faithfulness,
        answer_relevancy,
        answer_correctness,
        context_recall,
        context_precision,
    )

    # timeout 넉넉히, concurrency 적절히 (OpenAI rate limit 여유)
    run_config = RunConfig(
        timeout=300,       # 기본 180 → 300s (긴 컨텍스트 judge용)
        max_retries=5,     # 기본 10 → 5 (너무 많이 재시도 시 실패 확정)
        max_workers=8,     # 기본 16 → 8 (rate limit 완충)
    )

    result = evaluate(
        dataset=dataset,
        metrics=[
            faithfulness,
            answer_relevancy,
            answer_correctness,  # 신규: semantic + factual 정합성
            context_recall,
            context_precision,
        ],
        llm=llm,
        embeddings=embeddings,
        raise_exceptions=False,
        run_config=run_config,
    )
    return result


def _safe_mean(values):
    """NaN/None 제외 평균. 비어있으면 None."""
    import math
    clean = [
        v for v in values
        if v is not None and not (isinstance(v, float) and math.isnan(v))
    ]
    if not clean:
        return None
    return sum(clean) / len(clean)


def print_results(result, dataset):
    """결과 출력: 전체 + 언어별 + 카테고리별."""
    import collections
    import math

    df = result.to_pandas()

    metrics = [
        "faithfulness",
        "answer_relevancy",
        "answer_correctness",
        "context_recall",
        "context_precision",
    ]

    print()
    print("=" * 70)
    print("RAGAS 평가 결과 (전체, NaN 제외 평균)")
    print("=" * 70)
    for m in metrics:
        col = next(
            (c for c in df.columns if c.lower().replace(" ", "_") == m),
            None,
        )
        if col:
            values = list(df[col].dropna())
            val = _safe_mean(values)
            nan_cnt = int(df[col].isna().sum())
            val_str = f"{val:.4f}" if val is not None else "N/A"
            print(f"  {m:25s}: {val_str}  (n={len(values)}, NaN={nan_cnt})")

    # 언어별 집계
    faith_col = next((c for c in df.columns if "faithful" in c.lower()), None)
    rel_col   = next((c for c in df.columns if "relevancy" in c.lower() or "relevance" in c.lower()), None)
    corr_col  = next((c for c in df.columns if "correctness" in c.lower()), None)
    recall_col = next((c for c in df.columns if "recall" in c.lower()), None)
    prec_col   = next((c for c in df.columns if "precision" in c.lower()), None)

    print()
    print("언어별 집계 (NaN 제외)")
    print("-" * 70)
    lang_data = collections.defaultdict(list)
    for i in range(len(df)):
        lang = dataset[i]["_lang"]
        lang_data[lang].append(i)

    for lang, idxs in sorted(lang_data.items()):
        sub = df.iloc[idxs]
        print(f"  [{lang}] n={len(idxs)}")
        for label, col in [
            ("faithfulness", faith_col),
            ("answer_relevancy", rel_col),
            ("answer_correctness", corr_col),
            ("context_recall", recall_col),
            ("context_precision", prec_col),
        ]:
            if col:
                val = _safe_mean(list(sub[col].dropna()))
                val_str = f"{val:.4f}" if val is not None else "N/A"
                print(f"      {label:20s}: {val_str}")

    # 카테고리별 집계
    print()
    print("카테고리별 Faithfulness / AnswerCorrectness / AnswerRelevancy (NaN 제외)")
    print("-" * 80)
    cat_data = collections.defaultdict(list)
    for i in range(len(df)):
        cat = dataset[i]["_category"]
        f = df.iloc[i][faith_col] if faith_col else None
        a = df.iloc[i][rel_col]   if rel_col   else None
        c = df.iloc[i][corr_col]  if corr_col  else None
        cat_data[cat].append((f, a, c))

    for cat, vals in sorted(cat_data.items()):
        fs  = [v[0] for v in vals]
        as_ = [v[1] for v in vals]
        cs  = [v[2] for v in vals]
        fa  = _safe_mean(fs)
        aa  = _safe_mean(as_)
        ca  = _safe_mean(cs)
        fa_s = f"{fa:.3f}" if fa is not None else "N/A"
        aa_s = f"{aa:.3f}" if aa is not None else "N/A"
        ca_s = f"{ca:.3f}" if ca is not None else "N/A"
        print(f"  {cat:25s}: faith={fa_s}  corr={ca_s}  rel={aa_s}  (n={len(vals)})")

    # faithfulness 낮은 케이스 (환각 의심)
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
    model_slug = model.replace(":", "-").replace(".", "").replace("/", "-")
    out_path = out_dir / f"eval_ragas_{model_slug}_{ts}.json"

    faith_col = next((c for c in df.columns if "faithful" in c.lower()), None)
    rel_col   = next((c for c in df.columns if "relevancy" in c.lower() or "relevance" in c.lower()), None)
    corr_col  = next((c for c in df.columns if "correctness" in c.lower()), None)
    recall_col = next((c for c in df.columns if "recall" in c.lower()), None)
    prec_col   = next((c for c in df.columns if "precision" in c.lower()), None)

    def _v(i, col):
        if col is None:
            return None
        import math
        v = df.iloc[i][col]
        if isinstance(v, float) and math.isnan(v):
            return None
        return float(v) if v is not None else None

    rows = []
    for i in range(len(df)):
        rows.append({
            "id":                dataset[i]["_id"],
            "category":          dataset[i]["_category"],
            "lang":              dataset[i]["_lang"],
            "question":          dataset[i]["question"],
            "ground_truth":      dataset[i]["ground_truth"],
            "answer":            dataset[i]["answer"],
            "token_f1":          dataset[i]["_f1"],
            "faithfulness":      _v(i, faith_col),
            "answer_relevancy":  _v(i, rel_col),
            "answer_correctness":_v(i, corr_col),
            "context_recall":    _v(i, recall_col),
            "context_precision": _v(i, prec_col),
        })

    summary = {}
    for label, col in [
        ("faithfulness", faith_col),
        ("answer_relevancy", rel_col),
        ("answer_correctness", corr_col),
        ("context_recall", recall_col),
        ("context_precision", prec_col),
    ]:
        if col:
            val = _safe_mean(list(df[col].dropna()))
            summary[label] = round(val, 4) if val is not None else None

    with out_path.open("w", encoding="utf-8") as f:
        json.dump({"model": model, "summary": summary, "results": rows},
                  f, ensure_ascii=False, indent=2)

    print(f"\n결과 저장: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="RAGAS 기반 RAG 평가 (OpenAI judge)")
    parser.add_argument(
        "--input", required=True,
        help="eval_multilingual 결과 JSON 경로 (retrieved_contexts 필드 필요)",
    )
    parser.add_argument(
        "--model", default="gpt-4.1-mini",
        help="OpenAI 모델 ID (기본: gpt-4.1-mini). 대안: gpt-4o-mini, gpt-4o, gpt-4.1",
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

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("오류: OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  .env 파일에 OPENAI_API_KEY=sk-proj-... 추가")
        sys.exit(1)

    print(f"입력 파일: {args.input}")
    print(f"평가 모델: {args.model}")
    print(f"언어 필터: {args.lang or '전체 (KO+EN)'}")

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
