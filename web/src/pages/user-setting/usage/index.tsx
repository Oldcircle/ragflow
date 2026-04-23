/**
 * 租户配额 + 用量管理台（Phase 3.1b）。
 *
 * 管理员能看到：
 *   - 4 张 stat 卡（知识库 / 文档 / 本月 Token / 本月成本）带配额进度条
 *   - 今日用量（API / 机器人消息 / 子 Agent）
 *   - 近 30 天的 Token 小柱图
 *   - 配额硬执行模式（0=记审计 / 1=超限 429）状态徽标
 */

import { cn } from '@/lib/utils';
import { useQuery } from '@tanstack/react-query';
import {
  Bot,
  Coins,
  Database,
  FileText,
  Hash,
  ShieldAlert,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  ProfileSettingWrapperCard,
  Title,
} from '../components/user-setting-header';
import { QuotaSnapshot, UsageDaily, quotaApi } from './api';

function fmtNum(n: number | undefined) {
  if (n == null) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return n.toLocaleString();
}

function fmtCost(usd: number | undefined) {
  if (usd == null) return '—';
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

function pct(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.min(100, Math.round((used / limit) * 100));
}

function toneFor(p: number): string {
  if (p >= 90) return 'bg-state-error';
  if (p >= 70) return 'bg-state-warning';
  return 'bg-state-success';
}

function textToneFor(p: number): string {
  if (p >= 90) return 'text-state-error';
  if (p >= 70) return 'text-state-warning';
  return 'text-state-success';
}

export default function UsagePage() {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery<QuotaSnapshot>({
    queryKey: ['tenant-quota'],
    queryFn: () => quotaApi.getSnapshot(),
  });
  const { data: range = [] } = useQuery<UsageDaily[]>({
    queryKey: ['tenant-quota', 'range', 30],
    queryFn: () => quotaApi.getRange(30),
  });

  const q = data?.quota;
  const u = data?.usage;
  const monthTokens = u ? u.month.token_in + u.month.token_out : 0;

  return (
    <ProfileSettingWrapperCard
      header={
        <header className="flex items-start justify-between gap-4">
          <div>
            <Title>{t('usage.title')}</Title>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">
              {t('usage.description')}
            </p>
          </div>
          {q && (
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium',
                q.hard_enforce
                  ? 'bg-state-warning/15 text-state-warning'
                  : 'bg-bg-card text-text-secondary',
              )}
              title={
                q.hard_enforce
                  ? t('usage.hardEnforceTip')
                  : t('usage.softEnforceTip')
              }
            >
              {q.hard_enforce ? (
                <ShieldAlert className="size-3.5" />
              ) : (
                <ShieldCheck className="size-3.5" />
              )}
              {q.hard_enforce ? t('usage.hardEnforce') : t('usage.softEnforce')}
            </span>
          )}
        </header>
      }
    >
      <div className="space-y-6 p-6" data-testid="usage-page">
        {isLoading || !q || !u ? (
          <div className="text-sm text-text-secondary">
            {t('common.loading')}
          </div>
        ) : (
          <>
            <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <QuotaCard
                icon={<Database className="size-5" />}
                label={t('usage.cardKb')}
                used={u.kb_count}
                limit={q.kb_max}
                format={fmtNum}
              />
              <QuotaCard
                icon={<FileText className="size-5" />}
                label={t('usage.cardDoc')}
                used={u.doc_count}
                limit={q.doc_max}
                format={fmtNum}
              />
              <QuotaCard
                icon={<Hash className="size-5" />}
                label={t('usage.cardTokens')}
                used={monthTokens}
                limit={q.token_month_max}
                format={fmtNum}
                subtitle={t('usage.cardTokensSub')}
              />
              <QuotaCard
                icon={<Coins className="size-5" />}
                label={t('usage.cardCost')}
                valueOverride={fmtCost(u.month.cost_usd)}
                subtitle={t('usage.cardCostSub')}
              />
            </section>

            <section>
              <header className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-text-primary">
                  {t('usage.todayHeader')}
                </h3>
                <span className="text-xs text-text-disabled">
                  {fmtYmd(u.today.date_ymd)}
                </span>
              </header>
              <div className="grid gap-3 sm:grid-cols-3">
                <TodayChip
                  icon={<Zap className="size-4" />}
                  label={t('usage.apiRequests')}
                  value={u.today.api_requests}
                />
                <TodayChip
                  icon={<Bot className="size-4" />}
                  label={t('usage.botMessages')}
                  value={u.today.bot_messages}
                  limit={q.bot_message_day_max}
                />
                <TodayChip
                  icon={<ShieldCheck className="size-4" />}
                  label={t('usage.subagents')}
                  value={u.today.subagent_spawns}
                  limit={q.subagent_day_max}
                />
              </div>
            </section>

            <section>
              <h3 className="mb-3 text-sm font-semibold text-text-primary">
                {t('usage.rangeHeader', { days: 30 })}
              </h3>
              <UsageBarChart rows={range} />
            </section>

            <section>
              <h3 className="mb-3 text-sm font-semibold text-text-primary">
                {t('usage.limitsHeader')}
              </h3>
              <dl className="grid gap-3 rounded-lg border border-border-button bg-bg-component/60 p-4 text-xs md:grid-cols-2">
                <LimitRow
                  label={t('usage.cardTokens')}
                  value={fmtNum(q.token_month_max)}
                />
                <LimitRow
                  label={t('usage.apiRps')}
                  value={`${q.api_rps_max}/s`}
                />
                <LimitRow
                  label={t('usage.botMessagesLimit')}
                  value={`${fmtNum(q.bot_message_day_max)} / ${t('usage.perDay')}`}
                />
                <LimitRow
                  label={t('usage.subagentsLimit')}
                  value={`${fmtNum(q.subagent_day_max)} / ${t('usage.perDay')}`}
                />
              </dl>
            </section>
          </>
        )}
      </div>
    </ProfileSettingWrapperCard>
  );
}

