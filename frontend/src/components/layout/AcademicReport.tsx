"use client";
import { useState, useEffect } from "react";
import { GraduationCap, CheckCircle2, AlertCircle, Sparkles, Upload } from "lucide-react";
import type { Lang, TranscriptStatus } from "@/lib/types";
import { t } from "@/lib/i18n";
import { apiFetch } from "@/lib/api";

interface AcademicReportProps {
  lang: Lang;
  sessionId: string | null;
  hasTranscript: boolean;
  onAskAI: (question: string) => void;
}

export default function AcademicReport({ lang, sessionId, hasTranscript, onAskAI }: AcademicReportProps) {
  const [status, setStatus] = useState<TranscriptStatus | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sessionId || !hasTranscript) return;
    setLoading(true);
    apiFetch<TranscriptStatus>(`/api/transcript/status?session_id=${sessionId}`)
      .then(setStatus)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sessionId, hasTranscript]);

  // No transcript — show upload CTA
  if (!hasTranscript) {
    return (
      <div className="flex flex-col items-center justify-center py-20 px-6 text-center animate-fade-in">
        <div className="w-20 h-20 bg-blue-50 rounded-3xl flex items-center justify-center mb-6">
          <Upload className="w-10 h-10 text-blue-400" />
        </div>
        <h3 className="text-lg font-bold text-slate-700 mb-2">{t(lang, "report.title")}</h3>
        <p className="text-sm text-slate-500 max-w-sm">{t(lang, "report.no_transcript")}</p>
      </div>
    );
  }

  if (loading || !status) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex gap-2">
          <div className="w-2.5 h-2.5 bg-blue-400 rounded-full animate-bounce" />
          <div className="w-2.5 h-2.5 bg-blue-400 rounded-full animate-bounce [animation-delay:0.2s]" />
          <div className="w-2.5 h-2.5 bg-blue-400 rounded-full animate-bounce [animation-delay:0.4s]" />
        </div>
      </div>
    );
  }

  const progressPct = status.progress_pct;
  const isMet = status.total_shortage <= 0;

  return (
    <div className="p-4 md:p-6 space-y-6 animate-slide-up max-w-4xl mx-auto">
      {/* Dark hero card */}
      <div className="bg-slate-900 text-white p-6 md:p-8 rounded-[2rem] shadow-2xl overflow-hidden relative">
        <div className="absolute top-0 right-0 p-8 opacity-10 rotate-12">
          <GraduationCap className="w-40 h-40" />
        </div>
        <div className="relative z-10">
          <div className="inline-block px-3 py-1 bg-blue-600 rounded-full text-[10px] font-black uppercase tracking-widest mb-4">
            {t(lang, "report.official")}
          </div>
          <h3 className="text-xl md:text-2xl font-black mb-1">{t(lang, "report.semester")}</h3>
          {status.masked_name && (
            <p className="text-slate-400 text-sm mb-6 font-medium">{status.masked_name}</p>
          )}

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label={t(lang, "report.credits_label")} value={`${status.total_acquired}`} sub={`/ ${status.total_required}`} />
            <StatCard label={t(lang, "report.gpa_label")} value={status.gpa.toFixed(2)} highlight="text-blue-400" />
            <StatCard
              label={t(lang, "report.major_label")}
              value={isMet ? "PASS" : `${status.total_shortage}`}
              highlight={isMet ? "text-green-400 italic" : "text-orange-400"}
            />
            <StatCard label={lang === "ko" ? "이수율" : "Progress"} value={`${progressPct}%`} highlight="text-blue-400" />
          </div>
        </div>
      </div>

      {/* Met / Unmet requirements */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Met */}
        <div className="p-6 border border-slate-100 rounded-[1.5rem] bg-slate-50 shadow-sm">
          <h4 className="font-bold flex items-center gap-2 mb-4 text-slate-900">
            <div className="w-7 h-7 bg-green-100 rounded-lg flex items-center justify-center">
              <CheckCircle2 className="w-4 h-4 text-green-600" />
            </div>
            {t(lang, "report.met")}
          </h4>
          <ul className="space-y-3 text-sm text-slate-600 font-semibold">
            {progressPct >= 50 && <ReqItem label={lang === "ko" ? "전공 필수 과목 이수" : "Major required courses"} status="YES" />}
            {status.gpa >= 2.0 && <ReqItem label={lang === "ko" ? "최소 평점 기준 충족" : "Minimum GPA requirement"} status="YES" />}
            {status.total_acquired >= 60 && <ReqItem label={lang === "ko" ? "60학점 이상 이수" : "60+ credits completed"} status="DONE" />}
          </ul>
        </div>

        {/* Unmet */}
        <div className="p-6 border border-orange-100 rounded-[1.5rem] bg-orange-50/30 shadow-sm">
          <h4 className="font-bold flex items-center gap-2 mb-4 text-slate-900">
            <div className="w-7 h-7 bg-orange-100 rounded-lg flex items-center justify-center">
              <AlertCircle className="w-4 h-4 text-orange-600" />
            </div>
            {t(lang, "report.unmet")}
          </h4>
          <ul className="space-y-3 text-sm text-slate-600 font-semibold">
            {status.total_shortage > 0 && (
              <ReqItem
                label={lang === "ko" ? "잔여 졸업 학점" : "Remaining credits"}
                status={`${status.total_shortage} ${lang === "ko" ? "학점" : "cr"}`}
                warn
              />
            )}
            {status.dual_shortage > 0 && (
              <ReqItem
                label={lang === "ko" ? `복수전공 부족 (${status.dual_major})` : `Dual major shortage (${status.dual_major})`}
                status={`${status.dual_shortage} ${lang === "ko" ? "학점" : "cr"}`}
                warn
              />
            )}
            {status.total_required - status.total_acquired > 20 && (
              <ReqItem
                label={lang === "ko" ? "교양 필수 영역 확인 필요" : "Liberal arts requirement check needed"}
                status={lang === "ko" ? "확인" : "CHECK"}
                warn
              />
            )}
          </ul>
        </div>
      </div>

      {/* AI Guide */}
      <div className="p-6 md:p-8 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-[1.5rem] text-white shadow-xl shadow-blue-100 relative overflow-hidden group">
        <div className="absolute -right-4 -bottom-4 opacity-20 group-hover:scale-110 transition-transform">
          <Sparkles className="w-28 h-28" />
        </div>
        <div className="relative z-10">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles className="w-5 h-5 text-blue-200" />
            <h4 className="font-black text-lg uppercase tracking-tight">{t(lang, "report.ai_guide")}</h4>
          </div>
          <p className="text-blue-50 leading-relaxed font-semibold text-base max-w-2xl">
            {lang === "ko"
              ? `현재 ${status.total_shortage}학점이 부족합니다. AI에게 졸업 요건을 분석해달라고 요청해보세요.`
              : `You need ${status.total_shortage} more credits. Ask AI to analyze your graduation requirements.`}
          </p>
          <button
            onClick={() => onAskAI(t(lang, "qf.graduation_q"))}
            className="mt-5 px-5 py-2.5 bg-white text-blue-600 rounded-xl font-bold text-sm hover:bg-blue-50 transition-all shadow-lg"
          >
            {t(lang, "report.view_notice")}
          </button>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, sub, highlight }: { label: string; value: string; sub?: string; highlight?: string }) {
  return (
    <div className="bg-white/5 border border-white/10 p-4 rounded-2xl backdrop-blur-sm">
      <p className="text-[10px] font-bold text-slate-400 uppercase mb-1.5">{label}</p>
      <p className={`text-xl font-black ${highlight || ""}`}>
        {value}
        {sub && <span className="text-xs text-slate-500 font-bold ml-1">{sub}</span>}
      </p>
    </div>
  );
}

function ReqItem({ label, status, warn }: { label: string; status: string; warn?: boolean }) {
  return (
    <li className="flex justify-between items-center py-1.5 border-b border-slate-200/50 last:border-0">
      <span>{label}</span>
      <span className={`font-bold ${warn ? "text-orange-600" : "text-green-600"}`}>{status}</span>
    </li>
  );
}
