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
    /** 新会话走 TenantLLM：llm_name + factory。 */
    llm_name?: string;
    factory?: string;
    /** 老会话走内联凭据：直接给 model + 可选 base_url + auth_token。 */
    model?: string;
    base_url?: string | null;
    auth_token?: string | null;
    [k: string]: unknown;
  };
  max_turns: number;
  max_budget_usd: number | null;
  status: 'active' | 'archived' | 'deleted';
  /** Phase 2.5.1 — citation validator strictness. */
  citation_enforce_level?: 'off' | 'warn' | 'strict';
  /** Phase 2.5.1 — strict numeric-claim citation check. */
  citation_numeric_strict?: boolean;
  /** Phase 2.5.2 — multi-turn history depth. */
  history_turn_limit?: number;
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

export interface AgentV2ModelInfo {
  llm_name: string;
  factory: string;
  display_name: string;
  api_base: string;
  supported: boolean;
  note?: string | null;
}

export interface AgentV2Template {
  id: string;
  name: string;
  description: string;
  category: 'policy' | 'legal' | 'finance' | 'customer' | 'general' | string;
  icon: string;
  system_prompt: string;
  default_max_turns: number;
  default_max_budget_usd: number;
  suggested_tool_names: string[] | null;
  kb_hints: string[];
}

// Phase 2.7 Stage 1 — session attachments
export interface AgentV2Attachment {
  id: string;
  session_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  hash_xxh128: string;
  origin: 'upload' | 'web_fetch' | 'agent_generated' | string;
  source_url: string | null;
  preview_text: string | null;
  status: 'staged' | 'archived' | 'rejected' | 'expired' | string;
  archived_doc_id: string | null;
  archived_kb_id: string | null;
  archived_at: number | null;
  expires_at: number | null;
  create_time: number;
}

export interface AgentV2AttachmentUploadResponse {
  uploaded: AgentV2Attachment[];
  rejected: Array<{ filename: string; reason: string }>;
}

// ──────────────────────────────────────────────────────────────────────────
// Phase 2.8.1 — unified API error handling
//
// RAGFlow backend returns HTTP 200 even for validation / authz / quota
// errors, with a `{code, message}` envelope where success uses code=0
// (RetCode.SUCCESS) and any other code is a failure. The legacy pattern
// `return data.data as T` lies about the type — on error, `data.data` is
// undefined or null and the consumer crashes on first property access.
//
// `unwrap()` enforces the contract: it throws on non-success codes with
// the server's message attached, so consumers can catch via standard
// try/catch and the failure path is consistent across the whole module.
//
// This is intentionally local to agent-chat — applying it project-wide
// would touch dozens of unrelated callers and is out of scope.
// ──────────────────────────────────────────────────────────────────────────

interface ApiEnvelope<T> {
  code?: number;
  message?: string;
  data?: T | null;
}

class ApiError extends Error {
  code: number;
  constructor(code: number, message: string) {
    super(message);
    this.code = code;
    this.name = 'ApiError';
  }
}

function unwrap<T>(envelope: ApiEnvelope<T>): T {
  if (envelope == null) {
    throw new ApiError(-1, 'empty response from server');
  }
  if (typeof envelope.code === 'number' && envelope.code !== 0) {
    throw new ApiError(
      envelope.code,
      envelope.message ?? `request failed (code ${envelope.code})`,
    );
  }
  if (envelope.data == null) {
    // Code is 0 (success) but data is missing — backend handler bug.
    throw new ApiError(-1, envelope.message ?? 'server returned empty data');
  }
  return envelope.data;
}

