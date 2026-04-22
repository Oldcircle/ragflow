/**
 * 轻量行内 SVG 图标集，匹配 Linear/Vercel 风格线条。
 * 直接从 Claude Design 稿移植。
 */

import { CSSProperties, FC } from 'react';

type IconProps = {
  size?: number;
  color?: string;
  sw?: number;
  style?: CSSProperties;
};

function mk(d: string, vb = '0 0 24 24'): FC<IconProps> {
  const Icon: FC<IconProps> = ({
    size = 16,
    color = 'currentColor',
    sw = 1.6,
    style = {},
  }) => (
    <svg
      width={size}
      height={size}
      viewBox={vb}
      fill="none"
      stroke={color}
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0, ...style }}
      dangerouslySetInnerHTML={{ __html: d }}
    />
  );
  Icon.displayName = 'Icon';
  return Icon;
}

export const I = {
  chat: mk(
    '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
  ),
  brain: mk(
    '<path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0-3 3v1a3 3 0 0 0 0 6v1a3 3 0 0 0 3 3h1a3 3 0 0 0 3 3 3 3 0 0 0 3-3h1a3 3 0 0 0 3-3v-1a3 3 0 0 0 0-6V9a3 3 0 0 0-3-3V5a3 3 0 0 0-3-3z"/>',
  ),
  spark: mk(
    '<path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z"/><path d="M19 14l.8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8z"/>',
  ),
  search: mk('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>'),
  plus: mk(
    '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  ),
  book: mk(
    '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  ),
  wrench: mk(
    '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  ),
  sendUp: mk(
    '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
  ),
  stop: mk('<rect x="5" y="5" width="14" height="14" rx="2"/>'),
  copy: mk(
    '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  ),
  thumbUp: mk(
    '<path d="M7 10v12M15 5.88L14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H7V10l5-9a4 4 0 0 1 3 4v.88z"/>',
  ),
  thumbDn: mk(
    '<path d="M17 14V2M9 18.12L10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H17v12l-5 9a4 4 0 0 1-3-4v-.88z"/>',
  ),
  refresh: mk(
    '<polyline points="23 4 23 10 17 10"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/><polyline points="1 20 1 14 7 14"/>',
  ),
  chevR: mk('<polyline points="9 18 15 12 9 6"/>'),
  chevD: mk('<polyline points="6 9 12 15 18 9"/>'),
  trash: mk(
    '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/>',
  ),
  check: mk('<polyline points="20 6 9 17 4 12"/>'),
  x: mk(
    '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  ),
  alert: mk(
    '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
  ),
  settings: mk(
    '<circle cx="12" cy="12" r="3"/><path d="M12 1v6M12 17v6M4.22 4.22l4.24 4.24M15.54 15.54l4.24 4.24M1 12h6M17 12h6M4.22 19.78l4.24-4.24M15.54 8.46l4.24-4.24"/>',
  ),
};

export const Kbd: FC<{ children: React.ReactNode; style?: CSSProperties }> = ({
  children,
  style,
}) => (
  <span
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: '1px 5px',
      background: '#ffffff',
      border: '1px solid #d6d3d1',
      borderBottomWidth: 2,
      borderRadius: 3,
      fontSize: 10,
      fontFamily: '"JetBrains Mono", ui-monospace, monospace',
      color: '#57534e',
      lineHeight: 1.3,
      ...style,
    }}
  >
    {children}
  </span>
);
