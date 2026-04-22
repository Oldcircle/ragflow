/**
 * 工具调用卡片 — 右侧"工具调用"侧栏使用。
 * 设计语言对齐 Claude Design 的引用侧栏，适配 Agent v2 的 tool-call 语义。
 */

import { memo, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { StreamingToolCall } from '../hooks/use-agent-stream';
import { T } from '../theme';
import { Badge } from './badge';

interface Props {
  index: number;
  call: StreamingToolCall;
  active?: boolean;
}

export const ToolCallCard = memo(function ToolCallCard({
  index,
  call,
  active,
}: Props) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const toneColor =
    call.status === 'success'
      ? T.success
      : call.status === 'error'
        ? T.danger
        : T.warning;

  const shortName = useMemo(
    () => call.name.split('__').pop() ?? call.name,
    [call.name],
  );

  const resultPreview = useMemo(() => {
    if (call.error) return call.error;
    if (call.result == null) return '';
    if (typeof call.result === 'string') {
      try {
        return JSON.stringify(JSON.parse(call.result), null, 2);
      } catch {
        return call.result;
      }
    }
    try {
      return JSON.stringify(call.result, null, 2);
    } catch {
      return String(call.result);
    }
  }, [call.result, call.error]);

  return (
    <div
      style={{
        padding: '12px 14px',
        borderBottom: `1px solid ${T.divider}`,
        cursor: 'pointer',
        background: active ? T.accentSoft + '80' : 'transparent',
        borderLeft: `3px solid ${active ? T.accent : 'transparent'}`,
        transition: 'background .15s, border-color .15s',
      }}
      onClick={() => setExpanded((v) => !v)}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 8,
        }}
      >
        <div
          style={{
            width: 18,
            height: 18,
            borderRadius: 4,
            background: T.accent,
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 10,
            fontWeight: 700,
          }}
        >
          {index}
        </div>
        <div
          style={{
            flex: 1,
            fontSize: 11,
            color: T.text,
            fontWeight: 500,
            fontFamily: T.fontMono,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {shortName}
        </div>
        <Badge
          tone={
            call.status === 'success'
              ? 'success'
              : call.status === 'error'
                ? 'danger'
                : 'warning'
          }
          style={{ fontSize: 9, padding: '1px 5px' }}
        >
          {call.status === 'pending'
            ? t('agentV2.running')
            : call.durationMs != null
              ? `${call.durationMs}ms`
              : call.status}
        </Badge>
      </div>

      {!expanded && call.result != null && !call.error && (
        <div
          style={{
            fontSize: 11,
            color: T.textMuted,
            lineHeight: 1.6,
            maxHeight: 54,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            display: '-webkit-box',
            WebkitLineClamp: 3,
            WebkitBoxOrient: 'vertical',
          }}
        >
          {resultPreview.slice(0, 200)}
          {resultPreview.length > 200 && '…'}
        </div>
      )}

      {expanded && (
        <div style={{ fontSize: 11, color: T.textMuted, lineHeight: 1.6 }}>
          <div style={{ marginTop: 4 }}>
            <div
              style={{
                color: T.textDim,
                fontSize: 10,
                marginBottom: 4,
                textTransform: 'uppercase',
                letterSpacing: 0.4,
              }}
            >
              {t('agentV2.args')}
            </div>
            <pre
              style={{
                background: T.surface,
                border: `1px solid ${T.border}`,
                borderRadius: 4,
                padding: 8,
                margin: 0,
                fontSize: 10,
                fontFamily: T.fontMono,
                whiteSpace: 'pre-wrap',
                overflow: 'auto',
                maxHeight: 120,
              }}
            >
              {JSON.stringify(call.args, null, 2)}
            </pre>
          </div>
          <div style={{ marginTop: 8 }}>
            <div
              style={{
                color: call.error ? T.danger : T.textDim,
                fontSize: 10,
                marginBottom: 4,
                textTransform: 'uppercase',
                letterSpacing: 0.4,
              }}
            >
              {call.error ? t('agentV2.error') : t('agentV2.result')}
            </div>
            <pre
              style={{
                background: T.surface,
                border: `1px solid ${call.error ? T.dangerBg : T.border}`,
                borderRadius: 4,
                padding: 8,
                margin: 0,
                fontSize: 10,
                fontFamily: T.fontMono,
                whiteSpace: 'pre-wrap',
                overflow: 'auto',
                maxHeight: 280,
              }}
            >
              {resultPreview}
            </pre>
          </div>
        </div>
      )}

      <div
        style={{
          fontSize: 10,
          color: T.textDim,
          display: 'flex',
          gap: 10,
          marginTop: 8,
          fontFamily: T.fontMono,
          alignItems: 'center',
        }}
      >
        <span style={{ color: toneColor }}>●</span>
        <span style={{ flex: 1 }}>
          {expanded ? t('agentV2.collapse') : t('agentV2.expand')}
        </span>
        {call.durationMs != null && <span>{call.durationMs}ms</span>}
      </div>
    </div>
  );
});
