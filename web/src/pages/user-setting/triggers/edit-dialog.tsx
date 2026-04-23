/**
 * 新建 / 编辑 Agent Trigger 对话框（Phase 3.2）。
 */

import { Button, ButtonLoading } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import message from '@/components/ui/message';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useSessions } from '@/pages/agent-chat/hooks/use-sessions';
import { botChannelApi } from '@/pages/user-setting/bot-channels/api';
import { useQuery } from '@tanstack/react-query';
import { Clock } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentTrigger, DeliveryKind, TriggerInput, triggerApi } from './api';

const COMMON_CRONS: { label: string; expr: string }[] = [
  { label: '每天早 9 点 / Daily 9am', expr: '0 9 * * *' },
  { label: '工作日早 9 点 / Weekdays 9am', expr: '0 9 * * 1-5' },
  { label: '每小时整点 / Hourly', expr: '0 * * * *' },
  { label: '每周一早 10 点 / Weekly Mon 10am', expr: '0 10 * * 1' },
  { label: '每月 1 号早 8 点 / Monthly 1st 8am', expr: '0 8 1 * *' },
];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial: AgentTrigger | null;
  loading: boolean;
  onSubmit: (payload: TriggerInput) => void;
}

export function TriggerEditDialog({
  open,
  onOpenChange,
  initial,
  loading,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const isEdit = initial !== null;

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [cronExpr, setCronExpr] = useState('0 9 * * *');
  const [timezone, setTimezone] = useState('Asia/Shanghai');
  const [agentSessionId, setAgentSessionId] = useState<string>('');
  const [prompt, setPrompt] = useState('');
  const [deliveryKind, setDeliveryKind] = useState<DeliveryKind>('audit_only');
  const [botChannelId, setBotChannelId] = useState<string>('');
  const [chatId, setChatId] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [cronPreview, setCronPreview] = useState<number[]>([]);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      setName(initial.name);
      setDescription(initial.description || '');
      setCronExpr(initial.cron_expr || '0 9 * * *');
      setTimezone(initial.timezone || 'Asia/Shanghai');
      setAgentSessionId(initial.agent_session_id);
      setPrompt(initial.prompt);
      setDeliveryKind(initial.delivery_kind);
      setBotChannelId(
        (initial.delivery_config?.bot_channel_id as string) || '',
      );
      setChatId((initial.delivery_config?.chat_id as string) || '');
      setEnabled(initial.enabled);
    } else {
      setName('');
      setDescription('');
      setCronExpr('0 9 * * *');
      setTimezone('Asia/Shanghai');
      setAgentSessionId('');
      setPrompt('');
      setDeliveryKind('audit_only');
      setBotChannelId('');
      setChatId('');
      setEnabled(true);
      setCronPreview([]);
      setPreviewError(null);
    }
  }, [open, initial]);

  // Pull data
  const { data: sessions = [] } = useSessions();
  const { data: botChannels = [] } = useQuery({
    queryKey: ['bot-channels'],
    queryFn: () => botChannelApi.list(),
    enabled: open,
  });

  // Cron preview — debounced
  useEffect(() => {
    if (!cronExpr) {
      setCronPreview([]);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const fires = await triggerApi.previewCron(cronExpr, timezone);
        setCronPreview(fires);
        setPreviewError(null);
      } catch (err: any) {
        setCronPreview([]);
        setPreviewError(
          err?.response?.data?.message || 'invalid cron expression',
        );
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [cronExpr, timezone]);

  const sessionOptions = useMemo(
    () =>
      (sessions || []).map((s: any) => ({
        id: s.id as string,
        name: (s.name || s.id) as string,
      })),
    [sessions],
  );

  const feishuChannels = useMemo(
    () => botChannels.filter((b) => b.channel_type === 'feishu' && b.enabled),
    [botChannels],
  );

  const submit = () => {
    if (!name.trim()) {
      message.error(t('trigger.errName'));
      return;
    }
    if (!cronExpr.trim()) {
      message.error(t('trigger.errCron'));
      return;
    }
    if (!agentSessionId) {
      message.error(t('trigger.errSession'));
      return;
    }
    if (!prompt.trim()) {
      message.error(t('trigger.errPrompt'));
      return;
    }
    if (deliveryKind === 'feishu_bot' && (!botChannelId || !chatId)) {
      message.error(t('trigger.errDelivery'));
      return;
    }

    const payload: TriggerInput = {
      name: name.trim(),
      description,
      trigger_type: 'cron',
      cron_expr: cronExpr.trim(),
      timezone,
      agent_session_id: agentSessionId,
      prompt,
      delivery_kind: deliveryKind,
      delivery_config:
        deliveryKind === 'feishu_bot'
          ? { bot_channel_id: botChannelId, chat_id: chatId }
          : {},
      enabled,
    };
    onSubmit(payload);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? t('trigger.edit') : t('trigger.addNew')}
          </DialogTitle>
        </DialogHeader>

        <div className="max-h-[60vh] space-y-4 overflow-auto py-2 pr-2">
          <Field label={t('trigger.fieldName')}>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('trigger.fieldNamePlaceholder')}
              data-testid="trigger-name"
            />
          </Field>

          <Field label={t('trigger.fieldDescription')}>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('trigger.fieldDescriptionPlaceholder')}
            />
          </Field>

          <Field
            label={t('trigger.fieldCron')}
            hint={previewError || t('trigger.fieldCronHint')}
            hintError={!!previewError}
          >
            <Input
              value={cronExpr}
              onChange={(e) => setCronExpr(e.target.value)}
              placeholder="0 9 * * *"
              className="font-mono"
              data-testid="trigger-cron"
            />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {COMMON_CRONS.map((c) => (
                <button
                  key={c.expr}
                  type="button"
                  onClick={() => setCronExpr(c.expr)}
                  className="rounded-md border border-border-button bg-bg-component px-2 py-0.5 text-[10px] text-text-secondary hover:bg-bg-card"
                >
                  {c.label}
                </button>
              ))}
            </div>
            {cronPreview.length > 0 && (
              <div className="mt-2 rounded-md border border-border-button bg-bg-base/50 p-2 text-[11px] text-text-secondary">
                <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-wide text-text-disabled">
                  <Clock className="size-3" />
                  {t('trigger.nextRuns')}
                </div>
                <ul className="space-y-0.5 font-mono">
                  {cronPreview.map((ts) => (
                    <li key={ts}>{new Date(ts).toLocaleString()}</li>
                  ))}
                </ul>
              </div>
            )}
          </Field>

          <Field
            label={t('trigger.fieldSession')}
            hint={t('trigger.fieldSessionHint')}
          >
            <Select value={agentSessionId} onValueChange={setAgentSessionId}>
              <SelectTrigger data-testid="trigger-session">
                <SelectValue
                  placeholder={t('trigger.fieldSessionPlaceholder')}
                />
              </SelectTrigger>
              <SelectContent>
                {sessionOptions.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-text-disabled">
                    {t('agentV2.noSessions')}
                  </div>
                ) : (
                  sessionOptions.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name}
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>
          </Field>

          <Field
            label={t('trigger.fieldPrompt')}
            hint={t('trigger.fieldPromptHint')}
          >
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder={t('trigger.fieldPromptPlaceholder')}
              data-testid="trigger-prompt"
            />
          </Field>

          <Field label={t('trigger.fieldDelivery')}>
            <Select
              value={deliveryKind}
              onValueChange={(v) => setDeliveryKind(v as DeliveryKind)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="audit_only">
                  {t('trigger.deliveryAuditOnly')}
                </SelectItem>
                <SelectItem value="feishu_bot">
                  {t('trigger.deliveryFeishuBot')}
                </SelectItem>
              </SelectContent>
            </Select>
          </Field>

          {deliveryKind === 'feishu_bot' && (
            <>
              <Field label={t('trigger.fieldBotChannel')}>
                <Select value={botChannelId} onValueChange={setBotChannelId}>
                  <SelectTrigger>
                    <SelectValue
                      placeholder={t('trigger.fieldBotChannelPlaceholder')}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {feishuChannels.length === 0 ? (
                      <div className="px-3 py-2 text-xs text-text-disabled">
                        {t('trigger.noBotChannel')}
                      </div>
                    ) : (
                      feishuChannels.map((b) => (
                        <SelectItem key={b.id} value={b.id}>
                          {b.name}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              </Field>
              <Field
                label={t('trigger.fieldChatId')}
                hint={t('trigger.fieldChatIdHint')}
              >
                <Input
                  value={chatId}
                  onChange={(e) => setChatId(e.target.value)}
                  placeholder="oc_xxxxxxxxxxxxxxxxxx"
                  className="font-mono"
                />
              </Field>
            </>
          )}

          <div className="flex items-center gap-3">
            <Switch checked={enabled} onCheckedChange={setEnabled} />
            <span className="text-sm text-text-primary">
              {t('trigger.fieldEnabled')}
            </span>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <ButtonLoading
            onClick={submit}
            loading={loading}
            data-testid="trigger-submit"
          >
            {t('common.save')}
          </ButtonLoading>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  hint,
  hintError,
  children,
}: {
  label: string;
  hint?: string;
  hintError?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium text-text-primary">
        {label}
      </div>
      {children}
      {hint && (
        <div
          className={
            'mt-1 text-xs ' +
            (hintError ? 'text-state-error' : 'text-text-disabled')
          }
        >
          {hint}
        </div>
      )}
    </div>
  );
}
