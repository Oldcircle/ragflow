/**
 * Agent v2 SSE 客户端 hook。
 *
 * 读 `POST /v1/agent_v2/conversation` 的 SSE 流，把事件翻译成 React 状态。
 */

import { Authorization } from '@/constants/authorization';
import { getAuthorization } from '@/utils/authorization-util';
import { EventSourceParserStream } from 'eventsource-parser/stream';
import { useCallback, useRef, useState } from 'react';

export type AgentV2EventType =
  | 'text_delta'
  | 'thinking'
  | 'tool_call_start'
  | 'tool_call_end'
  | 'error'
  | 'end';

export interface AgentV2Event<T = any> {
  type: AgentV2EventType;
  data: T;
}

export interface StreamingToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: unknown;
  error?: string;
  durationMs?: number;
  status: 'pending' | 'success' | 'error';
  startTs: number;
}

export interface StreamingAssistantTurn {
  text: string;
  thinking: string;
  toolCalls: StreamingToolCall[];
  usage?: Record<string, unknown>;
  done: boolean;
  error?: string;
}

const EMPTY_TURN: StreamingAssistantTurn = {
  text: '',
  thinking: '',
  toolCalls: [],
  done: false,
};

export function useAgentStream() {
  const [turn, setTurn] = useState<StreamingAssistantTurn>(EMPTY_TURN);
  const [isStreaming, setIsStreaming] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    setTurn(EMPTY_TURN);
    setIsStreaming(false);
  }, []);

  const abort = useCallback(() => {
    controllerRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const send = useCallback(
    async (
      sessionId: string,
      message: string,
    ): Promise<StreamingAssistantTurn> => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      setIsStreaming(true);
      setTurn({ ...EMPTY_TURN });

      const localTurn: StreamingAssistantTurn = {
        ...EMPTY_TURN,
        toolCalls: [],
      };

      try {
        const response = await fetch('/v1/agent_v2/conversation', {
          method: 'POST',
          headers: {
            [Authorization]: getAuthorization(),
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ session_id: sessionId, message }),
          signal: controller.signal,
        });

        if (!response.ok || !response.body) {
          const text = await response.text().catch(() => '');
          localTurn.error = `HTTP ${response.status}: ${text.slice(0, 200)}`;
          localTurn.done = true;
          setTurn({ ...localTurn });
          return localTurn;
        }

        const reader = response.body
          .pipeThrough(new TextDecoderStream())
          .pipeThrough(new EventSourceParserStream())
          .getReader();

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          if (!value?.data) continue;

          let ev: AgentV2Event;
          try {
            ev = JSON.parse(value.data);
          } catch {
            continue;
          }

          switch (ev.type) {
            case 'text_delta':
              localTurn.text += ev.data?.text ?? '';
              break;
            case 'thinking':
              localTurn.thinking += ev.data?.text ?? '';
              break;
            case 'tool_call_start':
              // 不可变追加，确保 React.memo 能感知到数组变化
              localTurn.toolCalls = [
                ...localTurn.toolCalls,
                {
                  id: ev.data.id,
                  name: ev.data.name,
                  args: ev.data.args ?? {},
                  status: 'pending',
                  startTs: Date.now(),
                },
              ];
              break;
            case 'tool_call_end': {
              // 不可变替换被更新的 tool call，引用变化 ToolCallCard 才会 re-render
              localTurn.toolCalls = localTurn.toolCalls.map((c) =>
                c.id === ev.data.id
                  ? {
                      ...c,
                      result: ev.data.result,
                      error: ev.data.error ?? undefined,
                      durationMs: ev.data.duration_ms,
                      status: ev.data.error
                        ? ('error' as const)
                        : ('success' as const),
                    }
                  : c,
              );
              break;
            }
            case 'error':
              localTurn.error = `${ev.data?.code}: ${ev.data?.message}`;
              break;
            case 'end':
              localTurn.usage = ev.data?.usage ?? {};
              localTurn.done = true;
              break;
          }

          setTurn({
            ...localTurn,
            toolCalls: [...localTurn.toolCalls],
          });
        }
      } catch (err: any) {
        if (err?.name !== 'AbortError') {
          localTurn.error = err?.message ?? String(err);
        }
      } finally {
        localTurn.done = true;
        setIsStreaming(false);
        setTurn({ ...localTurn, toolCalls: [...localTurn.toolCalls] });
        controllerRef.current = null;
      }

      return localTurn;
    },
    [],
  );

  return { turn, isStreaming, send, abort, reset };
}
