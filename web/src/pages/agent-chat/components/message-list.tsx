import { memo, useCallback, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentV2Message, AgentV2ToolCall } from '../api';
import {
  StreamingAssistantTurn,
  StreamingToolCall,
} from '../hooks/use-agent-stream';
import { T } from '../theme';
import { I } from './icons';
import { AgentV2Markdown } from './markdown-content';
import { ReferencesList, ReferencesListHandle } from './references-list';
import { ThinkingBlock } from './thinking-block';

interface Props {
  historyMessages: AgentV2Message[];
  historyToolCalls: AgentV2ToolCall[];
  streaming: StreamingAssistantTurn | null;
  pendingUser: string | null;
  isStreaming: boolean;
  userInitials: string;
}

export const MessageList = memo(function MessageList({
  historyMessages,
  historyToolCalls,
  streaming,
  pendingUser,
  isStreaming,
  userInitials,
}: Props) {
  const { t } = useTranslation();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [
    historyMessages.length,
    streaming?.text,
    streaming?.toolCalls.length,
    pendingUser,
  ]);

  const toolCallsByMessage = new Map<string, AgentV2ToolCall[]>();
  for (const tc of historyToolCalls) {
    const key = tc.message_id ?? '';
    const arr = toolCallsByMessage.get(key) ?? [];
    arr.push(tc);
    toolCallsByMessage.set(key, arr);
  }

  return (
    <div
      style={{
        flex: 1,
        overflow: 'auto',
        padding: '28px 36px',
        background: T.bg,
      }}
    >
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        {historyMessages.length === 0 && !streaming && !pendingUser && (
          <EmptyHint />
        )}

        {historyMessages.map((m) => {
          if (m.role === 'user') {
            return (
              <UserMessage
                key={m.id}
                initials={userInitials}
                text={m.content}
              />
            );
          }
          if (m.role === 'assistant') {
            const mTools = toolCallsByMessage.get(m.id) ?? [];
            const toolSummary = summarizeHistoryTools(mTools);
            return (
              <AssistantMessage
                key={m.id}
                text={m.content}
                thinking={m.thinking}
                toolSummary={toolSummary}
                usage={m.usage}
                streaming={false}
                toolCalls={mTools}
              />
            );
          }
          return null;
        })}

        {pendingUser && (
          <UserMessage initials={userInitials} text={pendingUser} />
        )}

        {streaming && (
          <AssistantMessage
            text={streaming.text}
            thinking={streaming.thinking}
            toolSummary={summarizeStreamingTools(streaming)}
            usage={streaming.usage}
            streaming={isStreaming && !streaming.done}
            toolCalls={streaming.toolCalls}
          />
        )}

        {streaming?.citationWarning && (
          <CitationWarningPanel warning={streaming.citationWarning} />
        )}

        {streaming?.error && (
          <div
            style={{
              margin: '12px 0',
              padding: '10px 14px',
              border: `1px solid ${T.danger}33`,
              background: T.dangerBg,
              borderRadius: T.radius,
              fontSize: 12,
              color: T.danger,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <I.alert size={14} />
            <div>
              <div style={{ fontWeight: 500 }}>{t('agentV2.streamError')}</div>
              <div style={{ fontSize: 11, color: T.textMuted, marginTop: 2 }}>
                {streaming.error}
              </div>
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>
    </div>
  );
});

function EmptyHint() {
  const { t } = useTranslation();
  return (
    <div
      style={{
        padding: '80px 20px',
        textAlign: 'center',
      }}
    >
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: 10,
          background: `linear-gradient(135deg, ${T.accent}, #0d9488)`,
          color: '#fff',
          margin: '0 auto 16px',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <I.spark size={22} color="#fff" />
      </div>
      <div
        style={{
          fontSize: 15,
          fontWeight: 600,
          color: T.text,
          marginBottom: 6,
        }}
      >
        {t('agentV2.emptyTitle')}
      </div>
      <div
        style={{
          fontSize: 12,
          color: T.textDim,
          lineHeight: 1.7,
          maxWidth: 420,
          margin: '0 auto',
        }}
      >
        {t('agentV2.emptyHint')}
      </div>
    </div>
  );
}

function UserMessage({ initials, text }: { initials: string; text: string }) {
  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 24 }}>
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: '50%',
          background: '#475569',
          color: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 11,
          fontWeight: 600,
          flexShrink: 0,
        }}
      >
        {initials}
      </div>
      <div
        style={{
          flex: 1,
          padding: '6px 0',
          fontSize: 14,
          color: T.text,
          lineHeight: 1.7,
          whiteSpace: 'pre-wrap',
        }}
      >
        {text}
      </div>
    </div>
  );
}

interface AssistantMessageProps {
  text: string;
  thinking?: string;
  toolSummary?: string;
  usage?: Record<string, unknown>;
  streaming: boolean;
  toolCalls?: Array<StreamingToolCall | AgentV2ToolCall>;
}

