"use client";
import { useState, useCallback, useRef } from "react";
import { sseUrl } from "@/lib/api";
import { getAuthToken } from "@/hooks/useAuth";
import type { ChatMessage, StreamDoneData, SourceURL, SearchResultItem } from "@/lib/types";

export function useChat(sessionId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const esRef = useRef<EventSource | null>(null);

  const sendMessage = useCallback(
    (question: string) => {
      if (!sessionId || !question.trim() || isStreaming) return;

      // Add user message
      const userMsg: ChatMessage = { role: "user", content: question };
      setMessages((prev) => [...prev, userMsg]);
      setIsStreaming(true);
      setStreamText("");

      let accumulated = "";

      // 로그인 사용자면 JWT를 쿼리로 전달 — EventSource는 커스텀 헤더 불가.
      // 서버는 검증 실패 시 비로그인으로 폴백(채팅은 정상, 개인 DB 저장 스킵).
      const token = getAuthToken();
      const params: Record<string, string> = {
        session_id: sessionId,
        question: question.trim(),
      };
      if (token) params.access_token = token;
      const url = sseUrl("/api/chat/stream", params);

      const es = new EventSource(url);
      esRef.current = es;

      es.addEventListener("token", (e: MessageEvent) => {
        try {
          const { token } = JSON.parse(e.data);
          accumulated += token;
          setStreamText(accumulated);
        } catch { /* ignore parse errors */ }
      });

      es.addEventListener("clear", () => {
        accumulated = "";
        setStreamText("");
      });

      es.addEventListener("done", (e: MessageEvent) => {
        try {
          const data: StreamDoneData = JSON.parse(e.data);
          const assistantMsg: ChatMessage = {
            role: "assistant",
            content: data.answer,
            sourceUrls: data.source_urls,
            results: data.results,
            intent: data.intent,
            durationMs: data.duration_ms,
            rated: false,
          };
          setMessages((prev) => [...prev, assistantMsg]);
        } catch {
          setMessages((prev) => [
            ...prev,
            { role: "assistant", content: accumulated || "응답을 받지 못했습니다." },
          ]);
        }
        setIsStreaming(false);
        setStreamText("");
        es.close();
        esRef.current = null;
      });

      es.addEventListener("error", (e: MessageEvent) => {
        let errMsg = "오류가 발생했습니다. 다시 시도해 주세요.";
        try {
          const d = JSON.parse((e as MessageEvent).data);
          if (d.message) errMsg = d.message;
        } catch { /* use default */ }
        setMessages((prev) => [...prev, { role: "assistant", content: errMsg }]);
        setIsStreaming(false);
        setStreamText("");
        es.close();
        esRef.current = null;
      });

      es.onerror = () => {
        if (esRef.current) {
          // If we have accumulated text, use it as the answer
          if (accumulated) {
            setMessages((prev) => [...prev, { role: "assistant", content: accumulated }]);
          }
          setIsStreaming(false);
          setStreamText("");
          es.close();
          esRef.current = null;
        }
      };
    },
    [sessionId, isStreaming]
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    setStreamText("");
  }, []);

  return { messages, isStreaming, streamText, sendMessage, clearMessages, setMessages };
}
