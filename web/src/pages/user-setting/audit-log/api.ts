/**
 * 审计日志 API 客户端（Phase 3.1a）。
 * 后端：api/apps/audit_log_app.py  → GET /v1/audit_log/list
 */

import request from '@/utils/next-request';

export interface AuditLogEntry {
  id: string;
  user_id: string | null;
  tenant_id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  result: 'allow' | 'deny';
  reason: string | null;
  metadata: Record<string, unknown> | null;
  ip: string | null;
  user_agent: string | null;
  create_time: number;
}

export interface AuditLogQuery {
  action?: string;
  resource_type?: string;
  resource_id?: string;
  result?: 'allow' | 'deny';
  user_id?: string;
  start_ms?: number;
  end_ms?: number;
  page?: number;
  page_size?: number;
}

export interface AuditLogPage {
  logs: AuditLogEntry[];
  total: number;
  page: number;
  page_size: number;
}

export const auditLogApi = {
  async list(query: AuditLogQuery = {}): Promise<AuditLogPage> {
    const params: Record<string, string> = {};
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== '' && v !== null) {
        params[k] = String(v);
      }
    }
    const { data } = await request.get('/v1/audit_log/list', { params });
    return data?.data as AuditLogPage;
  },
};
