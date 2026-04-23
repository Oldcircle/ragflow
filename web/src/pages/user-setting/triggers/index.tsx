/**
 * Agent Trigger 管理页（Phase 3.2）。
 *
 * 列出当前租户的定时任务 + 手动 / 编辑 / 删除 + 执行历史。
 */

import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { Button, ButtonLoading } from '@/components/ui/button';
import message from '@/components/ui/message';
import { cn } from '@/lib/utils';
import { formatDate } from '@/utils/date';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Calendar,
  CheckCircle2,
  Clock,
  LucidePlus,
  LucideTrash2,
  Pencil,
  Play,
  XCircle,
} from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ProfileSettingWrapperCard,
  Title,
} from '../components/user-setting-header';
import { AgentTrigger, TriggerInput, TriggerRun, triggerApi } from './api';
import { TriggerEditDialog } from './edit-dialog';

export default function TriggersPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<AgentTrigger | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [expandedRuns, setExpandedRuns] = useState<string | null>(null);

  const { data: triggers = [], isLoading } = useQuery<AgentTrigger[]>({
    queryKey: ['agent-triggers'],
    queryFn: () => triggerApi.list(),
  });

  const createMut = useMutation({
    mutationFn: (p: TriggerInput) => triggerApi.create(p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-triggers'] });
      setDialogOpen(false);
      message.success(t('common.success'));
    },
    onError: (err: any) =>
      message.error(err?.response?.data?.message || String(err)),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, p }: { id: string; p: Partial<TriggerInput> }) =>
      triggerApi.update(id, p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-triggers'] });
      setDialogOpen(false);
      message.success(t('common.success'));
    },
    onError: (err: any) =>
      message.error(err?.response?.data?.message || String(err)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => triggerApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-triggers'] });
      message.success(t('common.success'));
    },
    onError: (err: any) =>
      message.error(err?.response?.data?.message || String(err)),
  });

  const handleRun = async (tr: AgentTrigger) => {
    setRunningId(tr.id);
    try {
      const res = await triggerApi.runNow(tr.id);
      if (res.status === 'success') {
        message.success(t('trigger.runSuccess'));
      } else {
        message.error(t('trigger.runFailed'));
      }
      qc.invalidateQueries({ queryKey: ['agent-triggers'] });
      qc.invalidateQueries({ queryKey: ['trigger-runs', tr.id] });
    } catch (err: any) {
      message.error(err?.response?.data?.message || t('trigger.runFailed'));
    } finally {
      setRunningId(null);
    }
  };

  const handleSubmit = (payload: TriggerInput) => {
    if (editing) {
      updateMut.mutate({ id: editing.id, p: payload });
    } else {
      createMut.mutate(payload);
    }
  };

  return (
    <ProfileSettingWrapperCard
      header={
        <header className="flex items-start justify-between gap-4">
          <div>
            <Title>{t('trigger.title')}</Title>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">
              {t('trigger.description')}
            </p>
          </div>
          <Button
            onClick={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
            data-testid="trigger-add"
          >
            <LucidePlus className="size-4" />
            {t('trigger.addNew')}
          </Button>
        </header>
      }
    >
      <div className="p-6" data-testid="triggers-page">
        {isLoading ? (
          <div className="text-sm text-text-secondary">
            {t('common.loading')}
          </div>
        ) : triggers.length === 0 ? (
          <EmptyState
            onAdd={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
          />
        ) : (
          <ul className="space-y-4">
            {triggers.map((tr) => (
              <TriggerCard
                key={tr.id}
                trigger={tr}
                running={runningId === tr.id}
                expanded={expandedRuns === tr.id}
                onRun={() => handleRun(tr)}
                onEdit={() => {
                  setEditing(tr);
                  setDialogOpen(true);
                }}
                onDelete={() => deleteMut.mutate(tr.id)}
                onToggleRuns={() =>
                  setExpandedRuns((curr) => (curr === tr.id ? null : tr.id))
                }
                onToggleEnabled={(v) =>
                  updateMut.mutate({ id: tr.id, p: { enabled: v } })
                }
              />
            ))}
          </ul>
        )}
      </div>

      <TriggerEditDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initial={editing}
        loading={createMut.isPending || updateMut.isPending}
        onSubmit={handleSubmit}
      />
    </ProfileSettingWrapperCard>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border-button bg-bg-component py-16 text-center">
      <div className="mb-4 grid size-14 place-items-center rounded-2xl bg-accent-primary/10 text-accent-primary">
        <Clock className="size-7" />
      </div>
      <h3 className="text-base font-semibold text-text-primary">
        {t('trigger.emptyTitle')}
      </h3>
      <p className="mt-1 max-w-md text-sm text-text-secondary">
        {t('trigger.emptyHint')}
      </p>
      <Button className="mt-5" onClick={onAdd}>
        <LucidePlus className="size-4" />
        {t('trigger.addNew')}
      </Button>
    </div>
  );
}

interface CardProps {
  trigger: AgentTrigger;
  running: boolean;
  expanded: boolean;
  onRun: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onToggleRuns: () => void;
  onToggleEnabled: (v: boolean) => void;
}

function TriggerCard({
  trigger: tr,
  running,
  expanded,
  onRun,
  onEdit,
  onDelete,
  onToggleRuns,
  onToggleEnabled,
}: CardProps) {
  const { t } = useTranslation();
  const statusTone =
    tr.last_run_status === 'success'
      ? 'text-state-success'
      : tr.last_run_status === 'error'
        ? 'text-state-error'
        : 'text-text-disabled';

  return (
    <li
      className="rounded-xl border border-border-button bg-bg-component p-5"
      data-testid="trigger-card"
      data-trigger-id={tr.id}
    >
      <header className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-lg bg-accent-primary/10 text-accent-primary">
            <Calendar className="size-5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-text-primary">
                {tr.name}
              </span>
              <button
                onClick={() => onToggleEnabled(!tr.enabled)}
                className={cn(
                  'rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition',
                  tr.enabled
                    ? 'bg-state-success/15 text-state-success hover:bg-state-success/25'
                    : 'bg-bg-card text-text-disabled hover:bg-bg-card/80',
                )}
              >
                {tr.enabled
                  ? t('setting.botEnabled')
                  : t('setting.botDisabled')}
              </button>
            </div>
            {tr.description && (
              <div className="mt-0.5 truncate text-[11px] text-text-secondary">
                {tr.description}
              </div>
            )}
            <div className="mt-0.5 flex flex-wrap items-center gap-3 font-mono text-[11px] text-text-disabled">
              <span>{tr.cron_expr}</span>
              <span>{tr.timezone}</span>
              {tr.next_run_at && (
                <span>
                  {t('trigger.nextRun')}: {formatDate(tr.next_run_at)}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <ButtonLoading
            variant="ghost"
            size="sm"
            onClick={onRun}
            loading={running}
            data-testid="trigger-run-now"
          >
            <Play className="size-4" />
            {t('trigger.runNow')}
          </ButtonLoading>
          <Button variant="ghost" size="icon-sm" onClick={onEdit}>
            <Pencil className="size-4" />
          </Button>
          <ConfirmDeleteDialog
            title={t('trigger.deleteConfirm')}
            onOk={onDelete}
          >
            <Button variant="ghost" size="icon-sm">
              <LucideTrash2 className="size-4 text-state-error" />
            </Button>
          </ConfirmDeleteDialog>
        </div>
      </header>

      {tr.last_run_at && (
        <div
          className={cn('flex items-center gap-1.5 text-[11px]', statusTone)}
        >
          {tr.last_run_status === 'success' ? (
            <CheckCircle2 className="size-3" />
          ) : (
            <XCircle className="size-3" />
          )}
          <span>
            {t('trigger.lastRun')}: {formatDate(tr.last_run_at)} ·{' '}
            {tr.last_run_status}
          </span>
          {tr.last_run_error && (
            <span className="truncate text-state-error">
              · {tr.last_run_error.slice(0, 100)}
            </span>
          )}
        </div>
      )}

      <div className="mt-3 border-t border-border-button pt-2">
        <button
          type="button"
          onClick={onToggleRuns}
          className="text-[11px] text-accent-primary hover:underline"
        >
          {expanded ? t('trigger.hideHistory') : t('trigger.showHistory')}
        </button>
        {expanded && <RunsList triggerId={tr.id} />}
      </div>
    </li>
  );
}

function RunsList({ triggerId }: { triggerId: string }) {
  const { t } = useTranslation();
  const { data: runs = [], isLoading } = useQuery<TriggerRun[]>({
    queryKey: ['trigger-runs', triggerId],
    queryFn: () => triggerApi.listRuns(triggerId),
  });

  if (isLoading) {
    return (
      <div className="mt-2 text-[11px] text-text-disabled">
        {t('common.loading')}
      </div>
    );
  }
  if (runs.length === 0) {
    return (
      <div className="mt-2 text-[11px] text-text-disabled">
        {t('trigger.noRuns')}
      </div>
    );
  }
  return (
    <ul className="mt-2 space-y-2">
      {runs.map((r) => (
        <RunRow key={r.id} run={r} />
      ))}
    </ul>
  );
}

function RunRow({ run }: { run: TriggerRun }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const tone =
    run.status === 'success'
      ? 'text-state-success'
      : run.status === 'error' || run.status === 'timeout'
        ? 'text-state-error'
        : 'text-text-secondary';

  return (
    <li className="rounded border border-border-button bg-bg-base/50 text-[11px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 p-2 text-left"
      >
        <span className={cn('font-semibold uppercase tracking-wide', tone)}>
          {run.status}
        </span>
        <span className="text-text-disabled">{formatDate(run.started_at)}</span>
        <span className="flex-1 truncate font-mono text-text-disabled">
          {run.kicked_by}
        </span>
        {run.duration_ms != null && (
          <span className="font-mono text-text-disabled">
            {run.duration_ms}ms
          </span>
        )}
        {run.cost_usd != null && run.cost_usd > 0 && (
          <span className="font-mono text-text-disabled">
            ${run.cost_usd.toFixed(3)}
          </span>
        )}
        <span className={cn('font-mono', tone)}>
          {run.delivery_status ?? 'n/a'}
        </span>
      </button>
      {open && (
        <div className="border-t border-border-button p-2">
          {run.error ? (
            <pre className="whitespace-pre-wrap text-state-error">
              {run.error}
            </pre>
          ) : (
            <pre className="whitespace-pre-wrap text-text-primary">
              {run.result_preview || t('trigger.noPreview')}
            </pre>
          )}
          {run.delivery_error && (
            <div className="mt-2 text-state-error">
              {t('trigger.deliveryError')}: {run.delivery_error}
            </div>
          )}
        </div>
      )}
    </li>
  );
}
