"use client";
import { useState, useCallback } from "react";
import { apiFetch } from "@/lib/api";

/* ── 타입 정의 ─────────────────────────────────────────── */

interface DashboardData {
  kpi: { total_questions: number; today_questions: number; avg_duration_sec: number; faq_count: number };
  daily_chart: { date: string; count: number }[];
  intent_distribution: { intent: string; count: number }[];
  recent_chats: { time: string; question: string; intent: string; duration_ms: number; rating: string }[];
}

export interface LogEntry {
  timestamp: string; session_id: string; student_id: string; intent: string;
  question: string; answer: string; duration_ms: number; rating: string;
}
export interface LogsResponse {
  total: number; today_count: number; avg_duration_ms: number; top_intent: string;
  entries: LogEntry[];
}
export interface CrawlerStatus {
  enabled: boolean; is_running: boolean; interval_minutes: number; next_run: string; notice_count: number;
}
export interface GraphStatus {
  total_nodes: number; total_edges: number;
  type_counts: Record<string, number>;
  early_grad_nodes: { id: string; type: string; data: Record<string, unknown> }[];
  recent_audit: string[];
  graph_path: string;
}
export interface ContactEntry {
  name: string; college: string; extension: string; phone: string; office: string;
  match_type?: string;
}
export interface GradRow {
  node_id: string; group: string; group_label: string; student_type: string; major: string;
  credits: number | string; liberal: number | string; global_comm: number | string;
  exam: string; cert: string;
  // 추가 필드
  community: string; nomad: string;
  career_explore: number | null; major_explore: number | null;
  exam_bool: boolean;
  second_major_method: string;
  double_major: number | null; fusion_major: number | null;
  micro_major: number | null; minor_major: number | null;
}
export interface GradOptions {
  groups: Record<string, string>;
  student_types: string[];
  dept_tree: Record<string, string[]>;
}
export interface DeptCertData {
  node_id: string | null;
  data: {
    cert_requirement: string; cert_subjects: string;
    cert_pass_criteria: string; cert_alternative: string;
  };
}
export interface EarlyGradData {
  schedules: { id?: string; semester: string; start_date: string; end_date: string; method: string }[];
  criteria: { id?: string; group: string; credits: number; note: string; condition: string }[];
  eligibility: Record<string, unknown>;
  notes: Record<string, unknown>;
}
export interface ScheduleEvent {
  event_name: string; semester: string; start_date: string; end_date: string; note: string;
}
export interface AttachmentStatus {
  pdf: { count: number; total_kb: number };
  hwp: { count: number; total_kb: number };
  other: { count: number; total_kb: number };
}

/* ── Hook ──────────────────────────────────────────────── */

const ADMIN_TOKEN_KEY = "admin_token";

