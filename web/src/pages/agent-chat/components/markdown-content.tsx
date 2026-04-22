/**
 * Agent v2 消息 Markdown 渲染器。
 *
 * 和 RAGFlow 原版 `components/markdown-content/` 不一样：
 * - 原版针对 Dialog 的 [N] 引用语法做了大量后处理
 * - 我们这里 Agent 输出是纯 markdown，无内嵌引用，更简单
 * - 引用来源单独在消息下方以卡片展示（见 references-list.tsx）
 */

import DOMPurify from 'dompurify';
import { memo } from 'react';
import Markdown from 'react-markdown';
import SyntaxHighlighter from 'react-syntax-highlighter';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

import 'katex/dist/katex.min.css';

interface Props {
  content: string;
}

export const AgentV2Markdown = memo(function AgentV2Markdown({
  content,
}: Props) {
  const cleaned = DOMPurify.sanitize(content || '');
  return (
    <div className="agent-v2-md">
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
