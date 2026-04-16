"use client";
import type { GraduationProjection, SemesterSummary } from "@/lib/types";
import { Clock, CheckCircle2, Target } from "lucide-react";

interface Props {
  projection: GraduationProjection;
  semesters: SemesterSummary[];
}

export default function GraduationTimeline({ projection, semesters }: Props) {
  const completed = semesters.length;
  const remaining = projection.semesters_remaining;
  const total = completed + remaining;

  return (
    <section className="bg-white border border-slate-200 rounded-2xl p-5 md:p-6 shadow-sm">
      <h3 className="flex items-center gap-2 text-base md:text-lg font-bold text-slate-900 mb-1">
        <Target className="w-5 h-5 text-emerald-500" />
        졸업 로드맵
      </h3>
      <p className="text-xs text-slate-500 mb-4">
        예정 학기: <strong className="text-slate-800">{projection.expected_term}</strong>
        <span className="mx-2 text-slate-300">·</span>
        남은 학기: <strong className="text-slate-800">{remaining}학기</strong>
      </p>

      {/* 타임라인 */}
      <div className="overflow-x-auto">
        <div className="flex items-center gap-1 pb-2 min-w-max">
          {Array.from({ length: Math.max(total, 1) }, (_, i) => {
            const isCompleted = i < completed;
            const isCurrent = i === completed - 1;
            const term = semesters[i]?.term;
            return (
              <div key={i} className="flex items-center">
                <div className={`w-8 h-8 md:w-10 md:h-10 rounded-full flex items-center justify-center text-[10px] font-black shrink-0 ${
                  isCompleted
                    ? "bg-green-500 text-white"
                    : "bg-slate-200 text-slate-400"
                }`}>
                  {isCompleted ? <CheckCircle2 className="w-4 h-4" /> : i + 1}
                </div>
                {i < total - 1 && (
                  <div className={`h-0.5 w-6 md:w-8 ${i < completed - 1 ? "bg-green-400" : "bg-slate-200"}`} />
                )}
                {term && (
                  <span className="absolute mt-14 -ml-6 md:-ml-8 text-[9px] text-slate-400 font-semibold">
                    {term}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 조기졸업 정보 */}
      <div className="mt-6 pt-4 border-t border-slate-100 space-y-3">
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-blue-500" />
          <span className="text-sm font-bold text-slate-900">조기졸업 판정</span>
          <span className={`ml-auto text-[11px] font-black px-2 py-0.5 rounded-full ${
            projection.can_early_graduate
              ? "bg-green-100 text-green-700"
              : "bg-slate-100 text-slate-500"
          }`}>
            {projection.can_early_graduate ? "가능" : "불가"}
          </span>
        </div>

        {projection.early_eligible_reasons.length > 0 && (
          <div className="pl-6">
            <p className="text-[11px] font-bold text-green-700 mb-1">충족 조건</p>
            <ul className="space-y-0.5">
              {projection.early_eligible_reasons.map((r, i) => (
                <li key={i} className="text-[11px] text-slate-600">• {r}</li>
              ))}
            </ul>
          </div>
        )}
        {projection.early_blocked_reasons.length > 0 && (
          <div className="pl-6">
            <p className="text-[11px] font-bold text-amber-700 mb-1">미충족 조건</p>
            <ul className="space-y-0.5">
              {projection.early_blocked_reasons.map((r, i) => (
                <li key={i} className="text-[11px] text-slate-600">• {r}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
