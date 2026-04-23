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
import { AgentV2Template } from '../api';
import { useAvailableModels, useTemplates } from '../hooks/use-sessions';

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
  const { data: templateData } = useTemplates();

  const supportedModels = useMemo(
    () => (modelData?.models ?? []).filter((m) => m.supported),
    [modelData?.models],
  );
  const templates = templateData?.templates ?? [];

  const [name, setName] = useState('');
  const [kbIds, setKbIds] = useState<string[]>([]);
  const [systemPrompt, setSystemPrompt] = useState(DEFAULT_SYSTEM_PROMPT);
  const [modelKey, setModelKey] = useState<string>(''); // `${factory}|${llm_name}`
  // 用 string 存原始输入值，允许用户擦空 / 中间态编辑；提交时再 parse + 兜底。
  const [maxTurns, setMaxTurns] = useState('20');
  const [maxBudget, setMaxBudget] = useState('0.5');
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>('');

  const applyTemplate = (tpl: AgentV2Template) => {
    setName(tpl.name);
    setSystemPrompt(tpl.system_prompt);
    setMaxTurns(String(tpl.default_max_turns));
    setMaxBudget(String(tpl.default_max_budget_usd));
    setSelectedTemplateId(tpl.id);
    // KB 不自动填，让用户自己根据 kb_hints 挑
  };

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
    // 擦空 / 非法值时回落到稳妥默认；范围夹紧由后端 create_session 再校验一次
    const parsedTurns = Number.parseInt(maxTurns, 10);
    const parsedBudget = Number.parseFloat(maxBudget);
    onSubmit({
      name: name.trim(),
      kb_ids: kbIds,
      system_prompt: systemPrompt,
      model_config: {
        llm_name: selectedModel.llm_name,
        factory: selectedModel.factory,
      },
      max_turns:
        Number.isFinite(parsedTurns) && parsedTurns > 0 ? parsedTurns : 20,
      max_budget_usd:
        Number.isFinite(parsedBudget) && parsedBudget > 0 ? parsedBudget : 0.5,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('agentV2.newSessionDialogTitle')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* 模板选择（可选） */}
          {templates.length > 0 && (
            <Field
              label={t('agentV2.startFromTemplate')}
              hint={t('agentV2.templateHint')}
            >
              <div className="grid grid-cols-2 gap-2 max-h-48 overflow-auto">
                {templates.map((tpl) => {
                  const active = selectedTemplateId === tpl.id;
                  return (
                    <button
                      key={tpl.id}
                      type="button"
                      onClick={() => applyTemplate(tpl)}
                      className={`text-left p-3 rounded-md border transition-colors ${
                        active
                          ? 'border-accent-primary bg-accent-primary/10'
                          : 'border-border-button bg-bg-component hover:bg-bg-card'
                      }`}
                    >
                      <div className="flex items-center gap-2 mb-1 text-sm font-medium text-text-primary">
                        <span>{tpl.icon}</span>
                        <span className="truncate">{tpl.name}</span>
                      </div>
                      <div
                        className="text-xs text-text-secondary"
                        style={{
                          display: '-webkit-box',
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                        }}
                      >
                        {tpl.description}
                      </div>
                    </button>
                  );
                })}
              </div>
            </Field>
          )}

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
                        ? 'bg-accent-primary text-white border-accent-primary'
                        : 'bg-bg-component hover:bg-bg-card border-border-button text-text-secondary'
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
              <div className="text-xs text-state-error">
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
                onChange={(e) => setMaxTurns(e.target.value)}
              />
            </Field>
            <Field label={t('agentV2.maxBudget')}>
              <Input
                type="number"
                step="0.1"
                min={0.1}
                value={maxBudget}
                onChange={(e) => setMaxBudget(e.target.value)}
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
      <div className="text-xs font-medium mb-1.5 text-text-primary">
        {label}
      </div>
      {children}
      {hint && <div className="text-xs text-muted-foreground mt-1">{hint}</div>}
    </div>
  );
}
