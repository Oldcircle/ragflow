/**
 * 知识库成员管理页（Phase 2.1 RBAC）。
 *
 * 路由: /dataset/dataset-member/:id
 *
 * 顶部 workbench header；中间为可编辑成员表（隐式 OWNER 行不可改、可移除）；
 * 右上方 "邀请成员" 按钮 → 弹窗输入邮箱 + 选角色。
 */

import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Button, ButtonLoading } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import message from '@/components/ui/message';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LucideTrash2, UserPlus } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { DatasetMember, DatasetRole, datasetMemberApi } from './api';

type ManageableRole = Exclude<DatasetRole, 'owner'>;

const MANAGEABLE_ROLES: ManageableRole[] = ['admin', 'contributor', 'viewer'];

function roleLabelKey(role: DatasetRole) {
  switch (role) {
    case 'owner':
      return 'knowledgeList.memberRoleOwner';
    case 'admin':
      return 'knowledgeList.memberRoleAdmin';
    case 'contributor':
      return 'knowledgeList.memberRoleContributor';
    case 'viewer':
      return 'knowledgeList.memberRoleViewer';
  }
}

function roleHintKey(role: DatasetRole) {
  switch (role) {
    case 'owner':
      return 'knowledgeList.memberRoleOwnerHint';
    case 'admin':
      return 'knowledgeList.memberRoleAdminHint';
    case 'contributor':
      return 'knowledgeList.memberRoleContributorHint';
    case 'viewer':
      return 'knowledgeList.memberRoleViewerHint';
  }
}

function roleBadgeTone(role: DatasetRole) {
  switch (role) {
    case 'owner':
      return 'bg-accent-primary text-white';
    case 'admin':
      return 'bg-accent-primary/10 text-accent-primary';
    case 'contributor':
      return 'bg-state-success/15 text-state-success';
    case 'viewer':
      return 'bg-bg-card text-text-secondary';
  }
}

function MemberRoleBadge({ role }: { role: DatasetRole }) {
  const { t } = useTranslation();
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium',
        roleBadgeTone(role),
      )}
    >
      {t(roleLabelKey(role))}
    </span>
  );
}

