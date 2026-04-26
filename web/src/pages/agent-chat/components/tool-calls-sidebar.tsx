import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentV2ToolCall } from '../api';
import { StreamingToolCall } from '../hooks/use-agent-stream';
import { T } from '../theme';
import { I } from './icons';
import { ToolCallCard } from './tool-call-card';

interface Props {
  streamingToolCalls: StreamingToolCall[];
  historyToolCalls: AgentV2ToolCall[];
}

export const ToolCallsSidebar = memo(function ToolCallsSidebar({
  streamingToolCalls,
  historyToolCalls,
}: Props) {
  const { t } = useTranslation();

  // 合并 streaming + history，按 startTs 升序排（与左侧消息流 "老→新 自上而下" 对齐）。
  //
  // 旧实现按 [...streaming, ...history] 拼接 + 首位保留，导致**当前轮**的工具调用
  // 强行顶到列表顶部、上一轮的反而排在下方，整体看起来乱序。
  //
  // 现策略：history 是后端 `start_time asc` 的 canonical 真值，覆盖 streaming
  // 同 id 项；但保留 streaming 上 subagent_start/end 注入的内联 trace（history 没有）。
  const byId = new Map<string, StreamingToolCall>();
  for (const c of streamingToolCalls) {
    byId.set(c.id, c);
  }
  for (const tc of historyToolCalls) {
    const existing = byId.get(tc.id);
    byId.set(tc.id, {
      ...(existing ?? {}),
      id: tc.id,
      name: tc.tool_name,
      args: tc.args,
      result: parseMaybeJson(tc.result),
      error: tc.error || undefined,
      durationMs: tc.duration_ms,
      status:
        tc.status === 'success'
          ? 'success'
          : tc.status === 'error'
            ? 'error'
            : 'pending',
      // 优先用 history 的服务端时间戳（同一时钟基准），其次保留 streaming 的本地时间戳。
      startTs: tc.start_time ?? existing?.startTs ?? 0,
    });
  }
  const dedup = Array.from(byId.values()).sort((a, b) => a.startTs - b.startTs);

  return (
    <aside
      style={{
        width: 340,
        borderLeft: `1px solid ${T.border}`,
        background: T.surface,
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
      }}
    >
      <div
        style={{
          padding: '14px 18px',
          borderBottom: `1px solid ${T.border}`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <I.wrench size={14} />
          <div style={{ fontSize: 13, fontWeight: 600, color: T.text }}>
            {t('agentV2.toolCalls')}
          </div>
        </div>
        <div style={{ fontSize: 11, color: T.textDim, marginTop: 4 }}>
          {dedup.length > 0
            ? t('agentV2.toolCallsCount', { count: dedup.length })
            : t('agentV2.noToolCallsYet')}
        </div>
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        {dedup.length === 0 && (
          <div
            style={{
              padding: '40px 20px',
              fontSize: 11,
              color: T.textDim,
              textAlign: 'center',
              lineHeight: 1.8,
            }}
          >
            <I.wrench size={24} style={{ opacity: 0.3, marginBottom: 8 }} />
            <div>{t('agentV2.emptyToolCallsHint')}</div>
          </div>
        )}
        {dedup.map((tc, i) => (
          <ToolCallCard key={tc.id} index={i + 1} call={tc} />
        ))}
      </div>
    </aside>
  );
});

function parseMaybeJson(s: string): unknown {
  if (!s) return null;
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}
