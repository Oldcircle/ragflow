/**
 * Phase 2.8.1 — Session settings drawer.
 *
 * Edits a subset of session fields after creation. Locked fields:
 * `model_config_json`, `system_prompt` — those change agent identity and
 * require a new session (matches Anthropic / OpenAI conventions).
 *
 * Fields editable here:
 *   - name
 *   - kb_ids (RBAC-checked server-side)
 *   - tool_names (validated against ALL_TOOLS server-side)
 *   - max_turns / max_budget_usd / history_turn_limit
 *   - citation_enforce_level / citation_numeric_strict
 *
 * Server-side: PATCH /v1/agent_v2/session/<id> — see
 * api/apps/agent_v2_app.py + api/agent_v2/session_patch.py
 */

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useFetchKnowledgeList } from '@/hooks/use-knowledge-request';
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { AgentV2Session } from '../api';
import { useTools, useUpdateSession } from '../hooks/use-sessions';

export interface SessionSettingsDrawerProps {
  session: AgentV2Session | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
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
    <div className="space-y-1.5">
      <div className="text-xs font-medium text-text-primary">{label}</div>
      {children}
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function SessionSettingsDrawer({
  session,
  open,
  onOpenChange,
}: SessionSettingsDrawerProps) {
  const { t } = useTranslation();
  const { list: kbList, loading: kbLoading } = useFetchKnowledgeList(true);
  const { data: toolsData, isLoading: toolsLoading } = useTools();
  const update = useUpdateSession();

  // Form state — initialized from session, reset whenever drawer opens.
  const [name, setName] = useState('');
  const [kbIds, setKbIds] = useState<string[]>([]);
  const [toolNames, setToolNames] = useState<string[] | null>(null);
  const [maxTurns, setMaxTurns] = useState<number>(20);
  const [maxBudget, setMaxBudget] = useState<number>(0.5);
  const [historyTurnLimit, setHistoryTurnLimit] = useState<number>(10);
  const [citationEnforceLevel, setCitationEnforceLevel] = useState<
    'off' | 'warn' | 'strict'
  >('warn');
  const [citationNumericStrict, setCitationNumericStrict] =
    useState<boolean>(true);

  // Hydrate form whenever drawer opens with a fresh session row.
  useEffect(() => {
    if (!open || !session) return;
    setName(session.name || '');
    setKbIds(session.kb_ids || []);
    setToolNames(session.tool_names);
    setMaxTurns(session.max_turns ?? 20);
    setMaxBudget(session.max_budget_usd ?? 0.5);
    setHistoryTurnLimit(session.history_turn_limit ?? 10);
    setCitationEnforceLevel(session.citation_enforce_level ?? 'warn');
    setCitationNumericStrict(session.citation_numeric_strict ?? true);
  }, [open, session]);

  // Compute the patch payload from the diff between form and session.
  // Only include fields that actually changed — this keeps the audit
  // log noise low and avoids triggering the tool_names warning when
  // the user didn't touch tools.
  const patch = useMemo(() => {
    if (!session) return {};
    const out: Record<string, unknown> = {};
    if (name.trim() && name.trim() !== session.name) out.name = name.trim();
    if (
      JSON.stringify([...kbIds].sort()) !==
      JSON.stringify([...(session.kb_ids || [])].sort())
    ) {
      out.kb_ids = kbIds;
    }
    if (
      JSON.stringify(toolNames ?? []) !==
      JSON.stringify(session.tool_names ?? [])
    ) {
      // Server treats empty list as "all tools" — pass through as-is.
      out.tool_names = toolNames ?? [];
    }
    if (maxTurns !== session.max_turns) out.max_turns = maxTurns;
    if (maxBudget !== session.max_budget_usd) out.max_budget_usd = maxBudget;
    if (historyTurnLimit !== (session.history_turn_limit ?? 10)) {
      out.history_turn_limit = historyTurnLimit;
    }
    if (citationEnforceLevel !== (session.citation_enforce_level ?? 'warn')) {
      out.citation_enforce_level = citationEnforceLevel;
    }
    if (citationNumericStrict !== (session.citation_numeric_strict ?? true)) {
      out.citation_numeric_strict = citationNumericStrict;
    }
    return out;
  }, [
    session,
    name,
    kbIds,
    toolNames,
    maxTurns,
    maxBudget,
    historyTurnLimit,
    citationEnforceLevel,
    citationNumericStrict,
  ]);

  const dirty = Object.keys(patch).length > 0;

  const allTools = toolsData?.tools ?? [];

  const handleSave = async () => {
    if (!session || !dirty) return;
    try {
      const res = await update.mutateAsync({ sessionId: session.id, patch });
      toast.success(t('agentV2.settingsSaved'));
      // Surface server-side warnings if any (e.g. tool_names changed but
      // the cached system_prompt is now stale). `res` may be null/undefined
      // on weird success shapes — defensively access via optional chaining.
      const warnings = res?.warnings ?? [];
      for (const w of warnings) {
        toast.warning(w);
      }
      onOpenChange(false);
    } catch (e) {
      const err = e as Error;
      toast.error(err.message || t('agentV2.settingsSaveFailed'));
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[400px] sm:w-[480px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{t('agentV2.settingsTitle')}</SheetTitle>
          <SheetDescription>{t('agentV2.settingsHint')}</SheetDescription>
        </SheetHeader>

        <div className="space-y-5 py-4">
          {/* name */}
          <Field label={t('agentV2.sessionName')}>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
            />
          </Field>

          {/* kb_ids */}
          <Field label={t('agentV2.kbScope')} hint={t('agentV2.kbScopeHint')}>
            {kbLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : kbList.length === 0 ? (
              <div className="text-xs text-muted-foreground">
                {t('agentV2.noKbHint')}
              </div>
            ) : (
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
            )}
          </Field>

          {/* tool_names */}
          <Field label={t('agentV2.tools')} hint={t('agentV2.toolsHint')}>
            {toolsLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <div className="flex flex-wrap gap-1.5 max-h-48 overflow-auto">
                {allTools.map((tool) => {
                  const list = toolNames ?? [];
                  const checked = list.includes(tool.name);
                  return (
                    <button
                      key={tool.name}
                      type="button"
                      title={tool.description}
                      onClick={() => {
                        setToolNames((prev) => {
                          const cur = prev ?? [];
                          return cur.includes(tool.name)
                            ? cur.filter((x) => x !== tool.name)
                            : [...cur, tool.name];
                        });
                      }}
                      className={`px-2.5 py-1 rounded-md border text-[11px] font-mono transition-colors ${
                        checked
                          ? 'bg-accent-primary/15 text-accent-primary border-accent-primary'
                          : 'bg-bg-component hover:bg-bg-card border-border-button text-text-secondary'
                      }`}
                    >
                      {tool.name}
                    </button>
                  );
                })}
              </div>
            )}
          </Field>

          {/* numeric */}
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('agentV2.maxTurns')}>
              <Input
                type="number"
                min={1}
                max={100}
                value={maxTurns}
                onChange={(e) =>
                  setMaxTurns(
                    Math.max(1, Math.min(100, Number(e.target.value) || 1)),
                  )
                }
              />
            </Field>
            <Field label={t('agentV2.maxBudget')}>
              <Input
                type="number"
                step={0.1}
                min={0.01}
                max={100}
                value={maxBudget}
                onChange={(e) =>
                  setMaxBudget(
                    Math.max(
                      0.01,
                      Math.min(100, Number(e.target.value) || 0.5),
                    ),
                  )
                }
              />
            </Field>
          </div>

          {/* history */}
          <Field
            label={t('agentV2.historyTurnLimit')}
            hint={t('agentV2.historyTurnLimitHint')}
          >
            <Input
              type="number"
              min={0}
              max={100}
              value={historyTurnLimit}
              onChange={(e) =>
                setHistoryTurnLimit(
                  Math.max(0, Math.min(100, Number(e.target.value) || 0)),
                )
              }
            />
          </Field>

          {/* citation */}
          <Field label={t('agentV2.citationEnforce')}>
            <Select
              value={citationEnforceLevel}
              onValueChange={(v) =>
                setCitationEnforceLevel(v as 'off' | 'warn' | 'strict')
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="off">{t('agentV2.citationOff')}</SelectItem>
                <SelectItem value="warn">
                  {t('agentV2.citationWarn')}
                </SelectItem>
                <SelectItem value="strict">
                  {t('agentV2.citationStrict')}
                </SelectItem>
              </SelectContent>
            </Select>
          </Field>

          <div className="flex items-center gap-2">
            <Checkbox
              id="citation-numeric-strict"
              checked={citationNumericStrict}
              onCheckedChange={(v) => setCitationNumericStrict(Boolean(v))}
            />
            <label
              htmlFor="citation-numeric-strict"
              className="text-xs cursor-pointer select-none"
            >
              <div>{t('agentV2.citationNumericStrict')}</div>
              <div className="text-muted-foreground">
                {t('agentV2.citationNumericStrictHint')}
              </div>
            </label>
          </div>

          <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-600 dark:text-amber-400">
            {t('agentV2.settingsLockedNotice')}
          </div>
        </div>

        <SheetFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={update.isPending}
          >
            {t('agentV2.settingsCancel')}
          </Button>
          <Button onClick={handleSave} disabled={!dirty || update.isPending}>
            {update.isPending && (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            )}
            {t('agentV2.settingsSave')}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
