/**
 * 知源 design tokens（从 Claude Design 稿移植，oklch 冷灰 + teal 品牌色）。
 *
 * 只在本页面局部使用，避免污染 RAGFlow 全局 Tailwind 变量。
 * 通过 React inline style 注入，无需修改 Tailwind 配置。
 */

export const T = {
  // Neutral scale
  bg: '#fafafa',
  surface: '#ffffff',
  surface2: '#f5f5f4',
  surface3: '#efeeec',
  border: '#e7e5e4',
  border2: '#d6d3d1',
  divider: '#eeeceb',

  // Text
  text: '#1c1917',
  textMuted: '#57534e',
  textDim: '#a8a29e',

  // Brand — teal
  accent: '#0f766e',
  accentHover: '#115e55',
  accentSoft: '#ccfbf1',
  accentFg: '#ffffff',
  accentBorder: '#14b8a6',

  // Status
  success: '#15803d',
  successBg: '#dcfce7',
  warning: '#b45309',
  warningBg: '#fef3c7',
  danger: '#b91c1c',
  dangerBg: '#fee2e2',
  info: '#1d4ed8',
  infoBg: '#dbeafe',

  // Highlight for retrieved chunk
  hlBg: '#fff7d6',
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
