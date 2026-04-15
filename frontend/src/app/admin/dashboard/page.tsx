"use client";
import { useState, useEffect} from "react";
import { t } from "@/lib/i18n";
import { useAdmin } from "@/hooks/useAdmin";

interface KPI { total_questions: number; today_questions: number; avg_duration_sec: number; faq_count: number }
interface DailyCount { date: string; count: number }
interface IntentCount { intent: string; count: number }
interface RecentChat { time: string; question: string; intent: string; duration_ms: number; rating: string }

export default function AdminDashboard() {
  const { token, fetchDashboard, logout } = useAdmin();

  const [kpi, setKpi] = useState<KPI | null>(null);
  const [daily, setDaily] = useState<DailyCount[]>([]);
  const [intents, setIntents] = useState<IntentCount[]>([]);
  const [recent, setRecent] = useState<RecentChat[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;
    fetchDashboard()
      .then((d) => { setKpi(d.kpi); setDaily(d.daily_chart); setIntents(d.intent_distribution); setRecent(d.recent_chats); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [token, fetchDashboard]);

  if (!token) {
    return (
      <div className="min-h-screen bg-main flex items-center justify-center">
        <div className="text-center">
          <p className="text-muted mb-4">인증이 필요합니다.</p>
          <a href={`/admin`} className="text-accent hover:underline">로그인 페이지로 이동</a>
        </div>
      </div>
    );
  }

  if (loading) return <div className="min-h-screen bg-main flex items-center justify-center"><p className="text-muted animate-pulse">로딩 중...</p></div>;

  const kpiCards = [
    { label: "총 대화 수", value: kpi?.total_questions ?? 0, icon: "\uD83D\uDCAC", color: "border-l-blue-500" },
    { label: "오늘 대화", value: kpi?.today_questions ?? 0, icon: "\uD83D\uDCCA", color: "border-l-green-500" },
    { label: "평균 응답", value: `${kpi?.avg_duration_sec ?? 0}s`, icon: "\u23F1\uFE0F", color: "border-l-orange-500" },
    { label: "FAQ 항목", value: kpi?.faq_count ?? 0, icon: "\uD83D\uDCCB", color: "border-l-purple-500" },
  ];

  return (
    <div className="bg-main min-h-full">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        <h1 className="text-lg font-bold text-navy">{"대시보드"}</h1>
        {/* KPI Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {kpiCards.map((c) => (
            <div key={c.label} className={`bg-white rounded-xl p-5 shadow-sm border border-gray-100 border-l-4 ${c.color}`}>
              <div className="text-2xl mb-1">{c.icon}</div>
              <div className="text-2xl font-bold text-text">{c.value}</div>
              <div className="text-xs text-muted mt-0.5">{c.label}</div>
            </div>
          ))}
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {/* Daily chart (simple bar representation) */}
          <div className="md:col-span-2 bg-white rounded-xl p-5 shadow-sm border border-gray-100">
            <h2 className="text-sm font-bold text-text mb-4">일별 대화 추이</h2>
            <div className="flex items-end gap-2 h-40">
              {daily.map((d) => {
                const maxCount = Math.max(...daily.map((x) => x.count), 1);
                const pct = (d.count / maxCount) * 100;
                return (
                  <div key={d.date} className="flex-1 flex flex-col items-center gap-1">
                    <span className="text-[0.65rem] text-muted">{d.count}</span>
                    <div className="w-full bg-blue-500 rounded-t" style={{ height: `${Math.max(pct, 4)}%` }} />
                    <span className="text-[0.6rem] text-muted">{d.date.length >= 5 ? d.date.slice(5) : d.date}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Intent distribution */}
          <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
            <h2 className="text-sm font-bold text-text mb-4">Intent 분포</h2>
            <div className="space-y-2">
              {intents.slice(0, 8).map((item) => (
                <div key={item.intent} className="flex items-center justify-between text-xs">
                  <span className="text-text-sub truncate">{item.intent}</span>
                  <span className="font-medium text-text">{item.count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Recent chats */}
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h2 className="text-sm font-bold text-text mb-4">최근 대화</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-left text-muted">
                  <th className="pb-2 pr-4">시간</th>
                  <th className="pb-2 pr-4">질문</th>
                  <th className="pb-2 pr-4">Intent</th>
                  <th className="pb-2 pr-4">응답(ms)</th>
                  <th className="pb-2">만족도</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((r, i) => (
                  <tr key={i} className="border-b border-border/50">
                    <td className="py-2 pr-4 text-muted whitespace-nowrap">{r.time}</td>
                    <td className="py-2 pr-4 text-text truncate max-w-[300px]">{r.question}</td>
                    <td className="py-2 pr-4 text-text-sub">{r.intent}</td>
                    <td className="py-2 pr-4 text-text-sub">{r.duration_ms}</td>
                    <td className="py-2">{r.rating}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
