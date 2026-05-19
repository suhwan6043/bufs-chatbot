"""137문 H100 답변 자동 채점 (1차 휴리스틱).

규칙:
1. 답변에 "관련 정보를 찾을 수 없습니다" / "찾을 수 없" / "확인하지 못했습니다" 포함
   AND 답변 본문 < 120자 → refusal
   - GT가 명시적 "정보 없음" 안내 권유면 refusal_acceptable
   - 그렇지 않으면 refusal_unacceptable

2. GT에서 핵심 사실 토큰 추출:
   - 숫자/단위 (예: "120학점", "5/1", "5164", "18학점")
   - 부서명 (예: "학생복지팀", "학사지원팀")
   - 키워드 (예: "면제", "불가", "가능", "휴업일", "휴학")

3. 답변에서 핵심 토큰 일치율 계산:
   - 95%+ 일치 AND 모순 키워드 없음 → correct
   - 50-95% 일치 → partial
   - <50% 일치 OR 정반대 결론 (positive/negative 키워드 반대) → wrong

4. 모호한 경우 → review (manual 검토 대상)

출력:
- reports/eval_5_7/scored_137_h100.jsonl (사례별 verdict + 근거 + 일치 토큰)
- reports/eval_5_7/score_summary.json (통계)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from collections import Counter

BASE = Path(__file__).parent.parent
DIR = BASE / "reports" / "eval_5_7"


def load_concat_json(path: Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8").strip()
    dec = json.JSONDecoder()
    pos, items = 0, []
    while pos < len(txt):
        while pos < len(txt) and txt[pos] in " \t\n\r":
            pos += 1
        if pos >= len(txt):
            break
        obj, end = dec.raw_decode(txt, pos)
        items.append(obj)
        pos = end
    return items


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


REFUSAL_PATTERNS = [
    "관련 정보를 찾을 수 없습니다",
    "관련 자료에서 해당 정보를 정확히 확인하지 못했습니다",
    "정확히 확인하지 못했습니다",
    "정보가 포함되어 있지 않",
    "답변 생성 중 오류",
    "해당 일정 정보를 찾을 수 없",
]


def is_refusal(text: str) -> bool:
    if not text:
        return True
    return any(p in text for p in REFUSAL_PATTERNS)


def strip_validation_footer(text: str) -> str:
    """답변 본문에서 검증경고/연락처 footer 제거하여 실제 답변 길이 측정."""
    # 검증 경고 / 학사 문의 footer 제거
    for delim in ["\n\n---\n", "\n---\n", "--- *검증 경고:*", "📞 학사 문의"]:
        if delim in text:
            text = text.split(delim, 1)[0]
    return text.strip()


# 핵심 사실 토큰 패턴 (GT에서 추출)
NUMBER_PAT = re.compile(r"\b(\d+(?:\.\d+)?\s*(?:학점|학기|회|시간|분|점|명|일|월|주|학년|차))\b")
PHONE_PAT = re.compile(r"(051-\d{3}-\d{4}(?:~\d+)?|509-\d{4})")
DEPT_PAT = re.compile(r"([가-힣]{2,8}팀|[가-힣]{2,6}처|[가-힣]{2,6}센터|학생복지|학사지원|국제교류|학사관리)")
DATE_PAT = re.compile(r"(\d{1,2}\.\d{1,2}\.?|\d{4}\.\d{1,2}\.\d{1,2}|\d{1,2}월\s*\d{1,2}일|2026-\d{2}-\d{2})")
URL_PAT = re.compile(r"(https?://[^\s\)]+|m\.bufs\.ac\.kr|sugang\.bufs\.ac\.kr|bufs\.ac\.kr)")

POSITIVE_KEYWORDS = ["가능", "면제", "허용", "있음", "휴업", "휴강"]
NEGATIVE_KEYWORDS = ["불가", "불포함", "안 됩니다", "안됩니다", "없음", "없습니다", "제외", "안 된다", "안된다"]


def extract_tokens(text: str) -> dict:
    return {
        "numbers": set(m.group(1) for m in NUMBER_PAT.finditer(text)),
        "phones": set(m.group(1) for m in PHONE_PAT.finditer(text)),
        "depts": set(m.group(1) for m in DEPT_PAT.finditer(text)),
        "dates": set(m.group(1) for m in DATE_PAT.finditer(text)),
        "urls": set(m.group(1) for m in URL_PAT.finditer(text)),
        "has_positive": any(k in text for k in POSITIVE_KEYWORDS),
        "has_negative": any(k in text for k in NEGATIVE_KEYWORDS),
    }


def text_overlap_ratio(a: str, b: str) -> float:
    """단순 토큰 overlap — 한국어/영어 mixed text용. 의미보다 단어 매칭."""
    def tok(s: str) -> set[str]:
        # 2글자 이상 한국어 명사·영어 단어 추출
        words = re.findall(r"[가-힣]{2,}|[A-Za-z]{2,}|\d+", s)
        return set(w for w in words if len(w) >= 2)
    ta, tb = tok(a), tok(b)
    if not ta or not tb:
        return 0.0
    common = ta & tb
    # gt 기준으로: gt 토큰 중 답변에 포함된 비율
    return len(common) / len(ta)


CRITICAL_KEYWORDS_PAIRS = [
    ("면제", ["면제"]),
    ("불가", ["불가", "안됩니다", "안 됩니다", "할 수 없", "수강 못", "신청 못"]),
    ("가능", ["가능", "할 수 있", "신청할 수 있", "허용"]),
    ("휴업일", ["휴업일", "휴강", "수업 없"]),
    ("학생복지팀", ["학생복지팀", "복지팀"]),
    ("학사지원팀", ["학사지원팀"]),
    ("학과사무실", ["학과사무실", "학과 사무실"]),
    ("국제교류", ["국제교류", "국제교류처", "국제교류팀"]),
    ("재무팀", ["재무팀"]),
]


def has_critical_keyword(text: str, gt: str) -> tuple[bool, str | None]:
    """GT에 핵심 키워드가 있을 때 답변에도 동의어 포함 여부 검사."""
    for gt_kw, ans_kws in CRITICAL_KEYWORDS_PAIRS:
        if gt_kw in gt:
            if any(k in text for k in ans_kws):
                return True, gt_kw
            else:
                return False, gt_kw
    return None, None


def score_one(gt: str, ans: str, q: str = "", y_ans: str = "", y_verdict: str = "") -> tuple[str, dict]:
    """단일 케이스 자동 채점. 반환: (verdict, details)

    추가 입력: y_ans (어제 답변), y_verdict (어제 verdict) — 보조 신호.
    """
    body = strip_validation_footer(ans)
    refusal = is_refusal(body) and len(body) < 180
    gt_tokens = extract_tokens(gt or "")
    ans_tokens = extract_tokens(body)

    # 토큰 일치
    matches: dict[str, list] = {}
    for k in ("numbers", "phones", "depts", "dates", "urls"):
        gt_set = gt_tokens[k]
        if not gt_set:
            continue
        hits = [t for t in gt_set if any(t in a or a in t for a in ans_tokens[k]) or t in body]
        matches[k] = {"gt": sorted(gt_set), "matched": sorted(hits)}

    # polarity 모순
    polarity_conflict = (
        (gt_tokens["has_positive"] and ans_tokens["has_negative"]
         and not (gt_tokens["has_negative"] or ans_tokens["has_positive"]))
        or
        (gt_tokens["has_negative"] and ans_tokens["has_positive"]
         and not (gt_tokens["has_positive"] or ans_tokens["has_negative"]))
    )

    total_gt_tokens = sum(len(matches[k]["gt"]) for k in matches if matches[k]["gt"])
    total_matched = sum(len(matches[k]["matched"]) for k in matches)

    overlap = text_overlap_ratio(gt, body) if gt else 0.0
    kw_ok, kw_name = has_critical_keyword(body, gt or "")
    h_vs_y_overlap = text_overlap_ratio(body, strip_validation_footer(y_ans)) if y_ans else 0.0

    details = {
        "body_len": len(body),
        "refusal": refusal,
        "polarity_conflict": polarity_conflict,
        "matches": matches,
        "token_match_ratio": (total_matched / total_gt_tokens) if total_gt_tokens else None,
        "gt_overlap_ratio": round(overlap, 2),
        "h_vs_y_overlap": round(h_vs_y_overlap, 2),
        "critical_keyword": kw_name,
        "critical_keyword_ok": kw_ok,
    }

    # verdict
    if refusal:
        # GT 자체가 "정보 없음" 안내 권유 (소량 케이스)
        if any(p in (gt or "") for p in ("정보 없음", "확인 어려움", "구체 안내", "정보가 없음", "외부 확인 필요")):
            return ("refusal_acceptable", details)
        return ("refusal_unacceptable", details)

    # critical keyword 모순 → wrong
    if kw_ok is False:
        return ("wrong", details)

    if polarity_conflict:
        return ("wrong", details)

    # critical keyword 일치 + overlap 0.5+ → correct/partial
    if kw_ok is True:
        if overlap >= 0.55 or (total_gt_tokens > 0 and total_matched / max(total_gt_tokens,1) >= 0.7):
            return ("correct", details)
        return ("partial", details)

    # 토큰 매칭 기준
    if total_gt_tokens > 0:
        r = total_matched / total_gt_tokens
        if r >= 0.85 and overlap >= 0.4:
            return ("correct", details)
        if r >= 0.5 or overlap >= 0.45:
            return ("partial", details)
        if r <= 0.25:
            return ("wrong", details)

    # GT overlap 기반 (토큰 없는 GT)
    if overlap >= 0.55:
        return ("correct", details)
    if overlap >= 0.35:
        return ("partial", details)
    if overlap < 0.15 and len(body) > 50:
        # 답변에 GT와 무관 내용 — wrong (단 어제도 wrong이었으면 동일)
        return ("wrong", details)

    # 어제 답변과 거의 동일하고 어제 verdict 있으면 그대로
    if h_vs_y_overlap >= 0.7 and y_verdict and y_verdict in ("correct", "partial", "wrong"):
        return (y_verdict, details)

    return ("review", details)


def main() -> int:
    h = {r["idx"]: r for r in load_jsonl(DIR / "responses_h100.jsonl")}
    y = {r["idx"]: r for r in load_jsonl(DIR / "responses_new.jsonl")}
    g = {r["idx"]: r for r in load_concat_json(DIR / "graded.jsonl")}

    out_lines = []
    h100_verdicts = []
    for idx in sorted(h.keys()):
        ans = h[idx].get("h100_answer", "") or ""
        gt = g.get(idx, {}).get("ground_truth", "") or ""
        q = h[idx].get("question", "")
        y_ans = (y.get(idx, {}) or {}).get("new_answer", "") or ""
        y_verdict = (g.get(idx, {}) or {}).get("verdict", "")
        verdict, details = score_one(gt, ans, q, y_ans=y_ans, y_verdict=y_verdict)
        h100_verdicts.append(verdict)
        out_lines.append({
            "idx": idx,
            "question": q,
            "yesterday_verdict": g.get(idx, {}).get("verdict"),
            "h100_verdict": verdict,
            "h100_intent": h[idx].get("h100_intent"),
            "yesterday_intent": y.get(idx, {}).get("new_intent"),
            "h100_duration_ms": h[idx].get("h100_duration_ms"),
            "yesterday_duration_ms": y.get(idx, {}).get("new_duration_ms"),
            "h100_answer": ans[:600],
            "yesterday_answer": (y.get(idx, {}).get("new_answer") or "")[:600],
            "ground_truth": gt[:600],
            "yesterday_reason": g.get(idx, {}).get("reason", ""),
            "score_details": details,
        })

    # 저장
    with (DIR / "scored_137_h100.jsonl").open("w", encoding="utf-8") as f:
        for r in out_lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 통계
    cnt = Counter(h100_verdicts)
    y_cnt = Counter(r["yesterday_verdict"] for r in out_lines if r["yesterday_verdict"])
    summary = {
        "total": len(out_lines),
        "h100_distribution": dict(cnt),
        "yesterday_distribution": dict(y_cnt),
    }
    (DIR / "score_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
