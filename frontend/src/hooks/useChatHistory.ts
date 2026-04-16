"use client";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import type { ChatHistoryItem, ChatHistoryResponse } from "@/lib/types";

/**
 * 로그인 사용자 질문 이력.
 * - 로그인 시: GET /api/user/chat-history 로 DB 이력 조회 (최신순)
 * - 비로그인 시: no-op — items: [], total: 0 반환
 *
 * 페이지 마운트·사이드바 열림 시 refresh() 호출.
 */
export function useChatHistory(opts?: { limit?: number; autoLoad?: boolean }) {
  const { isLoggedIn, authFetch } = useAuth();
  const [items, setItems] = useState<ChatHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const limit = opts?.limit ?? 20;
  const autoLoad = opts?.autoLoad ?? true;

  const refresh = useCallback(async () => {
    if (!isLoggedIn) {
      setItems([]);
      setTotal(0);
      return;
    }
    setLoading(true);
    try {
      const r = await authFetch<ChatHistoryResponse>(
        `/api/user/chat-history?limit=${limit}&offset=0`,
      );
      setItems(r.items);
      setTotal(r.total);
    } catch {
      // 401/네트워크 에러 — 조용히 폴백
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [isLoggedIn, authFetch, limit]);

  useEffect(() => {
    if (autoLoad) refresh();
  }, [autoLoad, refresh]);

  return { items, total, loading, refresh, isLoggedIn };
}