export function useAdmin() {
  const [token, setToken] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return localStorage.getItem(ADMIN_TOKEN_KEY);
  });
  const [error, setError] = useState("");

  const login = useCallback(async (password: string) => {
    setError("");
    try {
      const data = await apiFetch<{ token: string; expires_at: string }>("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      setToken(data.token);
      localStorage.setItem(ADMIN_TOKEN_KEY, data.token);
      return true;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "로그인 실패");
      return false;
    }
  }, []);

  const logout = useCallback(async () => {
    if (token) {
      try { await apiFetch("/api/admin/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }); } catch {}
    }
    setToken(null);
    localStorage.removeItem(ADMIN_TOKEN_KEY);
  }, [token]);

  /* ── 인증 fetch (GET/POST/PUT) ── */
  const authFetch = useCallback(async <T,>(path: string, opts?: RequestInit): Promise<T> => {
    if (!token) throw new Error("Not authenticated");
    const headers: Record<string, string> = { Authorization: `Bearer ${token}`, ...(opts?.headers as Record<string, string> ?? {}) };
    if (opts?.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    return apiFetch<T>(path, { ...opts, headers });
  }, [token]);

  /* ── 대시보드 ── */
  const fetchDashboard = useCallback(() => authFetch<DashboardData>("/api/admin/dashboard"), [authFetch]);

  /* ── 대화 로그 ── */
  const fetchLogDates = useCallback(() => authFetch<{ dates: string[] }>("/api/admin/logs/dates"), [authFetch]);
  const fetchLogs = useCallback((params: string) => authFetch<LogsResponse>(`/api/admin/logs?${params}`), [authFetch]);

  /* ── 크롤러 ── */
  const fetchCrawler = useCallback(() => authFetch<CrawlerStatus>("/api/admin/crawler"), [authFetch]);
  const triggerCrawl = useCallback(() => authFetch<{ ok: boolean; message: string }>("/api/admin/crawler/trigger", { method: "POST" }), [authFetch]);
  const resetHashes = useCallback(() => authFetch<{ ok: boolean }>("/api/admin/crawler/reset-hashes", { method: "POST" }), [authFetch]);
  const reingest = useCallback(() => authFetch<{ ok: boolean; deleted_notice: number; deleted_attachment: number }>("/api/admin/crawler/reingest", { method: "POST" }), [authFetch]);
  const fetchCrawlHistory = useCallback(() => authFetch<{ records: Record<string, unknown>[] }>("/api/admin/crawler/history"), [authFetch]);
  const fetchNotices = useCallback(() => authFetch<{ notices: Record<string, unknown>[] }>("/api/admin/crawler/notices"), [authFetch]);
  const fetchAttachments = useCallback(() => authFetch<AttachmentStatus>("/api/admin/crawler/attachments"), [authFetch]);

  /* ── 그래프 ── */
  const fetchGraph = useCallback(() => authFetch<GraphStatus>("/api/admin/graph"), [authFetch]);
  const resetChat = useCallback(() => authFetch<{ ok: boolean; message: string }>("/api/admin/graph/reset-chat", { method: "POST" }), [authFetch]);

  /* ── 연락처 ── */
  const fetchContacts = useCallback(() => authFetch<{ total: number; entries: ContactEntry[] }>("/api/admin/contacts"), [authFetch]);
  const searchContacts = useCallback((q: string) => authFetch<{ is_contact_query: boolean; results: ContactEntry[] }>(`/api/admin/contacts/search?q=${encodeURIComponent(q)}`), [authFetch]);
  const fetchContactsJson = useCallback(() => authFetch<{ json_content: string }>("/api/admin/contacts/json"), [authFetch]);
  const saveContactsJson = useCallback((json_content: string) => authFetch<{ ok: boolean }>("/api/admin/contacts", { method: "PUT", body: JSON.stringify({ json_content }) }), [authFetch]);

  /* ── 졸업요건 ── */
  const fetchGraduation = useCallback(() => authFetch<{ rows: GradRow[] }>("/api/admin/graduation"), [authFetch]);
  const saveGraduation = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/graduation", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const fetchGradOptions = useCallback(() => authFetch<GradOptions>("/api/admin/graduation/options"), [authFetch]);
  const fetchDeptCert = useCallback((major: string) => authFetch<DeptCertData>(`/api/admin/graduation/dept-cert?major=${encodeURIComponent(major)}`), [authFetch]);
  const saveDeptCert = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/graduation/dept-cert", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);

  /* ── 조기졸업 ── */
  const fetchEarlyGrad = useCallback(() => authFetch<EarlyGradData>("/api/admin/early-graduation"), [authFetch]);
  const saveEarlyGradSchedule = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/early-graduation/schedule", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const saveEarlyGradEligibility = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/early-graduation/eligibility", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const saveEarlyGradCriteria = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/early-graduation/criteria", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const saveEarlyGradNotes = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/early-graduation/notes", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);

  /* ── 학사일정 ── */
  const fetchSchedule = useCallback(() => authFetch<{ events: ScheduleEvent[] }>("/api/admin/schedule"), [authFetch]);
  const addSchedule = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/schedule", { method: "POST", body: JSON.stringify(body) }), [authFetch]);
  const updateSchedule = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/schedule", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);

  return {
    token, error, login, logout, authFetch,
    fetchDashboard,
    fetchLogDates, fetchLogs,
    fetchCrawler, triggerCrawl, resetHashes, reingest, fetchCrawlHistory, fetchNotices, fetchAttachments,
    fetchGraph, resetChat,
    fetchContacts, searchContacts, fetchContactsJson, saveContactsJson,
    fetchGraduation, saveGraduation, fetchGradOptions, fetchDeptCert, saveDeptCert,
    fetchEarlyGrad, saveEarlyGradSchedule, saveEarlyGradEligibility, saveEarlyGradCriteria, saveEarlyGradNotes,
    fetchSchedule, addSchedule, updateSchedule,
  };
}
