"use client";
import { useState, useEffect, use } from "react";
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

export default function LogsPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang: rawLang } = use(params);
  const lang = rawLang === "en" ? "en" : "ko";
  const { token, fetchLogDates, fetchLogs } = useAdmin();

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
  const limit = 50;

  useEffect(() => { if (token) fetchLogDates().then((d) => setDates(d.dates)).catch(() => {}); }, [token, fetchLogDates]);

  const load = async (o = 0) => {
    setLoading(true);
    setExpandedIdx(null);
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
  };

  useEffect(() => { if (token) load(); }, [token, selDate, intent]);

  if (!token) return null;

  const kpiCards = [
    { label: "조회 대화 수", value: total, color: "border-l-blue-500" },
    { label: "오늘 대화", value: todayCount, color: "border-l-green-500" },
    { label: "평균 응답", value: `${(avgDuration / 1000).toFixed(1)}s`, color: "border-l-orange-500" },
    // top_intent는 백엔드에서 이미 한국어 라벨로 번역되어 옴 → 그대로 표시
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
                  <tr key={`row-${i}`} className={`border-b border-border/50 hover:bg-gray-50 cursor-pointer ${expandedIdx === i ? "bg-blue-50" : ""}`}
                    onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}>
                    <td className="px-3 py-2 text-muted">{expandedIdx === i ? "▼" : "▶"}</td>
                    <td className="px-3 py-2 whitespace-nowrap text-muted">{e.timestamp}</td>
                    <td className="px-3 py-2 text-text-sub">{e.student_id || "-"}</td>
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
