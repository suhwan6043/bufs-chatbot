"use client";
import { useState, useEffect, useCallback } from "react";
import { useAdmin, type LogEntry } from "@/hooks/useAdmin";
import { BASE_URL } from "@/lib/api";

const INTENT_LABELS: Record<string, string> = {
  "GRADUATION_REQ": "졸업요건", "REGISTRATION": "수강신청",
  "SCHEDULE": "학사일정", "COURSE_INFO": "교과목", "MAJOR_CHANGE": "전과",
  "ALTERNATIVE": "대안/선택", "GENERAL": "일반",
  "LEAVE_OF_ABSENCE": "학적변동", "EARLY_GRADUATION": "조기졸업",
  "SCHOLARSHIP": "장학금", "CONTACT": "연락처",
};

function renderStars(rating: string | number | null | undefined) {
  if (rating == null || rating === "" || rating === "-") return "-";
  const n = typeof rating === "number" ? rating : parseInt(String(rating), 10);
  if (isNaN(n) || n < 1) return "-";
  return "★".repeat(Math.min(n, 5)) + "☆".repeat(Math.max(0, 5 - n));
}

interface PromoteForm {
  question: string;
  answer: string;
  category: string;
}

interface PromoteResult {
  idx: number;
  ok: boolean;
  msg: string;
}

