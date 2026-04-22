import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentV2Session } from '../api';
import { T } from '../theme';
import { Badge } from './badge';
import { ZButton } from './button';
import { I } from './icons';

export interface SessionSidebarProps {
  sessions: AgentV2Session[];
  loading: boolean;
  currentSessionId: string | undefined;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
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

const SectionLabel = ({ children }: { children: React.ReactNode }) => (
  <div
    style={{
      padding: '10px 14px 4px',
      fontSize: 10,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: 0.6,
      color: T.textDim,
    }}
  >
    {children}
  </div>
);

export const SessionSidebar = memo(function SessionSidebar({
  sessions,
  loading,
  currentSessionId,
  onSelect,
  onCreate,
  onDelete,
}: SessionSidebarProps) {
  const { t } = useTranslation();
  const groups = groupByTime(sessions);

  const renderGroup = (label: string, list: AgentV2Session[]) => {
    if (list.length === 0) return null;
    return (
      <div key={label}>
        <SectionLabel>{label}</SectionLabel>
        <div
          style={{
            padding: '0 8px',
            display: 'flex',
            flexDirection: 'column',
            gap: 1,
          }}
        >
          {list.map((s) => (
            <SessionItem
              key={s.id}
              session={s}
              active={s.id === currentSessionId}
              onSelect={onSelect}
              onDelete={onDelete}
            />
          ))}
        </div>
      </div>
    );
  };

  return (
    <aside
      style={{
        width: 260,
        background: T.surface2,
        borderRight: `1px solid ${T.border}`,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        flexShrink: 0,
      }}
    >
      <div style={{ padding: 12 }}>
        <ZButton
          variant="primary"
          size="md"
          icon={<I.plus size={13} />}
          style={{ width: '100%' }}
          onClick={onCreate}
        >
          {t('agentV2.newSession')}
        </ZButton>
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        {loading && (
          <div
            style={{
              padding: '24px 16px',
              fontSize: 12,
              color: T.textDim,
              textAlign: 'center',
            }}
          >
            {t('common.loading')}
          </div>
        )}
        {!loading && sessions.length === 0 && (
          <div
            style={{
              padding: '40px 20px',
              fontSize: 12,
              color: T.textDim,
              textAlign: 'center',
              lineHeight: 1.6,
            }}
          >
            {t('agentV2.noSessions')}
          </div>
        )}
        {renderGroup(t('agentV2.today'), groups.today)}
        {renderGroup(t('agentV2.pastWeek'), groups.week)}
        {renderGroup(t('agentV2.earlier'), groups.older)}
      </div>
    </aside>
  );
});

interface ItemProps {
  session: AgentV2Session;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

function SessionItem({ session, active, onSelect, onDelete }: ItemProps) {
  const { t } = useTranslation();
  const timeLabel = formatTime(session.update_time);
  return (
    <div
      onClick={() => onSelect(session.id)}
      onKeyDown={(e) => e.key === 'Enter' && onSelect(session.id)}
      role="button"
      tabIndex={0}
      style={{
        position: 'relative',
        padding: '9px 10px',
        borderRadius: T.radius,
        fontSize: 12,
        cursor: 'pointer',
        background: active ? T.surface3 : 'transparent',
        color: active ? T.text : T.textMuted,
        fontWeight: active ? 500 : 400,
        transition: 'background .1s',
      }}
      className="group"
    >
      <div
        style={{
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          paddingRight: 20,
        }}
      >
        {session.name || t('agentV2.untitled')}
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginTop: 2,
          fontSize: 10,
          color: T.textDim,
        }}
      >
        <span>{timeLabel}</span>
        {session.kb_ids?.length > 0 && (
          <Badge tone="neutral" style={{ fontSize: 9, padding: '1px 5px' }}>
            <I.book size={8} /> {session.kb_ids.length}
          </Badge>
        )}
      </div>
      <button
        onClick={(e) => {
          e.stopPropagation();
          if (window.confirm(t('agentV2.confirmDelete'))) {
            onDelete(session.id);
          }
        }}
        className="opacity-0 group-hover:opacity-100"
        style={{
          position: 'absolute',
          right: 6,
          top: '50%',
          transform: 'translateY(-50%)',
          width: 22,
          height: 22,
          border: 'none',
          background: 'transparent',
          color: T.textDim,
          borderRadius: 4,
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition: 'opacity .15s',
        }}
        title={t('common.delete')}
      >
        <I.trash size={12} />
      </button>
    </div>
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