export const agentV2Api = {
  async listSessions(params?: {
    page?: number;
    page_size?: number;
    status?: string;
  }) {
    const { data } = await request.get('/v1/agent_v2/session', { params });
    // List endpoint may return empty list legitimately — don't unwrap;
    // just be defensive about the optional .sessions key.
    return (data?.data?.sessions ?? []) as AgentV2Session[];
  },

  async getSession(sessionId: string) {
    const { data } = await request.get(`/v1/agent_v2/session/${sessionId}`);
    return unwrap<{
      session: AgentV2Session;
      messages: AgentV2Message[];
      tool_calls: AgentV2ToolCall[];
    }>(data);
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
    return unwrap<AgentV2Session>(data);
  },

  async deleteSession(sessionId: string) {
    const { data } = await request.delete(`/v1/agent_v2/session/${sessionId}`);
    return unwrap<{ deleted: boolean }>(data);
  },

  /**
   * Phase 2.8.1 — patch a subset of editable session fields.
   *
   * Server-side validator enforces:
   *   - kb_ids: every id must be accessible (≥VIEWER) to current user
   *   - tool_names: every name must be a registered MCP tool
   *   - max_turns 1..100, max_budget_usd > 0..100, history_turn_limit 0..100
   *   - citation_enforce_level: off|warn|strict (case-insensitive)
   *
   * Locked fields (`model_config_json`, `system_prompt`) reject — those
   * change agent identity and require a new session.
   */
  async updateSession(
    sessionId: string,
    patch: {
      name?: string;
      kb_ids?: string[];
      tool_names?: string[];
      max_turns?: number;
      max_budget_usd?: number;
      citation_enforce_level?: 'off' | 'warn' | 'strict';
      citation_numeric_strict?: boolean;
      history_turn_limit?: number;
    },
  ) {
    const { data } = await request.patch(
      `/v1/agent_v2/session/${sessionId}`,
      patch,
    );
    return unwrap<{
      session: AgentV2Session;
      updated_fields: string[];
      warnings: string[];
    }>(data);
  },

  async listTools() {
    const { data } = await request.get('/v1/agent_v2/tool');
    return unwrap<{
      tools: AgentV2ToolInfo[];
      mcp_tool_names: string[];
    }>(data);
  },

  async listModels() {
    const { data } = await request.get('/v1/agent_v2/model');
    return unwrap<{ models: AgentV2ModelInfo[] }>(data);
  },

  async listTemplates() {
    const { data } = await request.get('/v1/agent_v2/template');
    return unwrap<{ templates: AgentV2Template[] }>(data);
  },

  // ── Phase 2.7 Stage 1 — session attachments ──
  async uploadAttachments(
    sessionId: string,
    files: File[],
    onProgress?: (percent: number) => void,
    signal?: AbortSignal,
  ) {
    const form = new FormData();
    for (const f of files) {
      form.append('file', f);
    }
    // axios signature: post(url, body, config). The earlier code wrapped
    // FormData inside a config-style object ({ data: form, requestType:
    // 'form', ... }) — that's the umi-request shape, not axios. Axios
    // serialized the wrapper as JSON and the backend never saw any
    // multipart files → permanent "no file part" rejection.
    const { data } = await request.post(
      `/v1/agent_v2/session/${sessionId}/attachments`,
      form,
      {
        signal,
        // Let the browser set Content-Type with the right boundary.
        // Axios picks this up automatically when body is FormData;
        // we override to `undefined` only to clear any default JSON
        // Content-Type the global request instance might have set.
        headers: { 'Content-Type': undefined as unknown as string },
        onUploadProgress: (event) => {
          if (!onProgress) return;
          // Axios native progress event shape:
          //   { loaded: number, total?: number, progress?: number, ... }
          const pct =
            typeof event.progress === 'number'
              ? event.progress
              : event.total
                ? event.loaded / event.total
                : 0;
          onProgress(Math.round(pct * 100));
        },
      },
    );
    return unwrap<AgentV2AttachmentUploadResponse>(data);
  },

  async listAttachments(sessionId: string, includeRejected = false) {
    const { data } = await request.get(
      `/v1/agent_v2/session/${sessionId}/attachments`,
      { params: includeRejected ? { include_rejected: 1 } : undefined },
    );
    return (data?.data?.attachments ?? []) as AgentV2Attachment[];
  },

  async deleteAttachment(sessionId: string, attachmentId: string) {
    const { data } = await request.delete(
      `/v1/agent_v2/session/${sessionId}/attachments/${attachmentId}`,
    );
    return unwrap<{ rejected: boolean }>(data);
  },
};

export { ApiError, unwrap };
