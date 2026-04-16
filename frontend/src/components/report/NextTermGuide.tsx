"use client";
import type { AnalysisCategory } from "@/lib/types";
import { CalendarPlus } from "lucide-react";

interface Props {
  categories: AnalysisCategory[];
  limit: Record<string, unknown>;
}

export default function NextTermGuide({ categories, limit }: Props) {
  const shortageCats = categories.filter((c) => c.shortage > 0);
  const applied = (limit["적용_최대학점"] as number) || (limit["기본_최대학점"] as number) || null;
  const basic = limit["기본_최대학점"] as number | null;
  const extended = limit["우수_최대학점"] as number | null;

  if (shortageCats.length === 0 && !applied) return null;

  return (
    <section className="bg-gradient-to-br from-blue-600 to-indigo-700 text-white rounded-2xl p-5 md:p-6 shadow-xl shadow-blue-100">
      <h3 className="flex items-center gap-2 text-base md:text-lg font-bold mb-3">
        <CalendarPlus className="w-5 h-5" />
        다음 학기 수강 가이드
      </h3>

      {applied && (
        <div className="mb-4 p-3 bg-white/10 backdrop-blur-sm rounded-lg border border-white/20">
          <p className="text-xs font-semibold text-blue-100 mb-1">수강신청 한도</p>
          <p className="text-xl font-black">
            최대 <span className="text-amber-300">{applied}</span>학점
          </p>
          {basic && extended && (
            <p className="text-[10px] text-blue-200 mt-1">
              기본 {basic} · 평점 우수 시 {extended}학점
            </p>
          )}
        </div>
      )}

      {shortageCats.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-blue-100 mb-2">우선 수강 권장 영역</p>
          <ul className="space-y-1.5">
            {shortageCats.slice(0, 5).map((c) => (
              <li key={c.name} className="flex items-center justify-between p-2 bg-white/5 rounded-lg">
                <span className="text-sm font-semibold truncate pr-2">
                  {c.is_required && <span className="text-amber-300 mr-1">★</span>}
                  {c.name}
                </span>
                <span className="text-xs font-black shrink-0">
                  {c.shortage}학점 필요
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
