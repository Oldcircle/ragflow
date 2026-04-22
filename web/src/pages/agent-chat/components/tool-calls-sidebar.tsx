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

  // 合并：正在流式的 + 历史的，历史的转为 StreamingToolCall 结构
  const all: StreamingToolCall[] = [
    ...streamingToolCalls,
    ...historyToolCalls.map(
      (tc): StreamingToolCall => ({
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
        startTs: tc.start_time ?? 0,
      }),
    ),
  ];

  // 去重，保持先后顺序（流式 > 历史）
  const seen = new Set<string>();
  const dedup = all.filter((c) => {
    if (seen.has(c.id)) return false;
    seen.add(c.id);
    return true;
  });

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
