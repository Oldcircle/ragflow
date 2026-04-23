/**
 * 审计日志查询页（Phase 3.1a）。
 *
 * 管理员查过去租户内发生过的访问/授权/机器人事件等。
 * 前端只负责展示与筛选；所有写入在后端各服务中 AuditLogService.log 自动完成。
 */

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import message from '@/components/ui/message';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { formatDate } from '@/utils/date';
import { useQuery } from '@tanstack/react-query';
import {
  Filter,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ProfileSettingWrapperCard,
  Title,
} from '../components/user-setting-header';
import { AuditLogEntry, AuditLogQuery, auditLogApi } from './api';

const DEFAULT_PAGE_SIZE = 50;

const ACTION_OPTIONS = [
  'kb.retrieve',
  'kb.list_members',
  'kb.grant',
  'kb.revoke',
  'agent_v2.create_session',
  'bot.receive',
  'bot.reply',
];

const RESOURCE_OPTIONS = [
  'knowledgebase',
  'document',
  'agent_v2_session',
  'bot_channel',
  'subagent_trace',
];

export default function AuditLogPage() {
  const { t } = useTranslation();
  const [filters, setFilters] = useState<AuditLogQuery>({
    page: 1,
    page_size: DEFAULT_PAGE_SIZE,
  });
  const [draft, setDraft] = useState<AuditLogQuery>(filters);

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['audit-log', filters],
    queryFn: () => auditLogApi.list(filters),
    placeholderData: (prev) => prev,
  });

  const apply = () => {
    const next: AuditLogQuery = {
      ...draft,
      page: 1,
      page_size: DEFAULT_PAGE_SIZE,
    };
    // 删掉空值
    for (const k of Object.keys(next) as (keyof AuditLogQuery)[]) {
      const v = next[k];
      if (v === '' || v === undefined || v === null) delete next[k];
    }
    setFilters(next);
  };

  const reset = () => {
    const clean: AuditLogQuery = { page: 1, page_size: DEFAULT_PAGE_SIZE };
    setDraft(clean);
    setFilters(clean);
  };

  const total = data?.total ?? 0;
  const logs = data?.logs ?? [];
  const page = filters.page ?? 1;
  const pages = Math.max(1, Math.ceil(total / DEFAULT_PAGE_SIZE));

  const copy = (text: string) => {
    try {
      navigator.clipboard?.writeText(text);
      message.success(t('common.copy') + ' ✓');
    } catch {
      /* ignore */
    }
  };

  return (
    <ProfileSettingWrapperCard
      header={
        <header className="flex items-start justify-between gap-4">
          <div>
            <Title>{t('audit.title')}</Title>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">
              {t('audit.description')}
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => refetch()}
            disabled={isFetching}
            data-testid="audit-refresh"
          >
            <RefreshCw className={cn('size-4', isFetching && 'animate-spin')} />
            {t('common.refresh')}
          </Button>
        </header>
      }
    >
      <div className="p-6" data-testid="audit-log-page">
        {/* Filter bar */}
        <section className="mb-4 grid gap-3 rounded-lg border border-border-button bg-bg-component/60 p-4 md:grid-cols-4">
          <FilterField label={t('audit.fieldAction')}>
            <Select
              value={draft.action ?? '__any'}
              onValueChange={(v) =>
                setDraft((d) => ({
                  ...d,
                  action: v === '__any' ? undefined : v,
                }))
              }
            >
              <SelectTrigger>
                <SelectValue placeholder={t('common.any')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__any">{t('common.any')}</SelectItem>
                {ACTION_OPTIONS.map((a) => (
                  <SelectItem key={a} value={a}>
                    {a}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterField>

          <FilterField label={t('audit.fieldResourceType')}>
            <Select
              value={draft.resource_type ?? '__any'}
              onValueChange={(v) =>
                setDraft((d) => ({
                  ...d,
                  resource_type: v === '__any' ? undefined : v,
                }))
              }
            >
              <SelectTrigger>
                <SelectValue placeholder={t('common.any')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__any">{t('common.any')}</SelectItem>
                {RESOURCE_OPTIONS.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterField>

          <FilterField label={t('audit.fieldResult')}>
            <Select
              value={draft.result ?? '__any'}
              onValueChange={(v) =>
                setDraft((d) => ({
                  ...d,
                  result: v === '__any' ? undefined : (v as 'allow' | 'deny'),
                }))
              }
            >
              <SelectTrigger>
                <SelectValue placeholder={t('common.any')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__any">{t('common.any')}</SelectItem>
                <SelectItem value="allow">{t('audit.resultAllow')}</SelectItem>
                <SelectItem value="deny">{t('audit.resultDeny')}</SelectItem>
              </SelectContent>
            </Select>
          </FilterField>

          <FilterField label={t('audit.fieldUser')}>
            <Input
              value={draft.user_id ?? ''}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  user_id: e.target.value || undefined,
                }))
              }
              placeholder={t('audit.fieldUserPlaceholder')}
            />
          </FilterField>

          <FilterField
            label={t('audit.fieldResourceId')}
            className="md:col-span-2"
          >
            <Input
              value={draft.resource_id ?? ''}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  resource_id: e.target.value || undefined,
                }))
              }
              placeholder={t('audit.fieldResourceIdPlaceholder')}
            />
          </FilterField>

          <div className="col-span-full flex justify-end gap-2">
            <Button variant="outline" onClick={reset}>
              {t('common.reset')}
            </Button>
            <Button onClick={apply}>
              <Search className="size-4" />
              {t('audit.applyFilters')}
            </Button>
          </div>
        </section>

        {/* Table */}
        <section className="overflow-hidden rounded-lg border border-border-button bg-bg-component">
          <div className="flex items-center justify-between border-b border-border-button bg-bg-base/60 px-4 py-2 text-xs text-text-secondary">
            <span className="inline-flex items-center gap-2">
              <Filter className="size-3.5" />
              {t('audit.totalCount', { count: total })}
            </span>
            <span>{t('audit.pagination', { page, pages })}</span>
          </div>

          {isLoading ? (
            <div className="p-8 text-center text-sm text-text-secondary">
              {t('common.loading')}
            </div>
          ) : logs.length === 0 ? (
            <div className="p-12 text-center">
              <ShieldCheck className="mx-auto mb-3 size-8 text-text-disabled" />
              <div className="text-sm text-text-secondary">
                {t('audit.empty')}
              </div>
            </div>
          ) : (
            <ul className="divide-y divide-border-button">
              {logs.map((log) => (
                <LogRow key={log.id} log={log} onCopy={copy} />
              ))}
            </ul>
          )}
        </section>

        {/* Pagination */}
        {pages > 1 && (
          <footer className="mt-4 flex items-center justify-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() =>
                setFilters((f) => ({
                  ...f,
                  page: Math.max(1, (f.page ?? 1) - 1),
                }))
              }
            >
              {t('common.previous')}
            </Button>
            <span className="font-mono text-xs text-text-secondary">
              {page} / {pages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() =>
                setFilters((f) => ({ ...f, page: (f.page ?? 1) + 1 }))
              }
            >
              {t('common.next')}
            </Button>
          </footer>
        )}
      </div>
    </ProfileSettingWrapperCard>
  );
}

