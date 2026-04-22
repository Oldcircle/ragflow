/**
 * Agent v2 消息 Markdown 渲染器。
 *
 * - 支持 `[N]` 脚注语法 → 渲染为 teal pill 上标
 *   点击/hover 触发 onCitationClick 回调（用于滚动到引用卡片）
 * - 标准 markdown + GFM + 数学公式 + 代码高亮
 * - 原始文本用 DOMPurify 清洗后走 react-markdown，保留表格/换行
 */

import DOMPurify from 'dompurify';
import { memo, useMemo } from 'react';
import Markdown from 'react-markdown';
import SyntaxHighlighter from 'react-syntax-highlighter';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

import 'katex/dist/katex.min.css';

interface Props {
  content: string;
  onCitationClick?: (idx: number) => void;
}

/** 把文本里的 `[N]` / `[1][2]` 脚注包裹成上标 HTML。 */
function injectCitationSups(raw: string): string {
  // 只匹配 1-999 的数字，避免把 `[key]` 这种 markdown 链接引用误匹配
  return raw.replace(/\[(\d{1,3})\]/g, (_, n) => {
    const idx = Number(n);
    if (!idx || idx > 999) return `[${n}]`;
    return `<sup class="agent-v2-cite" data-idx="${idx}">${idx}</sup>`;
  });
}

export const AgentV2Markdown = memo(function AgentV2Markdown({
  content,
  onCitationClick,
}: Props) {
  const cleaned = useMemo(() => {
    const withSups = injectCitationSups(content || '');
    return DOMPurify.sanitize(withSups, {
      ADD_ATTR: ['data-idx'],
    });
  }, [content]);

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!onCitationClick) return;
    const target = e.target as HTMLElement;
    if (
      target.tagName === 'SUP' &&
      target.classList.contains('agent-v2-cite')
    ) {
      const idx = Number(target.getAttribute('data-idx'));
      if (idx) onCitationClick(idx);
    }
  };

  return (
    <div className="agent-v2-md" onClick={handleClick}>
      <Markdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeRaw]}
        components={{
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          code(props: any) {
            const { inline, className, children } = props;
            const match = /language-(\w+)/.exec(className || '');
            if (!inline && match) {
              return (
                <SyntaxHighlighter
                  language={match[1]}
                  PreTag="div"
                  customStyle={{
                    margin: '8px 0',
                    fontSize: 12,
                    borderRadius: 6,
                  }}
                >
                  {String(children).replace(/\n$/, '')}
                </SyntaxHighlighter>
              );
            }
            return (
              <code className={className} style={{ fontSize: '0.92em' }}>
                {children}
              </code>
            );
          },
        }}
      >
        {cleaned}
      </Markdown>
    </div>
  );
});
