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

  useEffect(() => {
    const existing = getCookie(COOKIE_KEY);
    if (existing) {
      apiFetch<SessionInfo>(`/api/session/${existing}`)
        .then((s) => { setSessionId(existing); setSession(s); })
        .catch(() => createSession())
        .finally(() => setLoading(false));
    } else {
      createSession().finally(() => setLoading(false));
    }
  }, [createSession]);

  const updateProfile = useCallback(async (profile: UserProfile) => {
    if (!sessionId) return;
    await apiFetch(`/api/session/${sessionId}/profile`, {
      method: "PUT",
      body: JSON.stringify(profile),
    });
    setSession((prev) => prev ? { ...prev, user_profile: profile } : prev);
  }, [sessionId]);

  return { sessionId, session, loading, updateProfile, createSession };
}