function FilterField({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <div className="mb-1 text-[11px] font-medium text-text-disabled">
        {label}
      </div>
      {children}
    </div>
  );
}

function LogRow({
  log,
  onCopy,
}: {
  log: AuditLogEntry;
  onCopy: (text: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const deny = log.result === 'deny';

  const hasMeta = log.metadata && Object.keys(log.metadata ?? {}).length > 0;

  return (
    <li className="px-4 py-3 text-sm" data-testid="audit-row">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 text-left"
      >
        <span
          className={cn(
            'grid size-7 shrink-0 place-items-center rounded-md',
            deny
              ? 'bg-state-error/15 text-state-error'
              : 'bg-state-success/15 text-state-success',
          )}
        >
          {deny ? (
            <ShieldAlert className="size-4" />
          ) : (
            <ShieldCheck className="size-4" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[13px] font-medium text-text-primary">
              {log.action}
            </span>
            <span
              className={cn(
                'rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
                deny
                  ? 'bg-state-error/15 text-state-error'
                  : 'bg-state-success/15 text-state-success',
              )}
            >
              {log.result}
            </span>
            <span className="text-[11px] text-text-disabled">
              {log.resource_type}
              {log.resource_id ? (
                <>
                  {' · '}
                  <code className="font-mono">
                    {log.resource_id.slice(0, 12)}
                  </code>
                </>
              ) : null}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-3 text-[11px] text-text-secondary">
            <span>{formatDate(log.create_time)}</span>
            {log.user_id && (
              <span className="font-mono">
                {t('audit.byUser')} {log.user_id.slice(0, 12)}
              </span>
            )}
            {log.ip && <span className="font-mono">{log.ip}</span>}
          </div>
        </div>
      </button>

      {open && (
        <div className="mt-3 space-y-2 rounded-md border border-border-button bg-bg-base/50 p-3 text-xs">
          <Row label={t('audit.fieldReason')} value={log.reason || '—'} />
          {log.user_agent && (
            <Row
              label="User-Agent"
              value={log.user_agent}
              onCopy={() => onCopy(log.user_agent!)}
            />
          )}
          <Row
            label="Resource"
            value={`${log.resource_type} / ${log.resource_id ?? '—'}`}
            onCopy={
              log.resource_id ? () => onCopy(log.resource_id!) : undefined
            }
          />
          {hasMeta && (
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wide text-text-disabled">
                Metadata
              </div>
              <pre className="overflow-auto rounded bg-bg-component p-2 font-mono text-[11px] text-text-primary">
                {JSON.stringify(log.metadata, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function Row({
  label,
  value,
  onCopy,
}: {
  label: string;
  value: string;
  onCopy?: () => void;
}) {
  return (
    <div className="flex items-start gap-3">
      <span className="w-24 shrink-0 text-text-disabled">{label}</span>
      <span className="min-w-0 flex-1 break-all text-text-primary">
        {value}
      </span>
      {onCopy && (
        <button
          type="button"
          onClick={onCopy}
          className="shrink-0 text-[11px] text-text-secondary transition hover:text-text-primary"
        >
          copy
        </button>
      )}
    </div>
  );
}
