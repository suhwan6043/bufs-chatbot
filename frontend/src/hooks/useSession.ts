"use client";
import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "@/lib/api";
import type { Lang, SessionInfo, UserProfile } from "@/lib/types";

const COOKIE_KEY = "camchat_session_id";

function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

function setCookie(name: string, value: string, days = 1) {
  const d = new Date();
  d.setTime(d.getTime() + days * 86400000);
  document.cookie = `${name}=${encodeURIComponent(value)};expires=${d.toUTCString()};path=/;SameSite=Lax`;
}

export function useSession(lang: Lang) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);

  const createSession = useCallback(async () => {
    const data = await apiFetch<SessionInfo>("/api/session", {
      method: "POST",
      body: JSON.stringify({ lang }),
    });
    setCookie(COOKIE_KEY, data.session_id);
    setSessionId(data.session_id);
    setSession(data);
    return data.session_id;
  }, [lang]);

  const syncSessionLang = useCallback(async (sid: string, sessionLang: string) => {
    if (sessionLang === lang) return;

    await apiFetch(`/api/session/${sid}/lang?lang=${lang}`, {
      method: "PUT",
    });
  }, [lang]);

  useEffect(() => {
    let cancelled = false;

    async function loadSession() {
      try {
        const existing = getCookie(COOKIE_KEY);

        if (existing) {
          const s = await apiFetch<SessionInfo>(`/api/session/${existing}`);
          await syncSessionLang(existing, s.lang);
          if (!cancelled) {
            setSessionId(existing);
            setSession({ ...s, lang });
          }
          return;
        }

        await createSession();
      } catch {
        await createSession();
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadSession();

    return () => {
      cancelled = true;
    };
  }, [createSession, lang, syncSessionLang]);

  const updateProfile = useCallback(async (profile: UserProfile) => {
    if (!sessionId) return;
    await apiFetch(`/api/session/${sessionId}/profile`, {
      method: "PUT",
      body: JSON.stringify(profile),
    });
    setSession((prev) => prev ? { ...prev, user_profile: profile } : prev);
  }, [sessionId]);

  // 서버에서 최신 세션 상태를 재조회 (업로드 후 has_transcript 갱신용)
  const refreshSession = useCallback(async () => {
    if (!sessionId) return;
    try {
      const data = await apiFetch<SessionInfo>(`/api/session/${sessionId}`);
      setSession({ ...data, lang });
    } catch {
      // ignore — 세션 무효면 loadSession이 재생성
    }
  }, [sessionId, lang]);

  // 세션 완전 리셋 — 로그아웃용. 쿠키 삭제 + 상태 클리어 + 새 빈 세션 생성.
  const resetSession = useCallback(async () => {
    if (typeof document !== "undefined") {
      document.cookie = `${COOKIE_KEY}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax`;
    }
    setSessionId(null);
    setSession(null);
    await createSession();
  }, [createSession]);

  return { sessionId, session, loading, updateProfile, createSession, refreshSession, resetSession };
}