function AssistantMessage({
  text,
  thinking,
  toolSummary,
  usage,
  streaming,
  toolCalls = [],
}: AssistantMessageProps) {
  const refsRef = useRef<ReferencesListHandle>(null);
  const handleCitationClick = useCallback((idx: number) => {
    refsRef.current?.highlightCitation(idx);
  }, []);

  const costStr =
    typeof usage?.total_cost_usd === 'number'
      ? `$${(usage.total_cost_usd as number).toFixed(4)}`
      : '';

  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: 7,
          background: `linear-gradient(135deg, ${T.accent}, #0d9488)`,
          color: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <I.spark size={14} color="#fff" />
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {thinking && (
          <div style={{ marginBottom: 10 }}>
            <ThinkingBlock thinking={thinking} summary={toolSummary} />
          </div>
        )}

        {text ? (
          <div style={{ position: 'relative' }}>
            <AgentV2Markdown
              content={text}
              onCitationClick={handleCitationClick}
            />
            {streaming && (
              <span
                style={{
                  display: 'inline-block',
                  width: 2,
                  height: 14,
                  marginLeft: 2,
                  background: T.text,
                  verticalAlign: 'middle',
                  animation: 'agent-v2-blink 1s infinite',
                }}
              />
            )}
          </div>
        ) : (
          streaming && (
            <div
              style={{
                fontSize: 13,
                color: T.textDim,
                fontStyle: 'italic',
              }}
            >
              …
            </div>
          )
        )}

        {toolCalls.length > 0 && (
          <ReferencesList ref={refsRef} toolCalls={toolCalls} />
        )}

        {(costStr || toolSummary) && !streaming && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              marginTop: 10,
              fontSize: 11,
              color: T.textDim,
              fontFamily: T.fontMono,
            }}
          >
            {toolSummary && <span>{toolSummary}</span>}
            {costStr && <span>{costStr}</span>}
            {typeof usage?.duration_ms === 'number' && (
              <span>{((usage.duration_ms as number) / 1000).toFixed(1)}s</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function summarizeStreamingTools(
  s: StreamingAssistantTurn,
): string | undefined {
  if (s.toolCalls.length === 0) return undefined;
  const ok = s.toolCalls.filter((c) => c.status === 'success').length;
  const err = s.toolCalls.filter((c) => c.status === 'error').length;
  const pending = s.toolCalls.filter((c) => c.status === 'pending').length;
  const parts = [`🔧 ${s.toolCalls.length}`];
  if (pending > 0) parts.push(`${pending} running`);
  if (err > 0) parts.push(`${err} err`);
  if (ok > 0 && pending === 0 && err === 0) parts.push('all ok');
  return parts.join(' · ');
}

function summarizeHistoryTools(calls: AgentV2ToolCall[]): string | undefined {
  if (calls.length === 0) return undefined;
  const ok = calls.filter((c) => c.status === 'success').length;
  return `🔧 ${calls.length} · ${ok} ok`;
}

function CitationWarningPanel({
  warning,
}: {
  warning: import('../hooks/use-agent-stream').CitationWarning;
}) {
  const { t } = useTranslation();
  const severe = warning.level === 'strict_failed';
  return (
    <div
      style={{
        margin: '12px 0',
        padding: '10px 14px',
        border: `1px solid ${severe ? T.danger : T.warning}33`,
        background: severe ? T.dangerBg : T.warningBg,
        borderRadius: T.radius,
        fontSize: 12,
        color: severe ? T.danger : T.warning,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontWeight: 600,
          marginBottom: 6,
        }}
      >
        <I.alert size={14} />
        <span>
          {t('agentV2.citationWarningTitle', {
            count: warning.issues.length,
          })}
        </span>
        <span
          style={{
            fontFamily: T.fontMono,
            fontSize: 10,
            padding: '1px 6px',
            borderRadius: 3,
            background: severe ? T.danger : T.warning,
            color: '#fff',
            textTransform: 'uppercase',
          }}
        >
          {warning.level}
        </span>
      </div>
      <ul
        style={{
          margin: 0,
          padding: '0 0 0 18px',
          listStyle: 'disc',
          fontSize: 11,
          color: T.textMuted,
          lineHeight: 1.6,
        }}
      >
        {warning.issues.slice(0, 10).map((iss, i) => (
          <li key={i}>
            <strong style={{ color: severe ? T.danger : T.warning }}>
              {issueKindLabel(iss.kind, t)}
            </strong>
            {iss.citation_index != null && ` [${iss.citation_index}]`}:{' '}
            <code
              style={{
                fontFamily: T.fontMono,
                fontSize: 10,
                background: T.surface2,
                padding: '0 4px',
                borderRadius: 3,
              }}
            >
              {iss.claim}
            </code>{' '}
            — <span style={{ color: T.textDim }}>{iss.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function issueKindLabel(kind: string, t: (k: string) => string): string {
  switch (kind) {
    case 'missing_chunk':
      return t('agentV2.citationKindMissing');
    case 'number_unsupported':
      return t('agentV2.citationKindNumber');
    case 'no_citation_for_numeric':
      return t('agentV2.citationKindNoCite');
    default:
      return kind;
  }
}
