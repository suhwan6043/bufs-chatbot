"use client";
import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "@/lib/api";

export interface AuthUser {
  id: number;
  username: string;
  nickname: string;
  student_id: string;
  department: string;
  student_type: string;
}

const TOKEN_KEY = "camchat_auth_token";
const USER_KEY = "camchat_user";

function getStored<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Validate token on mount
  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    const cached = getStored<AuthUser>(USER_KEY);

    if (!token) {
      setLoading(false);
      return;
    }

    // Try to validate with /api/user/me
    apiFetch<AuthUser>("/api/user/me", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((data) => {
        setUser(data);
        localStorage.setItem(USER_KEY, JSON.stringify(data));
      })
      .catch(() => {
        // Token expired or invalid — clear
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        setUser(null);
      })
      .finally(() => setLoading(false));

    // Use cached user while validating (prevents flash)
    if (cached) setUser(cached);
  }, []);

  const login = useCallback(async (username: string, password: string): Promise<{ ok: boolean; error?: string }> => {
    try {
      const data = await apiFetch<{ token: string; user: AuthUser }>("/api/user/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      localStorage.setItem(TOKEN_KEY, data.token);
      localStorage.setItem(USER_KEY, JSON.stringify(data.user));
      setUser(data.user);
      return { ok: true };
    } catch (e: unknown) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  }, []);

  const register = useCallback(async (body: {
    username: string;
    nickname: string;
    password: string;
    student_id: string;
    department: string;
    student_type: string;
  }): Promise<{ ok: boolean; error?: string }> => {
    try {
      const data = await apiFetch<{ token: string; user: AuthUser }>("/api/user/register", {
        method: "POST",
        body: JSON.stringify(body),
      });
      localStorage.setItem(TOKEN_KEY, data.token);
      localStorage.setItem(USER_KEY, JSON.stringify(data.user));
      setUser(data.user);
      return { ok: true };
    } catch (e: unknown) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  }, []);

  const logout = useCallback(async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) {
      try {
        await apiFetch("/api/user/logout", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
      } catch {}
    }
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setUser(null);
  }, []);

  return {
    user,
    isLoggedIn: !!user,
    loading,
    login,
    register,
    logout,
  };
}
