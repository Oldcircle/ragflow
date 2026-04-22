/**
 * 引用来源卡片 —— 从 rag_retrieve 工具的 result 里抽取 chunks/docs，
 * 展示在 assistant 消息下方，风格对齐 RAGFlow 原版 Chat 的引用区。
 *
 * 约束：
 * - 只认工具名结尾为 `rag_retrieve` 的结果
 * - result 可能是 string（JSON 文本）或 object，兼容两种
 * - 去重按 doc_id + chunk content hash
 */

import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import { AgentV2ToolCall } from '../api';
import { StreamingToolCall } from '../hooks/use-agent-stream';
import { T } from '../theme';
import { I } from './icons';

interface Chunk {
  doc_id: string;
  doc_name: string;
  content: string;
  similarity?: number;
  page?: number | null;
}

interface DocAgg {
  doc_id: string;
  doc_name: string;
  count: number;
}

interface Props {
  toolCalls: Array<StreamingToolCall | AgentV2ToolCall>;
}

/** 暴露给父组件的 imperative API — 让 [N] 点击触发滚动高亮。 */
export interface ReferencesListHandle {
  highlightCitation: (idx: number) => void;
}

export const ReferencesList = memo(
  forwardRef<ReferencesListHandle, Props>(function ReferencesList(
    { toolCalls },
    ref,
  ) {
    const { t } = useTranslation();
    const [openChunk, setOpenChunk] = useState<string | null>(null);
    const [activeIdx, setActiveIdx] = useState<number | null>(null);
    const chunkRefs = useRef<Record<number, HTMLDivElement | null>>({});

    const highlightCitation = useCallback((idx: number) => {
      setActiveIdx(idx);
      // 确保 details 展开
      const el = chunkRefs.current[idx - 1];
      if (el) {
        const details = el.closest('details') as HTMLDetailsElement | null;
        if (details && !details.open) details.open = true;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, []);

    useImperativeHandle(ref, () => ({ highlightCitation }), [
      highlightCitation,
    ]);

    // 点击外部清除高亮
    useEffect(() => {
      if (activeIdx == null) return;
      const timer = setTimeout(() => setActiveIdx(null), 2500);
      return () => clearTimeout(timer);
    }, [activeIdx]);

    const { chunks, docs } = useMemo(
      () => extractChunks(toolCalls),
      [toolCalls],
    );

    if (docs.length === 0) return null;

    return (
      <div
        style={{
          marginTop: 14,
          padding: '12px 14px',
          background: T.surface,
          border: `1px solid ${T.border}`,
          borderRadius: T.radius,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 11,
            color: T.textMuted,
            fontWeight: 500,
            marginBottom: 8,
            textTransform: 'uppercase',
            letterSpacing: 0.4,
          }}
        >
          <I.book size={12} />
          {t('agentV2.references')} · {docs.length} {t('agentV2.filesLabel')} ·{' '}
          {chunks.length} {t('agentV2.chunksLabel')}
        </div>

        {/* 文件列表 */}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 6,
            marginBottom: chunks.length > 0 ? 8 : 0,
          }}
        >
          {docs.map((d) => (
            <div
              key={d.doc_id}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '4px 10px',
                background: T.accentSoft,
                color: T.accent,
                border: `1px solid ${T.accentBorder}33`,
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 500,
                maxWidth: 320,
              }}
              title={d.doc_name}
            >
              <I.book size={10} />
              <span
                style={{
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {shortName(d.doc_name)}
              </span>
              <span
                style={{
                  fontSize: 10,
                  opacity: 0.75,
                  fontFamily: T.fontMono,
                }}
              >
                {d.count}
              </span>
            </div>
          ))}
        </div>

        {/* chunk 列表（可展开） */}
        {chunks.length > 0 && (
          <details
            style={{
              marginTop: 4,
              fontSize: 11,
              color: T.textMuted,
              lineHeight: 1.6,
            }}
          >
            <summary
              style={{
                cursor: 'pointer',
                userSelect: 'none',
                color: T.textDim,
                fontSize: 11,
                padding: '2px 0',
              }}
            >
              {t('agentV2.viewChunks')}
            </summary>
            <div
              style={{
                marginTop: 6,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              {chunks.map((c, i) => {
                const key = `${c.doc_id}:${i}`;
                const expanded = openChunk === key;
                const isActive = activeIdx === i + 1;
                return (
                  <div
                    key={key}
                    ref={(el) => {
                      chunkRefs.current[i] = el;
                    }}
                    onClick={() => setOpenChunk(expanded ? null : key)}
                    className={`agent-v2-ref-chunk${isActive ? ' is-active' : ''}`}
                    style={{
                      padding: '8px 10px',
                      background: T.surface2,
                      border: `1px solid ${T.border}`,
                      borderRadius: T.radius,
                      cursor: 'pointer',
                      transition: 'background 0.2s, outline 0.2s',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        marginBottom: 4,
                      }}
                    >
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: 18,
                          height: 18,
                          background: T.accent,
                          color: '#fff',
                          borderRadius: 4,
                          fontSize: 10,
                          fontWeight: 700,
                        }}
                      >
                        {i + 1}
                      </span>
                      <span
                        style={{
                          fontSize: 11,
                          color: T.text,
                          fontWeight: 500,
                          flex: 1,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {shortName(c.doc_name)}
                      </span>
                      {c.page != null && (
                        <span
                          style={{
                            fontSize: 10,
                            color: T.textDim,
                            fontFamily: T.fontMono,
                          }}
                        >
                          p.{c.page}
                        </span>
                      )}
                      {c.similarity != null && (
                        <span
                          style={{
                            fontSize: 10,
                            color: T.success,
                            fontFamily: T.fontMono,
                          }}
                        >
                          {c.similarity.toFixed(3)}
                        </span>
                      )}
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        color: T.textMuted,
                        lineHeight: 1.6,
                        whiteSpace: 'pre-wrap',
                        maxHeight: expanded ? 'unset' : 54,
                        overflow: 'hidden',
                        display: expanded ? 'block' : '-webkit-box',
                        WebkitLineClamp: 3,
                        WebkitBoxOrient: 'vertical',
                      }}
                    >
                      {c.content}
                    </div>
                  </div>
                );
              })}
            </div>
          </details>
        )}
      </div>
    );
  }),
);

