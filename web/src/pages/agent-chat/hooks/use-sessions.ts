/**
 * Session / Tool TanStack Query wrappers。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentV2Api } from '../api';

const K = {
  sessions: ['agent-v2', 'sessions'] as const,
  session: (id: string) => ['agent-v2', 'session', id] as const,
  tools: ['agent-v2', 'tools'] as const,
  models: ['agent-v2', 'models'] as const,
};

export function useSessions() {
  return useQuery({
    queryKey: K.sessions,
    queryFn: () => agentV2Api.listSessions(),
    staleTime: 5_000,
  });
}

export function useSession(sessionId: string | undefined) {
  return useQuery({
    queryKey: sessionId
      ? K.session(sessionId)
      : ['agent-v2', 'session', 'none'],
    queryFn: () => agentV2Api.getSession(sessionId!),
    enabled: Boolean(sessionId),
  });
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: agentV2Api.createSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: K.sessions }),
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => agentV2Api.deleteSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: K.sessions }),
  });
}

/**
 * Phase 2.8.1 — patch a subset of editable session fields.
 * Invalidates sessions list + the specific session detail on success.
 */
export function useUpdateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      sessionId,
      patch,
    }: {
      sessionId: string;
      patch: Parameters<typeof agentV2Api.updateSession>[1];
    }) => agentV2Api.updateSession(sessionId, patch),
    onSuccess: (_data, { sessionId }) => {
      qc.invalidateQueries({ queryKey: K.sessions });
      qc.invalidateQueries({ queryKey: K.session(sessionId) });
    },
  });
}

export function useTools() {
  return useQuery({
    queryKey: K.tools,
    queryFn: () => agentV2Api.listTools(),
    staleTime: 60_000,
  });
}

export function useAvailableModels() {
  return useQuery({
    queryKey: K.models,
    queryFn: () => agentV2Api.listModels(),
    staleTime: 30_000,
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: ['agent-v2', 'templates'] as const,
    queryFn: () => agentV2Api.listTemplates(),
    staleTime: 300_000, // 模板不常变
  });
}

export const AgentV2QueryKeys = K;