function InviteDialog({
  open,
  onOpenChange,
  onSubmit,
  loading,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: { email: string; role: ManageableRole }) => void;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<ManageableRole>('viewer');

  const submit = () => {
    if (!email.trim()) {
      message.error(t('knowledgeList.memberInviteEmailPlaceholder'));
      return;
    }
    onSubmit({ email: email.trim(), role });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setEmail('');
          setRole('viewer');
        }
        onOpenChange(o);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('knowledgeList.memberInvite')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-text-primary">
              {t('knowledgeList.memberInviteEmail')}
            </label>
            <Input
              type="email"
              value={email}
              placeholder={t('knowledgeList.memberInviteEmailPlaceholder')}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="dataset-member-email"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-text-primary">
              {t('knowledgeList.memberInviteRole')}
            </label>
            <Select
              value={role}
              onValueChange={(v) => setRole(v as ManageableRole)}
            >
              <SelectTrigger data-testid="dataset-member-role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MANAGEABLE_ROLES.map((r) => (
                  <SelectItem value={r} key={r}>
                    <div className="flex flex-col">
                      <span className="text-sm font-medium">
                        {t(roleLabelKey(r))}
                      </span>
                      <span className="text-xs text-text-secondary">
                        {t(roleHintKey(r))}
                      </span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <ButtonLoading
            onClick={submit}
            loading={loading}
            data-testid="dataset-member-submit"
          >
            {t('knowledgeList.memberInviteSubmit')}
          </ButtonLoading>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function DatasetMembers() {
  const { t } = useTranslation();
  const { id: kbId } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const [inviteOpen, setInviteOpen] = useState(false);

  const queryKey = ['dataset-members', kbId];

  const {
    data: members = [],
    isLoading,
    error,
  } = useQuery<DatasetMember[]>({
    queryKey,
    queryFn: () => datasetMemberApi.list(kbId!),
    enabled: !!kbId,
  });

  const grantMut = useMutation({
    mutationFn: (payload: {
      email?: string;
      user_id?: string;
      role: DatasetRole;
    }) => datasetMemberApi.grant(kbId!, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey });
      setInviteOpen(false);
      message.success(t('common.success'));
    },
    onError: (err: any) => {
      message.error(err?.response?.data?.message || String(err));
    },
  });

  const revokeMut = useMutation({
    mutationFn: (userId: string) => datasetMemberApi.revoke(kbId!, userId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey });
      message.success(t('common.success'));
    },
    onError: (err: any) => {
      message.error(err?.response?.data?.message || String(err));
    },
  });

  const accessDenied =
    (error as any)?.response?.data?.code === 109 ||
    (error as any)?.response?.status === 401;

  return (
    <article
      className="flex min-h-0 flex-1 flex-col"
      data-testid="dataset-members"
    >
      <header className="flex items-start justify-between gap-4 border-b border-border-button bg-bg-component/60 px-6 pb-5 pt-6">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold leading-tight tracking-normal text-text-primary">
            {t('knowledgeList.membersTitle')}
          </h1>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">
            {t('knowledgeList.membersDescription')}
          </p>
        </div>
        {!accessDenied && (
          <Button
            onClick={() => setInviteOpen(true)}
            data-testid="dataset-member-invite"
          >
            <UserPlus className="size-4" />
            {t('knowledgeList.memberInvite')}
          </Button>
        )}
      </header>

      <section className="min-h-0 flex-1 overflow-auto px-6 py-5 scrollbar-auto">
        {accessDenied ? (
          <div className="rounded-lg border border-state-error/30 bg-state-error/5 p-4 text-sm text-state-error">
            {t('knowledgeList.memberAccessDenied')}
          </div>
        ) : isLoading ? (
          <div className="text-sm text-text-secondary">
            {t('common.loading')}
          </div>
        ) : (
          <ul className="divide-y divide-border-button overflow-hidden rounded-lg border border-border-button bg-bg-component">
            {members.map((m) => {
              const isOwner = m.role === 'owner';
              return (
                <li
                  key={m.user_id}
                  className="flex items-center gap-4 px-4 py-3"
                  data-testid="dataset-member-row"
                  data-member-role={m.role}
                >
                  <RAGFlowAvatar
                    avatar={m.avatar || undefined}
                    name={m.nickname || m.email || m.user_id}
                    isPerson
                    className="size-9 shrink-0"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-text-primary">
                        {m.nickname || m.email || m.user_id}
                      </span>
                      <MemberRoleBadge role={m.role} />
                      {m.implicit && (
                        <span className="text-[11px] text-text-disabled">
                          {t('knowledgeList.memberImplicitTip')}
                        </span>
                      )}
                    </div>
                    {m.email && (
                      <div className="truncate text-xs text-text-secondary">
                        {m.email}
                      </div>
                    )}
                  </div>

                  <div className="flex shrink-0 items-center gap-2">
                    {!isOwner && (
                      <Select
                        value={m.role}
                        onValueChange={(v) =>
                          grantMut.mutate({
                            user_id: m.user_id,
                            role: v as DatasetRole,
                          })
                        }
                      >
                        <SelectTrigger className="h-8 w-32 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {MANAGEABLE_ROLES.map((r) => (
                            <SelectItem value={r} key={r}>
                              {t(roleLabelKey(r))}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                    {!isOwner && (
                      <ConfirmDeleteDialog
                        title={t('knowledgeList.memberRevokeConfirm')}
                        onOk={() => revokeMut.mutate(m.user_id)}
                      >
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          data-testid="dataset-member-remove"
                        >
                          <LucideTrash2 className="size-4 text-state-error" />
                        </Button>
                      </ConfirmDeleteDialog>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <InviteDialog
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        loading={grantMut.isPending}
        onSubmit={(p) => grantMut.mutate(p)}
      />
    </article>
  );
}
