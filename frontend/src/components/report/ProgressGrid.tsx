"use client";
import type { AnalysisCategory } from "@/lib/types";
import { CheckCircle2, AlertCircle } from "lucide-react";

interface Props {
  categories: AnalysisCategory[];
  title?: string;
}

export default function ProgressGrid({ categories, title }: Props) {
  if (!categories.length) return null;

  return (
    <section className="bg-white border border-slate-200 rounded-2xl p-5 md:p-6 shadow-sm">
      <h3 className="text-base md:text-lg font-bold text-slate-900 mb-4">
        {title || "카테고리별 이수 현황"}
      </h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {categories.map((c) => {
          const met = c.shortage <= 0;
          const severityColor = met
            ? "from-green-500 to-emerald-500"
            : c.is_required
              ? "from-red-500 to-orange-500"
              : "from-amber-500 to-orange-400";
          const barBg = met ? "bg-green-100" : c.is_required ? "bg-red-50" : "bg-amber-50";
          return (
            <div key={c.name} className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  {met ? (
                    <CheckCircle2 className="w-4 h-4 text-green-600 shrink-0" />
                  ) : (
                    <AlertCircle className={`w-4 h-4 shrink-0 ${c.is_required ? "text-red-600" : "text-amber-600"}`} />
                  )}
                  <span className="text-sm font-semibold text-slate-800 truncate" title={c.name}>
                    {c.name}
                    {c.is_required && <span className="ml-1 text-[10px] text-red-500 font-black">★</span>}
                  </span>
                </div>
                <span className="text-xs font-bold text-slate-500 shrink-0">
                  {c.acquired}/{c.required}
                </span>
              </div>
              <div className={`h-2 rounded-full overflow-hidden ${barBg}`}>
                <div
                  className={`h-full bg-gradient-to-r ${severityColor} transition-all`}
                  style={{ width: `${Math.min(100, c.progress_pct)}%` }}
                />
              </div>
              <div className="flex justify-between text-[11px]">
                <span className={met ? "text-green-600 font-semibold" : "text-slate-500"}>
                  {c.progress_pct}% 이수
                </span>
                {!met && (
                  <span className={`font-bold ${c.is_required ? "text-red-600" : "text-amber-600"}`}>
                    -{c.shortage}학점
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-4 text-[10px] text-slate-400">★ 필수 영역 (미충족 시 졸업 불가)</p>
    </section>
  );
}
