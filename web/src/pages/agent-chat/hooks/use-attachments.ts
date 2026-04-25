/**
 * Phase 2.7 Stage 4 — session attachment upload hook.
 *
 * Owns the "staged attachments awaiting send" list shown in the composer.
 * The composer:
 *   1. User drops / picks files → `upload(files)`
 *   2. Optimistic pending chip shown immediately; replaced on server response
 *   3. User can click × to call `remove(attachmentId)` (server marks rejected)
 *   4. On send, parent passes `stagedIds()` to the conversation endpoint
 *      and calls `clearStaged()` so the composer starts fresh
 *
 * Deliberately NOT a React Query mutation — this state is component-local
 * (per-composer), not a cache to share.
 */

import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import type { AgentV2Attachment } from '../api';
import { agentV2Api } from '../api';

export interface StagedAttachment {
  /** Server-assigned id once upload completes; absent while in-flight. */
  id?: string;
  /** Local uuid so we can key React lists before `id` is known. */
  localId: string;
  filename: string;
  size_bytes: number;
  mime_type?: string;
  status: 'uploading' | 'ready' | 'failed' | 'rejected';
  progress: number;
  error?: string;
  /** Populated from server. */
  preview_text?: string | null;
  source_url?: string | null;
}

export interface UseAttachmentsResult {
  staged: StagedAttachment[];
  upload: (files: FileList | File[]) => Promise<void>;
  remove: (localId: string) => Promise<void>;
  /** Returns currently ready attachment ids; used by composer on send. */
  stagedReadyIds: () => string[];
  clearStaged: () => void;
}

let _counter = 0;
const makeLocalId = () => `local_${Date.now()}_${++_counter}`;

export function useAttachments(
  sessionId: string | undefined,
): UseAttachmentsResult {
  const { t } = useTranslation();
  const [staged, setStaged] = useState<StagedAttachment[]>([]);

  const upload = useCallback(
    async (files: FileList | File[]) => {
      if (!sessionId) {
        toast.error(t('agentV2.attachmentNoSession', '请先创建会话再上传附件'));
        return;
      }
      const list = Array.from(files);
      if (list.length === 0) return;

      // Optimistic placeholders
      const placeholders: StagedAttachment[] = list.map((f) => ({
        localId: makeLocalId(),
        filename: f.name,
        size_bytes: f.size,
        mime_type: f.type,
        status: 'uploading',
        progress: 0,
      }));
      setStaged((s) => [...s, ...placeholders]);

      try {
        const res = await agentV2Api.uploadAttachments(
          sessionId,
          list,
          (pct) => {
            setStaged((s) =>
              s.map((it) =>
                placeholders.some((p) => p.localId === it.localId) &&
                it.status === 'uploading'
                  ? { ...it, progress: pct }
                  : it,
              ),
            );
          },
        );

        // Backend may return HTTP 200 with `data: null` when the request
        // fails server-side validation (RAGFlow's `get_data_error_result`
        // shape). axios won't throw in that case, so axios's `data.data`
        // falls through as null. Treat that the same as an exception:
        // mark placeholders failed and surface the message.
        if (!res || !Array.isArray((res as { uploaded?: unknown }).uploaded)) {
          throw new Error(
            t('agentV2.attachmentUploadFailed', '附件上传失败（服务器拒绝）'),
          );
        }

        // Replace placeholders with server rows.
        // We correlate by filename; if multiple same-filename files, match
        // in order. Not perfect but good enough — most users attach one file
        // at a time.
        setStaged((s) => {
          const withoutPlaceholders = s.filter(
            (it) => !placeholders.some((p) => p.localId === it.localId),
          );
          const uploaded: StagedAttachment[] = res.uploaded.map(
            (a: AgentV2Attachment) => ({
              id: a.id,
              localId: makeLocalId(),
              filename: a.filename,
              size_bytes: a.size_bytes,
              mime_type: a.mime_type,
              status: a.status === 'archived' ? 'ready' : 'ready',
              progress: 100,
              preview_text: a.preview_text,
              source_url: a.source_url,
            }),
          );
          return [...withoutPlaceholders, ...uploaded];
        });

        // Surface rejections (size / MIME / quota)
        const rejected = res.rejected ?? [];
        if (rejected.length > 0) {
          for (const r of rejected) {
            toast.error(`${r.filename}: ${r.reason}`);
          }
        }
      } catch (err: any) {
        // Mark placeholders failed so user sees what went wrong
        setStaged((s) =>
          s.map((it) =>
            placeholders.some((p) => p.localId === it.localId)
              ? { ...it, status: 'failed', error: err?.message ?? String(err) }
              : it,
          ),
        );
        toast.error(
          t('agentV2.attachmentUploadFailed', '附件上传失败') +
            ': ' +
            (err?.message ?? String(err)),
        );
      }
    },
    [sessionId, t],
  );

  const remove = useCallback(
    async (localId: string) => {
      const target = staged.find((it) => it.localId === localId);
      if (!target) return;
      // Drop optimistically
      setStaged((s) => s.filter((it) => it.localId !== localId));
      // If server-side exists, notify backend to mark rejected
      if (target.id && sessionId) {
        try {
          await agentV2Api.deleteAttachment(sessionId, target.id);
        } catch (err: any) {
          // Non-fatal — the chip is already gone from UI. Surface a quiet
          // toast so the user knows server state didn't match.
          toast.error(
            t(
              'agentV2.attachmentRejectFailed',
              '附件撤销在服务器端失败（UI 已移除）',
            ) +
              ': ' +
              (err?.message ?? String(err)),
          );
        }
      }
    },
    [staged, sessionId, t],
  );

  const stagedReadyIds = useCallback(
    () =>
      staged
        .filter((it) => it.status === 'ready' && !!it.id)
        .map((it) => it.id!),
    [staged],
  );

  const clearStaged = useCallback(() => setStaged([]), []);

  return { staged, upload, remove, stagedReadyIds, clearStaged };
}
