/**
 * Agent v2 工作台主页面。
 *
 * 3 列布局：左 = 会话列表 / 中 = 消息流 + 输入框 / 右 = 工具调用
 * 设计语言来自 Claude Design 的"知源·企业知识库"稿，适配 Agent v2 语义。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { AgentV2Session } from './api';
import { Badge } from './components/badge';
import { Composer } from './components/composer';
import { useAttachments } from './hooks/use-attachments';
import { I } from './components/icons';
import { MessageList } from './components/message-list';
import { NewSessionDialog } from './components/new-session-dialog';
import { SessionSidebar } from './components/session-sidebar';
import { ToolCallsSidebar } from './components/tool-calls-sidebar';
import { useAgentStream } from './hooks/use-agent-stream';
import {
  useCreateSession,
  useDeleteSession,
  useSession,
  useSessions,
} from './hooks/use-sessions';
import './styles.css';
import { T } from './theme';

export default function AgentChatPage() {
  const { t } = useTranslation();
  const { data: userInfo } = useFetchUserInfo();

  const [currentSessionId, setCurrentSessionId] = useState<string>();
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [newDialogOpen, setNewDialogOpen] = useState(false);
  // 会话侧栏折叠；本地持久化，避免每次刷新弹回默认态
  const [sessionsCollapsed, setSessionsCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem('agent-v2:sessionsCollapsed') === '1';
    } catch {
      return false;
    }
  });
  const toggleSessionsCollapsed = useCallback(() => {
    setSessionsCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('agent-v2:sessionsCollapsed', next ? '1' : '0');
      } catch {
        // 忽略 localStorage 写入失败（隐私模式等）
      }
      return next;
    });
  }, []);

  const { data: sessions = [], isLoading: sessionsLoading } = useSessions();
  const { data: sessionDetail, refetch: refetchSessionDetail } =
    useSession(currentSessionId);
  const createMut = useCreateSession();
  const deleteMut = useDeleteSession();
  const { turn, isStreaming, send, abort, reset } = useAgentStream();
  // Phase 2.7 Stage 4 — session attachment upload state
  const attachments = useAttachments(currentSessionId);

  const currentSession: AgentV2Session | undefined = sessionDetail?.session;
  const historyMessages = useMemo(
    () => sessionDetail?.messages ?? [],
    [sessionDetail?.messages],
  );
  const historyToolCalls = useMemo(
    () => sessionDetail?.tool_calls ?? [],
    [sessionDetail?.tool_calls],
  );

  // 自动选中第一个 session
  useEffect(() => {
    if (!currentSessionId && sessions.length > 0) {
      setCurrentSessionId(sessions[0].id);
    }
    if (currentSessionId && !sessions.find((s) => s.id === currentSessionId)) {
      setCurrentSessionId(sessions[0]?.id);
    }
  }, [sessions, currentSessionId]);

  // 切换 session 时重置流式状态
  useEffect(() => {
    reset();
    setPendingUser(null);
  }, [currentSessionId, reset]);

  const handleSend = useCallback(
    async (text: string) => {
      if (!currentSessionId) return;
      setPendingUser(text);
      try {
        const result = await send(currentSessionId, text);
        if (result.error) {
          toast.error(result.error);
        }
        await refetchSessionDetail();
        // refetch 完成后 `historyMessages` 已经包含刚才那轮 assistant 消息；
        // 若不清掉 streaming turn，`hasStreamingTurn` 仍会看到 `turn.text`，
        // 导致同一条 assistant 消息被渲染两遍。
        reset();
      } finally {
        setPendingUser(null);
      }
    },
    [currentSessionId, send, refetchSessionDetail, reset],
  );

  const handleCreateSession = useCallback(
    (values: Parameters<typeof createMut.mutateAsync>[0]) => {
      createMut.mutate(values, {
        onSuccess: (newSession) => {
          setCurrentSessionId(newSession.id);
          setNewDialogOpen(false);
          toast.success(t('agentV2.sessionCreated'));
        },
        onError: (err: any) => {
          toast.error(err?.message ?? t('agentV2.createFailed'));
        },
      });
    },
    [createMut, t],
  );

  const handleDeleteSession = useCallback(
    (id: string) => {
      deleteMut.mutate(id, {
        onSuccess: () => {
          if (id === currentSessionId) {
            setCurrentSessionId(undefined);
          }
          toast.success(t('agentV2.sessionDeleted'));
        },
      });
    },
    [deleteMut, currentSessionId, t],
  );

  // Phase 2.6 v0.6-fix: a pending plan / question must keep the streaming
  // container rendered even after `end`, otherwise the approval card vanishes
  // and the user has no way to click Approve/Reject. useAgentStream.reset()
  // carries these two fields across, but they only reach the UI if the
  // parent also keeps `streaming` non-null while they exist.
  const hasStreamingTurn =
    isStreaming ||
    Boolean(
      turn.text ||
      turn.toolCalls.length > 0 ||
      turn.thinking ||
      turn.pendingPlan ||
      turn.pendingQuestion,
    );

  const userInitials = getInitials(
    userInfo?.nickname || userInfo?.email || 'User',
  );

  return (
    <div
      className="agent-v2-root"
      style={{
        height: '100%',
        display: 'flex',
        background: T.bg,
        color: T.text,
        fontFamily: T.font,
      }}
    >
      {/* 左：会话列表（可折叠，本地持久化） */}
      <SessionSidebar
        sessions={sessions}
        loading={sessionsLoading}
        currentSessionId={currentSessionId}
        onSelect={setCurrentSessionId}
        onCreate={() => setNewDialogOpen(true)}
        onDelete={handleDeleteSession}
        collapsed={sessionsCollapsed}
        onToggleCollapse={toggleSessionsCollapsed}
      />

      {/* 中：消息流 + 输入框 */}
      <main
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
        }}
      >
        <Topbar session={currentSession} />

        <MessageList
          historyMessages={historyMessages}
          historyToolCalls={historyToolCalls}
          streaming={hasStreamingTurn ? turn : null}
          pendingUser={pendingUser}
          isStreaming={isStreaming}
          userInitials={userInitials}
          onSubmitAnswer={handleSend}
        />

        <Composer
          session={currentSession}
          disabled={!currentSessionId}
          isStreaming={isStreaming}
          onSend={handleSend}
          onAbort={abort}
          attachments={attachments.staged}
          onUploadFiles={attachments.upload}
          onRemoveAttachment={attachments.remove}
          onClearAttachments={attachments.clearStaged}
        />
      </main>

      {/* 右：工具调用 */}
      <ToolCallsSidebar
        streamingToolCalls={turn.toolCalls}
        historyToolCalls={historyToolCalls}
      />

      <NewSessionDialog
        open={newDialogOpen}
        onOpenChange={setNewDialogOpen}
        onSubmit={handleCreateSession}
        submitting={createMut.isPending}
      />
    </div>
  );
}

