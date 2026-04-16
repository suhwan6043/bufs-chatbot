"use client";
import type { RetakeCandidate } from "@/lib/types";
import { RotateCcw } from "lucide-react";

interface Props {
  candidates: RetakeCandidate[];
  limit: Record<string, unknown>;
}

export default function RetakeTable({ candidates, limit }: Props) {
  if (!candidates.length) {
    return null;
  }

  const basic = limit["기본_최대학점"] as number | null;
  const extended = limit["우수_최대학점"] as number | null;
  const currentGpa = limit["현재_평점"] as number | undefined;

  return (
    <section className="bg-white border border-slate-200 rounded-2xl p-5 md:p-6 shadow-sm">
      <h3 className="flex items-center gap-2 text-base md:text-lg font-bold text-slate-900 mb-1">
        <RotateCcw className="w-5 h-5 text-purple-500" />
        재수강 후보
      </h3>
      <p className="text-xs text-slate-500 mb-4">성적 낮은 순 · 평점 향상·졸업 학점 영향 검토용</p>

      {/* 규정 배너 */}
      {(basic || extended) && (
        <div className="mb-4 p-3 bg-purple-50 border border-purple-100 rounded-lg">
          <p className="text-xs text-purple-900 font-semibold mb-1">📘 재수강 규정 (graph 기반)</p>
          <ul className="text-[11px] text-purple-700 space-y-0.5">
            {basic && <li>· 한 학기 최대 수강: {basic}학점 (우수 시 {extended || "—"}학점)</li>}
            {currentGpa !== undefined && <li>· 현재 평점: {currentGpa}</li>}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] font-bold text-slate-500 uppercase border-b border-slate-200">
              <th className="pb-2 pr-2">과목명</th>
              <th className="pb-2 pr-2">이수학기</th>
              <th className="pb-2 pr-2 text-center">학점</th>
              <th className="pb-2 pr-2 text-center">성적</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c, idx) => (
              <tr key={`${c.course}-${idx}`} className="border-b border-slate-100 last:border-0">
                <td className="py-2 pr-2 font-semibold text-slate-800 truncate max-w-[200px]" title={c.course}>
                  {c.course}
                </td>
                <td className="py-2 pr-2 text-slate-500 text-xs">{c.term}</td>
                <td className="py-2 pr-2 text-center text-slate-600">{c.credits}</td>
                <td className="py-2 pr-2 text-center">
                  <span className={`inline-block px-2 py-0.5 rounded font-bold text-[11px] ${gradeBgColor(c.grade)}`}>
                    {c.grade}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function gradeBgColor(grade: string): string {
  if (grade.startsWith("A")) return "bg-blue-100 text-blue-800";
  if (grade.startsWith("B")) return "bg-green-100 text-green-800";
  if (grade.startsWith("C")) return "bg-amber-100 text-amber-800";
  if (grade.startsWith("D")) return "bg-orange-100 text-orange-800";
  if (grade === "F") return "bg-red-100 text-red-800";
  return "bg-slate-100 text-slate-600";
}
