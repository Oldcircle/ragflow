/**
 * Agent v2 HTTP API 客户端。
 *
 * 复用 RAGFlow 全局 axios 实例（见 utils/next-request）及 401 拦截器。
 * Session CRUD / Tool 列表 走 axios；对话流（SSE）走原生 fetch —— 见 hooks/use-agent-stream.ts。
 */

import request from '@/utils/next-request';

export interface AgentV2Session {
  id: string;
  tenant_id: string;
  user_id: string;
  name: string;
  kb_ids: string[];
  tool_names: string[] | null;
  system_prompt: string;
  model_config_json: {
    model: string;
    base_url?: string | null;
    auth_token?: string | null;
    [k: string]: unknown;
  };
  max_turns: number;
  max_budget_usd: number | null;
  status: 'active' | 'archived' | 'deleted';
  create_time?: number;
  update_time?: number;
}

export interface AgentV2Message {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  tool_call_ids?: string[];
  usage?: Record<string, unknown>;
  create_time?: number;
}

export interface AgentV2ToolCall {
  id: string;
  session_id: string;
  message_id: string | null;
  tool_name: string;
  args: Record<string, unknown>;
  result: string;
  error: string;
  status: 'pending' | 'success' | 'error' | 'timeout';
  duration_ms: number;
  start_time?: number;
}

export interface AgentV2ToolInfo {
  name: string;
  mcp_name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export const agentV2Api = {
  async listSessions(params?: {
    page?: number;
    page_size?: number;
    status?: string;
  }) {
    const { data } = await request.get('/v1/agent_v2/session', { params });
    return (data.data?.sessions ?? []) as AgentV2Session[];
  },

  async getSession(sessionId: string) {
    const { data } = await request.get(`/v1/agent_v2/session/${sessionId}`);
    return data.data as {
      session: AgentV2Session;
      messages: AgentV2Message[];
      tool_calls: AgentV2ToolCall[];
    };
  },

  async createSession(payload: {
    name?: string;
    kb_ids: string[];
    system_prompt?: string;
    tool_names?: string[];
    model_config?: Record<string, unknown>;
    max_turns?: number;
    max_budget_usd?: number;
  }) {
    const { data } = await request.post('/v1/agent_v2/session', payload);
    return data.data as AgentV2Session;
  },

  async deleteSession(sessionId: string) {
    const { data } = await request.delete(`/v1/agent_v2/session/${sessionId}`);
    return data.data as { deleted: boolean };
  },

  async listTools() {
    const { data } = await request.get('/v1/agent_v2/tool');
    return data.data as {
      tools: AgentV2ToolInfo[];
      mcp_tool_names: string[];
    };
  },
};
