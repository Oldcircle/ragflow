/**
 * 知源 design tokens — 映射到全局 CSS 变量，自动跟随 light / dark 主题。
 *
 * 历史上这里是写死的 oklch 冷灰 + teal 十六进制色，但全局默认主题是 dark，
 * 会造成 /agent-chat 变成一块亮白色块嵌在深色 shell 里。
 *
 * 现在所有值都走 `var(--*)`，而 `--bg-base` / `--bg-component` / `--bg-card`
 * / `--text-primary` 等都在 `tailwind.css` 里为 light 与 `.dark` 分别定义了值，
 * 组件用 inline style 写 `background: T.bg` 时就会自动拿到当前主题下的颜色。
 *
 * 状态色（success / warning / danger / info / 高亮）保持固定，两种主题下语义一致。
 */

export const T = {
  // Neutral surfaces — 走 CSS var，自动跟随主题
  bg: 'var(--bg-base)',
  surface: 'var(--bg-component)',
  surface2: 'var(--bg-card)',
  surface3: 'var(--bg-card)',
  border: 'var(--border-button)',
  border2: 'var(--border-button)',
  divider: 'var(--border-button)',

  // Text — 用 `rgb(var(--*))` 拆 channel，和 Tailwind 保持一致
  text: 'rgb(var(--text-primary))',
  textMuted: 'rgb(var(--text-secondary))',
  textDim: 'rgb(var(--text-secondary) / 0.65)',

  // Brand — teal，light/dark 下语义一致
  accent: 'rgb(var(--accent-primary))',
  accentHover: 'rgb(var(--accent-primary) / 0.85)',
  accentSoft: 'rgb(var(--accent-primary) / 0.15)',
  accentFg: '#ffffff',
  accentBorder: 'rgb(var(--accent-primary))',

  // Status — 固定色，两种主题下语义一致
  success: '#15803d',
  successBg: 'rgba(21, 128, 61, 0.15)',
  warning: '#b45309',
  warningBg: 'rgba(180, 83, 9, 0.15)',
  danger: '#b91c1c',
  dangerBg: 'rgba(185, 28, 28, 0.15)',
  info: '#1d4ed8',
  infoBg: 'rgba(29, 78, 216, 0.15)',

  // Highlight for retrieved chunk
  hlBg: 'rgba(234, 179, 8, 0.18)',
  hlBorder: '#eab308',

  // Typography
  font: '"Inter Tight", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
  fontMono: '"JetBrains Mono", "Menlo", ui-monospace, monospace',

  // Radius
  radius: 6,
  radiusLg: 10,
  radiusSm: 4,
} as const;

export type ThemeTokens = typeof T;
