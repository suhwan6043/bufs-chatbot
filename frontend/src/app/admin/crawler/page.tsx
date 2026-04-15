"use client";
import { useState, useEffect } from "react";
import { useAdmin, type CrawlerStatus, type AttachmentStatus } from "@/hooks/useAdmin";

interface HistoryRecord {
  timestamp?: string; job_id?: string;
  added?: number; updated?: number; deleted?: number; skipped?: number;
  errors?: string[]; duration_ms?: number;
}

export default function CrawlerPage() {
  const { token, fetchCrawler, triggerCrawl, resetHashes, reingest, fetchCrawlHistory, fetchNotices, fetchAttachments } = useAdmin();

  const [status, setStatus] = useState<CrawlerStatus | null>(null);
  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [notices, setNotices] = useState<Record<string, unknown>[]>([]);
  const [attachments, setAttachments] = useState<AttachmentStatus | null>(null);
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [showErrors, setShowErrors] = useState(false);

  const reload = async () => {
    try {
      const [s, h, n, a] = await Promise.all([
        fetchCrawler(), fetchCrawlHistory(), fetchNotices(), fetchAttachments(),
      ]);
      setStatus(s);
      setHistory((h.records || []) as HistoryRecord[]);
      setNotices(n.notices || []);
      setAttachments(a);
    } catch {}
  };

  useEffect(() => { if (token) reload(); }, [token]);

  const act = async (fn: () => Promise<unknown>, label: string) => {
    setLoading(true); setMsg("");
    try { await fn(); setMsg(`${label} 완료`); await reload(); }
    catch (e) { setMsg(`오류: ${e}`); }
    finally { setLoading(false); }
  };

  if (!token) return null;

  const lastRun = history[0];
  const errorRecords = history.slice(0, 10).filter((r) => r.errors?.length);

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-lg font-bold text-navy">크롤러 관리</h1>

      {/* Status */}
      {status && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[
            { l: "상태", v: status.enabled ? "활성" : "비활성", c: status.enabled ? "text-green-600" : "text-red-500" },
            { l: "실행 중", v: status.is_running ? "예" : "아니오", c: status.is_running ? "text-orange-500" : "" },
            { l: "간격", v: `${status.interval_minutes}분`, c: "" },
            { l: "다음 실행", v: status.next_run || "-", c: "" },
            { l: "공지 수", v: String(status.notice_count), c: "" },
          ].map((s) => (
            <div key={s.l} className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
              <div className="text-xs text-muted">{s.l}</div>
              <div className={`text-sm font-medium mt-1 ${s.c}`}>{s.v}</div>
            </div>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-3">
        <button onClick={() => act(triggerCrawl, "수동 크롤링")} disabled={loading}
          className="px-4 py-2 text-sm bg-accent text-white rounded-lg hover:bg-accent/90 disabled:opacity-50">수동 크롤링</button>
        <button onClick={() => act(resetHashes, "해시 초기화")} disabled={loading}
          className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-gray-50 disabled:opacity-50">해시 초기화</button>
        <button onClick={() => act(reingest, "전체 재인제스트")} disabled={loading}
          className="px-4 py-2 text-sm border border-red-200 text-red-500 rounded-lg hover:bg-red-50 disabled:opacity-50">전체 재인제스트</button>
      </div>
      {msg && <p className="text-xs text-green-600 bg-green-50 p-2 rounded">{msg}</p>}

      {/* Crawl History Summary */}
      {history.length > 0 && lastRun && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
            <div className="text-xs text-muted">총 실행 횟수</div>
            <div className="text-lg font-bold mt-1">{history.length}</div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
            <div className="text-xs text-muted">마지막 실행</div>
            <div className="text-sm font-medium mt-1">{(lastRun.timestamp || "").slice(0, 16)}</div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
            <div className="text-xs text-muted">마지막 추가/수정</div>
            <div className="text-lg font-bold mt-1">{(lastRun.added || 0) + (lastRun.updated || 0)}건</div>
          </div>
          <div className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
            <div className="text-xs text-muted">마지막 오류</div>
            <div className={`text-lg font-bold mt-1 ${(lastRun.errors?.length || 0) > 0 ? "text-red-500" : ""}`}>
              {lastRun.errors?.length || 0}건
            </div>
          </div>
        </div>
      )}

      {/* Structured History Table */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
        <h2 className="text-sm font-bold text-text mb-3">실행 이력 (최신순, 최대 20건)</h2>
        {history.length === 0 ? <p className="text-xs text-muted">히스토리 없음</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-left text-muted">
                  <th className="py-1.5 pr-3">시각</th><th className="py-1.5 pr-3">잡ID</th>
                  <th className="py-1.5 pr-3">추가</th><th className="py-1.5 pr-3">수정</th>
                  <th className="py-1.5 pr-3">삭제</th><th className="py-1.5 pr-3">건너뜀</th>
                  <th className="py-1.5 pr-3">오류</th><th className="py-1.5">소요(초)</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 20).map((r, i) => (
                  <tr key={i} className="border-b border-border/50">
                    <td className="py-1.5 pr-3 text-muted whitespace-nowrap">{(r.timestamp || "").slice(0, 16)}</td>
                    <td className="py-1.5 pr-3 text-text-sub">{r.job_id || "-"}</td>
                    <td className="py-1.5 pr-3">{r.added || 0}</td>
                    <td className="py-1.5 pr-3">{r.updated || 0}</td>
                    <td className="py-1.5 pr-3">{r.deleted || 0}</td>
                    <td className="py-1.5 pr-3">{r.skipped || 0}</td>
                    <td className={`py-1.5 pr-3 ${(r.errors?.length || 0) > 0 ? "text-red-500 font-medium" : ""}`}>{r.errors?.length || 0}</td>
                    <td className="py-1.5">{((r.duration_ms || 0) / 1000).toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Error Detail */}
      {errorRecords.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
          <button onClick={() => setShowErrors(!showErrors)} className="text-sm text-red-500 hover:underline">
            {showErrors ? "▼" : "▶"} 최근 오류 상세 ({errorRecords.length}건)
          </button>
          {showErrors && (
            <div className="mt-3 space-y-2">
              {errorRecords.map((r, i) => (
                <div key={i}>
                  <div className="text-xs font-medium text-text">{(r.timestamp || "").slice(0, 16)}</div>
                  {r.errors?.map((e, j) => (
                    <div key={j} className="text-xs text-muted ml-3">- {e.slice(0, 120)}</div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Attachment Status */}
      {attachments && (
        <div>
          <h2 className="text-sm font-bold text-text mb-3">첨부파일 다운로드 현황</h2>
          <div className="grid grid-cols-3 gap-3">
            {(["pdf", "hwp", "other"] as const).map((key) => {
              const label = key === "pdf" ? "PDF" : key === "hwp" ? "HWP" : "기타";
              const d = attachments[key];
              return (
                <div key={key} className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
                  <div className="text-xs text-muted">{label} 파일</div>
                  <div className="text-lg font-bold mt-1">{d.count}개</div>
                  <div className="text-xs text-muted">{d.total_kb}KB</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Notices */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
        <h2 className="text-sm font-bold text-text mb-3">추적 중인 공지 ({notices.length}건)</h2>
        <div className="overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-left text-muted sticky top-0 bg-white">
                <th className="py-1.5 pr-3">제목</th>
                <th className="py-1.5 pr-3">게시일</th>
                <th className="py-1.5 pr-3">학기</th>
                <th className="py-1.5 pr-3">최초 수집</th>
                <th className="py-1.5 pr-3">최근 확인</th>
                <th className="py-1.5">URL</th>
              </tr>
            </thead>
            <tbody>
              {notices.map((n, i) => (
                <tr key={i} className="border-b border-border/30 hover:bg-gray-50">
                  <td className="py-1.5 pr-3 max-w-[300px] truncate font-medium">{String(n.title || "-")}</td>
                  <td className="py-1.5 pr-3 text-muted whitespace-nowrap">{String(n.post_date || "-")}</td>
                  <td className="py-1.5 pr-3 text-muted">{String(n.semester || "-")}</td>
                  <td className="py-1.5 pr-3 text-muted whitespace-nowrap">{String(n.first_seen || "-")}</td>
                  <td className="py-1.5 pr-3 text-muted whitespace-nowrap">{String(n.last_seen || "-")}</td>
                  <td className="py-1.5 text-muted max-w-[200px] truncate">{String(n.url || "-")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