function QuotaCard({
  icon,
  label,
  used,
  limit,
  subtitle,
  format = fmtNum,
  valueOverride,
}: {
  icon: React.ReactNode;
  label: string;
  used?: number;
  limit?: number;
  subtitle?: string;
  format?: (n: number | undefined) => string;
  valueOverride?: string;
}) {
  const p = used != null && limit != null && limit > 0 ? pct(used, limit) : 0;
  return (
    <div className="rounded-xl border border-border-button bg-bg-component p-4">
      <div className="mb-3 flex items-center gap-2 text-xs text-text-secondary">
        <span className="grid size-7 place-items-center rounded-lg bg-accent-primary/10 text-accent-primary">
          {icon}
        </span>
        <span className="font-medium">{label}</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold text-text-primary">
          {valueOverride ?? format(used)}
        </span>
        {limit != null && limit > 0 && !valueOverride && (
          <span className="text-[11px] text-text-disabled">
            / {format(limit)}
          </span>
        )}
      </div>
      {subtitle && (
        <div className="mt-1 text-[11px] text-text-disabled">{subtitle}</div>
      )}
      {limit != null && limit > 0 && used != null && !valueOverride && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-bg-card">
            <div
              className={cn('h-full transition-all', toneFor(p))}
              style={{ width: `${p}%` }}
            />
          </div>
          <div
            className={cn(
              'mt-1 text-right text-[10px] font-mono',
              textToneFor(p),
            )}
          >
            {p}%
          </div>
        </div>
      )}
    </div>
  );
}

function TodayChip({
  icon,
  label,
  value,
  limit,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  limit?: number;
}) {
  const p = limit != null && limit > 0 ? pct(value, limit) : 0;
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border-button bg-bg-component px-4 py-3">
      <span className="grid size-8 place-items-center rounded-lg bg-bg-card text-text-secondary">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[11px] text-text-disabled">{label}</div>
        <div className="flex items-baseline gap-1.5">
          <span className="text-lg font-semibold text-text-primary">
            {fmtNum(value)}
          </span>
          {limit != null && limit > 0 && (
            <span className={cn('text-[10px] font-mono', textToneFor(p))}>
              / {fmtNum(limit)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function UsageBarChart({ rows }: { rows: UsageDaily[] }) {
  if (!rows?.length) {
    return (
      <div className="rounded-lg border border-dashed border-border-button bg-bg-component/40 p-6 text-center text-xs text-text-disabled">
        no usage yet
      </div>
    );
  }
  const max = Math.max(
    1,
    ...rows.map((r) => (r.token_in || 0) + (r.token_out || 0)),
  );
  return (
    <div className="rounded-xl border border-border-button bg-bg-component p-4">
      <div className="flex items-end gap-1" style={{ height: 100 }}>
        {rows.map((r) => {
          const total = (r.token_in || 0) + (r.token_out || 0);
          const h = (total / max) * 100;
          return (
            <div
              key={r.date_ymd}
              className="flex min-w-0 flex-1 flex-col items-center gap-0.5"
              title={`${fmtYmd(r.date_ymd)}: ${fmtNum(total)} tokens, ${fmtCost(r.cost_usd)}`}
            >
              <div
                className="w-full bg-accent-primary/70 hover:bg-accent-primary"
                style={{ height: `${h}%`, minHeight: total > 0 ? 2 : 0 }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-[10px] text-text-disabled">
        <span>{rows.length > 0 ? fmtYmd(rows[0].date_ymd) : ''}</span>
        <span>
          {rows.length > 0 ? fmtYmd(rows[rows.length - 1].date_ymd) : ''}
        </span>
      </div>
    </div>
  );
}

function LimitRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-text-secondary">{label}</dt>
      <dd className="font-mono text-text-primary">{value}</dd>
    </div>
  );
}

function fmtYmd(ymd: number | undefined): string {
  if (!ymd) return '';
  const s = String(ymd);
  if (s.length !== 8) return s;
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
}
