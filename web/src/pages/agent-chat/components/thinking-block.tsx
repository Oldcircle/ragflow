import { memo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { T } from '../theme';
import { I } from './icons';

interface Props {
  thinking: string;
  summary?: string;
}

export const ThinkingBlock = memo(function ThinkingBlock({
  thinking,
  summary,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <div
      style={{
        border: `1px solid ${T.border}`,
        borderRadius: T.radius,
        background: T.surface2,
        fontSize: 12,
        color: T.textMuted,
        overflow: 'hidden',
      }}
    >
      <div
        onClick={() => setOpen((o) => !o)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && setOpen((o) => !o)}
        style={{
          padding: '8px 12px',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          userSelect: 'none',
        }}
      >
        <I.brain size={12} />
        <span style={{ fontWeight: 500 }}>{t('agentV2.thinking')}</span>
        {summary && (
          <span style={{ color: T.textDim, fontSize: 11 }}>{summary}</span>
        )}
        <div style={{ flex: 1 }} />
        {open ? <I.chevD size={11} /> : <I.chevR size={11} />}
      </div>
      {open && (
        <div
          style={{
            padding: '8px 12px 12px',
            borderTop: `1px solid ${T.border}`,
            fontFamily: T.fontMono,
            fontSize: 11,
            lineHeight: 1.7,
            whiteSpace: 'pre-wrap',
          }}
        >
          {thinking}
        </div>
      )}
    </div>
  );
});
