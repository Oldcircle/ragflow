/**
 * 数据集成员管理 API 客户端（Phase 2.1）。
 * 后端：`api/apps/kb_app.py` 的 /v1/kb/<kb_id>/member 三个端点。
 */

import request from '@/utils/next-request';

export type DatasetRole = 'owner' | 'admin' | 'contributor' | 'viewer';

export interface DatasetMember {
  user_id: string;
  role: DatasetRole;
  granted_by: string | null;
  create_time: number | null;
  implicit: boolean;
  nickname: string | null;
  email: string | null;
  avatar: string | null;
}

export const datasetMemberApi = {
  async list(kbId: string): Promise<DatasetMember[]> {
    const { data } = await request.get(`/v1/kb/${kbId}/member`);
    return (data?.data?.members ?? []) as DatasetMember[];
  },

  async grant(
    kbId: string,
    payload: { user_id?: string; email?: string; role: DatasetRole },
  ): Promise<{ kb_id: string; user_id: string; role: DatasetRole }> {
    const { data } = await request.post(`/v1/kb/${kbId}/member`, payload);
    return data?.data;
  },

  async revoke(kbId: string, userId: string): Promise<{ deleted: number }> {
    const { data } = await request.delete(`/v1/kb/${kbId}/member/${userId}`);
    return data?.data;
  },
};