function Topbar({ session }: { session: AgentV2Session | undefined }) {
  const { t } = useTranslation();
  return (
    <div
      style={{
        height: 56,
        borderBottom: `1px solid ${T.border}`,
        background: T.surface,
        display: 'flex',
        alignItems: 'center',
        padding: '0 24px',
        flexShrink: 0,
        gap: 16,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 11,
            color: T.textDim,
            marginBottom: 2,
          }}
        >
          <I.chat size={10} />
          <span>Agent v2</span>
          {session && (
            <>
              <I.chevR size={10} />
              <span style={{ color: T.textMuted }}>
                {session.name || t('agentV2.untitled')}
              </span>
            </>
          )}
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 10,
            minWidth: 0,
          }}
        >
          <div
            style={{
              fontSize: 15,
              fontWeight: 600,
              color: T.text,
              letterSpacing: -0.2,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {session?.name || t('agentV2.title')}
          </div>
          {session && (
            <div
              style={{
                fontSize: 12,
                color: T.textDim,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {session.kb_ids?.length ?? 0} KB · max {session.max_turns} turns
            </div>
          )}
        </div>
      </div>
      {session && (
        <Badge tone="brand">
          <I.brain size={10} />
          {session.model_config_json?.llm_name ??
            session.model_config_json?.model ??
            '—'}
        </Badge>
      )}
    </div>
  );
}

function getInitials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return 'U';
  // 中文取前两个字
  if (/[一-龥]/.test(trimmed)) return trimmed.slice(0, 1);
  const parts = trimmed.split(/\s+|@/).filter(Boolean);
  if (parts.length === 0) return trimmed.slice(0, 2).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}
