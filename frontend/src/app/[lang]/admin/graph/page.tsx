"use client";
import { useState, useEffect, use } from "react";
import { useAdmin, type GraphStatus } from "@/hooks/useAdmin";

export default function GraphPage({ params }: { params: Promise<{ lang: string }> }) {
  use(params);
  const { token, fetchGraph, resetChat } = useAdmin();
  const [data, setData] = useState<GraphStatus | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => { if (token) fetchGraph().then(setData).catch(() => {}); }, [token, fetchGraph]);

  const handleReset = async () => {
    try { const r = await resetChat(); setMsg(r.message); } catch (e) { setMsg(`오류: ${e}`); }
  };

  if (!token || !data) return null;

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-lg font-bold text-navy">그래프 현황</h1>

      {/* Stats - 4 cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white rounded-xl p-5 border border-gray-100 shadow-sm border-l-4 border-l-blue-500">
          <div className="text-2xl font-bold">{data.total_nodes}</div><div className="text-xs text-muted">전체 노드</div>
        </div>
        <div className="bg-white rounded-xl p-5 border border-gray-100 shadow-sm border-l-4 border-l-green-500">
          <div className="text-2xl font-bold">{data.total_edges}</div><div className="text-xs text-muted">전체 엣지</div>
        </div>
        <div className="bg-white rounded-xl p-5 border border-gray-100 shadow-sm border-l-4 border-l-purple-500">
          <div className="text-2xl font-bold">{data.type_counts["조기졸업"] || 0}</div><div className="text-xs text-muted">조기졸업 노드</div>
        </div>
        <div className="bg-white rounded-xl p-5 border border-gray-100 shadow-sm border-l-4 border-l-orange-500">
          <div className="text-2xl font-bold">{data.type_counts["학사일정"] || 0}</div><div className="text-xs text-muted">학사일정 노드</div>
        </div>
      </div>

      {/* Chat session reset */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 space-y-2">
        <h2 className="text-sm font-bold">채팅 세션에 변경사항 반영</h2>
        <p className="text-xs text-muted">그래프 저장 후 이 버튼을 누르면, 채팅 페이지에서 다음 질문 시 그래프를 새로 로드합니다.</p>
        <div className="flex items-center gap-3">
          <button onClick={handleReset} className="px-4 py-2 text-sm bg-orange-500 text-white rounded-lg hover:bg-orange-600">채팅 세션 초기화</button>
          {msg && <span className="text-xs text-green-600">{msg}</span>}
        </div>
      </div>

      {/* Type counts */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
        <h2 className="text-sm font-bold text-text mb-3">노드 타입별 분포</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {Object.entries(data.type_counts).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
            <div key={type} className="flex justify-between items-center text-xs p-2 bg-gray-50 rounded">
              <span className="text-text-sub truncate">{type}</span>
              <span className="font-medium text-text ml-2">{count}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Early grad nodes */}
      {data.early_grad_nodes.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
          <h2 className="text-sm font-bold text-text mb-3">조기졸업 노드 ({data.early_grad_nodes.length})</h2>
          <div className="space-y-1 text-xs max-h-40 overflow-y-auto">
            {data.early_grad_nodes.map((n, i) => (
              <div key={i} className="text-text-sub">{n.id}: {JSON.stringify(n.data)}</div>
            ))}
          </div>
        </div>
      )}

      {/* Audit log */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
        <h2 className="text-sm font-bold text-text mb-3">감사 로그 (최근 20줄)</h2>
        <div className="space-y-0.5 text-xs font-mono text-text-sub max-h-48 overflow-y-auto">
          {data.recent_audit.length === 0 ? <p className="text-muted">로그 없음</p> :
            data.recent_audit.map((l, i) => <div key={i}>{l}</div>)}
        </div>
      </div>

      <p className="text-xs text-muted">그래프 파일: {data.graph_path}</p>
    </div>
  );
}
