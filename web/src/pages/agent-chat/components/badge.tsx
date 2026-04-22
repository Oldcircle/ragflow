import { CSSProperties, FC, ReactNode } from 'react';
import { T } from '../theme';

type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info';

interface Props {
  children: ReactNode;
  tone?: Tone;
  style?: CSSProperties;
}

const tones: Record<Tone, { bg: string; fg: string; bd: string }> = {
  neutral: { bg: T.surface2, fg: T.textMuted, bd: T.border },
  brand: { bg: T.accentSoft, fg: T.accent, bd: 'transparent' },
  success: { bg: T.successBg, fg: T.success, bd: 'transparent' },
  warning: { bg: T.warningBg, fg: T.warning, bd: 'transparent' },
  danger: { bg: T.dangerBg, fg: T.danger, bd: 'transparent' },
  info: { bg: T.infoBg, fg: T.info, bd: 'transparent' },
};

export const Badge: FC<Props> = ({ children, tone = 'neutral', style }) => {
  const t = tones[tone];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        background: t.bg,
        color: t.fg,
        border: `1px solid ${t.bd}`,
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 500,
        whiteSpace: 'nowrap',
        lineHeight: 1.4,
        ...style,
      }}
    >
      {children}
    </span>
  );
};
