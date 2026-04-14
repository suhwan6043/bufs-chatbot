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

interface Props { lang: Lang; sessionId: string | null }

export default function TranscriptUpload({ lang, sessionId }: Props) {
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

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !sessionId) return;
    setUploading(true); setError("");

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`${BASE_URL}/api/transcript/upload?session_id=${sessionId}`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!data.ok) { setError(data.error || "업로드 실패"); }
      else { await fetchStatus(); }
    } catch (err) {
      setError("업로드 중 오류가 발생했습니다.");
    } finally { setUploading(false); }
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

  // Upload form
  return (
    <div className="px-3 pt-3">
      <p className="text-[0.7rem] font-bold text-muted uppercase tracking-wider mb-1.5">{t(lang, "sidebar.transcript")}</p>
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
            accept=".xls,.pdf,.doc,.docx,.ppt,.pptx,.hwp,.png,.jpg,.jpeg"
            className="block w-full text-xs text-text-sub file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-medium file:bg-accent file:text-white hover:file:bg-accent/90" />
          {uploading && <p className="text-xs text-accent mt-1 animate-pulse">업로드 중...</p>}
          {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
        </div>
      )}
    </div>
  );
}
