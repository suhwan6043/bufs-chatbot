"use client";
import { useState, useEffect, useCallback } from "react";
import type { Lang } from "@/lib/types";
import { t } from "@/lib/i18n";
import { apiFetch, BASE_URL } from "@/lib/api";

interface TranscriptStatus {
  has_transcript: boolean;
  remaining_seconds: number;
  masked_name: string;
  gpa: number;
  total_acquired: number;
  total_required: number;
  total_shortage: number;
  progress_pct: number;
}

interface Props {
  lang: Lang;
  sessionId: string | null;
  /** "compact" (기본, Sidebar) | "full" (AcademicReport 탭 대형 CTA) */
  variant?: "compact" | "full";
  /** 업로드 성공 시 부모 콜백 — 세션 상태 새로고침 트리거 */
  onUploaded?: () => void;
}

// 지원 파일 형식 — 파서가 실제 지원하는 5가지만 (원칙 4: 하드코딩 최소화)
const SUPPORTED_FORMATS = [
  { ext: "PDF", colorHex: "#dc2626", bg: "bg-red-50", border: "border-red-200" },
  { ext: "DOC", colorHex: "#2563eb", bg: "bg-blue-50", border: "border-blue-200" },
  { ext: "XLS", colorHex: "#16a34a", bg: "bg-green-50", border: "border-green-200" },
  { ext: "PPT", colorHex: "#ea580c", bg: "bg-orange-50", border: "border-orange-200" },
  { ext: "HWP", colorHex: "#0891b2", bg: "bg-cyan-50", border: "border-cyan-200" },
];

