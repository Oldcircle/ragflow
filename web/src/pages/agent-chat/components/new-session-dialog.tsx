/**
 * 新建 Agent 会话对话框。
 *
 * 接 RAGFlow 现有的 useFetchKnowledgeList 拿 KB 清单；
 * 模型 key 走后端 AGENT_V2_DEEPSEEK_KEY / AGENT_V2_ANTHROPIC_KEY 环境变量。
 */

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useFetchKnowledgeList } from '@/hooks/use-knowledge-request';
import { memo, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

const DEFAULT_SYSTEM_PROMPT = `你是一名严谨的企业知识库顾问。工作方式：

1. 判断用户问题是否与知识库内容相关。无关则直接说明，不调工具。
2. 相关问题：使用 rag_retrieve 工具检索原文；必要时换关键词多次检索。
3. 严格基于检索结果回答：数字、年限、比例、条款必须有原文支撑；原文未涵盖的内容，回答"未查到相关规定，建议联系业务方确认"。
4. 回答结尾列出「依据文件」清单。

禁止事项：
- 用训练知识补充原文未说的内容；
- 编造数字、名称、时间；
- 把其他场景的规则套到本知识库。`;

const MODEL_PRESETS = [
  {
    label: 'DeepSeek (DeepSeek 官方 /anthropic 端点)',
    value: 'deepseek',
    model: 'deepseek-chat',
    base_url: 'https://api.deepseek.com/anthropic',
  },
  {
    label: 'Claude Sonnet 4.5 (Anthropic)',
    value: 'claude-sonnet-4-5',
    model: 'claude-sonnet-4-5',
    base_url: null,
  },
  {
    label: 'Claude Haiku 4.5 (Anthropic)',
    value: 'claude-haiku-4-5',
    model: 'claude-haiku-4-5',
    base_url: null,
  },
];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  submitting: boolean;
  onSubmit: (payload: {
    name: string;
    kb_ids: string[];
    system_prompt: string;
    model_config: { model: string; base_url: string | null };
    max_turns: number;
    max_budget_usd: number;
  }) => void;
}

export const NewSessionDialog = memo(function NewSessionDialog({
  open,
  onOpenChange,
  submitting,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const { list: kbList, loading: kbLoading } = useFetchKnowledgeList(true);

  const [name, setName] = useState('');
  const [kbIds, setKbIds] = useState<string[]>([]);
  const [systemPrompt, setSystemPrompt] = useState(DEFAULT_SYSTEM_PROMPT);
  const [preset, setPreset] = useState(MODEL_PRESETS[0].value);
  const [maxTurns, setMaxTurns] = useState(20);
  const [maxBudget, setMaxBudget] = useState(0.5);

  const selectedPreset = useMemo(
    () => MODEL_PRESETS.find((p) => p.value === preset) ?? MODEL_PRESETS[0],
    [preset],
  );

  const canSubmit = name.trim().length > 0 && kbIds.length > 0 && !submitting;

  const handleSubmit = () => {
    if (!canSubmit) return;
    onSubmit({
      name: name.trim(),
      kb_ids: kbIds,
      system_prompt: systemPrompt,
      model_config: {
        model: selectedPreset.model,
        base_url: selectedPreset.base_url,
      },
      max_turns: maxTurns,
      max_budget_usd: maxBudget,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('agentV2.newSessionDialogTitle')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* 会话名称 */}
          <Field label={t('agentV2.sessionName')}>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('agentV2.sessionNamePlaceholder')}
            />
          </Field>

          {/* 知识库 */}
          <Field
            label={t('agentV2.selectKnowledgeBases')}
            hint={t('agentV2.multiKbHint')}
          >
            {kbLoading && (
              <div className="text-xs text-muted-foreground">
                {t('common.loading')}
              </div>
            )}
            {!kbLoading && kbList.length === 0 && (
              <div className="text-xs text-muted-foreground">
                {t('agentV2.noKbHint')}
              </div>
            )}
            <div className="flex flex-wrap gap-2 max-h-40 overflow-auto">
              {kbList.map((kb) => {
                const checked = kbIds.includes(kb.id);
                return (
                  <button
                    key={kb.id}
                    type="button"
                    onClick={() => {
                      setKbIds((prev) =>
                        prev.includes(kb.id)
                          ? prev.filter((x) => x !== kb.id)
                          : [...prev, kb.id],
                      );
                    }}
                    className={`px-3 py-1.5 rounded-full border text-xs transition-colors ${
                      checked
                        ? 'bg-teal-600 text-white border-teal-600'
                        : 'bg-white hover:bg-gray-50 border-gray-200 text-gray-700'
                    }`}
                  >
                    {kb.name}
                  </button>
                );
              })}
            </div>
          </Field>

          {/* 模型 */}
          <Field label={t('agentV2.model')}>
            <Select value={preset} onValueChange={setPreset}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODEL_PRESETS.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="text-xs text-muted-foreground mt-1">
              {t('agentV2.modelKeyHint')}
            </div>
          </Field>

          {/* System Prompt */}
          <Field label={t('agentV2.systemPrompt')}>
            <Textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
              className="font-mono text-xs"
            />
          </Field>

          {/* Turns + Budget */}
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('agentV2.maxTurns')}>
              <Input
                type="number"
                min={1}
                max={50}
                value={maxTurns}
                onChange={(e) => setMaxTurns(Number(e.target.value) || 20)}
              />
            </Field>
            <Field label={t('agentV2.maxBudget')}>
              <Input
                type="number"
                step="0.1"
                min={0.1}
                value={maxBudget}
                onChange={(e) => setMaxBudget(Number(e.target.value) || 0.5)}
              />
            </Field>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            {submitting ? t('common.saving') : t('agentV2.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
});

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
      <div className="text-xs font-medium mb-1.5 text-gray-700">{label}</div>
      {children}
      {hint && <div className="text-xs text-muted-foreground mt-1">{hint}</div>}
    </div>
  );
}
