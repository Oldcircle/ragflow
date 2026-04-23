/**
 * IM 机器人渠道 API 客户端（Phase 2.2）。
 * 后端：`api/apps/bot_app.py` 与 `api/apps/bot_channel_app.py`。
 */

import request from '@/utils/next-request';

export type ChannelType = 'feishu' | 'dingtalk' | 'wecom';

export type SessionScope =
  | 'group'
  | 'group_sender'
  | 'group_topic'
  | 'group_topic_sender';

export interface BotChannel {
  id: string;
  tenant_id: string;
  channel_type: ChannelType;
  account_id: string;
  name: string;
  config_json: {
    app_id?: string;
    app_secret?: string;
    encrypt_key?: string;
    verification_token?: string;
    api_base?: string;
    [k: string]: unknown;
  };
  default_kb_ids: string[];
  default_agent_template_id: string | null;
  default_model_config_json: { llm_name?: string; factory?: string } | null;
  default_system_prompt: string;
  session_scope: SessionScope;
  enabled: boolean;
  create_time: number;
  update_time: number;
}

export interface BotConversation {
  id: string;
  conversation_key: string;
  agent_session_id: string;
  im_user_id: string | null;
  im_user_name: string | null;
  last_activity_ms: number;
  create_time: number;
}

export interface BotChannelInput {
  channel_type: ChannelType;
  account_id: string;
  name: string;
  config_json: Record<string, unknown>;
  default_kb_ids?: string[];
  default_agent_template_id?: string | null;
  default_model_config_json?: Record<string, unknown> | null;
  default_system_prompt?: string;
  session_scope?: SessionScope;
  enabled?: boolean;
}

export const botChannelApi = {
  async list(): Promise<BotChannel[]> {
    const { data } = await request.get('/v1/bot_channel/list');
    return (data?.data?.channels ?? []) as BotChannel[];
  },

  async get(channelId: string): Promise<BotChannel> {
    const { data } = await request.get(`/v1/bot_channel/${channelId}`);
    return data?.data as BotChannel;
  },

  async create(payload: BotChannelInput): Promise<BotChannel> {
    const { data } = await request.post('/v1/bot_channel', payload);
    return data?.data as BotChannel;
  },

  async update(
    channelId: string,
    fields: Partial<BotChannelInput>,
  ): Promise<BotChannel> {
    const { data } = await request.put(`/v1/bot_channel/${channelId}`, fields);
    return data?.data as BotChannel;
  },

  async remove(channelId: string): Promise<{ deleted: number }> {
    const { data } = await request.delete(`/v1/bot_channel/${channelId}`);
    return data?.data;
  },

  async listSupported(): Promise<ChannelType[]> {
    const { data } = await request.get('/v1/bot/_supported');
    return (data?.data?.channels ?? []) as ChannelType[];
  },

  async selfTest(
    channelType: ChannelType,
    accountId: string,
  ): Promise<{ ok: boolean; bot_open_id?: string | null }> {
    const { data } = await request.post(
      `/v1/bot/${channelType}/${accountId}/test`,
    );
    return data?.data ?? { ok: false };
  },

  async listConversations(
    channelType: ChannelType,
    accountId: string,
  ): Promise<BotConversation[]> {
    const { data } = await request.get(
      `/v1/bot/${channelType}/${accountId}/conversation`,
    );
    return (data?.data?.conversations ?? []) as BotConversation[];
  },

  async revokeConversation(mappingId: string): Promise<{ deleted: number }> {
    const { data } = await request.delete(`/v1/bot/conversation/${mappingId}`);
    return data?.data;
  },
};

/** 拼出公网回调 URL（用户复制到飞书后台）。 */
export function buildCallbackUrl(
  channelType: ChannelType,
  accountId: string,
): string {
  const base =
    typeof window !== 'undefined' ? window.location.origin : 'http://localhost';
  return `${base}/v1/bot/${channelType}/${accountId}/events`;
}
