/**
 * Agent v2 会话侧栏。
 *
 * 版式对齐 `next-chats/chat/sessions.tsx`：
 *   - w-[296px] + p-5 + border-r + bg-bg-component
 *   - header: avatar/title 左对齐 + 右侧图标按钮 + 收起按钮
 *   - list item: group / rounded-lg / aria-selected:bg-accent-primary/10
 *                + shadow-[inset_2px_0_0_rgb(var(--accent-primary))] 左边线高亮
 *   - 折叠态：窄带 + avatar 展开按钮 + 快捷新建
 *
 * 差异化：我们保留按时间 groupByTime 分组（今天 / 本周 / 更早），
 *         这是 Agent 场景的独有价值；对话页不分组。
 */

import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import {
  LucideBot,
  LucidePanelLeftClose,
  LucidePanelLeftOpen,
  LucidePlus,
  LucideTrash2,
} from 'lucide-react';
import { memo, MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentV2Session } from '../api';

export interface SessionSidebarProps {
  sessions: AgentV2Session[];
  loading: boolean;
  currentSessionId: string | undefined;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  /** 受控折叠；由父组件持有以便同步调整主区宽度。 */
  collapsed: boolean;
  onToggleCollapse: () => void;
}

function groupByTime(sessions: AgentV2Session[]) {
  const now = Date.now();
  const today: AgentV2Session[] = [];
  const week: AgentV2Session[] = [];
  const older: AgentV2Session[] = [];
  for (const s of sessions) {
    const ts = s.update_time ?? 0;
    const delta = now - ts;
    if (delta < 24 * 3600 * 1000) today.push(s);
    else if (delta < 7 * 24 * 3600 * 1000) week.push(s);
    else older.push(s);
  }
  return { today, week, older };
}

export const SessionSidebar = memo(function SessionSidebar({
  sessions,
  loading,
  currentSessionId,
  onSelect,
  onCreate,
  onDelete,
  collapsed,
  onToggleCollapse,
}: SessionSidebarProps) {
  const { t } = useTranslation();

  // ── 折叠态：窄带 ──
  if (collapsed) {
    return (
      <aside
        className="flex w-14 shrink-0 flex-col items-center gap-2 border-r bg-bg-component py-4"
        aria-label={t('agentV2.sessionsCollapsed')}
        data-testid="agent-v2-sessions-collapsed"
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="transparent"
              size="icon-sm"
              className="border-0"
              onClick={onToggleCollapse}
              data-testid="agent-v2-sessions-open"
            >
              <LucidePanelLeftOpen />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">
            {t('agentV2.expandSessions')}
          </TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="transparent"
              size="icon-sm"
              className="border-0"
              onClick={onCreate}
              data-testid="agent-v2-sessions-new-collapsed"
            >
              <LucidePlus />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">
            {t('agentV2.newSession')}
          </TooltipContent>
        </Tooltip>
      </aside>
    );
  }

  const groups = groupByTime(sessions);
  const total = sessions.length;

  return (
    <aside
      className="chat-workbench-panel flex w-[296px] shrink-0 flex-col border-r bg-bg-component p-5"
      role="complementary"
      data-testid="agent-v2-sessions"
    >
      {/* ── Header：产品标识 + [+] + [收起] ── */}
      <header className="flex items-center justify-between gap-3 text-base">
        <div className="flex min-w-0 items-center gap-3">
          <div
            aria-hidden="true"
            className="grid size-8 shrink-0 place-items-center rounded-md bg-accent-primary/15 text-accent-primary"
          >
            <LucideBot size={18} />
          </div>
          <span className="flex-1 truncate font-medium">
            {t('agentV2.title')}
          </span>
        </div>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              onClick={onCreate}
              size="icon-xs"
              data-testid="agent-v2-sessions-new"
            >
              <LucidePlus />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t('agentV2.newSession')}</TooltipContent>
        </Tooltip>

        <Button
          variant="transparent"
          size="icon-sm"
          className="ml-auto border-0"
          onClick={onToggleCollapse}
          data-testid="agent-v2-sessions-close"
        >
          <LucidePanelLeftClose />
        </Button>
      </header>

      {/* ── Title row：和对话页 `pt-10` 间距保持一致 ── */}
      <div className="mb-4 flex items-center justify-between pt-10">
        <div className="flex items-center gap-3">
          <span className="text-base font-bold">{t('agentV2.sessions')}</span>
          <data className="text-xs text-text-secondary" value={total}>
            {total}
          </data>
        </div>
      </div>

      {/* ── List ── */}
      <div className="scrollbar-auto flex-1 overflow-auto">
        {loading && (
          <div className="py-6 text-center text-xs text-text-secondary">
            {t('common.loading')}
          </div>
        )}
        {!loading && total === 0 && (
          <div className="px-5 py-10 text-center text-xs leading-relaxed text-text-secondary">
            {t('agentV2.noSessions')}
          </div>
        )}

        {renderGroup(
          t('agentV2.today'),
          groups.today,
          currentSessionId,
          onSelect,
          onDelete,
          t('common.delete'),
        )}
        {renderGroup(
          t('agentV2.pastWeek'),
          groups.week,
          currentSessionId,
          onSelect,
          onDelete,
          t('common.delete'),
        )}
        {renderGroup(
          t('agentV2.earlier'),
          groups.older,
          currentSessionId,
          onSelect,
          onDelete,
          t('common.delete'),
        )}
      </div>
    </aside>
  );
});