function shortName(full: string): string {
  // 去掉 kb-data/<kb-name>/ 前缀，保留文件名
  const last = full.split('/').pop() ?? full;
  return last;
}

function extractChunks(calls: Array<StreamingToolCall | AgentV2ToolCall>): {
  chunks: Chunk[];
  docs: DocAgg[];
} {
  const chunks: Chunk[] = [];
  const docMap = new Map<string, DocAgg>();

  for (const call of calls) {
    const name =
      'name' in call ? call.name : (call as AgentV2ToolCall).tool_name;
    if (!name || !name.endsWith('rag_retrieve')) continue;

    // 兼容两种 result 形态
    const raw = 'result' in call ? call.result : undefined;
    const parsed = parseMaybe(raw);
    if (!parsed || typeof parsed !== 'object') continue;

    const payload = parsed as {
      chunks?: Array<{
        doc_id?: string;
        doc_name?: string;
        content?: string;
        similarity?: number;
        page?: number | null;
      }>;
      doc_aggs?: Array<{
        doc_id?: string;
        doc_name?: string;
        count?: number;
      }>;
    };

    for (const c of payload.chunks ?? []) {
      if (!c.content) continue;
      chunks.push({
        doc_id: c.doc_id ?? '',
        doc_name: c.doc_name ?? 'unknown',
        content: c.content,
        similarity: c.similarity,
        page: c.page ?? null,
      });
    }
    for (const d of payload.doc_aggs ?? []) {
      const key = d.doc_id ?? d.doc_name ?? '';
      if (!key) continue;
      const prev = docMap.get(key);
      if (prev) {
        prev.count += d.count ?? 0;
      } else {
        docMap.set(key, {
          doc_id: d.doc_id ?? '',
          doc_name: d.doc_name ?? 'unknown',
          count: d.count ?? 0,
        });
      }
    }
  }

  // 若没从 doc_aggs 拿到，从 chunks 聚合
  if (docMap.size === 0) {
    for (const c of chunks) {
      const key = c.doc_id || c.doc_name;
      const prev = docMap.get(key);
      if (prev) prev.count += 1;
      else
        docMap.set(key, { doc_id: c.doc_id, doc_name: c.doc_name, count: 1 });
    }
  }

  return {
    chunks,
    docs: Array.from(docMap.values()).sort((a, b) => b.count - a.count),
  };
}

function parseMaybe(v: unknown): unknown {
  if (v == null) return null;
  if (typeof v === 'object') return v;
  if (typeof v === 'string') {
    try {
      return JSON.parse(v);
    } catch {
      return null;
    }
  }
  return null;
}
