/**
 * Phase 2.6 — Agent 主动询问用户时的多选卡片。
 *
 * 数据源：SSE `ask_user_question` 事件，挂在 `turn.pendingQuestion`。
 * 用户点选后，父组件把选项文本作为 user message 发下一个 turn，
 * Agent 接着处理。
 */

import { cn } from '@/lib/utils';
import { LucideCheck, LucideMessageCircleQuestion } from 'lucide-react';
import { memo, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { PendingQuestion } from '../hooks/use-agent-stream';

interface Props {
  question: PendingQuestion;
  disabled?: boolean;
  /** user 点完 submit 后调；父组件负责把 text 作为下一条 user message 发出 */
  onSubmit: (answerText: string) => void;
}

export const PendingQuestionCard = memo(function PendingQuestionCard({
  question,
  disabled,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [custom, setCustom] = useState('');

  const toggle = (i: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (question.multiSelect) {
        if (next.has(i)) next.delete(i);
        else next.add(i);
      } else {
        next.clear();
        next.add(i);
      }
      return next;
    });
  };

  const canSubmit = useMemo(
    () => selected.size > 0 || custom.trim().length > 0,
    [selected, custom],
  );

  const handleSubmit = () => {
    if (!canSubmit || disabled) return;
    const labels = [...selected]
      .sort((a, b) => a - b)
      .map((i) => question.options[i]?.label)
      .filter(Boolean);
    const answer = [...labels, custom.trim() ? `其他：${custom.trim()}` : null]
      .filter(Boolean)
      .join('，');
    onSubmit(answer);
  };

  return (
    <aside
      role="group"
      aria-label={t('agentV2.pendingQuestion', '待回答')}
      className={cn(
        'my-3 rounded-xl border border-accent-primary/30',
        'bg-accent-primary/5 p-4 text-sm',
      )}
      data-testid="agent-v2-pending-question"
    >
      <header className="mb-3 flex items-center gap-2 text-text-primary">
        <span className="grid size-6 place-items-center rounded-md bg-accent-primary/15 text-accent-primary">
          <LucideMessageCircleQuestion size={14} />
        </span>
        <span className="rounded-md bg-accent-primary/15 px-2 py-0.5 text-[11px] font-medium text-accent-primary">
          {question.header}
        </span>
        <span className="text-[11px] text-text-secondary">
          {question.multiSelect
            ? t('agentV2.multiSelectHint', '可多选')
            : t('agentV2.singleSelectHint', '单选')}
        </span>
      </header>

      <p className="mb-3 text-[13px] font-medium leading-relaxed text-text-primary">
        {question.question}
      </p>

      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {question.options.map((opt, i) => {
          const isSel = selected.has(i);
          return (
            <button
              key={i}
              type="button"
              onClick={() => toggle(i)}
              disabled={disabled}
              className={cn(
                'flex items-start gap-2 rounded-lg border px-3 py-2 text-left text-[12px] transition',
                isSel
                  ? 'border-accent-primary bg-accent-primary/10 text-text-primary'
                  : 'border-border-button bg-bg-component hover:bg-bg-card',
                disabled && 'cursor-not-allowed opacity-50',
              )}
            >
              <span
                className={cn(
                  'mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border',
                  isSel
                    ? 'border-accent-primary bg-accent-primary text-white'
                    : 'border-border-button',
                )}
              >
                {isSel && <LucideCheck size={10} strokeWidth={3} />}
              </span>
              <span className="flex-1">
                <span className="block font-medium">{opt.label}</span>
                {opt.description && (
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-text-secondary">
                    {opt.description}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-3 space-y-2">
        <label className="block text-[11px] font-medium text-text-secondary">
          {t('agentV2.customAnswer', '或输入自定义答复（可选）')}
        </label>
        <input
          type="text"
          className="w-full rounded-md border border-border-button bg-bg-component px-3 py-1.5 text-[12px] outline-none focus:border-accent-primary"
          placeholder={t('agentV2.customPlaceholder', '例如：另一个 KB 名称')}
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          disabled={disabled}
        />
      </div>

      <footer className="mt-3 flex justify-end">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit || disabled}
          className={cn(
            'rounded-md px-3 py-1.5 text-[12px] font-medium transition',
            canSubmit && !disabled
              ? 'bg-accent-primary text-white hover:opacity-90'
              : 'cursor-not-allowed bg-bg-component text-text-secondary opacity-60',
          )}
          data-testid="agent-v2-pending-question-submit"
        >
          {t('agentV2.submitAnswer', '发送回答')}
        </button>
      </footer>
    </aside>
  );
});