export default function LogsPage() {
  const { token, fetchLogDates, fetchLogs, fetchFaqCategories, createFaq } = useAdmin();

  const [dates, setDates] = useState<string[]>([]);
  const [selDate, setSelDate] = useState("");
  const [intent, setIntent] = useState("");
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [todayCount, setTodayCount] = useState(0);
  const [avgDuration, setAvgDuration] = useState(0);
  const [topIntent, setTopIntent] = useState("-");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  // FAQ 이송 상태
  const [categories, setCategories] = useState<string[]>([]);
  const [promoteIdx, setPromoteIdx] = useState<number | null>(null);
  const [promoteForm, setPromoteForm] = useState<PromoteForm>({ question: "", answer: "", category: "" });
  const [promoteLoading, setPromoteLoading] = useState(false);
  const [promoteResult, setPromoteResult] = useState<PromoteResult | null>(null);

  const limit = 50;

  useEffect(() => {
    if (token) fetchLogDates().then((d) => setDates(d.dates)).catch(() => {});
  }, [token, fetchLogDates]);

  useEffect(() => {
    if (token) {
      fetchFaqCategories().then((r) => setCategories(r.categories)).catch(() => {});
    }
  }, [token, fetchFaqCategories]);

  const load = useCallback(async (o = 0) => {
    setLoading(true);
    setExpandedIdx(null);
    setPromoteIdx(null);
    setPromoteResult(null);
    const p = new URLSearchParams({ limit: String(limit), offset: String(o) });
    if (selDate) p.set("log_date", selDate);
    if (intent) p.set("intent", intent);
    try {
      const r = await fetchLogs(p.toString());
      setEntries(r.entries);
      setTotal(r.total);
      setTodayCount(r.today_count);
      setAvgDuration(r.avg_duration_ms);
      setTopIntent(r.top_intent);
      setOffset(o);
    } catch {} finally { setLoading(false); }
  }, [selDate, intent, fetchLogs]);

  useEffect(() => { if (token) load(); }, [token, selDate, intent]);

  const openPromote = (idx: number, entry: LogEntry) => {
    setPromoteIdx(idx);
    setPromoteResult(null);
    setPromoteForm({
      question: entry.question,
      answer: entry.answer || "",
      category: categories[0] || "",
    });
  };

  const closePromote = () => {
    setPromoteIdx(null);
    setPromoteResult(null);
  };

  const submitPromote = async (sourceEntry: LogEntry) => {
    if (!promoteForm.question.trim() || !promoteForm.answer.trim() || !promoteForm.category.trim()) return;
    setPromoteLoading(true);
    try {
      const loggedInUser = sourceEntry.user_id != null;
      await createFaq({
        question: promoteForm.question.trim(),
        answer: promoteForm.answer.trim(),
        category: promoteForm.category.trim(),
        source_question: sourceEntry.question !== promoteForm.question.trim()
          ? sourceEntry.question
          : undefined,
        // 로그인 사용자의 질문이면 user_id·message_id 연결 → FAQ 수정 시 알림 발송
        source_user_id: sourceEntry.user_id ?? null,
        source_chat_message_id: sourceEntry.chat_message_id ?? null,
      });
      setPromoteResult({
        idx: promoteIdx!,
        ok: true,
        msg: loggedInUser
          ? "FAQ에 등록되었습니다. 질문자에게 알림이 발송됩니다."
          : "FAQ에 등록되었습니다.",
      });
      setPromoteIdx(null);
    } catch (e: unknown) {
      setPromoteResult({
        idx: promoteIdx!,
        ok: false,
        msg: e instanceof Error ? e.message : "등록 실패",
      });
    } finally {
      setPromoteLoading(false);
    }
  };

  if (!token) return null;

  const kpiCards = [
    { label: "조회 대화 수", value: total, color: "border-l-blue-500" },
    { label: "오늘 대화", value: todayCount, color: "border-l-green-500" },
    { label: "평균 응답", value: `${(avgDuration / 1000).toFixed(1)}s`, color: "border-l-orange-500" },
    { label: "최다 인텐트", value: topIntent || "-", color: "border-l-purple-500" },
  ];

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-lg font-bold text-navy">대화 로그</h1>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {kpiCards.map((c) => (
          <div key={c.label} className={`bg-white rounded-xl p-4 shadow-sm border border-gray-100 border-l-4 ${c.color}`}>
            <div className="text-xl font-bold text-text">{c.value}</div>
            <div className="text-xs text-muted mt-0.5">{c.label}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-end">
        <div>
          <label className="text-xs text-muted block mb-1">날짜</label>
          <select value={selDate} onChange={(e) => setSelDate(e.target.value)} className="px-3 py-2 border border-border rounded-lg text-sm">
            <option value="">전체</option>
            {dates.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted block mb-1">Intent</label>
          <input value={intent} onChange={(e) => setIntent(e.target.value)} placeholder="필터..." className="px-3 py-2 border border-border rounded-lg text-sm w-40" />
        </div>
        <a href={`${BASE_URL}/api/admin/logs/export/csv${selDate ? `?log_date=${selDate}` : ""}`} target="_blank" rel="noreferrer"
          className="px-3 py-2 text-sm border border-border rounded-lg hover:bg-gray-50">CSV 내보내기</a>
        <a href={`${BASE_URL}/api/admin/logs/export/jsonl${selDate ? `?log_date=${selDate}` : ""}`} target="_blank" rel="noreferrer"
          className="px-3 py-2 text-sm border border-border rounded-lg hover:bg-gray-50">JSONL 내보내기</a>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-left text-muted bg-gray-50">
              <th className="px-3 py-2 w-6"></th>
              <th className="px-3 py-2">시간</th>
              <th className="px-3 py-2">학번</th>
              <th className="px-3 py-2">Intent</th>
              <th className="px-3 py-2">질문</th>
              <th className="px-3 py-2">답변</th>
              <th className="px-3 py-2">응답(ms)</th>
              <th className="px-3 py-2">만족도</th>
            </tr>
          </thead>
          <tbody>
            {loading ? <tr><td colSpan={8} className="text-center py-8 text-muted">로딩 중...</td></tr> :
              entries.length === 0 ? <tr><td colSpan={8} className="text-center py-8 text-muted">데이터 없음</td></tr> :
              entries.map((e, i) => (
                <>
                  <tr key={`row-${i}`}
                    className={`border-b border-border/50 hover:bg-gray-50 cursor-pointer ${expandedIdx === i ? "bg-blue-50" : ""}`}
                    onClick={() => {
                      const next = expandedIdx === i ? null : i;
                      setExpandedIdx(next);
                      if (next === null) { setPromoteIdx(null); setPromoteResult(null); }
                    }}>
                    <td className="px-3 py-2 text-muted">{expandedIdx === i ? "▼" : "▶"}</td>
                    <td className="px-3 py-2 whitespace-nowrap text-muted">{e.timestamp}</td>
                    <td className="px-3 py-2 text-text-sub">
                      {e.student_id || "-"}
                      {e.user_id != null && (
                        <span className="ml-1 inline-block px-1.5 py-0.5 text-[10px] rounded bg-emerald-50 text-emerald-700 border border-emerald-200" title="로그인 사용자 — FAQ 이송 시 알림 대상">
                          로그인
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-text-sub">{INTENT_LABELS[e.intent] || e.intent}</td>
                    <td className="px-3 py-2 max-w-[250px] truncate">{e.question}</td>
                    <td className="px-3 py-2 max-w-[250px] truncate text-text-sub">{e.answer ? e.answer.slice(0, 80) + "..." : "-"}</td>
                    <td className="px-3 py-2 text-text-sub">{e.duration_ms}</td>
                    <td className="px-3 py-2 text-yellow-500">{renderStars(e.rating)}</td>
                  </tr>

                  {expandedIdx === i && (
                    <tr key={`detail-${i}`} className="bg-gray-50">
                      <td colSpan={8} className="px-4 py-3">
                        <div className="space-y-2">
                          {/* 질문 / 답변 */}
                          <div>
                            <span className="text-xs font-medium text-text">질문:</span>
                            <p className="text-xs text-text-sub mt-1 whitespace-pre-wrap">{e.question}</p>
                          </div>
                          <div className="border-t border-border/50 pt-2">
                            <span className="text-xs font-medium text-text">답변:</span>
                            <p className="text-xs text-text-sub mt-1 whitespace-pre-wrap leading-relaxed">{e.answer || "(답변 없음)"}</p>
                          </div>
                          <div className="flex gap-4 text-xs text-muted border-t border-border/50 pt-2">
                            <span>세션: {e.session_id || "-"}</span>
                            <span>응답시간: {e.duration_ms}ms</span>
                            <span>만족도: {renderStars(e.rating)}</span>
                          </div>

                          {/* FAQ 이송 결과 배너 */}
                          {promoteResult?.idx === i && (
                            <div className={`text-xs px-3 py-2 rounded-lg ${promoteResult.ok ? "bg-green-50 text-green-700 border border-green-200" : "bg-red-50 text-red-600 border border-red-200"}`}>
                              {promoteResult.ok ? "✓ " : "✗ "}{promoteResult.msg}
                            </div>
                          )}

                          {/* FAQ 이송 버튼 */}
                          {promoteIdx !== i && !(promoteResult?.idx === i && promoteResult.ok) && (
                            <div className="border-t border-border/50 pt-2">
                              <button
                                onClick={(ev) => { ev.stopPropagation(); openPromote(i, e); }}
                                className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                              >
                                FAQ로 등록
                              </button>
                            </div>
                          )}

                          {/* FAQ 이송 인라인 폼 */}
                          {promoteIdx === i && (
                            <div
                              className="border border-blue-200 rounded-xl p-4 bg-blue-50 space-y-3 mt-2"
                              onClick={(ev) => ev.stopPropagation()}
                            >
                              <p className="text-xs font-semibold text-blue-800">FAQ 등록 — 올바른 답변으로 수정 후 등록하세요</p>
                              {e.user_id != null && (
                                <p className="text-[11px] text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-md px-2 py-1">
                                  ✉ 등록 시 이 질문자(user_id {e.user_id})에게 자동으로 알림이 발송됩니다.
                                </p>
                              )}

                              {/* 카테고리 */}
                              <div>
                                <label className="text-xs text-muted block mb-1">카테고리 <span className="text-red-500">*</span></label>
                                <div className="flex gap-2">
                                  <select
                                    value={promoteForm.category}
                                    onChange={(ev) => setPromoteForm(f => ({ ...f, category: ev.target.value }))}
                                    className="flex-1 px-2 py-1.5 border border-border rounded-lg text-xs"
                                  >
                                    <option value="">선택...</option>
                                    {categories.map((c) => <option key={c} value={c}>{c}</option>)}
                                  </select>
                                  <input
                                    placeholder="직접 입력"
                                    value={categories.includes(promoteForm.category) ? "" : promoteForm.category}
                                    onChange={(ev) => setPromoteForm(f => ({ ...f, category: ev.target.value }))}
                                    className="w-32 px-2 py-1.5 border border-border rounded-lg text-xs"
                                  />
                                </div>
                              </div>

                              {/* 질문 */}
                              <div>
                                <label className="text-xs text-muted block mb-1">질문 <span className="text-red-500">*</span></label>
                                <textarea
                                  rows={2}
                                  value={promoteForm.question}
                                  onChange={(ev) => setPromoteForm(f => ({ ...f, question: ev.target.value }))}
                                  className="w-full px-2 py-1.5 border border-border rounded-lg text-xs resize-none"
                                />
                              </div>

                              {/* 올바른 답변 */}
                              <div>
                                <label className="text-xs text-muted block mb-1">
                                  올바른 답변 <span className="text-red-500">*</span>
                                  <span className="ml-1 text-orange-500 font-normal">← 잘못된 내용을 수정하세요</span>
                                </label>
                                <textarea
                                  rows={5}
                                  value={promoteForm.answer}
                                  onChange={(ev) => setPromoteForm(f => ({ ...f, answer: ev.target.value }))}
                                  className="w-full px-2 py-1.5 border border-orange-300 rounded-lg text-xs resize-y bg-white"
                                />
                              </div>

                              <div className="flex gap-2 justify-end">
                                <button
                                  onClick={closePromote}
                                  className="px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-gray-100"
                                >
                                  취소
                                </button>
                                <button
                                  onClick={() => submitPromote(e)}
                                  disabled={promoteLoading || !promoteForm.question.trim() || !promoteForm.answer.trim() || !promoteForm.category.trim()}
                                  className="px-4 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-40 transition-colors"
                                >
                                  {promoteLoading ? "등록 중..." : "등록"}
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > limit && (
        <div className="flex items-center gap-2 justify-center">
          <button onClick={() => load(Math.max(0, offset - limit))} disabled={offset === 0} className="px-3 py-1.5 text-xs border rounded-lg disabled:opacity-30">이전</button>
          <span className="text-xs text-muted">{offset + 1}~{Math.min(offset + limit, total)} / {total}</span>
          <button onClick={() => load(offset + limit)} disabled={offset + limit >= total} className="px-3 py-1.5 text-xs border rounded-lg disabled:opacity-30">다음</button>
        </div>
      )}
    </div>
  );
}
