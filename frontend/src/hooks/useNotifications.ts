"use client";
import { useState, useCallback, useEffect } from "react";
import { useAuth } from "@/hooks/useAuth";

export interface NotificationItem {
  id: number;
  kind: string;              // 'faq_answered' | 'faq_updated'
  faq_id: string | null;
  chat_message_id: number | null;
  title: string;
  body: string;
  read: boolean;
  created_at: string;
}

interface ListResponse {
  unread_count: number;
  items: NotificationItem[];
}

/**
 * 로그인 사용자 알림 관리 훅.
 *
 * 전략: "페이지 방문 시만" — refresh() 는 외부(페이지 마운트)에서 호출.
 * Guest(비로그인)일 때는 완전한 no-op (items=[], unreadCount=0).
 */
export function useNotifications() {
  const { isLoggedIn, authFetch } = useAuth();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  const refreshCount = useCallback(async () => {
    if (!isLoggedIn) { setUnreadCount(0); return; }
    try {
      const r = await authFetch<{ unread_count: number }>("/api/user/notifications/unread-count");
      setUnreadCount(r.unread_count);
    } catch {
      setUnreadCount(0);
    }
  }, [isLoggedIn, authFetch]);

  const refreshList = useCallback(async () => {
    if (!isLoggedIn) { setItems([]); setUnreadCount(0); return; }
    setLoading(true);
    try {
      const r = await authFetch<ListResponse>("/api/user/notifications?limit=50");
      setItems(r.items);
      setUnreadCount(r.unread_count);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [isLoggedIn, authFetch]);

  const markRead = useCallback(async (id: number) => {
    if (!isLoggedIn) return;
    try {
      await authFetch(`/api/user/notifications/${id}/read`, { method: "POST" });
      setItems((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
      setUnreadCount((c) => Math.max(0, c - 1));
    } catch {}
  }, [isLoggedIn, authFetch]);

  const markAllRead = useCallback(async () => {
    if (!isLoggedIn) return;
    try {
      await authFetch<{ ok: boolean; updated: number }>("/api/user/notifications/read-all", { method: "POST" });
      setItems((prev) => prev.map((n) => ({ ...n, read: true })));
      setUnreadCount(0);
    } catch {}
  }, [isLoggedIn, authFetch]);

  const openPanel = useCallback(() => {
    setOpen(true);
    refreshList();
  }, [refreshList]);

  const closePanel = useCallback(() => setOpen(false), []);

  // 로그인 상태 전환 시 뱃지 초기화
  useEffect(() => {
    if (!isLoggedIn) {
      setItems([]); setUnreadCount(0); setOpen(false);
    }
  }, [isLoggedIn]);

  return {
    items,
    unreadCount,
    loading,
    open,
    refreshCount,
    refreshList,
    markRead,
    markAllRead,
    openPanel,
    closePanel,
    isLoggedIn,
  };
}
