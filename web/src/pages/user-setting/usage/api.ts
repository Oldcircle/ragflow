/**
 * 租户配额 + 用量 API 客户端（Phase 3.1b）。
 * 后端：api/apps/tenant_quota_app.py → /v1/tenant_quota
 */

import request from '@/utils/next-request';

export interface QuotaLimits {
  kb_max: number;
  doc_max: number;
  token_month_max: number;
  api_rps_max: number;
  bot_message_day_max: number;
  subagent_day_max: number;
  hard_enforce: boolean;
}

export interface UsageDaily {
  date_ymd: number;
  token_in: number;
  token_out: number;
  cost_usd: number;
  api_requests: number;
  bot_messages: number;
  subagent_spawns: number;
}

export interface QuotaSnapshot {
  quota: QuotaLimits;
  usage: {
    kb_count: number;
    doc_count: number;
    today: UsageDaily;
    month: Omit<UsageDaily, 'date_ymd'>;
  };
}

export const quotaApi = {
  async getSnapshot(): Promise<QuotaSnapshot> {
    const { data } = await request.get('/v1/tenant_quota');
    return data?.data as QuotaSnapshot;
  },

  async getRange(days: number = 30): Promise<UsageDaily[]> {
    const { data } = await request.get('/v1/tenant_quota/range', {
      params: { days },
    });
    return (data?.data?.entries ?? []) as UsageDaily[];
  },
};
