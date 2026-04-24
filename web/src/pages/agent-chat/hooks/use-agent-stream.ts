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
  | 'subagent_start'
  | 'subagent_end'
  | 'citation_warning'
  // Phase 2.6 — interactive tools
  | 'ask_user_question'
  | 'plan_submitted'
  | 'error'
  | 'end';

export type CitationIssueKind =
  | 'missing_chunk'
  | 'number_unsupported'
  | 'no_citation_for_numeric';

export interface CitationIssue {
  kind: CitationIssueKind;
  citation_index: number | null;
  claim: string;
  detail: string;
}

export interface CitationWarning {
  issues: CitationIssue[];
  level: 'warn' | 'strict_rewritten' | 'strict_failed';
}

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
  /** Phase 2.3 — when `name === 'spawn_subagent'`, we enrich this with the
   *  live trace from subagent_start / subagent_end events so the UI can
   *  render a nested execution summary before the tool_call_end arrives. */
  subagent?: SubagentTraceInline;
}

export interface SubagentTraceInline {
  traceId: string;
  description: string;
  allowedTools: string[];
  maxTurns: number;
  maxBudgetUsd: number | null;
  status: 'running' | 'success' | 'error' | 'truncated' | 'cancelled';
  resultPreview?: string;
  error?: string;
  costUsd?: number;
  durationMs?: number;
}

// Phase 2.6 — interactive tool payloads
export interface PendingQuestion {
  pendingId: string;
  toolUseId: string | null;
  question: string;
  header: string;
  options: { label: string; description?: string }[];
  multiSelect: boolean;
}

export interface PlanAffectedResource {
  kind: 'kb' | 'doc' | 'doc_count' | 'url' | 'tag' | string;
  id?: string;
  value?: string | number;
  action?: string;
}

export interface PendingPlan {
  pendingId: string;
  toolUseId: string | null;
  title: string;
  steps: string[];
  affectedResources: PlanAffectedResource[];
  riskLevel: 'low' | 'medium' | 'high' | string;
  estimatedCostUsd: number | null;
  reversible: boolean;
  reversibleHint: string | null;
}

export interface StreamingAssistantTurn {
  text: string;
  thinking: string;
  toolCalls: StreamingToolCall[];
  usage?: Record<string, unknown>;
  done: boolean;
  error?: string;
  citationWarning?: CitationWarning;
  /** Phase 2.6 — latest ask_user_question not yet replied to */
  pendingQuestion?: PendingQuestion;
  /** Phase 2.6 — latest submit_plan not yet approved/rejected */
  pendingPlan?: PendingPlan;
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
    // Phase 2.6 v0.6-fix: preserve pendingPlan / pendingQuestion across a
    // reset so the plan-approval / ask-user-question card keeps rendering
    // after the SSE turn ends. The card lives in the streaming container
    // (`streaming?.pendingPlan`) but the parent always calls reset() on
    // turn completion (to avoid double-rendering the text that just got
    // refetched into historyMessages). Without this carry-over, the card
    // flashes for one frame and disappears as soon as `end` lands.
    //
    // On the next `send()`, the card clears naturally because send() resets
    // turn to a fresh EMPTY_TURN at the start of the request.
    setTurn((prev) => ({
      ...EMPTY_TURN,
      pendingPlan: prev.pendingPlan,
      pendingQuestion: prev.pendingQuestion,
    }));
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
            case 'subagent_start': {
              // 挂到对应 parent tool call 的 subagent 子状态上
              const parentId = ev.data?.parent_tool_call_id;
              localTurn.toolCalls = localTurn.toolCalls.map((c) =>
                c.id === parentId
                  ? {
                      ...c,
                      subagent: {
                        traceId: ev.data.trace_id,
                        description: ev.data.description ?? '',
                        allowedTools: ev.data.allowed_tools ?? [],
                        maxTurns: ev.data.max_turns ?? 10,
                        maxBudgetUsd: ev.data.max_budget_usd ?? null,
                        status: 'running',
                      },
                    }
                  : c,
              );
              break;
            }
            case 'subagent_end': {
              localTurn.toolCalls = localTurn.toolCalls.map((c) =>
                c.subagent?.traceId === ev.data?.trace_id
                  ? {
                      ...c,
                      subagent: {
                        ...c.subagent!,
                        status: ev.data.status,
                        resultPreview: ev.data.result_preview,
                        error: ev.data.error ?? undefined,
                        costUsd: ev.data.cost_usd ?? undefined,
                        durationMs: ev.data.duration_ms ?? undefined,
                      },
                    }
                  : c,
              );
              break;
            }
            case 'citation_warning': {
              localTurn.citationWarning = {
                issues: (ev.data?.issues ?? []) as CitationIssue[],
                level: ev.data?.level ?? 'warn',
              };
              break;
            }
            case 'ask_user_question': {
              localTurn.pendingQuestion = {
                pendingId: ev.data?.pending_id,
                toolUseId: ev.data?.tool_use_id ?? null,
                question: ev.data?.question ?? '',
                header: ev.data?.header ?? '',
                options: Array.isArray(ev.data?.options) ? ev.data.options : [],
                multiSelect: Boolean(ev.data?.multi_select),
              };
              break;
            }
            case 'plan_submitted': {
              localTurn.pendingPlan = {
                pendingId: ev.data?.pending_id,
                toolUseId: ev.data?.tool_use_id ?? null,
                title: ev.data?.title ?? '',
                steps: Array.isArray(ev.data?.steps) ? ev.data.steps : [],
                affectedResources: Array.isArray(ev.data?.affected_resources)
                  ? ev.data.affected_resources
                  : [],
                riskLevel: ev.data?.risk_level ?? 'medium',
                estimatedCostUsd: ev.data?.estimated_cost_usd ?? null,
                reversible: Boolean(ev.data?.reversible),
                reversibleHint: ev.data?.reversible_hint ?? null,
              };
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
