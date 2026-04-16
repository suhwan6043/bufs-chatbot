"use client";
import type { SemesterSummary } from "@/lib/types";
import { TrendingUp } from "lucide-react";

interface Props {
  semesters: SemesterSummary[];
}

export default function SemesterChart({ semesters }: Props) {
  if (!semesters.length) return null;

  const maxCredits = Math.max(...semesters.map((s) => s.credits), 1);

  return (
    <section className="bg-white border border-slate-200 rounded-2xl p-5 md:p-6 shadow-sm">
      <h3 className="flex items-center gap-2 text-base md:text-lg font-bold text-slate-900 mb-4">
        <TrendingUp className="w-5 h-5 text-blue-500" />
        학기별 수강 추이
      </h3>

      <div className="space-y-3">
        {semesters.map((s) => {
          const widthPct = Math.max(4, (s.credits / maxCredits) * 100);
          // GPA 색상: 3.5 이상=파랑, 3.0 이상=초록, 그 외=주황 (객관: 단순 구간)
          const gpaColor = s.gpa == null
            ? "bg-slate-300"
            : s.gpa >= 3.5
              ? "bg-blue-500"
              : s.gpa >= 3.0
                ? "bg-green-500"
                : "bg-amber-500";
          return (
            <div key={s.term} className="flex items-center gap-3">
              <div className="w-16 text-xs font-bold text-slate-600 shrink-0">{s.term}</div>
              <div className="flex-1 h-7 bg-slate-100 rounded-lg relative overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-blue-400 to-blue-500 rounded-lg flex items-center px-2"
                  style={{ width: `${widthPct}%` }}
                >
                  <span className="text-[10px] font-black text-white">{s.credits}학점</span>
                </div>
                <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${gpaColor}`} />
                  <span className="text-[10px] font-bold text-slate-600">
                    GPA {s.gpa != null ? s.gpa.toFixed(2) : "—"}
                  </span>
                  <span className="text-[10px] text-slate-400">· {s.course_count}과목</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
