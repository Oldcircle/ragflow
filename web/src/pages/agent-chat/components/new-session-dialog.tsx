/**
 * 新建 Agent 会话对话框。
 *
 * - KB 列表走 RAGFlow 的 useFetchKnowledgeList
 * - 模型下拉走后端 /v1/agent_v2/model（从 TenantLLM 读）
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
import { memo, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAvailableModels } from '../hooks/use-sessions';

const DEFAULT_SYSTEM_PROMPT = `你是一名严谨的企业知识库顾问。工作方式：

1. 判断用户问题是否与知识库内容相关。无关则直接说明，不调工具。
2. 相关问题：使用 rag_retrieve 工具检索原文；必要时换关键词多次检索。
3. 严格基于检索结果回答：数字、年限、比例、条款必须有原文支撑；原文未涵盖的内容，回答"未查到相关规定，建议联系业务方确认"。
4. **引用规范**：当某句话依据检索到的某个文档片段时，在该句末尾加形如 [1]、[2]、[3] 的上标编号，编号对应该次对话里按检索顺序出现的文档。一句话同时依据多片段可写 [1][2]。不要在正文里列文件名——文件名会自动展示在答复下方的"引用来源"区。
5. 回答结尾无需重复「依据文件」清单，编号本身已标出归属。

禁止事项：
- 用训练知识补充原文未说的内容；
- 编造数字、名称、时间；
- 把其他场景的规则套到本知识库；
- 给没有检索依据的句子加 [N] 编号。`;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  submitting: boolean;
  onSubmit: (payload: {
    name: string;
    kb_ids: string[];
    system_prompt: string;
    model_config: { llm_name: string; factory: string };
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
  const { data: modelData, isLoading: modelsLoading } = useAvailableModels();

  const supportedModels = useMemo(
    () => (modelData?.models ?? []).filter((m) => m.supported),
    [modelData?.models],
  );

  const [name, setName] = useState('');
  const [kbIds, setKbIds] = useState<string[]>([]);
  const [systemPrompt, setSystemPrompt] = useState(DEFAULT_SYSTEM_PROMPT);
  const [modelKey, setModelKey] = useState<string>(''); // `${factory}|${llm_name}`
  const [maxTurns, setMaxTurns] = useState(20);
  const [maxBudget, setMaxBudget] = useState(0.5);

  // 默认选中第一个 supported 模型
  useEffect(() => {
    if (!modelKey && supportedModels.length > 0) {
      const m = supportedModels[0];
      setModelKey(`${m.factory}|${m.llm_name}`);
    }
  }, [supportedModels, modelKey]);

  const selectedModel = useMemo(() => {
    const [factory, llm_name] = modelKey.split('|');
    return supportedModels.find(
      (m) => m.factory === factory && m.llm_name === llm_name,
    );
  }, [modelKey, supportedModels]);

  const canSubmit =
    name.trim().length > 0 &&
    kbIds.length > 0 &&
    !!selectedModel &&
    !submitting;

  const handleSubmit = () => {
    if (!canSubmit || !selectedModel) return;
    onSubmit({
      name: name.trim(),
      kb_ids: kbIds,
      system_prompt: systemPrompt,
      model_config: {
        llm_name: selectedModel.llm_name,
        factory: selectedModel.factory,
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
            {modelsLoading ? (
              <div className="text-xs text-muted-foreground">
                {t('common.loading')}
              </div>
            ) : supportedModels.length === 0 ? (
              <div className="text-xs text-red-600">
                {t('agentV2.noModelHint')}
              </div>
            ) : (
              <Select value={modelKey} onValueChange={setModelKey}>
                <SelectTrigger>
                  <SelectValue placeholder={t('agentV2.selectModel')} />
                </SelectTrigger>
                <SelectContent>
                  {supportedModels.map((m) => (
                    <SelectItem
                      key={`${m.factory}|${m.llm_name}`}
                      value={`${m.factory}|${m.llm_name}`}
                    >
                      {m.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <div className="text-xs text-muted-foreground mt-1">
              {t('agentV2.modelProviderHint')}
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
