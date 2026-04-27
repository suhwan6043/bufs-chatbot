"""신규 파이프라인 v2 — 전체 PDF 코퍼스 일괄 인제스트 (dedupe + lifecycle 메타).

- `data/pdfs/`(직접 보유) > `data/pdfs/portal/`(학생포털) > `data/pdfs/crawled/`
  (크롤 첨부) > `data/portal/`(중복 가능 디렉토리) 순으로 우선순위.
- `file_sha256[:8]` 기반 dedupe (첫 등장 채택).
- 위치/파일명으로 doc_type·lifecycle·term·semester 자동 매핑.
- ChromaDB는 `bufs_v2` 컬렉션에 저장 (메인 `bufs_academic` 무영향).
- 같은 source_hash 기존 청크는 자동 삭제 후 재인덱스.

사용:
    python scripts/ingest_all_v2.py                       # dry-run 없이 풀 인제스트
    python scripts/ingest_all_v2.py --dry-run             # 청크만 만들어 통계
    python scripts/ingest_all_v2.py --no-vlm              # VLM 표 폴백 비활성
    python scripts/ingest_all_v2.py --limit 5             # 처음 5개만 처리 (스모크)
    python scripts/ingest_all_v2.py --collection bufs_smoke  # 다른 컬렉션
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 프로젝트 루트 sys.path 보정
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.ingestion.chunking_v2 import (
    chunks_from_pdf, validate_chunk, file_sha256, ChunkV2,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ── 위치 정의 (우선순위 순서) ───────────────────────────────────────────────
# (glob, fallback_doc_type, fallback_lifecycle)
# fallback은 파일명 패턴 매칭이 실패했을 때 적용.
LOCATIONS: list[tuple[str, str, str]] = [
    ("data/pdfs/*.pdf",          "domestic",          "static"),     # 직접 보유
    ("data/pdfs/portal/*.pdf",   "domestic",          "static"),     # 학생포털 매뉴얼
    ("data/pdfs/crawled/*.pdf",  "notice_attachment", "dynamic"),    # 공지 첨부
    ("data/portal/*.pdf",        "domestic",          "static"),     # 중복 가능
]


# ── 파일명 → 메타 매핑 ──────────────────────────────────────────────────────
def map_filename_meta(filename: str) -> dict:
    """파일명 패턴으로 doc_type/lifecycle/term/semester 추정.

    매칭 실패 시 빈 dict 반환 (호출부에서 fallback 적용).
    macOS APFS는 한글을 NFD(자모 분리형)로 반환하는 경우가 있어 NFC 정규화 필수.
    """
    # NFD → NFC (macOS APFS 호환)
    name = unicodedata.normalize("NFC", filename)
    # 학사안내 (학년도 + 학기) — 예: "2026학년도1학기학사안내.pdf"
    m = re.search(r"(20\d{2}).*?([12])학기.*?학사\s*안내(?!.*요약)", name)
    if m:
        y, s = m.group(1), m.group(2)
        return {
            "doc_type": "domestic", "lifecycle": "term",
            "term": y, "semester": f"{y}-{s}",
        }
    # 시간표 (학년도 + 학기) — 예: "2026학년도 1학기 수업시간표.pdf"
    m = re.search(r"(20\d{2}).*?([12])학기.*?시간표", name)
    if m:
        y, s = m.group(1), m.group(2)
        return {
            "doc_type": "timetable", "lifecycle": "term",
            "term": y, "semester": f"{y}-{s}",
        }
    # 신입생 가이드북 — 입학년도 단위, 학기 구분 없음
    m = re.search(r"(20\d{2}).*?신입생.*?가이드북", name)
    if m:
        return {
            "doc_type": "guide", "lifecycle": "term",
            "term": m.group(1), "semester": None,
        }
    # 학사안내 요약본 — 정적 (자주 갱신 안 됨)
    if "학사안내" in name and ("요약" in name or "요약본" in name):
        return {
            "doc_type": "guide", "lifecycle": "static",
            "term": None, "semester": None,
        }
    # 장학 관련 — 정적·동적 분기는 위치로 결정 (fallback)
    if "장학" in name:
        return {"doc_type": "scholarship"}
    # 등록 관련
    if "등록" in name or "납부" in name:
        return {"doc_type": "domestic"}
    # 결석·복학·휴학 매뉴얼
    if any(kw in name for kw in ("결석", "복학", "휴학", "수강신청", "성적")):
        return {"doc_type": "domestic"}
    return {}


def term_period(term: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """학년도 → (valid_from, valid_to) ISO date.

    한국 학년도 = 3월 1일 ~ 익년 2월 말.
    """
    if not term or not term.isdigit():
        return None, None
    y = int(term)
    # 윤년 영향 없는 안전한 종료일 28
    return f"{y:04d}-03-01", f"{y+1:04d}-02-28"


# ── ChromaDB ────────────────────────────────────────────────────────────────
# 모듈 레벨 싱글턴 — embeddings를 직접 생성해서 add() 시 명시 전달
_EMBEDDER = None


def get_embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from app.embedding import Embedder
        _EMBEDDER = Embedder()
    return _EMBEDDER


def get_collection(name: str):
    """v2 전용 ChromaDB 컬렉션.

    embedding_function은 fallback용으로만 등록 (실제 add()에는 명시 embeddings 전달).
    이렇게 분리해야 인제스트 시 청크 raw text와 임베딩 입력 텍스트를 다르게 만들 수 있음
    (section_path를 임베딩 텍스트에만 prefix하기 위함).
    """
    import chromadb

    persist_dir = os.getenv("CHROMA_PERSIST_DIR_V2", "data/chromadb_new")
    client = chromadb.PersistentClient(path=persist_dir)

    embedder = get_embedder()

    class _Embed:
        def __call__(self, input):
            return embedder.embed_passages_batch(input)
        def name(self):
            return "bge-m3"

    return client.get_or_create_collection(
        name=name,
        embedding_function=_Embed(),
        metadata={"hnsw:space": "cosine"},
    )


def build_embedding_text(text: str, metadata: dict) -> str:
    """청크의 임베딩 입력 텍스트를 만든다 (raw text는 보존, embedding 입력만 prefix).

    section_path가 있으면 `[섹션] 본문` 형태로 prefix해 dense 임베딩이 헤더 정보를
    반영하도록 한다. document(저장되는 raw text)와는 분리.
    """
    sec = (metadata or {}).get("section_path", "")
    if sec:
        return f"[{sec}] {text}"
    return text


def flatten_metadata(meta: dict) -> dict:
    """ChromaDB는 scalar 메타만 지원 — 리스트·dict 직렬화, None 제거."""
    out = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple, dict)):
            out[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            out[k] = bool(v)
        elif isinstance(v, (int, float, str)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


# ── 코퍼스 수집 + dedupe ────────────────────────────────────────────────────
def collect_corpus(verbose: bool = False) -> list[dict]:
    """모든 위치에서 PDF 수집 후 source_hash 기반 dedupe.

    Returns:
        list of dict: {path, sha, doc_type, lifecycle, term, semester,
                       valid_from, valid_to, source_priority}
    """
    seen_hashes: set[str] = set()
    out: list[dict] = []

    for prio, (glob_pat, fb_doc, fb_lc) in enumerate(LOCATIONS, start=1):
        matched = sorted(Path(".").glob(glob_pat))
        for p in matched:
            if not p.is_file():
                continue
            try:
                sha = file_sha256(str(p))[:8]
            except Exception as e:
                logger.warning("sha 계산 실패 %s: %s", p, e)
                continue
            if sha in seen_hashes:
                if verbose:
                    logger.info("[dedupe] 스킵 %s (sha=%s)", p, sha)
                continue
            seen_hashes.add(sha)

            # 파일명 패턴 매칭
            meta = map_filename_meta(p.name)
            doc_type = meta.get("doc_type") or fb_doc
            lifecycle = meta.get("lifecycle") or fb_lc
            term = meta.get("term")
            semester = meta.get("semester")
            valid_from, valid_to = term_period(term)

            out.append({
                "path": str(p),
                "sha": sha,
                "doc_type": doc_type,
                "lifecycle": lifecycle,
                "term": term,
                "semester": semester,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "source_priority": prio,
            })

    return out


# ── 단일 PDF 인제스트 ───────────────────────────────────────────────────────
def ingest_one(
    coll, entry: dict, *,
    enable_vlm: bool = True, dry_run: bool = False,
) -> dict:
    """단일 PDF → 청크 → bufs_v2 저장.

    Returns:
        {path, sha, chunks_passed, chunks_rejected, elapsed_sec, doc_type, lifecycle}
    """
    pdf_path = entry["path"]
    sha = entry["sha"]
    doc_type = entry["doc_type"]
    last_seen = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # lifecycle 메타 (모든 청크에 공통 주입)
    lifecycle_meta = {
        "lifecycle": entry["lifecycle"],
        "term": entry["term"],
        "semester": entry["semester"],
        "valid_from": entry["valid_from"],
        "valid_to": entry["valid_to"],
        "last_seen": last_seen,
        "source_path": pdf_path,
    }

    # 기존 source_hash 청크 삭제 (재인덱스)
    if not dry_run:
        try:
            existing = coll.get(where={"source_hash": sha}, limit=10000)
            if existing.get("ids"):
                coll.delete(ids=existing["ids"])
                logger.info(
                    "[%s] 기존 동일 source 청크 %d개 삭제",
                    Path(pdf_path).name, len(existing["ids"]),
                )
        except Exception as e:
            logger.warning("기존 청크 삭제 실패 (무시): %s", e)

    t0 = time.monotonic()
    passed: list[ChunkV2] = []
    rejected: list[tuple] = []
    for chunk in chunks_from_pdf(pdf_path, doc_type=doc_type, enable_vlm=enable_vlm):
        # lifecycle 메타 주입
        chunk.metadata.update(lifecycle_meta)
        valid, reason = validate_chunk(chunk)
        if valid:
            passed.append(chunk)
        else:
            rejected.append((chunk.chunk_id, reason))
    elapsed = time.monotonic() - t0

    if not dry_run and passed:
        ids = [c.chunk_id for c in passed]
        docs = [c.text for c in passed]
        metas = [flatten_metadata(c.metadata) for c in passed]
        # 임베딩 입력 텍스트: section_path를 prefix해 헤더 정보를 dense 임베딩에 반영.
        # document(raw text)는 그대로 저장 → UI/LLM 컨텍스트 노이즈 없음.
        embed_inputs = [build_embedding_text(c.text, c.metadata) for c in passed]
        embedder = get_embedder()
        embeddings = embedder.embed_passages_batch(embed_inputs)
        embeddings = [
            e.tolist() if hasattr(e, "tolist") else list(e) for e in embeddings
        ]
        coll.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)

    return {
        "path": pdf_path,
        "sha": sha,
        "chunks_passed": len(passed),
        "chunks_rejected": len(rejected),
        "elapsed_sec": round(elapsed, 1),
        "doc_type": doc_type,
        "lifecycle": entry["lifecycle"],
        "term": entry["term"],
        "semester": entry["semester"],
        "rejected_samples": rejected[:5],
    }


# ── 메인 ────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="v2 전체 PDF 인제스트 (dedupe + lifecycle 메타)")
    parser.add_argument("--collection", default="bufs_v2",
                        help="저장할 ChromaDB 컬렉션 (기본 bufs_v2)")
    parser.add_argument("--no-vlm", action="store_true",
                        help="VLM 표 폴백 비활성화")
    parser.add_argument("--dry-run", action="store_true",
                        help="ChromaDB 저장 없이 청크만 만들어 통계")
    parser.add_argument("--limit", type=int, default=None,
                        help="처음 N개 PDF만 처리 (스모크용)")
    parser.add_argument("--report", default=None,
                        help="결과 리포트 JSON 경로")
    parser.add_argument("--verbose-dedupe", action="store_true",
                        help="dedupe 스킵 로그 표시")
    args = parser.parse_args()

    logger.info("=== v2 전체 PDF 인제스트 시작 ===")
    logger.info("collection=%s, VLM=%s, dry-run=%s, limit=%s",
                args.collection, not args.no_vlm, args.dry_run, args.limit)

    # 1. 코퍼스 수집 + dedupe
    corpus = collect_corpus(verbose=args.verbose_dedupe)
    logger.info("코퍼스 dedupe 완료: %d 고유 PDF", len(corpus))
    if args.limit:
        corpus = corpus[: args.limit]
        logger.info("--limit 적용: 처음 %d개만 처리", len(corpus))

    # 매핑 요약
    print("\n" + "=" * 70)
    print(f"{'파일':<55} {'doc_type':<14} {'lifecycle':<8} {'term':<5} {'semester':<8}")
    print("=" * 70)
    for e in corpus[:30]:
        name = Path(e["path"]).name
        if len(name) > 54:
            name = name[:51] + "..."
        print(f"{name:<55} {e['doc_type'] or '-':<14} {e['lifecycle'] or '-':<8} "
              f"{(e['term'] or '-'):<5} {(e['semester'] or '-'):<8}")
    if len(corpus) > 30:
        print(f"... + {len(corpus) - 30}개")
    print()

    # 2. 컬렉션 가져오기 (dry-run이 아니면)
    coll = None
    if not args.dry_run:
        coll = get_collection(args.collection)
        before = coll.count()
        logger.info("컬렉션 '%s' 현재 청크 수: %d", args.collection, before)

    # 3. 각 PDF 인제스트
    t_start = time.monotonic()
    results = []
    for i, entry in enumerate(corpus, start=1):
        logger.info("\n--- [%d/%d] %s (%s) ---",
                    i, len(corpus), Path(entry["path"]).name, entry["doc_type"])
        try:
            r = ingest_one(coll, entry, enable_vlm=not args.no_vlm, dry_run=args.dry_run)
            results.append(r)
            logger.info("결과: 통과 %d, 거부 %d (%.1fs)",
                        r["chunks_passed"], r["chunks_rejected"], r["elapsed_sec"])
        except Exception as e:
            logger.error("인제스트 실패 %s: %s", entry["path"], e, exc_info=True)
            results.append({"path": entry["path"], "error": str(e)})

    total_elapsed = time.monotonic() - t_start

    # 4. 요약 통계
    total_passed = sum(r.get("chunks_passed", 0) for r in results)
    total_rejected = sum(r.get("chunks_rejected", 0) for r in results)
    by_doc_type = {}
    by_lifecycle = {}
    for r in results:
        if "error" in r:
            continue
        by_doc_type[r["doc_type"]] = by_doc_type.get(r["doc_type"], 0) + r["chunks_passed"]
        by_lifecycle[r["lifecycle"]] = by_lifecycle.get(r["lifecycle"], 0) + r["chunks_passed"]

    print("\n" + "=" * 70)
    print("[전체 통계]")
    print("=" * 70)
    print(f"  처리 PDF        : {len(results)}")
    print(f"  통과 청크        : {total_passed}")
    print(f"  거부 청크        : {total_rejected}")
    print(f"  총 소요          : {total_elapsed:.1f}초")
    print(f"  doc_type 분포    : {by_doc_type}")
    print(f"  lifecycle 분포   : {by_lifecycle}")
    if not args.dry_run and coll is not None:
        after = coll.count()
        print(f"  컬렉션 사이즈    : {after} (변화 {after - before:+d})")

    # 5. 리포트 저장
    if args.report:
        Path(args.report).write_text(
            json.dumps({
                "collection": args.collection,
                "dry_run": args.dry_run,
                "total_elapsed_sec": round(total_elapsed, 1),
                "total_passed": total_passed,
                "total_rejected": total_rejected,
                "by_doc_type": by_doc_type,
                "by_lifecycle": by_lifecycle,
                "results": results,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("리포트 저장: %s", args.report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
