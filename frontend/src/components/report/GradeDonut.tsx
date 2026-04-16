"use client";
import { Award } from "lucide-react";

interface Props {
  distribution: Record<string, number>;
}

// 성적 등급별 색상 (Tailwind 기본)
const GRADE_COLORS: Record<string, string> = {
  "A+": "#2563eb", "A": "#3b82f6",
  "B+": "#16a34a", "B": "#22c55e",
  "C+": "#eab308", "C": "#f59e0b",
  "D+": "#f97316", "D": "#fb923c",
  "F": "#dc2626",
  "P": "#6366f1", "NP": "#94a3b8",
};

// 렌더 순서 (성적 높은 순)
const DISPLAY_ORDER = ["A+", "A", "B+", "B", "C+", "C", "D+", "D", "F", "P", "NP"];

export default function GradeDonut({ distribution }: Props) {
  const total = Object.values(distribution).reduce((s, v) => s + v, 0);
  if (total === 0) {
    return (
      <section className="bg-white border border-slate-200 rounded-2xl p-5 md:p-6 shadow-sm">
        <h3 className="text-base md:text-lg font-bold text-slate-900 mb-2">성적 분포</h3>
        <p className="text-sm text-slate-400">이수 과목이 없습니다.</p>
      </section>
    );
  }

  // conic-gradient stops 생성
  let cursor = 0;
  const stops: string[] = [];
  const legend: { grade: string; count: number; pct: number; color: string }[] = [];
  for (const grade of DISPLAY_ORDER) {
    const count = distribution[grade] || 0;
    if (count === 0) continue;
    const pct = (count / total) * 100;
    const color = GRADE_COLORS[grade] || "#cbd5e1";
    stops.push(`${color} ${cursor}% ${cursor + pct}%`);
    legend.push({ grade, count, pct, color });
    cursor += pct;
  }
  const gradient = `conic-gradient(${stops.join(", ")})`;

  return (
    <section className="bg-white border border-slate-200 rounded-2xl p-5 md:p-6 shadow-sm">
      <h3 className="flex items-center gap-2 text-base md:text-lg font-bold text-slate-900 mb-4">
        <Award className="w-5 h-5 text-indigo-500" />
        성적 분포
      </h3>
      <div className="flex flex-col sm:flex-row items-center gap-6">
        <div className="relative shrink-0">
          <div
            className="w-36 h-36 rounded-full"
            style={{ background: gradient }}
          />
          <div className="absolute inset-6 bg-white rounded-full flex flex-col items-center justify-center shadow-inner">
            <span className="text-2xl font-black text-slate-900">{total}</span>
            <span className="text-[10px] font-bold text-slate-500">총 과목</span>
          </div>
        </div>
        <ul className="flex-1 grid grid-cols-2 gap-2 w-full">
          {legend.map((l) => (
            <li key={l.grade} className="flex items-center gap-2 text-xs">
              <span className="w-3 h-3 rounded-sm shrink-0" style={{ background: l.color }} />
              <span className="font-bold text-slate-700">{l.grade}</span>
              <span className="text-slate-500 ml-auto">
                {l.count}개 · {l.pct.toFixed(0)}%
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
