/**
 * Phase 2.6 — Agent 提交执行计划等审批时的卡片。
 *
 * 数据源：SSE `plan_submitted` 事件，挂在 `turn.pendingPlan`。
 * 用户点 Approve / Reject / Request Changes 后，父组件把决定
 * 作为下一条 user message 发给 Agent。
 */

import { cn } from '@/lib/utils';
import {
  LucideAlertTriangle,
  LucideCheck,
  LucideListOrdered,
  LucideX,
} from 'lucide-react';
import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import type { PendingPlan } from '../hooks/use-agent-stream';

interface Props {
  plan: PendingPlan;
  disabled?: boolean;
  onDecide: (
    decision: 'approve' | 'reject' | 'request_changes',
    note?: string,
  ) => void;
}

const riskColors: Record<string, string> = {
  low: 'border-green-500/40 bg-green-500/10 text-green-500',
  medium: 'border-amber-500/40 bg-amber-500/10 text-amber-500',
  high: 'border-red-500/40 bg-red-500/10 text-red-500',
};

export const PendingPlanCard = memo(function PendingPlanCard({
  plan,
  disabled,
  onDecide,
}: Props) {
  const { t } = useTranslation();
  const riskClass = riskColors[plan.riskLevel] ?? riskColors.medium;

  return (
    <aside
      role="group"
      aria-label={t('agentV2.pendingPlan', '待审批计划')}
      className="my-3 rounded-xl border border-border-button bg-bg-component p-4 text-sm"
      data-testid="agent-v2-pending-plan"
    >
      <header className="mb-3 flex items-start gap-2">
        <span className="mt-0.5 grid size-6 place-items-center rounded-md bg-accent-primary/15 text-accent-primary">
          <LucideListOrdered size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[13px] font-semibold text-text-primary">
              {plan.title}
            </h3>
            <span
              className={cn(
                'rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase',
                riskClass,
              )}
              title={t('agentV2.riskLevel', '风险等级')}
            >
              {plan.riskLevel}
            </span>
            {!plan.reversible && (
              <span className="flex items-center gap-1 rounded-md bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-500">
                <LucideAlertTriangle size={10} />
                {t('agentV2.irreversible', '不可逆')}
              </span>
            )}
          </div>
          {typeof plan.estimatedCostUsd === 'number' && (
            <div className="mt-1 text-[11px] text-text-secondary">
              {t('agentV2.estimatedCost', '预估成本')}: $
              {plan.estimatedCostUsd.toFixed(4)}
            </div>
          )}
        </div>
      </header>

      <section className="mb-3">
        <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-text-secondary">
          {t('agentV2.planSteps', '执行步骤')}
        </div>
        <ol className="ml-4 list-decimal space-y-1 text-[12px] text-text-primary">
          {plan.steps.map((s, i) => (
            <li key={i} className="leading-relaxed">
              {s}
            </li>
          ))}
        </ol>
      </section>

      {plan.affectedResources.length > 0 && (
        <section className="mb-3">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-text-secondary">
            {t('agentV2.affectedResources', '受影响资源')}
          </div>
          <ul className="flex flex-wrap gap-1.5">
            {plan.affectedResources.map((r, i) => (
              <li
                key={i}
                className="rounded-md bg-bg-card px-2 py-0.5 text-[11px] text-text-primary"
              >
                <span className="mr-1 text-text-secondary">{r.kind}</span>
                {r.id && (
                  <code className="rounded bg-bg-base/50 px-1">{r.id}</code>
                )}
                {r.value !== undefined && (
                  <span className="ml-1 font-medium">{String(r.value)}</span>
                )}
                {r.action && (
                  <span className="ml-1 text-[10px] text-text-secondary">
                    ({r.action})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {plan.reversibleHint && (
        <section className="mb-3 rounded-md bg-bg-base/40 px-2 py-1.5 text-[11px] leading-relaxed text-text-secondary">
          <span className="font-medium text-text-primary">
            {t('agentV2.reversibleHint', '回滚方法')}:{' '}
          </span>
          {plan.reversibleHint}
        </section>
      )}

      <footer className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => onDecide('reject')}
          disabled={disabled}
          className={cn(
            'flex items-center gap-1 rounded-md border border-border-button px-3 py-1.5 text-[12px]',
            'text-text-secondary transition hover:bg-bg-base',
            disabled && 'cursor-not-allowed opacity-50',
          )}
          data-testid="agent-v2-plan-reject"
        >
          <LucideX size={12} /> {t('agentV2.planReject', '拒绝')}
        </button>
        <button
          type="button"
          onClick={() => {
            const note = window.prompt(
              t('agentV2.planChangesPrompt', '告诉 Agent 要怎么改：'),
            );
            if (note && note.trim()) {
              onDecide('request_changes', note.trim());
            }
          }}
          disabled={disabled}
          className={cn(
            'rounded-md border border-border-button px-3 py-1.5 text-[12px]',
            'text-text-primary transition hover:bg-bg-base',
            disabled && 'cursor-not-allowed opacity-50',
          )}
          data-testid="agent-v2-plan-request-changes"
        >
          {t('agentV2.planRequestChanges', '要求调整')}
        </button>
        <button
          type="button"
          onClick={() => onDecide('approve')}
          disabled={disabled}
          className={cn(
            'flex items-center gap-1 rounded-md bg-accent-primary px-3 py-1.5 text-[12px] font-medium text-white transition hover:opacity-90',
            disabled && 'cursor-not-allowed opacity-50',
          )}
          data-testid="agent-v2-plan-approve"
        >
          <LucideCheck size={12} /> {t('agentV2.planApprove', '批准执行')}
        </button>
      </footer>
    </aside>
  );
});