function renderGroup(
  label: string,
  list: AgentV2Session[],
  currentSessionId: string | undefined,
  onSelect: (id: string) => void,
  onDelete: (id: string) => void,
  deleteLabel: string,
) {
  if (list.length === 0) return null;
  return (
    <section key={label} className="mb-3 last:mb-0">
      <div className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-text-secondary">
        {label}
      </div>
      <ul className="space-y-1">
        {list.map((s) => (
          <SessionItem
            key={s.id}
            session={s}
            active={s.id === currentSessionId}
            onSelect={onSelect}
            onDelete={onDelete}
            deleteLabel={deleteLabel}
          />
        ))}
      </ul>
    </section>
  );
}

interface ItemProps {
  session: AgentV2Session;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  deleteLabel: string;
}

function SessionItem({
  session,
  active,
  onSelect,
  onDelete,
  deleteLabel,
}: ItemProps) {
  const { t } = useTranslation();
  const timeLabel = formatTime(session.update_time);

  return (
    <li
      aria-selected={active}
      className={cn(
        'group flex items-center gap-1 rounded-lg pr-1',
        'aria-selected:bg-accent-primary/10',
        'aria-selected:shadow-[inset_2px_0_0_rgb(var(--accent-primary))]',
        'has-[>button:focus-visible]:bg-bg-card',
      )}
    >
      <button
        type="button"
        onClick={() => onSelect(session.id)}
        data-testid="agent-v2-session-item"
        data-session-id={session.id}
        className="flex min-w-0 flex-1 flex-col items-start gap-0.5 truncate px-3 py-2 text-left text-sm text-text-secondary focus-visible:outline-none group-aria-selected:text-text-primary"
      >
        <span className="w-full truncate">
          {session.name || t('agentV2.untitled')}
        </span>
        <span className="flex items-center gap-2 text-[10px] text-text-secondary">
          {timeLabel && <span>{timeLabel}</span>}
          {session.kb_ids?.length > 0 && (
            <span className="rounded bg-bg-card px-1.5 py-0.5">
              {session.kb_ids.length} KB
            </span>
          )}
        </span>
      </button>

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="transparent"
            size="icon-xs"
            className="border-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            onClick={(e: MouseEvent<HTMLButtonElement>) => {
              e.stopPropagation();
              if (window.confirm(t('agentV2.confirmDelete'))) {
                onDelete(session.id);
              }
            }}
            data-testid="agent-v2-session-delete"
            data-session-id={session.id}
          >
            <LucideTrash2 />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{deleteLabel}</TooltipContent>
      </Tooltip>
    </li>
  );
}

function formatTime(ts?: number): string {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
