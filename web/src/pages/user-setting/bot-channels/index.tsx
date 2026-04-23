/**
 * 机器人渠道管理页（Phase 2.2）。
 *
 * 路由：/user-setting/bot-channels
 */

import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { Button, ButtonLoading } from '@/components/ui/button';
import message from '@/components/ui/message';
import { cn } from '@/lib/utils';
import { formatDate } from '@/utils/date';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bot,
  CheckCircle2,
  Copy,
  LucidePlus,
  LucideTrash2,
  Pencil,
  PlugZap,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ProfileSettingWrapperCard,
  Title,
} from '../components/user-setting-header';
import {
  BotChannel,
  botChannelApi,
  BotChannelInput,
  buildCallbackUrl,
} from './api';
import { BotChannelEditDialog } from './edit-dialog';

function useCopyText() {
  const { t } = useTranslation();
  return async (text: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      message.success(t('setting.botCopied'));
    } catch {
      message.error('copy failed');
    }
  };
}

export default function BotChannelsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<BotChannel | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const copy = useCopyText();

  const { data: channels = [], isLoading } = useQuery<BotChannel[]>({
    queryKey: ['bot-channels'],
    queryFn: () => botChannelApi.list(),
  });

  const createMut = useMutation({
    mutationFn: (payload: BotChannelInput) => botChannelApi.create(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bot-channels'] });
      setDialogOpen(false);
      message.success(t('setting.botSavedTitle'));
    },
    onError: (err: any) => {
      message.error(err?.response?.data?.message || String(err));
    },
  });

  const updateMut = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: Partial<BotChannelInput>;
    }) => botChannelApi.update(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bot-channels'] });
      setDialogOpen(false);
      message.success(t('common.success'));
    },
    onError: (err: any) => {
      message.error(err?.response?.data?.message || String(err));
    },
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => botChannelApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bot-channels'] });
      message.success(t('common.success'));
    },
    onError: (err: any) => {
      message.error(err?.response?.data?.message || String(err));
    },
  });

  const handleSubmit = (payload: BotChannelInput) => {
    if (editing) {
      updateMut.mutate({ id: editing.id, payload });
    } else {
      createMut.mutate(payload);
    }
  };

  const handleTest = async (bc: BotChannel) => {
    setTestingId(bc.id);
    try {
      const res = await botChannelApi.selfTest(bc.channel_type, bc.account_id);
      if (res.ok) {
        message.success(
          t('setting.botTestSuccess', { id: res.bot_open_id || '?' }),
        );
      } else {
        message.error(t('setting.botTestFailed'));
      }
    } catch (err: any) {
      message.error(err?.response?.data?.message || t('setting.botTestFailed'));
    } finally {
      setTestingId(null);
    }
  };

  const hasChannels = channels.length > 0;
  const mutLoading = createMut.isPending || updateMut.isPending;

  return (
    <ProfileSettingWrapperCard
      header={
        <header className="flex items-start justify-between gap-4">
          <div>
            <Title>{t('setting.botChannels')}</Title>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">
              {t('setting.botChannelsDescription')}
            </p>
          </div>
          <Button
            onClick={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
            data-testid="bot-add"
          >
            <LucidePlus className="size-4" />
            {t('setting.botAddChannel')}
          </Button>
        </header>
      }
    >
      <section className="p-6" data-testid="bot-channels-page">
        {isLoading ? (
          <div className="text-sm text-text-secondary">
            {t('common.loading')}
          </div>
        ) : !hasChannels ? (
          <EmptyState
            onAdd={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
          />
        ) : (
          <ul className="grid gap-4 lg:grid-cols-2">
            {channels.map((bc) => (
              <BotCard
                key={bc.id}
                bc={bc}
                onEdit={() => {
                  setEditing(bc);
                  setDialogOpen(true);
                }}
                onDelete={() => deleteMut.mutate(bc.id)}
                onTest={() => handleTest(bc)}
                testing={testingId === bc.id}
                onCopy={copy}
              />
            ))}
          </ul>
        )}
      </section>

      <BotChannelEditDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initial={editing}
        loading={mutLoading}
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
        <Bot className="size-7" />
      </div>
      <h3 className="text-base font-semibold text-text-primary">
        {t('setting.botEmptyTitle')}
      </h3>
      <p className="mt-1 max-w-md text-sm text-text-secondary">
        {t('setting.botEmptyHint')}
      </p>
      <Button className="mt-5" onClick={onAdd}>
        <LucidePlus className="size-4" />
        {t('setting.botAddChannel')}
      </Button>
    </div>
  );
}

interface BotCardProps {
  bc: BotChannel;
  onEdit: () => void;
  onDelete: () => void;
  onTest: () => void;
  testing: boolean;
  onCopy: (text: string) => void;
}

function BotCard({
  bc,
  onEdit,
  onDelete,
  onTest,
  testing,
  onCopy,
}: BotCardProps) {
  const { t } = useTranslation();
  const callbackUrl = useMemo(
    () => buildCallbackUrl(bc.channel_type, bc.account_id),
    [bc.channel_type, bc.account_id],
  );

  return (
    <li
      className="flex flex-col gap-3 rounded-xl border border-border-button bg-bg-component p-5"
      data-testid="bot-card"
      data-bot-id={bc.id}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid size-10 place-items-center rounded-lg bg-accent-primary/10 text-accent-primary">
            <Bot className="size-5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-text-primary">
                {bc.name}
              </span>
              <span
                className={cn(
                  'rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
                  bc.enabled
                    ? 'bg-state-success/15 text-state-success'
                    : 'bg-bg-card text-text-disabled',
                )}
              >
                {bc.enabled
                  ? t('setting.botEnabled')
                  : t('setting.botDisabled')}
              </span>
            </div>
            <div className="mt-0.5 font-mono text-[11px] text-text-disabled">
              {bc.channel_type} / {bc.account_id}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <ButtonLoading
            variant="ghost"
            size="sm"
            onClick={onTest}
            loading={testing}
            data-testid="bot-test"
          >
            <PlugZap className="size-4" />
          </ButtonLoading>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onEdit}
            data-testid="bot-edit"
          >
            <Pencil className="size-4" />
          </Button>
          <ConfirmDeleteDialog
            title={t('setting.botRevokeConfirm')}
            onOk={onDelete}
          >
            <Button variant="ghost" size="icon-sm" data-testid="bot-delete">
              <LucideTrash2 className="size-4 text-state-error" />
            </Button>
          </ConfirmDeleteDialog>
        </div>
      </header>

      <div className="rounded-lg border border-border-button bg-bg-base/50 p-3">
        <div className="mb-1 flex items-center justify-between text-[11px] text-text-disabled">
          <span>{t('setting.botCallbackUrl')}</span>
          <button
            type="button"
            onClick={() => onCopy(callbackUrl)}
            className="inline-flex items-center gap-1 text-text-secondary transition hover:text-text-primary"
          >
            <Copy className="size-3" />
            {t('setting.botCopyUrl')}
          </button>
        </div>
        <code className="block break-all font-mono text-[11px] text-text-primary">
          {callbackUrl}
        </code>
      </div>

      <dl className="grid grid-cols-2 gap-3 text-[11px] text-text-secondary">
        <div>
          <dt className="text-text-disabled">App ID</dt>
          <dd className="truncate font-mono text-text-primary">
            {bc.config_json?.app_id || '—'}
          </dd>
        </div>
        <div>
          <dt className="text-text-disabled">
            {t('setting.botFormDefaultKbs')}
          </dt>
          <dd className="truncate text-text-primary">
            {(bc.default_kb_ids || []).length || '—'}
          </dd>
        </div>
        <div>
          <dt className="text-text-disabled">
            {t('setting.botFormSessionScope')}
          </dt>
          <dd className="truncate text-text-primary">{bc.session_scope}</dd>
        </div>
        <div>
          <dt className="text-text-disabled">{t('knowledgeList.updatedAt')}</dt>
          <dd className="truncate text-text-primary">
            {formatDate(bc.update_time)}
          </dd>
        </div>
      </dl>

      {bc.enabled && (
        <div className="flex items-center gap-1.5 text-[11px] text-state-success">
          <CheckCircle2 className="size-3" />
          <span>{t('setting.botSavedHint')}</span>
        </div>
      )}
    </li>
  );
}
