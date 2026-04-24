/**
 * Phase 2.7 Stage 4 — composer 上方展示 staged 附件 chip。
 *
 * 状态机：
 * - uploading  (进度条 + 百分比)
 * - ready      (✓ 可发；右上角 × 撤销)
 * - failed     (!  红色；右上角 × 撤销)
 * - rejected   (短暂展示的过渡状态，父组件移除)
 */

import { cn } from '@/lib/utils';
import {
  LucideFile,
  LucideFileImage,
  LucideFileSpreadsheet,
  LucideFileText,
  LucideFileType,
  LucideGlobe,
  LucideLoader2,
  LucideTriangleAlert,
  LucideX,
} from 'lucide-react';
import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import type { StagedAttachment } from '../hooks/use-attachments';

interface Props {
  attachment: StagedAttachment;
  onRemove: (localId: string) => void;
  disabled?: boolean;
}

function mimeIcon(mime: string | undefined, size = 13) {
  if (!mime) return <LucideFile size={size} />;
  if (mime.startsWith('image/')) return <LucideFileImage size={size} />;
  if (mime.includes('sheet') || mime.includes('excel'))
    return <LucideFileSpreadsheet size={size} />;
  if (mime === 'application/pdf') return <LucideFileType size={size} />;
  if (mime.startsWith('text/') || mime === 'application/json')
    return <LucideFileText size={size} />;
  return <LucideFile size={size} />;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export const AttachmentChip = memo(function AttachmentChip({
  attachment,
  onRemove,
  disabled,
}: Props) {
  const { t } = useTranslation();
  const { filename, mime_type, size_bytes, status, progress, error, source_url } =
    attachment;

  const isUploading = status === 'uploading';
  const isFailed = status === 'failed';

  return (
    <div
      className={cn(
        'group inline-flex max-w-[280px] items-center gap-2 rounded-md border px-2 py-1 text-[11px]',
        isFailed
          ? 'border-red-500/40 bg-red-500/10 text-red-500'
          : 'border-border-button bg-bg-component text-text-primary',
        isUploading && 'opacity-80',
      )}
      data-testid="agent-v2-attachment-chip"
      data-status={status}
      title={error || filename}
    >
      <span className="shrink-0 text-accent-primary">
        {isUploading ? (
          <LucideLoader2 size={12} className="animate-spin" />
        ) : isFailed ? (
          <LucideTriangleAlert size={12} />
        ) : source_url ? (
          <LucideGlobe size={12} />
        ) : (
          mimeIcon(mime_type, 12)
        )}
      </span>
      <span className="min-w-0 flex-1 truncate font-medium">{filename}</span>
      <span className="shrink-0 text-text-secondary">
        {isUploading ? `${progress}%` : fmtSize(size_bytes)}
      </span>
      <button
        type="button"
        aria-label={t('agentV2.attachmentRemove', '移除附件')}
        onClick={() => onRemove(attachment.localId)}
        disabled={disabled}
        className={cn(
          'shrink-0 rounded p-0.5 text-text-disabled opacity-60 transition',
          'hover:bg-bg-base hover:text-text-primary hover:opacity-100',
          disabled && 'cursor-not-allowed',
        )}
      >
        <LucideX size={10} />
      </button>
    </div>
  );
});
