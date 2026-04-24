/**
 * 底部输入区 — chips 展示当前 session 上下文 + 发送/中断按钮。
 *
 * Phase 2.7 Stage 4 — 支持文件上传：
 * - 左下角 "+" 按钮打开系统文件选择器
 * - 拖拽文件到输入区域触发上传
 * - 上方展示 staged 附件 chip 列表（上传进度 / 撤销按钮）
 *
 * 发送时**不需要**把 attachment_ids 塞进消息体——后端 `send_message` 入口
 * 会从 DB 扫当前 session 的 staged + archived 附件注入 ctx.attachments。
 * 前端只负责"发完清本地 chip 状态"。
 */

import { KeyboardEvent, memo, useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LucidePaperclip } from 'lucide-react';
import { AgentV2Session } from '../api';
import { T } from '../theme';
import { Badge } from './badge';
import { I, Kbd } from './icons';
import { AttachmentChip } from './attachment-chip';
import type { StagedAttachment } from '../hooks/use-attachments';

interface Props {
  session: AgentV2Session | undefined;
  disabled: boolean;
  isStreaming: boolean;
  onSend: (text: string) => void;
  onAbort: () => void;
  // Phase 2.7 Stage 4 — attachment integration
  attachments?: StagedAttachment[];
  onUploadFiles?: (files: FileList | File[]) => void;
  onRemoveAttachment?: (localId: string) => void;
  onClearAttachments?: () => void;
}

export const Composer = memo(function Composer({
  session,
  disabled,
  isStreaming,
  onSend,
  onAbort,
  attachments,
  onUploadFiles,
  onRemoveAttachment,
  onClearAttachments,
}: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);

  const hasPendingUpload = Boolean(
    attachments?.some((a) => a.status === 'uploading'),
  );

  const handleSend = useCallback(() => {
    const v = value.trim();
    if (!v || disabled || isStreaming || hasPendingUpload) return;
    onSend(v);
    setValue('');
    // Backend picks up attachments by scanning session's staged rows; the
    // chip list clears locally so the next message doesn't visually imply
    // the same attachments are still in play. (They stay in DB either way,
    // tracked via # Session attachments in the supervisor prompt.)
    onClearAttachments?.();
  }, [
    value,
    disabled,
    isStreaming,
    hasPendingUpload,
    onSend,
    onClearAttachments,
  ]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const canSend =
    !disabled && !isStreaming && !hasPendingUpload && value.trim().length > 0;

  const openFilePicker = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFilesSelected = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        onUploadFiles?.(e.target.files);
      }
      // Reset so the same file can be picked again if user cancels
      e.target.value = '';
    },
    [onUploadFiles],
  );

  const handleDragEnter = useCallback(
    (e: React.DragEvent) => {
      if (!onUploadFiles || disabled) return;
      e.preventDefault();
      dragDepth.current += 1;
      if (e.dataTransfer.types.includes('Files')) {
        setIsDragging(true);
      }
    },
    [onUploadFiles, disabled],
  );

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setIsDragging(false);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      dragDepth.current = 0;
      setIsDragging(false);
      if (!onUploadFiles || disabled) return;
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        onUploadFiles(e.dataTransfer.files);
      }
    },
    [onUploadFiles, disabled],
  );

  return (
    <div style={{ padding: '0 36px 28px', background: T.bg }}>
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        <div
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          style={{
            background: T.surface,
            border: `1px solid ${isDragging ? T.accent : T.border2}`,
            borderRadius: T.radiusLg,
            padding: '10px 12px',
            boxShadow: isDragging
              ? `0 0 0 2px ${T.accent}33`
              : '0 1px 3px rgba(0,0,0,.04)',
            opacity: disabled ? 0.6 : 1,
            transition: 'opacity .15s, border-color .15s, box-shadow .15s',
            position: 'relative',
          }}
        >
          {isDragging && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                borderRadius: T.radiusLg,
                background: `${T.accent}15`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 13,
                color: T.accent,
                fontWeight: 500,
                pointerEvents: 'none',
                zIndex: 1,
              }}
            >
              {t('agentV2.dropToAttach', '松开鼠标上传附件')}
            </div>
          )}

          {attachments && attachments.length > 0 && (
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 6,
                marginBottom: 6,
              }}
            >
              {attachments.map((a) => (
                <AttachmentChip
                  key={a.localId}
                  attachment={a}
                  onRemove={(id) => onRemoveAttachment?.(id)}
                  disabled={disabled || isStreaming}
                />
              ))}
            </div>
          )}

          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled || isStreaming}
            rows={2}
            placeholder={
              disabled
                ? t('agentV2.pickSessionFirst')
                : t('agentV2.inputPlaceholder')
            }
            style={{
              width: '100%',
              border: 'none',
              outline: 'none',
              resize: 'none',
              fontSize: 14,
              fontFamily: T.font,
              color: T.text,
              background: 'transparent',
              lineHeight: 1.5,
            }}
          />
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginTop: 4,
              flexWrap: 'wrap',
            }}
          >
            {onUploadFiles && (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  hidden
                  onChange={handleFilesSelected}
                  accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.md,.txt,.csv,.json,.html,.htm,.jpg,.jpeg,.png,.webp,.gif"
                />
                <button
                  type="button"
                  onClick={openFilePicker}
                  disabled={disabled || isStreaming}
                  title={t('agentV2.attachFile', '添加附件')}
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: 6,
                    background: 'transparent',
                    color: T.textDim,
                    border: `1px solid ${T.border2}`,
                    cursor:
                      disabled || isStreaming ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'background .15s',
                  }}
                  data-testid="agent-v2-attach-button"
                >
                  <LucidePaperclip size={13} />
                </button>
              </>
            )}

            {session && (
              <>
                <Badge tone="neutral">
                  <I.book size={10} /> {session.kb_ids?.length ?? 0}{' '}
                  {t('agentV2.knowledgeBaseShort')}
                </Badge>
                <Badge tone="brand">
                  <I.brain size={10} />{' '}
                  {session.model_config_json?.llm_name ??
                    session.model_config_json?.model ??
                    t('agentV2.model')}
                </Badge>
                {session.tool_names?.length ? (
                  <Badge tone="neutral">
                    <I.wrench size={10} /> {session.tool_names.length}{' '}
                    {t('agentV2.toolsShort')}
                  </Badge>
                ) : (
                  <Badge tone="neutral">
                    <I.wrench size={10} /> {t('agentV2.allTools')}
                  </Badge>
                )}
              </>
            )}
            <div style={{ flex: 1 }} />
            <div
              style={{
                fontSize: 10,
                color: T.textDim,
                display: 'flex',
                gap: 6,
                alignItems: 'center',
              }}
            >
              <Kbd>↵</Kbd> {t('agentV2.send')}
            </div>
            {isStreaming ? (
              <button
                onClick={onAbort}
                title={t('agentV2.abort')}
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 6,
                  background: T.danger,
                  color: '#fff',
                  border: 'none',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <I.stop size={11} color="#fff" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!canSend}
                title={
                  hasPendingUpload
                    ? t('agentV2.waitUpload', '等待附件上传完成')
                    : t('agentV2.send')
                }
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 6,
                  background: canSend ? T.accent : T.surface3,
                  color: canSend ? '#fff' : T.textDim,
                  border: 'none',
                  cursor: canSend ? 'pointer' : 'not-allowed',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'background .15s',
                }}
              >
                <I.sendUp size={13} color={canSend ? '#fff' : T.textDim} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
});
