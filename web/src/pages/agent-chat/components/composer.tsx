/**
 * 底部输入区 — chips 展示当前 session 上下文 + 发送/中断按钮。
 */

import { KeyboardEvent, memo, useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentV2Session } from '../api';
import { T } from '../theme';
import { Badge } from './badge';
import { I, Kbd } from './icons';

interface Props {
  session: AgentV2Session | undefined;
  disabled: boolean;
  isStreaming: boolean;
  onSend: (text: string) => void;
  onAbort: () => void;
}

export const Composer = memo(function Composer({
  session,
  disabled,
  isStreaming,
  onSend,
  onAbort,
}: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState('');

  const handleSend = useCallback(() => {
    const v = value.trim();
    if (!v || disabled || isStreaming) return;
    onSend(v);
    setValue('');
  }, [value, disabled, isStreaming, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const canSend = !disabled && !isStreaming && value.trim().length > 0;

  return (
    <div style={{ padding: '0 36px 28px', background: T.bg }}>
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        <div
          style={{
            background: T.surface,
            border: `1px solid ${T.border2}`,
            borderRadius: T.radiusLg,
            padding: '10px 12px',
            boxShadow: '0 1px 3px rgba(0,0,0,.04)',
            opacity: disabled ? 0.6 : 1,
            transition: 'opacity .15s',
          }}
        >
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled || isStreaming}
            rows={2}
            placeholder={
              disabled
                ? t('agentV2.pickSessionFirst')
                : t('agentV2.inputPlaceholder')
            }
            style={{
              width: '100%',
              border: 'none',
              outline: 'none',
              resize: 'none',
              fontSize: 14,
              fontFamily: T.font,
              color: T.text,
              background: 'transparent',
              lineHeight: 1.5,
            }}
          />
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginTop: 4,
              flexWrap: 'wrap',
            }}
          >
            {session && (
              <>
                <Badge tone="neutral">
                  <I.book size={10} /> {session.kb_ids?.length ?? 0}{' '}
                  {t('agentV2.knowledgeBaseShort')}
                </Badge>
                <Badge tone="brand">
                  <I.brain size={10} />{' '}
                  {session.model_config_json?.model ?? 'claude-sonnet-4-5'}
                </Badge>
                {session.tool_names?.length ? (
                  <Badge tone="neutral">
                    <I.wrench size={10} /> {session.tool_names.length}{' '}
                    {t('agentV2.toolsShort')}
                  </Badge>
                ) : (
                  <Badge tone="neutral">
                    <I.wrench size={10} /> {t('agentV2.allTools')}
                  </Badge>
                )}
              </>
            )}
            <div style={{ flex: 1 }} />
            <div
              style={{
                fontSize: 10,
                color: T.textDim,
                display: 'flex',
                gap: 6,
                alignItems: 'center',
              }}
            >
              <Kbd>↵</Kbd> {t('agentV2.send')}
            </div>
            {isStreaming ? (
              <button
                onClick={onAbort}
                title={t('agentV2.abort')}
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 6,
                  background: T.danger,
                  color: '#fff',
                  border: 'none',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <I.stop size={11} color="#fff" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!canSend}
                title={t('agentV2.send')}
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 6,
                  background: canSend ? T.accent : T.surface3,
                  color: canSend ? '#fff' : T.textDim,
                  border: 'none',
                  cursor: canSend ? 'pointer' : 'not-allowed',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'background .15s',
                }}
              >
                <I.sendUp size={13} color={canSend ? '#fff' : T.textDim} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});
