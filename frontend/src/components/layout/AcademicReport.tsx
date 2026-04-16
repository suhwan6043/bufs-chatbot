"use client";
import { useState, useEffect } from "react";
import { GraduationCap, Upload, Sparkles } from "lucide-react";
import type { Lang, TranscriptAnalysisData } from "@/lib/types";
import { t } from "@/lib/i18n";
import { apiFetch } from "@/lib/api";
import TranscriptUpload from "@/components/layout/TranscriptUpload";
import ProgressGrid from "@/components/report/ProgressGrid";
import SemesterChart from "@/components/report/SemesterChart";
import GradeDonut from "@/components/report/GradeDonut";
import ActionChecklist from "@/components/report/ActionChecklist";
import RetakeTable from "@/components/report/RetakeTable";
import GraduationTimeline from "@/components/report/GraduationTimeline";
import NextTermGuide from "@/components/report/NextTermGuide";

interface AcademicReportProps {
  lang: Lang;
  sessionId: string | null;
  hasTranscript: boolean;
  onAskAI: (question: string) => void;
  onUploaded?: () => void;
}

export default function AcademicReport({ lang, sessionId, hasTranscript, onAskAI, onUploaded }: AcademicReportProps) {
  const [analysis, setAnalysis] = useState<TranscriptAnalysisData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!sessionId || !hasTranscript) return;
    setLoading(true);
    setError("");
    apiFetch<TranscriptAnalysisData>(`/api/transcript/analysis?session_id=${sessionId}`)
      .then((data) => {
        if (!data.has_transcript) {
          setAnalysis(null);
        } else {
          setAnalysis(data);
        }
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "분석 로드 실패");
      })
      .finally(() => setLoading(false));
  }, [sessionId, hasTranscript]);

  // 업로드 필요 상태
  if (!hasTranscript || (!loading && !analysis && !error)) {
    return (
      <div className="flex flex-col items-center py-12 px-6 animate-fade-in space-y-6">
        <div className="text-center">
          <div className="w-20 h-20 bg-blue-50 rounded-3xl flex items-center justify-center mb-4 mx-auto">
            <Upload className="w-10 h-10 text-blue-400" />
          </div>
          <h3 className="text-lg font-bold text-slate-700 mb-2">{t(lang, "report.title")}</h3>
          <p className="text-sm text-slate-500 max-w-sm mx-auto">{t(lang, "report.no_transcript")}</p>
        </div>
        <TranscriptUpload lang={lang} sessionId={sessionId} variant="full" onUploaded={onUploaded} />
      </div>
    );
  }

  if (loading) {
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

  if (error || !analysis) {
    return (
      <div className="flex flex-col items-center py-20 px-6 text-center">
        <p className="text-sm text-red-500">{error || "분석 데이터를 불러오지 못했습니다."}</p>
      </div>
    );
  }

  const s = analysis.summary;
  const p = analysis.profile as Record<string, string | number | null | undefined>;
  const pDept = (p.department as string) || "";
  const pMasked = (p.masked_name as string) || "";
  const pGrade = p.grade;

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-6xl mx-auto animate-slide-up">
      {/* 히어로 */}
      <div className="bg-slate-900 text-white p-6 md:p-8 rounded-[2rem] shadow-2xl overflow-hidden relative">
        <div className="absolute top-0 right-0 p-8 opacity-10 rotate-12">
          <GraduationCap className="w-40 h-40" />
        </div>
        <div className="relative z-10">
          <div className="inline-block px-3 py-1 bg-blue-600 rounded-full text-[10px] font-black uppercase tracking-widest mb-4">
            {t(lang, "report.official")}
          </div>
          <h3 className="text-xl md:text-2xl font-black mb-1">{t(lang, "report.semester")}</h3>
          <p className="text-slate-400 text-sm mb-6 font-medium">
            {pMasked}
            {pDept && <span className="mx-2 text-slate-600">·</span>}
            {pDept}
            {pGrade ? <span className="ml-2 text-slate-500">{String(pGrade)}학년</span> : null}
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label={t(lang, "report.credits_label")} value={`${s.acquired}`} sub={`/ ${s.required}`} />
            <Stat label={t(lang, "report.gpa_label")} value={s.gpa.toFixed(2)} highlight="text-blue-400" />
            <Stat
              label={t(lang, "report.major_label")}
              value={s.shortage <= 0 ? "PASS" : `${s.shortage}`}
              highlight={s.shortage <= 0 ? "text-green-400" : "text-orange-400"}
            />
            <Stat label={lang === "ko" ? "이수율" : "Progress"} value={`${s.progress_pct}%`} highlight="text-blue-400" />
          </div>
        </div>
      </div>

      {/* 액션 체크리스트 (최상단 배치) */}
      <ActionChecklist actions={analysis.actions} onAskAI={onAskAI} />

      {/* 카테고리별 이수 현황 */}
      <ProgressGrid categories={analysis.categories} title={lang === "ko" ? "카테고리별 이수 현황" : "Category Progress"} />

      {/* 학기별 추이 + 성적 분포 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <SemesterChart semesters={analysis.semesters} />
        <GradeDonut distribution={analysis.grade_distribution} />
      </div>

      {/* 재수강 테이블 */}
      <RetakeTable candidates={analysis.retake_candidates} limit={analysis.registration_limit} />

      {/* 졸업 로드맵 + 다음 학기 가이드 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <GraduationTimeline projection={analysis.graduation} semesters={analysis.semesters} />
        <NextTermGuide categories={analysis.categories} limit={analysis.registration_limit} />
      </div>

      {/* AI 가이드 CTA */}
      <div className="p-6 md:p-8 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-[1.5rem] text-white shadow-xl shadow-blue-100 relative overflow-hidden">
        <div className="absolute -right-4 -bottom-4 opacity-20">
          <Sparkles className="w-28 h-28" />
        </div>
        <div className="relative z-10">
          <h4 className="font-black text-lg mb-3 flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-blue-200" />
            {t(lang, "report.ai_guide")}
          </h4>
          <p className="text-blue-50 font-semibold text-sm md:text-base max-w-2xl mb-4">
            {lang === "ko"
              ? "이 분석 결과를 바탕으로 AI에게 질문하면 개인 맞춤 답변을 받을 수 있습니다."
              : "Ask AI based on this analysis to get personalized answers."}
          </p>
          <div className="flex flex-wrap gap-2">
            <AskBtn q="내 졸업까지 남은 학점은?" label="졸업까지 남은 학점" onAskAI={onAskAI} />
            <AskBtn q="내 재수강 대상 과목은?" label="재수강 대상" onAskAI={onAskAI} />
            <AskBtn q="다음 학기 수강 몇 학점까지 가능해?" label="수강 한도" onAskAI={onAskAI} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, sub, highlight }: { label: string; value: string; sub?: string; highlight?: string }) {
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

function AskBtn({ q, label, onAskAI }: { q: string; label: string; onAskAI: (q: string) => void }) {
  return (
    <button
      onClick={() => onAskAI(q)}
      className="px-4 py-2 bg-white text-blue-600 rounded-xl font-bold text-xs hover:bg-blue-50 transition-all shadow-lg"
    >
      {label}
    </button>
  );
}
