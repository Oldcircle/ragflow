import { CSSProperties, FC, MouseEvent, ReactNode, useState } from 'react';
import { T } from '../theme';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

interface Props {
  children?: ReactNode;
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  iconRight?: ReactNode;
  onClick?: (e: MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  style?: CSSProperties;
  type?: 'button' | 'submit';
  title?: string;
}

const variants: Record<
  Variant,
  { bg: string; fg: string; bd: string; hoverBg: string }
> = {
  primary: { bg: T.accent, fg: '#fff', bd: T.accent, hoverBg: T.accentHover },
  secondary: { bg: T.surface, fg: T.text, bd: T.border, hoverBg: T.surface2 },
  ghost: {
    bg: 'transparent',
    fg: T.textMuted,
    bd: 'transparent',
    hoverBg: T.surface2,
  },
  danger: { bg: T.surface, fg: T.danger, bd: T.border, hoverBg: T.dangerBg },
};

const sizes: Record<Size, { py: number; px: number; fs: number }> = {
  sm: { py: 4, px: 10, fs: 12 },
  md: { py: 6, px: 12, fs: 13 },
  lg: { py: 9, px: 16, fs: 14 },
};

export const ZButton: FC<Props> = ({
  children,
  variant = 'secondary',
  size = 'md',
  icon,
  iconRight,
  onClick,
  disabled,
  style,
  type = 'button',
  title,
}) => {
  const v = variants[variant];
  const s = sizes[size];
  const [hover, setHover] = useState(false);
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        background: hover && !disabled ? v.hoverBg : v.bg,
        color: v.fg,
        border: `1px solid ${v.bd}`,
        borderRadius: T.radius,
        padding: `${s.py}px ${s.px}px`,
        fontSize: s.fs,
        fontWeight: 500,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background .12s',
        fontFamily: T.font,
        ...style,
      }}
    >
      {icon}
      {children}
      {iconRight}
    </button>
  );
};
