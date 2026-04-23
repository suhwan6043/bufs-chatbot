"""데이터 동일성 비교용 지문(fingerprint) 생성.

양쪽(사용자 Mac / 팀원 Windows)에서 같은 명령으로 실행하고 출력을 비교.
관리자 로그인·SSH 없이 파일시스템 기반으로 상태 요약.

실행: python scripts/data_fingerprint.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def sha256_short(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def file_fingerprint(p: Path) -> dict:
    if not p.exists():
        return {"exists": False}
    stat = p.stat()
    with open(p, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()[:16]
    return {"exists": True, "size": stat.st_size, "sha256_16": h}


def chromadb_stats(path: Path) -> dict:
    db = path / "chroma.sqlite3"
    if not db.exists():
        return {"exists": False}
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        result: dict = {"exists": True, "sqlite_size": db.stat().st_size}
        cur.execute("SELECT COUNT(*) FROM embeddings")
        result["embeddings"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM collections")
        result["collections"] = cur.fetchone()[0]
        cur.execute("SELECT name FROM collections")
        result["collection_names"] = sorted(r[0] for r in cur.fetchall())
        # 청크별 doc_type 분포 (메타데이터)
        try:
            cur.execute(
                "SELECT string_value, COUNT(*) FROM embedding_metadata "
                "WHERE key='doc_type' GROUP BY string_value ORDER BY string_value"
            )
            result["doc_type_dist"] = dict(cur.fetchall())
        except sqlite3.OperationalError:
            result["doc_type_dist"] = "schema mismatch"
    finally:
        conn.close()
    return result


def crawl_meta_stats(path: Path) -> dict:
    f = path / "content_hashes.json"
    if not f.exists():
        return {"exists": False}
    with open(f, encoding="utf-8") as fh:
        data = json.load(fh)
    hashes = sorted(v["content_hash"] for v in data.values())
    # source 타입별 집계 (faq://, notice://, etc.)
    from collections import Counter
    type_counts = Counter(k.split("://", 1)[0] for k in data.keys())
    return {
        "exists": True,
        "entries": len(data),
        "combined_hash": sha256_short("".join(hashes)),
        "by_type": dict(sorted(type_counts.items())),
    }


def graph_stats(path: Path) -> dict:
    pkl = path / "academic_graph.pkl"
    if not pkl.exists():
        return {"exists": False}
    info: dict = {"exists": True, "size": pkl.stat().st_size, "sha256_16": ""}
    with open(pkl, "rb") as f:
        info["sha256_16"] = hashlib.sha256(f.read()).hexdigest()[:16]
    # NetworkX 의존 없이 기본 메타만. 로드가 되면 추가.
    try:
        import pickle
        with open(pkl, "rb") as f:
            g = pickle.load(f)
        if hasattr(g, "number_of_nodes") and hasattr(g, "number_of_edges"):
            info["nodes"] = g.number_of_nodes()
            info["edges"] = g.number_of_edges()
    except Exception as e:
        info["load_error"] = str(e)[:120]
    return info


def pdf_stats(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    files = {}
    for p in sorted(path.rglob("*.pdf")):
        rel = p.relative_to(path).as_posix()
        files[rel] = p.stat().st_size
    return {"exists": True, "count": len(files), "total_size": sum(files.values()), "files": files}


def dir_summary(path: Path, pattern: str = "*") -> dict:
    """임의 디렉터리의 파일 수·총 크기·상위 파일 리스트."""
    if not path.exists():
        return {"exists": False}
    files = sorted(p for p in path.rglob(pattern) if p.is_file())
    total = sum(p.stat().st_size for p in files)
    return {
        "exists": True,
        "count": len(files),
        "total_size": total,
        "top_files": {p.relative_to(path).as_posix(): p.stat().st_size for p in files[:10]},
    }


def main():
    report = {
        # ChromaDB — 레거시 + 현재 운영(chromadb_new)
        "chromadb_legacy": chromadb_stats(DATA / "chromadb"),
        "chromadb_new": chromadb_stats(DATA / "chromadb_new"),
        "graph": graph_stats(DATA / "graphs"),
        "crawl_meta": crawl_meta_stats(DATA / "crawl_meta"),
        # 정적 리소스
        "faq_academic": file_fingerprint(DATA / "faq_academic.json"),
        "faq_combined": file_fingerprint(DATA / "faq_combined.json"),
        "faq_admin": file_fingerprint(DATA / "faq_admin.json"),
        "scholarships": file_fingerprint(DATA / "scholarships.json"),
        "early_graduation": file_fingerprint(DATA / "early_graduation.json"),
        "schema_discovered_fields": file_fingerprint(DATA / "schema_discovered_fields.json"),
        # 파일 기반 컬렉션
        "pdfs_root": pdf_stats(DATA / "pdfs"),
        "pdfs_crawled": pdf_stats(DATA / "pdfs" / "crawled"),
        "portal": pdf_stats(DATA / "portal"),
        "attachments": dir_summary(DATA / "attachments"),
        "extracted": dir_summary(DATA / "extracted"),
        "contacts": dir_summary(DATA / "contacts"),
        "feedback": dir_summary(DATA / "feedback"),
        "eval": dir_summary(DATA / "eval"),
        "eval_multilingual": dir_summary(DATA / "eval_multilingual"),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
