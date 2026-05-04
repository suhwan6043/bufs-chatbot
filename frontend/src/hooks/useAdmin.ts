"use client";
import { useState, useCallback, useEffect } from "react";
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
  user_id?: number | null;
  chat_message_id?: number | null;
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
  data: { cert_requirement: string; cert_subjects: string; cert_pass_criteria: string; cert_alternative: string; };
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
export interface FaqItem {
  id: string;
  category: string;
  question: string;
  answer: string;
  source: "academic" | "admin";
  created_by?: string | null;
  created_at?: string | null;
  source_question?: string | null;
  // 2026-04-28: 복수 paraphrase 지원 — 학생이 실제로 묻는 다양한 표현.
  // 단일 source_question(하위호환)과 함께 사용 가능.
  source_questions?: string[] | null;
  answer_type?: string | null;
}
export interface FaqListResponse {
  total: number;
  items: FaqItem[];
  categories: string[];
}
export interface FaqCreateBody {
  question: string;
  answer: string;
  category: string;
  source_question?: string;
  source_questions?: string[];
  source_user_id?: number | null;
  source_chat_message_id?: number | null;
}
export interface FaqUpdateBody {
  question?: string;
  answer?: string;
  category?: string;
  source_question?: string;
  // null|undefined=기존 유지, []=모두 제거, [...]=교체
  source_questions?: string[];
}
export interface UncoveredExample {
  question: string;
  answer: string;
  timestamp: string;
  session_id: string;
  rating: number | null;
  refused: boolean;
}
export interface UncoveredCluster {
  representative_question: string;
  count: number;
  last_asked: string;
  examples: UncoveredExample[];
}
export interface UncoveredResponse {
  scanned_days: number;
  total_candidates: number;
  clusters: UncoveredCluster[];
}

/* ── 보안 상수 ──────────────────────────────────────────── */

const TOKEN_KEY = "admin_token";
const EXPIRES_KEY = "admin_expires_ms"; // 브라우저 로컬 밀리초 (타임존 무관)

function isExpired(): boolean {
  if (typeof window === "undefined") return true;
  const e = localStorage.getItem(EXPIRES_KEY);
  if (!e) return true;
  return Number(e) <= Date.now();
}

function setExpiry(ttlMs: number = 30 * 60 * 1000) {
  // 서버 expires_at 대신 브라우저 로컬 시간 기준으로 만료 설정 (타임존 문제 회피)
  localStorage.setItem(EXPIRES_KEY, String(Date.now() + ttlMs));
}

function readToken(): string | null {
  if (typeof window === "undefined") return null;
  const t = localStorage.getItem(TOKEN_KEY);
  if (!t) return null;
  if (isExpired()) {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EXPIRES_KEY);
    return null;
  }
  return t;
}

/* ── Hook ──────────────────────────────────────────────── */

