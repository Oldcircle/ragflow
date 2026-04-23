/**
 * 添加 / 编辑机器人渠道的对话框（Phase 2.2）。
 *
 * 渠道类型 v1 仅飞书可选；钉钉 / 企微显示但置灰。
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
import { useFetchKnowledgeList } from '@/hooks/use-knowledge-request';
import { cn } from '@/lib/utils';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BotChannel, BotChannelInput, ChannelType, SessionScope } from './api';

const SCOPE_OPTIONS: SessionScope[] = [
  'group_sender',
  'group',
  'group_topic',
  'group_topic_sender',
];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial: BotChannel | null; // null = create
  loading: boolean;
  onSubmit: (payload: BotChannelInput) => void;
}

export function BotChannelEditDialog({
  open,
  onOpenChange,
  initial,
  loading,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const { list: kbList } = useFetchKnowledgeList(true);
  const isEdit = initial !== null;

  // Form state
  const [channelType, setChannelType] = useState<ChannelType>('feishu');
  const [accountId, setAccountId] = useState('');
  const [name, setName] = useState('');
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [encryptKey, setEncryptKey] = useState('');
  const [verificationToken, setVerificationToken] = useState('');
  const [apiBase, setApiBase] = useState('https://open.feishu.cn');
  const [defaultKbIds, setDefaultKbIds] = useState<string[]>([]);
  const [sessionScope, setSessionScope] =
    useState<SessionScope>('group_sender');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [enabled, setEnabled] = useState(true);

  // Reset on open / when editing
  useEffect(() => {
    if (!open) return;
    if (initial) {
      setChannelType(initial.channel_type);
      setAccountId(initial.account_id);
      setName(initial.name);
      const cfg = initial.config_json || {};
      setAppId(String(cfg.app_id ?? ''));
      // Edit 时密钥字段是脱敏后的 "abcd***xy"；如果用户不改就不发回
      setAppSecret('');
      setEncryptKey('');
      setVerificationToken('');
      setApiBase(String(cfg.api_base ?? 'https://open.feishu.cn'));
      setDefaultKbIds(initial.default_kb_ids || []);
      setSessionScope(initial.session_scope || 'group_sender');
      setSystemPrompt(initial.default_system_prompt || '');
      setEnabled(initial.enabled);
    } else {
      setChannelType('feishu');
      setAccountId('');
      setName('');
      setAppId('');
      setAppSecret('');
      setEncryptKey('');
      setVerificationToken('');
      setApiBase('https://open.feishu.cn');
      setDefaultKbIds([]);
      setSessionScope('group_sender');
      setSystemPrompt('');
      setEnabled(true);
    }
  }, [open, initial]);

  const validate = (): string | null => {
    if (!accountId.trim()) return t('knowledgeList.namePlaceholder');
    if (!/^[a-zA-Z0-9_-]+$/.test(accountId.trim())) {
      return t('setting.botFormAccountIdHint');
    }
    if (!name.trim()) return t('setting.botFormName');
    if (!isEdit) {
      if (!appId.trim()) return t('setting.botFormAppId');
      if (!appSecret.trim()) return t('setting.botFormAppSecret');
      if (!encryptKey.trim()) return t('setting.botFormEncryptKey');
    }
    return null;
  };

  const submit = () => {
    const err = validate();
    if (err) {
      message.error(err);
      return;
    }
    // Edit 时秘密字段为空表示"保留旧值"，不要把 "" 写回 config
    const cfg: Record<string, unknown> = {
      app_id: appId.trim(),
      api_base: apiBase.trim() || 'https://open.feishu.cn',
    };
    if (appSecret.trim()) cfg.app_secret = appSecret.trim();
    if (encryptKey.trim()) cfg.encrypt_key = encryptKey.trim();
    if (verificationToken.trim())
      cfg.verification_token = verificationToken.trim();

    const payload: BotChannelInput = {
      channel_type: channelType,
      account_id: accountId.trim(),
      name: name.trim(),
      config_json: cfg,
      default_kb_ids: defaultKbIds,
      session_scope: sessionScope,
      default_system_prompt: systemPrompt,
      enabled,
    };
    onSubmit(payload);
  };

  const kbOptions = useMemo(
    () =>
      (kbList || []).map((k: any) => ({
        id: k.id as string,
        name: (k.name || k.id) as string,
      })),
    [kbList],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? t('setting.botEdit') : t('setting.botAddChannel')}
          </DialogTitle>
        </DialogHeader>

        <div className="max-h-[60vh] space-y-4 overflow-auto py-2 pr-2">
          {/* Channel type */}
          <Field label={t('setting.botFormChannelType')}>
            <Select
              value={channelType}
              onValueChange={(v) => setChannelType(v as ChannelType)}
              disabled={isEdit}
            >
              <SelectTrigger data-testid="bot-channel-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="feishu">
                  {t('setting.botFormChannelTypeFeishu')}
                </SelectItem>
                <SelectItem value="dingtalk" disabled>
                  {t('setting.botFormChannelTypeDingtalk')}
                </SelectItem>
                <SelectItem value="wecom" disabled>
                  {t('setting.botFormChannelTypeWecom')}
                </SelectItem>
              </SelectContent>
            </Select>
          </Field>

          {/* Account ID + Name */}
          <div className="grid grid-cols-2 gap-3">
            <Field
              label={t('setting.botFormAccountId')}
              hint={t('setting.botFormAccountIdHint')}
            >
              <Input
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
                placeholder="prod-bot"
                disabled={isEdit}
                data-testid="bot-account-id"
              />
            </Field>
            <Field label={t('setting.botFormName')}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="生产环境飞书机器人"
                data-testid="bot-name"
              />
            </Field>
          </div>

          {/* App credentials */}
          <Field label={t('setting.botFormAppId')}>
            <Input
              value={appId}
              onChange={(e) => setAppId(e.target.value)}
              placeholder="cli_xxxxxxxx"
              data-testid="bot-app-id"
            />
          </Field>
          <Field
            label={t('setting.botFormAppSecret')}
            hint={isEdit ? '(留空则保留旧值)' : undefined}
          >
            <Input
              type="password"
              value={appSecret}
              onChange={(e) => setAppSecret(e.target.value)}
              placeholder={isEdit ? '••••' : ''}
              data-testid="bot-app-secret"
            />
          </Field>
          <Field
            label={t('setting.botFormEncryptKey')}
            hint={
              isEdit ? '(留空则保留旧值)' : t('setting.botFormEncryptKeyHint')
            }
          >
            <Input
              type="password"
              value={encryptKey}
              onChange={(e) => setEncryptKey(e.target.value)}
              placeholder={isEdit ? '••••' : ''}
              data-testid="bot-encrypt-key"
            />
          </Field>
          <Field
            label={t('setting.botFormVerificationToken')}
            hint={isEdit ? '(留空则保留旧值)' : '(可选)'}
          >
            <Input
              type="password"
              value={verificationToken}
              onChange={(e) => setVerificationToken(e.target.value)}
              placeholder={isEdit ? '••••' : ''}
              data-testid="bot-verification-token"
            />
          </Field>
          <Field
            label={t('setting.botFormApiBase')}
            hint={t('setting.botFormApiBaseHint')}
          >
            <Input
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="https://open.feishu.cn"
            />
          </Field>

          {/* Default KBs */}
          <Field
            label={t('setting.botFormDefaultKbs')}
            hint={t('setting.botFormDefaultKbsHint')}
          >
            <div className="flex max-h-32 flex-wrap gap-1.5 overflow-auto rounded-md border border-border-button p-2">
              {kbOptions.length === 0 && (
                <div className="text-xs text-text-disabled">
                  {t('agentV2.noKbHint')}
                </div>
              )}
              {kbOptions.map((kb) => {
                const checked = defaultKbIds.includes(kb.id);
                return (
                  <button
                    type="button"
                    key={kb.id}
                    onClick={() => {
                      setDefaultKbIds((prev) =>
                        prev.includes(kb.id)
                          ? prev.filter((x) => x !== kb.id)
                          : [...prev, kb.id],
                      );
                    }}
                    className={cn(
                      'rounded-full border px-3 py-1 text-xs transition-colors',
                      checked
                        ? 'border-accent-primary bg-accent-primary text-white'
                        : 'border-border-button bg-bg-component text-text-secondary hover:bg-bg-card',
                    )}
                  >
                    {kb.name}
                  </button>
                );
              })}
            </div>
          </Field>

          {/* Session scope */}
          <Field label={t('setting.botFormSessionScope')}>
            <Select
              value={sessionScope}
              onValueChange={(v) => setSessionScope(v as SessionScope)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SCOPE_OPTIONS.map((s) => (
                  <SelectItem value={s} key={s}>
                    {t(
                      `setting.botFormSessionScope${s
                        .split('_')
                        .map((w) => w[0].toUpperCase() + w.slice(1))
                        .join('')}` as any,
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          {/* System prompt */}
          <Field
            label={t('setting.botFormSystemPrompt')}
            hint={t('setting.botFormSystemPromptHint')}
          >
            <Textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={4}
              className="font-mono text-xs"
            />
          </Field>

          {/* Enabled toggle */}
          <div className="flex items-center gap-3">
            <Switch checked={enabled} onCheckedChange={setEnabled} />
            <span className="text-sm text-text-primary">
              {t('setting.botFormEnabled')}
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
            data-testid="bot-submit"
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
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium text-text-primary">
        {label}
      </div>
      {children}
      {hint && <div className="mt-1 text-xs text-text-disabled">{hint}</div>}
    </div>
  );
}
