"""
관리자 FAQ 피드백 루프 — 미답변 질의 탐지 + 인간 큐레이션 FAQ CRUD.

원칙:
  1. 스키마 진화 — 기존 `faq_academic.json` 스키마 그대로 사용,
     admin 전용 파일에 저장 (source/created_by/source_question 선택 필드).
  2. 비용·지연 최적화 — uncovered 탐지·클러스터링에 LLM 호출 없음,
     토큰 자카드 + stem 커버리지만 사용.
  3. 생애주기 관리 — `scripts/ingest_faq.py::ingest_incremental()` 재사용으로
     전체 재빌드 없이 그래프·벡터 동시 증분 반영.
  4. 하드코딩 금지 — 임계치·경로 모두 `settings.admin_faq` 로부터 주입.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from backend.database import (
    add_faq_subscriber,
    create_notification,
    delete_faq_subscribers,
    list_faq_subscribers,
)
from backend.routers.admin.auth import require_admin, _audit
from backend.schemas.admin import (
    FaqCreate,
    FaqItem,
    FaqListResponse,
    FaqUpdate,
    UncoveredCluster,
    UncoveredExample,
    UncoveredResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_write_lock = asyncio.Lock()
_ADMIN_ID_PREFIX = "ADMIN-"


# ── 파일 IO ────────────────────────────────────────────────

def _load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("FAQ 파일 파싱 실패(%s): %s", path, exc)
        return []
    if isinstance(data, dict) and "faq" in data:
        data = data["faq"]
    return data if isinstance(data, list) else []


def _save_admin_faqs(items: list[dict]) -> None:
    path = Path(settings.admin_faq.admin_faq_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _merged_faqs() -> tuple[list[dict], list[dict]]:
    """(큐레이션 FAQ, 관리자 FAQ) 튜플 반환. 두 리스트는 ingest용 병합에 그대로 사용 가능.

    큐레이션 코퍼스는 academic_faq + library_faq (동등 등급 큐레이션 파일).
    """
    academic = _load_json_list(Path(settings.admin_faq.academic_faq_path))
    library = _load_json_list(Path(settings.admin_faq.library_faq_path))
    admin = _load_json_list(Path(settings.admin_faq.admin_faq_path))
    return academic + library, admin


# ── ID 생성 ────────────────────────────────────────────────

def _next_admin_id(existing: list[dict]) -> str:
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"{_ADMIN_ID_PREFIX}{today}-"
    seqs: list[int] = []
    for item in existing:
        fid = item.get("id") or ""
        if fid.startswith(prefix):
            try:
                seqs.append(int(fid[len(prefix):]))
            except ValueError:
                continue
    next_seq = max(seqs) + 1 if seqs else 1
    return f"{prefix}{next_seq:04d}"


# ── 카테고리 ────────────────────────────────────────────────

def _known_categories(academic: list[dict], admin: list[dict]) -> list[str]:
    """기존 FAQ 에서 추출한 카테고리 + 기본 카테고리 맵 키."""
    seen: set[str] = set()
    for item in academic + admin:
        c = (item.get("category") or "").strip()
        if c:
            seen.add(c)
    try:
        from app.graphdb.faq_node_builder import CATEGORY_TO_NODE_TYPES
        seen.update(CATEGORY_TO_NODE_TYPES.keys())
    except Exception:
        pass
    return sorted(seen)


# ── 유사도 유틸 ─────────────────────────────────────────────

def _stems(text: str) -> set[str]:
    """빈 문자열 안전 stems. 2자 미만·stopword 제거한 핵심 어근 셋."""
    if not text:
        return set()
    try:
        from app.pipeline.ko_tokenizer import stems, FAQ_STOPWORDS
    except Exception:
        return set(text.split())
    return {s for s in stems(text) if s not in FAQ_STOPWORDS and len(s) >= 2}


def _stem_coverage(query: str, ref_q: str, ref_a: str = "") -> float:
    """query 의 핵심 어근이 ref_q ∪ ref_a 에 포함된 비율."""
    q_core = _stems(query)
    if not q_core:
        return 0.0
    ref_core = _stems(ref_q) | _stems(ref_a)
    return len(q_core & ref_core) / len(q_core)


def _jaccard(a: str, b: str) -> float:
    A, B = _stems(a), _stems(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


# ── 런타임 그래프/스토어 ────────────────────────────────────

def _live_graph_and_store():
    """
    파이프라인이 현재 사용 중인 그래프/스토어 참조를 가져온다.
    변경이 즉시 검색 결과에 반영되려면 **라우터가 쥐고 있는 동일 인스턴스**를 건드려야 한다.
    """
    from backend.dependencies import get_router
    router_inst = get_router()
    if router_inst is None or router_inst.academic_graph is None:
        raise HTTPException(status_code=503, detail="파이프라인이 초기화되지 않았습니다.")
    from app.shared_resources import get_chroma_store
    store = get_chroma_store()
    return router_inst.academic_graph, store


def _reingest_faq(academic: list[dict], admin: list[dict]) -> None:
    """
    현행 라이브 그래프·스토어에 academic + admin 을 증분 반영.
    scripts/ingest_faq.py 의 ingest_incremental() 재사용 (동일 해시 체크 경로).
    """
    graph, store = _live_graph_and_store()
    from scripts.ingest_faq import ingest_incremental  # noqa: WPS433

    merged = academic + admin
    ingest_incremental(
        store=store,
        graph=graph,
        faq_data=merged,
        source_file=Path(settings.admin_faq.admin_faq_path).name,
    )
    graph.save()


# ── 직렬화 ─────────────────────────────────────────────────

def _to_faq_item(item: dict) -> FaqItem:
    fid = item.get("id") or ""
    is_admin = fid.startswith(_ADMIN_ID_PREFIX) or item.get("source") == "admin"
    return FaqItem(
        id=fid,
        category=item.get("category", ""),
        question=item.get("question", ""),
        answer=item.get("answer", ""),
        source="admin" if is_admin else "academic",
        created_by=item.get("created_by"),
        created_at=item.get("created_at"),
        source_question=item.get("source_question"),
        answer_type=item.get("answer_type"),
        student_types=item.get("student_types") or [],
        cohort_from=item.get("cohort_from"),
        cohort_to=item.get("cohort_to"),
    )


# ── GET: 목록 ──────────────────────────────────────────────

@router.get("/faq", response_model=FaqListResponse)
async def list_faq(
    source: str = Query("all", pattern="^(all|admin|academic)$"),
    _=Depends(require_admin),
):
    academic, admin = _merged_faqs()
    if source == "admin":
        items_raw = admin
    elif source == "academic":
        items_raw = academic
    else:
        items_raw = academic + admin
    items = [_to_faq_item(it) for it in items_raw if it.get("id")]
    return FaqListResponse(
        total=len(items),
        items=items,
        categories=_known_categories(academic, admin),
    )


# ── GET: 카테고리 목록 (경량) ───────────────────────────────

@router.get("/faq/categories")
async def list_faq_categories(_=Depends(require_admin)):
    """FAQ 카테고리 목록만 반환 (로그→FAQ 이송 폼용)."""
    academic, admin = _merged_faqs()
    return {"categories": _known_categories(academic, admin)}


# ── POST: 추가 ─────────────────────────────────────────────

def _truncate_body(text: str, limit: Optional[int] = None) -> str:
    """알림 body 는 본문 요약 일부만 — 개인정보·긴 답변 유출 최소화."""
    if not text:
        return ""
    limit = int(limit or settings.notifications.body_max_chars)
    t = text.strip()
    return t if len(t) <= limit else (t[:limit].rstrip() + "…")


def _notify_subscribers(
    faq_id: str,
    kind: str,
    title: str,
    body: str,
) -> int:
    """해당 FAQ 구독자 전원에 알림 발송. 실패는 로그만, HTTP 500 안 냄."""
    try:
        subs = list_faq_subscribers(faq_id)
    except Exception as exc:
        logger.error("FAQ 구독자 조회 실패 faq_id=%s: %s", faq_id, exc)
        return 0
    sent = 0
    for s in subs:
        try:
            create_notification(
                user_id=int(s["user_id"]),
                kind=kind,
                title=title,
                body=body,
                faq_id=faq_id,
                chat_message_id=s.get("chat_message_id"),
            )
            sent += 1
        except Exception as exc:
            logger.error(
                "알림 생성 실패 user_id=%s faq_id=%s: %s",
                s.get("user_id"), faq_id, exc,
            )
    return sent


@router.post("/faq", response_model=FaqItem)
async def create_faq(body: FaqCreate, _=Depends(require_admin)):
    async with _write_lock:
        academic, admin = _merged_faqs()
        new_id = _next_admin_id(admin)
        now = datetime.now().isoformat(timespec="seconds")
        item = {
            "id": new_id,
            "category": body.category.strip(),
            "question": body.question.strip(),
            "answer": body.answer.strip(),
            "source": "admin",
            "created_by": "admin",
            "created_at": now,
        }
        if body.source_question and body.source_question.strip():
            item["source_question"] = body.source_question.strip()
        # 학생 속성 분기 필드 (선택)
        if body.student_types:
            item["student_types"] = body.student_types
        if body.cohort_from is not None:
            item["cohort_from"] = body.cohort_from
        if body.cohort_to is not None:
            item["cohort_to"] = body.cohort_to

        admin.append(item)
        _save_admin_faqs(admin)
        _audit("ADMIN_FAQ_ADDED", f"id={new_id} category={item['category']}")
        try:
            _reingest_faq(academic, admin)
        except Exception as exc:
            logger.error("FAQ 증분 반영 실패: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"FAQ 저장은 되었으나 증분 반영 실패: {exc}")

        # 대화 로그 이송 시 구독자 등록 + 즉시 '답변 정정' 알림 발송
        if body.source_user_id is not None:
            try:
                add_faq_subscriber(
                    faq_id=new_id,
                    user_id=int(body.source_user_id),
                    chat_message_id=body.source_chat_message_id,
                )
                create_notification(
                    user_id=int(body.source_user_id),
                    kind="faq_answered",
                    title=settings.notifications.title_answered_ko,
                    body=_truncate_body(item["answer"]),
                    faq_id=new_id,
                    chat_message_id=body.source_chat_message_id,
                )
                _audit("FAQ_SUBSCRIBER_ADDED", f"faq_id={new_id} user_id={body.source_user_id}")
            except Exception as exc:
                logger.error("FAQ 구독자/알림 생성 실패: %s", exc, exc_info=True)

    return _to_faq_item(item)


# ── PUT: 수정 ──────────────────────────────────────────────

@router.put("/faq/{faq_id}", response_model=FaqItem)
async def update_faq(faq_id: str, body: FaqUpdate, _=Depends(require_admin)):
    if not faq_id.startswith(_ADMIN_ID_PREFIX):
        raise HTTPException(status_code=403, detail="관리자 추가 FAQ(ADMIN-*)만 수정할 수 있습니다.")

    async with _write_lock:
        academic, admin = _merged_faqs()
        idx = next((i for i, it in enumerate(admin) if it.get("id") == faq_id), -1)
        if idx < 0:
            raise HTTPException(status_code=404, detail="FAQ 없음")

        target = dict(admin[idx])
        if body.question is not None:
            target["question"] = body.question.strip()
        if body.answer is not None:
            target["answer"] = body.answer.strip()
        if body.category is not None:
            target["category"] = body.category.strip()
        if body.source_question is not None:
            sq = body.source_question.strip()
            if sq:
                target["source_question"] = sq
            else:
                target.pop("source_question", None)
        # 학생 속성 분기 필드 업데이트 (None이면 기존값 유지)
        if body.student_types is not None:
            if body.student_types:
                target["student_types"] = body.student_types
            else:
                target.pop("student_types", None)  # 빈 리스트 = 필드 제거 (전체 허용)
        if body.cohort_from is not None:
            target["cohort_from"] = body.cohort_from
        if body.cohort_to is not None:
            target["cohort_to"] = body.cohort_to
        target["updated_at"] = datetime.now().isoformat(timespec="seconds")
        admin[idx] = target

        _save_admin_faqs(admin)
        _audit("ADMIN_FAQ_UPDATED", f"id={faq_id}")
        try:
            _reingest_faq(academic, admin)
        except Exception as exc:
            logger.error("FAQ 증분 반영 실패: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"수정은 되었으나 증분 반영 실패: {exc}")

        # 답변이 요청에 포함됐으면 구독자에게 알림
        if body.answer is not None:
            sent = _notify_subscribers(
                faq_id=faq_id,
                kind="faq_updated",
                title=settings.notifications.title_updated_ko,
                body=_truncate_body(target.get("answer", "")),
            )
            if sent:
                _audit("FAQ_UPDATE_NOTIFIED", f"faq_id={faq_id} users={sent}")

    return _to_faq_item(target)


# ── DELETE: 삭제 ───────────────────────────────────────────

@router.delete("/faq/{faq_id}")
async def delete_faq(faq_id: str, _=Depends(require_admin)):
    if not faq_id.startswith(_ADMIN_ID_PREFIX):
        raise HTTPException(status_code=403, detail="관리자 추가 FAQ(ADMIN-*)만 삭제할 수 있습니다.")

    async with _write_lock:
        academic, admin = _merged_faqs()
        new_admin = [it for it in admin if it.get("id") != faq_id]
        if len(new_admin) == len(admin):
            raise HTTPException(status_code=404, detail="FAQ 없음")

        _save_admin_faqs(new_admin)
        _audit("ADMIN_FAQ_DELETED", f"id={faq_id}")
        try:
            _reingest_faq(academic, new_admin)
        except Exception as exc:
            logger.error("FAQ 증분 반영 실패: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"삭제는 되었으나 증분 반영 실패: {exc}")

        # 구독자 매핑 정리 (notifications 은 개인 이력 보존을 위해 유지)
        try:
            removed = delete_faq_subscribers(faq_id)
            if removed:
                _audit("FAQ_SUBSCRIBERS_CLEARED", f"faq_id={faq_id} count={removed}")
        except Exception as exc:
            logger.error("FAQ 구독자 정리 실패: %s", exc)

    return {"ok": True, "id": faq_id}


# ── GET: 미답변 질의 ────────────────────────────────────────

def _is_refusal(answer: str) -> bool:
    if not answer:
        return True
    cfg = settings.admin_faq
    return cfg.refusal_phrase_ko in answer or cfg.refusal_phrase_en.lower() in answer.lower()


def _is_already_covered(question: str, all_faqs: list[dict], threshold: float) -> bool:
    """기존 FAQ 중 하나라도 stem 커버리지 ≥ threshold 면 이미 답변 가능."""
    for item in all_faqs:
        fq = item.get("question") or ""
        fa = item.get("answer") or ""
        sq = item.get("source_question") or ""
        ref_q = f"{fq} {sq}".strip() if sq else fq
        if _stem_coverage(question, ref_q, fa) >= threshold:
            return True
    return False


def _normalize_question(q: str) -> str:
    """공백·특수문자 정리 — 클러스터링 전처리."""
    return re.sub(r"\s+", " ", (q or "").strip())


@router.get("/faq/uncovered", response_model=UncoveredResponse)
async def list_uncovered(
    days: int = Query(default=None, ge=1, le=90),
    limit: int = Query(default=None, ge=1, le=500),
    _=Depends(require_admin),
):
    cfg = settings.admin_faq
    days = days or cfg.uncovered_default_days
    limit = limit or cfg.uncovered_max_return

    from app.logging import ChatLogger
    from datetime import date, timedelta

    logger_inst = ChatLogger()
    since = date.today() - timedelta(days=days - 1)

    candidates: list[dict] = []
    for d in sorted(logger_inst.list_dates()):
        if d < since:
            continue
        for entry in logger_inst.read(d):
            question = _normalize_question(entry.get("question", ""))
            if not question or len(question) < 2:
                continue
            answer = entry.get("answer", "") or ""
            rating = entry.get("rating")
            refused = _is_refusal(answer)
            low_rated = isinstance(rating, int) and rating <= cfg.uncovered_rating_threshold
            if not (refused or low_rated):
                continue
            candidates.append({
                "question": question,
                "answer": answer,
                "timestamp": entry.get("timestamp", ""),
                "session_id": entry.get("session_id", ""),
                "rating": rating if isinstance(rating, int) else None,
                "refused": refused,
            })

    # 이미 기존 FAQ 로 커버된 질문은 제외
    academic, admin = _merged_faqs()
    all_faqs = academic + admin
    filtered = [
        c for c in candidates
        if not _is_already_covered(c["question"], all_faqs, cfg.dedup_sim_threshold)
    ]

    # 자카드 기반 클러스터링
    clusters: list[list[dict]] = []
    for cand in filtered:
        placed = False
        for cluster in clusters:
            if _jaccard(cand["question"], cluster[0]["question"]) >= cfg.cluster_sim_threshold:
                cluster.append(cand)
                placed = True
                break
        if not placed:
            clusters.append([cand])

    # 대표·최신·빈도 요약
    out_clusters: list[UncoveredCluster] = []
    for cluster in clusters:
        cluster.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        # 대표 질문: 해당 클러스터에서 가장 긴 원문 (정보량 최대화)
        representative = max((c["question"] for c in cluster), key=len)
        examples = [
            UncoveredExample(
                question=c["question"],
                answer=(c["answer"] or "")[:200],
                timestamp=c["timestamp"],
                session_id=c["session_id"],
                rating=c["rating"],
                refused=c["refused"],
            )
            for c in cluster[:3]
        ]
        out_clusters.append(UncoveredCluster(
            representative_question=representative,
            count=len(cluster),
            last_asked=cluster[0].get("timestamp", ""),
            examples=examples,
        ))

    out_clusters.sort(key=lambda x: (x.count, x.last_asked), reverse=True)
    out_clusters = out_clusters[:limit]

    return UncoveredResponse(
        scanned_days=days,
        total_candidates=len(candidates),
        clusters=out_clusters,
    )
