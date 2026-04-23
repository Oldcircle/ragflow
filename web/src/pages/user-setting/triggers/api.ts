/**
 * Agent Trigger API 客户端（Phase 3.2）。
 * 后端：api/apps/agent_trigger_app.py → /v1/agent_trigger
 */

import request from '@/utils/next-request';

export type TriggerType = 'cron' | 'manual' | 'webhook';
export type DeliveryKind = 'audit_only' | 'feishu_bot';
export type RunStatus =
  | 'running'
  | 'success'
  | 'error'
  | 'timeout'
  | 'cancelled';

export interface AgentTrigger {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  trigger_type: TriggerType;
  cron_expr: string | null;
  timezone: string;
  agent_session_id: string;
  prompt: string;
  max_turns: number;
  max_budget_usd: number | null;
  delivery_kind: DeliveryKind;
  delivery_config: {
    bot_channel_id?: string;
    chat_id?: string;
    [k: string]: unknown;
  };
  enabled: boolean;
  next_run_at: number | null;
  last_run_at: number | null;
  last_run_status: RunStatus | null;
  last_run_error: string | null;
  create_time: number;
  update_time: number;
}

export interface TriggerInput {
  name: string;
  description?: string;
  trigger_type?: TriggerType;
  cron_expr?: string;
  timezone?: string;
  agent_session_id: string;
  prompt: string;
  max_turns?: number;
  max_budget_usd?: number;
  delivery_kind?: DeliveryKind;
  delivery_config?: Record<string, unknown>;
  enabled?: boolean;
}

export interface TriggerRun {
  id: string;
  trigger_id: string;
  kicked_by: 'scheduler' | 'manual' | 'webhook';
  status: RunStatus;
  started_at: number;
  completed_at: number | null;
  duration_ms: number | null;
  result_preview: string | null;
  error: string | null;
  token_usage_json: Record<string, unknown> | null;
  cost_usd: number | null;
  delivery_status: 'skipped' | 'success' | 'error' | null;
  delivery_error: string | null;
}

export const triggerApi = {
  async list(): Promise<AgentTrigger[]> {
    const { data } = await request.get('/v1/agent_trigger/list');
    return (data?.data?.triggers ?? []) as AgentTrigger[];
  },

  async get(id: string): Promise<AgentTrigger> {
    const { data } = await request.get(`/v1/agent_trigger/${id}`);
    return data?.data as AgentTrigger;
  },

  async create(payload: TriggerInput): Promise<AgentTrigger> {
    const { data } = await request.post('/v1/agent_trigger', payload);
    return data?.data as AgentTrigger;
  },

  async update(
    id: string,
    fields: Partial<TriggerInput>,
  ): Promise<AgentTrigger> {
    const { data } = await request.put(`/v1/agent_trigger/${id}`, fields);
    return data?.data as AgentTrigger;
  },

  async remove(id: string): Promise<{ deleted: number }> {
    const { data } = await request.delete(`/v1/agent_trigger/${id}`);
    return data?.data;
  },

  async runNow(id: string): Promise<{ run_id: string; status: RunStatus }> {
    const { data } = await request.post(`/v1/agent_trigger/${id}/run`);
    return data?.data;
  },

  async listRuns(id: string): Promise<TriggerRun[]> {
    const { data } = await request.get(`/v1/agent_trigger/${id}/run`);
    return (data?.data?.runs ?? []) as TriggerRun[];
  },

  async previewCron(
    cron_expr: string,
    timezone = 'Asia/Shanghai',
  ): Promise<number[]> {
    const { data } = await request.post('/v1/agent_trigger/cron_preview', {
      cron_expr,
      timezone,
    });
    return (data?.data?.next_runs ?? []) as number[];
  },
};