export default function TranscriptUpload({ lang, sessionId, variant = "compact", onUploaded }: Props) {
  const [status, setStatus] = useState<TranscriptStatus | null>(null);
  const [consented, setConsented] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  const fetchStatus = useCallback(async () => {
    if (!sessionId) return;
    try {
      const data = await apiFetch<TranscriptStatus>(`/api/transcript/status?session_id=${sessionId}`);
      setStatus(data);
    } catch { /* ignore */ }
  }, [sessionId]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  // Auto-refresh countdown
  useEffect(() => {
    if (!status?.has_transcript) return;
    const id = setInterval(fetchStatus, 30000);
    return () => clearInterval(id);
  }, [status?.has_transcript, fetchStatus]);

  const [uploadStage, setUploadStage] = useState<"" | "consent" | "upload" | "parse" | "done">("");

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !sessionId) return;
    setUploading(true); setError(""); setUploadStage("consent");

    const formData = new FormData();
    formData.append("file", file);

    try {
      const token = typeof window !== "undefined"
        ? localStorage.getItem("camchat_auth_token")
        : null;
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;

      // 1) consent 서버 전달
      await fetch(`${BASE_URL}/api/transcript/consent?session_id=${sessionId}&consent=true`, {
        method: "POST",
        headers,
      });

      // 2) 파일 업로드 + 파싱
      setUploadStage("upload");
      const res = await fetch(`${BASE_URL}/api/transcript/upload?session_id=${sessionId}`, {
        method: "POST",
        body: formData,
        headers,
      });
      const data = await res.json();
      if (!data.ok) {
        setError(data.error || "업로드 실패");
        setUploadStage("");
      } else {
        setUploadStage("parse");
        await fetchStatus();
        setUploadStage("done");
        onUploaded?.();
      }
    } catch (err) {
      setError("업로드 중 오류가 발생했습니다.");
      setUploadStage("");
    } finally { setUploading(false); }
  };

  // 진행 단계 표시 문구
  const stageLabel = (): string => {
    if (uploadStage === "consent") return lang === "ko" ? "동의 확인 중..." : "Confirming consent...";
    if (uploadStage === "upload") return lang === "ko" ? "파일 업로드 중..." : "Uploading file...";
    if (uploadStage === "parse") return lang === "ko" ? "성적표 파싱 중..." : "Parsing transcript...";
    if (uploadStage === "done") return lang === "ko" ? "완료! 요약을 불러오는 중..." : "Done! Loading summary...";
    return "";
  };

  const handleDelete = async () => {
    if (!sessionId) return;
    await apiFetch(`/api/transcript?session_id=${sessionId}`, { method: "DELETE" });
    setStatus(null);
    setConsented(false);
  };

  // Transcript loaded — show summary
  if (status?.has_transcript) {
    const remaining = Math.max(0, Math.floor(status.remaining_seconds / 60));
    return (
      <div className="px-3 pt-3">
        <p className="text-[0.7rem] font-bold text-muted uppercase tracking-wider mb-1.5">{t(lang, "sidebar.transcript")}</p>
        <div className="p-2.5 rounded-lg bg-green-50 border border-green-200">
          <div className="flex items-baseline justify-between">
            <span className="text-sm font-bold text-green-800">{status.masked_name}</span>
            <span className="text-xs text-green-600">{t(lang, "sidebar.gpa", { v: String(status.gpa) })}</span>
          </div>
          {/* Progress bar */}
          <div className="mt-2 h-1.5 bg-green-100 rounded-full overflow-hidden">
            <div className="h-full bg-gradient-to-r from-green-500 to-green-600 rounded-full" style={{ width: `${status.progress_pct}%` }} />
          </div>
          <p className="text-[0.7rem] text-green-700 mt-1">
            {t(lang, "sidebar.credits_fmt", { done: String(status.total_acquired), total: String(status.total_required), pct: String(status.progress_pct) })}
          </p>
          {status.total_shortage > 0 ? (
            <p className="text-[0.7rem] text-amber-700 bg-amber-50 rounded px-1.5 py-0.5 mt-1">{t(lang, "sidebar.shortage", { n: String(status.total_shortage) })}</p>
          ) : (
            <p className="text-[0.7rem] text-green-700 bg-green-50 rounded px-1.5 py-0.5 mt-1">{t(lang, "sidebar.req_met")}</p>
          )}
        </div>
        <div className="flex gap-2 mt-2">
          <button onClick={fetchStatus} className="flex-1 py-1.5 text-xs text-text-sub border border-border rounded-lg hover:bg-gray-50">{t(lang, "sidebar.btn_refresh")}</button>
          <button onClick={handleDelete} className="flex-1 py-1.5 text-xs text-red-500 border border-red-200 rounded-lg hover:bg-red-50">{t(lang, "sidebar.btn_delete")}</button>
        </div>
        <p className="text-[0.65rem] text-muted mt-1">{t(lang, "sidebar.auto_delete", { m: String(remaining) })}</p>
      </div>
    );
  }

  // Full variant — AcademicReport 탭 대형 CTA
  if (variant === "full") {
    return (
      <div className="max-w-md mx-auto w-full">
        <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
          <h3 className="text-lg font-bold text-slate-900 mb-2">
            {lang === "ko" ? "학업성적사정표 업로드" : "Upload transcript"}
          </h3>
          <p className="text-sm text-slate-500 mb-4">
            {lang === "ko"
              ? "학생포털에서 다운받은 파일을 업로드하면 졸업 요건·부족 학점을 자동 분석합니다."
              : "Upload transcript from student portal to analyze graduation requirements."}
          </p>

          {/* 지원 파일 형식 (이미지 스타일) */}
          <div className="flex gap-2 mb-4 flex-wrap">
            {SUPPORTED_FORMATS.map((f) => (
              <div
                key={f.ext}
                className={`${f.bg} ${f.border} border rounded-lg px-3 py-1.5 flex items-center gap-1.5`}
              >
                <span className="text-xs font-black" style={{ color: f.colorHex }}>
                  {f.ext}
                </span>
              </div>
            ))}
          </div>

          <div className="p-3 bg-amber-50 border border-amber-300 rounded-lg mb-4">
            <p className="text-xs text-amber-800 leading-relaxed">
              {t(lang, "sidebar.privacy_html")}
            </p>
          </div>

          <label className="flex items-center gap-2 cursor-pointer mb-4">
            <input
              type="checkbox"
              checked={consented}
              onChange={(e) => setConsented(e.target.checked)}
              className="w-4 h-4 rounded"
            />
            <span className="text-sm text-slate-700 font-medium">
              {t(lang, "sidebar.consent")}
            </span>
          </label>

          {consented ? (
            <label className={`block w-full text-center py-3 rounded-xl font-bold text-sm cursor-pointer transition-all ${
              uploading
                ? "bg-slate-200 text-slate-500 cursor-wait"
                : "bg-blue-600 text-white hover:bg-blue-700 shadow-lg shadow-blue-100 active:scale-95"
            }`}>
              {uploading
                ? stageLabel() || (lang === "ko" ? "처리 중..." : "Processing...")
                : (lang === "ko" ? "파일 선택하여 업로드" : "Choose file to upload")}
              <input
                type="file"
                onChange={handleUpload}
                disabled={uploading}
                accept=".xls,.xlsx,.pdf,.doc,.docx,.ppt,.pptx,.hwp"
                className="hidden"
              />
            </label>
          ) : (
            <div className="w-full text-center py-3 rounded-xl font-bold text-sm bg-slate-100 text-slate-400 cursor-not-allowed">
              {lang === "ko" ? "동의 후 업로드 가능" : "Consent required"}
            </div>
          )}

          {/* 진행 단계 표시 */}
          {uploading && (
            <div className="mt-4 flex items-center gap-3 p-3 bg-blue-50 border border-blue-100 rounded-xl">
              <div className="flex gap-1 shrink-0">
                <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" />
                <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce [animation-delay:0.15s]" />
                <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce [animation-delay:0.3s]" />
              </div>
              <p className="text-sm text-blue-700 font-semibold">{stageLabel()}</p>
            </div>
          )}

          {error && (
            <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-xl">
              <p className="text-sm text-red-600 font-semibold">{error}</p>
            </div>
          )}
        </div>
      </div>
    );
  }

  // Compact variant (기본, Sidebar용)
  return (
    <div className="px-3 pt-3">
      <p className="text-[0.7rem] font-bold text-muted uppercase tracking-wider mb-1.5">{t(lang, "sidebar.transcript")}</p>

      {/* 지원 파일 형식 시각 표시 */}
      <div className="flex gap-1.5 mb-2 flex-wrap">
        {SUPPORTED_FORMATS.map((f) => (
          <div
            key={f.ext}
            className={`${f.bg} ${f.border} border rounded-md px-1.5 py-0.5 flex items-center gap-1`}
            title={f.ext}
          >
            <span className="text-[9px] font-black" style={{ color: f.colorHex }}>
              {f.ext}
            </span>
          </div>
        ))}
      </div>

      <div className="p-2.5 bg-amber-50 border border-amber-300 rounded-lg mb-2">
        <p className="text-[0.7rem] text-amber-800 leading-relaxed">{t(lang, "sidebar.privacy_html")}</p>
      </div>
      <label className="flex items-center gap-2 cursor-pointer mb-2">
        <input type="checkbox" checked={consented} onChange={(e) => setConsented(e.target.checked)} className="w-3.5 h-3.5 rounded" />
        <span className="text-xs text-text">{t(lang, "sidebar.consent")}</span>
      </label>
      {consented && (
        <div>
          <input type="file" onChange={handleUpload} disabled={uploading}
            accept=".xls,.xlsx,.pdf,.doc,.docx,.ppt,.pptx,.hwp"
            className="block w-full text-xs text-text-sub file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-medium file:bg-accent file:text-white hover:file:bg-accent/90 disabled:opacity-60" />

          {/* 진행 단계 표시 (compact) */}
          {uploading && (
            <div className="mt-2 flex items-center gap-2 p-2 bg-blue-50 border border-blue-100 rounded-lg">
              <div className="flex gap-1 shrink-0">
                <div className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" />
                <div className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce [animation-delay:0.15s]" />
                <div className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce [animation-delay:0.3s]" />
              </div>
              <p className="text-[0.7rem] text-blue-700 font-semibold">{stageLabel()}</p>
            </div>
          )}

          {error && (
            <div className="mt-2 p-2 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-[0.7rem] text-red-600 font-semibold">{error}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