export function useAdmin() {
  const [token, setToken] = useState<string | null>(() => readToken());
  const [error, setError] = useState("");

  // 주기적 만료 체크 (1분)
  useEffect(() => {
    if (!token) return;
    const id = setInterval(() => {
      if (isExpired()) {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(EXPIRES_KEY);
        setToken(null);
      }
    }, 60_000);
    return () => clearInterval(id);
  }, [token]);

  // 탭 간 동기화
  useEffect(() => {
    const fn = (e: StorageEvent) => {
      if (e.key === TOKEN_KEY && !e.newValue) setToken(null);
    };
    window.addEventListener("storage", fn);
    return () => window.removeEventListener("storage", fn);
  }, []);

  const login = useCallback(async (password: string) => {
    setError("");
    try {
      const data = await apiFetch<{ token: string; expires_at: string }>("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      localStorage.setItem(TOKEN_KEY, data.token);
      setExpiry(30 * 60 * 1000); // 30분
      setToken(data.token);
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
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EXPIRES_KEY);
    setToken(null);
  }, [token]);

  const authFetch = useCallback(async <T,>(path: string, opts?: RequestInit): Promise<T> => {
    if (!token || isExpired()) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(EXPIRES_KEY);
      setToken(null);
      throw new Error("Not authenticated");
    }
    const headers: Record<string, string> = { Authorization: `Bearer ${token}`, ...(opts?.headers as Record<string, string> ?? {}) };
    if (opts?.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    try {
      return await apiFetch<T>(path, { ...opts, headers });
    } catch (e: unknown) {
      // 401 → 토큰 만료/무효 → 자동 로그아웃
      if (e instanceof Error && e.message.includes("401")) {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(EXPIRES_KEY);
        setToken(null);
      }
      throw e;
    }
  }, [token]);

  const fetchDashboard = useCallback(() => authFetch<DashboardData>("/api/admin/dashboard"), [authFetch]);
  const fetchLogDates = useCallback(() => authFetch<{ dates: string[] }>("/api/admin/logs/dates"), [authFetch]);
  const fetchLogs = useCallback((params: string) => authFetch<LogsResponse>(`/api/admin/logs?${params}`), [authFetch]);
  const fetchCrawler = useCallback(() => authFetch<CrawlerStatus>("/api/admin/crawler"), [authFetch]);
  const triggerCrawl = useCallback(() => authFetch<{ ok: boolean; message: string }>("/api/admin/crawler/trigger", { method: "POST" }), [authFetch]);
  const resetHashes = useCallback(() => authFetch<{ ok: boolean }>("/api/admin/crawler/reset-hashes", { method: "POST" }), [authFetch]);
  const reingest = useCallback(() => authFetch<{ ok: boolean; deleted_notice: number; deleted_attachment: number }>("/api/admin/crawler/reingest", { method: "POST" }), [authFetch]);
  const fetchCrawlHistory = useCallback(() => authFetch<{ records: Record<string, unknown>[] }>("/api/admin/crawler/history"), [authFetch]);
  const fetchNotices = useCallback(() => authFetch<{ notices: Record<string, unknown>[] }>("/api/admin/crawler/notices"), [authFetch]);
  const fetchAttachments = useCallback(() => authFetch<AttachmentStatus>("/api/admin/crawler/attachments"), [authFetch]);
  const fetchGraph = useCallback(() => authFetch<GraphStatus>("/api/admin/graph"), [authFetch]);
  const resetChat = useCallback(() => authFetch<{ ok: boolean; message: string }>("/api/admin/graph/reset-chat", { method: "POST" }), [authFetch]);
  const fetchContacts = useCallback(() => authFetch<{ total: number; entries: ContactEntry[] }>("/api/admin/contacts"), [authFetch]);
  const searchContacts = useCallback((q: string) => authFetch<{ is_contact_query: boolean; results: ContactEntry[] }>(`/api/admin/contacts/search?q=${encodeURIComponent(q)}`), [authFetch]);
  const fetchContactsJson = useCallback(() => authFetch<{ json_content: string }>("/api/admin/contacts/json"), [authFetch]);
  const saveContactsJson = useCallback((json_content: string) => authFetch<{ ok: boolean }>("/api/admin/contacts", { method: "PUT", body: JSON.stringify({ json_content }) }), [authFetch]);
  const fetchGraduation = useCallback(() => authFetch<{ rows: GradRow[] }>("/api/admin/graduation"), [authFetch]);
  const saveGraduation = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/graduation", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const fetchGradOptions = useCallback(() => authFetch<GradOptions>("/api/admin/graduation/options"), [authFetch]);
  const fetchDeptCert = useCallback((major: string) => authFetch<DeptCertData>(`/api/admin/graduation/dept-cert?major=${encodeURIComponent(major)}`), [authFetch]);
  const saveDeptCert = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/graduation/dept-cert", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const fetchEarlyGrad = useCallback(() => authFetch<EarlyGradData>("/api/admin/early-graduation"), [authFetch]);
  const saveEarlyGradSchedule = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/early-graduation/schedule", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const saveEarlyGradEligibility = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/early-graduation/eligibility", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const saveEarlyGradCriteria = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/early-graduation/criteria", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const saveEarlyGradNotes = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/early-graduation/notes", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const fetchSchedule = useCallback(() => authFetch<{ events: ScheduleEvent[] }>("/api/admin/schedule"), [authFetch]);
  const addSchedule = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/schedule", { method: "POST", body: JSON.stringify(body) }), [authFetch]);
  const updateSchedule = useCallback((body: unknown) => authFetch<{ ok: boolean }>("/api/admin/schedule", { method: "PUT", body: JSON.stringify(body) }), [authFetch]);

  // FAQ 피드백 루프 — 미답변 질의 수집 → 관리자 큐레이션 → 증분 반영
  const fetchFaqList = useCallback((source: "all" | "admin" | "academic" = "all") =>
    authFetch<FaqListResponse>(`/api/admin/faq?source=${source}`), [authFetch]);
  const fetchFaqCategories = useCallback(() =>
    authFetch<{ categories: string[] }>("/api/admin/faq/categories"), [authFetch]);
  const createFaq = useCallback((body: FaqCreateBody) =>
    authFetch<FaqItem>("/api/admin/faq", { method: "POST", body: JSON.stringify(body) }), [authFetch]);
  const updateFaq = useCallback((faqId: string, body: FaqUpdateBody) =>
    authFetch<FaqItem>(`/api/admin/faq/${encodeURIComponent(faqId)}`, { method: "PUT", body: JSON.stringify(body) }), [authFetch]);
  const deleteFaq = useCallback((faqId: string) =>
    authFetch<{ ok: boolean; id: string }>(`/api/admin/faq/${encodeURIComponent(faqId)}`, { method: "DELETE" }), [authFetch]);
  const fetchUncovered = useCallback((days?: number, limit?: number) => {
    const params = new URLSearchParams();
    if (days !== undefined) params.set("days", String(days));
    if (limit !== undefined) params.set("limit", String(limit));
    const q = params.toString();
    return authFetch<UncoveredResponse>(`/api/admin/faq/uncovered${q ? "?" + q : ""}`);
  }, [authFetch]);

  return {
    token, error, login, logout, authFetch,
    verifying: false, // layout 호환
    fetchDashboard, fetchLogDates, fetchLogs,
    fetchCrawler, triggerCrawl, resetHashes, reingest, fetchCrawlHistory, fetchNotices, fetchAttachments,
    fetchGraph, resetChat,
    fetchContacts, searchContacts, fetchContactsJson, saveContactsJson,
    fetchGraduation, saveGraduation, fetchGradOptions, fetchDeptCert, saveDeptCert,
    fetchEarlyGrad, saveEarlyGradSchedule, saveEarlyGradEligibility, saveEarlyGradCriteria, saveEarlyGradNotes,
    fetchSchedule, addSchedule, updateSchedule,
    fetchFaqList, fetchFaqCategories, createFaq, updateFaq, deleteFaq, fetchUncovered,
  };
}
