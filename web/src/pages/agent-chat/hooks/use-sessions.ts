/**
 * Session / Tool TanStack Query wrappers。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agentV2Api } from '../api';

const K = {
  sessions: ['agent-v2', 'sessions'] as const,
  session: (id: string) => ['agent-v2', 'session', id] as const,
  tools: ['agent-v2', 'tools'] as const,
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

export function useTools() {
  return useQuery({
    queryKey: K.tools,
    queryFn: () => agentV2Api.listTools(),
    staleTime: 60_000,
  });
}

export const AgentV2QueryKeys = K;
