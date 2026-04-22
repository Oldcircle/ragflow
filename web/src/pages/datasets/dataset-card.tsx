import { MoreButton } from '@/components/more-button';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Card } from '@/components/ui/card';
import { PermissionRole } from '@/constants/permission';
import { useNavigatePage } from '@/hooks/logic-hooks/navigate-hooks';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { IDataset } from '@/interfaces/database/dataset';
import { cn } from '@/lib/utils';
import { formatDate } from '@/utils/date';
import { ChevronRight, Database, Layers, Lock, Users } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DatasetDropdown } from './dataset-dropdown';
import { useRenameDataset } from './use-rename-dataset';

export type DatasetCardProps = {
  dataset: IDataset;
} & Pick<ReturnType<typeof useRenameDataset>, 'showDatasetRenameModal'>;

function formatNumber(value: number | undefined) {
  if (!value || Number.isNaN(value)) return '0';
  if (value >= 1000)
    return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  return value.toLocaleString();
}

export function DatasetCard({
  dataset,
  showDatasetRenameModal,
}: DatasetCardProps) {
  const { t } = useTranslation();
  const { navigateToDataset } = useNavigatePage();
  const { data: userInfo } = useFetchUserInfo();

  const isTeam = dataset.permission === PermissionRole.Team;
  const isOwner = !!dataset.nickname && userInfo?.nickname === dataset.nickname;

  return (
    <Card
      as="article"
      tabIndex={0}
      onClick={navigateToDataset(dataset.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          navigateToDataset(dataset.id)();
        }
      }}
      data-testid="dataset-card"
      data-dataset-id={dataset.id}
      data-dataset-name={dataset.name}
      className={cn(
        'group flex h-full w-full cursor-pointer flex-col gap-4 p-5',
        'border border-border-button bg-bg-component transition',
        'hover:border-accent-primary/60 hover:shadow-md',
      )}
    >
      <header className="flex items-start gap-3">
        <RAGFlowAvatar
          avatar={dataset.avatar}
          name={dataset.name}
          className="size-10 shrink-0 rounded-lg"
        />
        <div className="min-w-0 flex-1">
          <h3
            className="truncate text-[15px] font-semibold leading-snug text-text-primary"
            data-testid="dataset-name"
          >
            {dataset.name}
          </h3>
          <div className="mt-1 flex items-center gap-1.5 text-xs text-text-disabled">
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5',
                isTeam
                  ? 'bg-accent-primary/10 text-accent-primary'
                  : 'bg-bg-card text-text-secondary',
              )}
            >
              {isTeam ? (
                <Users className="size-3" />
              ) : (
                <Lock className="size-3" />
              )}
              {isTeam
                ? t('knowledgeList.permissionTeam')
                : t('knowledgeList.permissionMe')}
            </span>
            {!isOwner && dataset.nickname ? (
              <span className="truncate">
                {t('knowledgeList.ownerPrefix')} · {dataset.nickname}
              </span>
            ) : null}
          </div>
        </div>

        <div onClick={(event) => event.stopPropagation()}>
          <DatasetDropdown
            showDatasetRenameModal={showDatasetRenameModal}
            dataset={dataset}
          >
            <MoreButton />
          </DatasetDropdown>
        </div>
      </header>

      {dataset.description ? (
        <p className="line-clamp-2 min-h-[2.5em] text-[13px] leading-5 text-text-secondary">
          {dataset.description}
        </p>
      ) : (
        <p className="min-h-[2.5em] text-[13px] leading-5 text-text-disabled">
          &nbsp;
        </p>
      )}

      <dl className="grid grid-cols-2 gap-3 rounded-lg border border-border-button/70 bg-bg-base/60 px-3 py-2.5 text-xs text-text-secondary">
        <div className="flex items-center gap-2">
          <Database className="size-3.5 text-accent-primary" />
          <dt className="text-text-disabled">
            {t('knowledgeList.metricDocuments')}
          </dt>
          <dd className="ml-auto font-mono text-text-primary">
            {formatNumber(dataset.document_count)}
          </dd>
        </div>
        <div className="flex items-center gap-2">
          <Layers className="size-3.5 text-accent-primary" />
          <dt className="text-text-disabled">
            {t('knowledgeList.metricChunks')}
          </dt>
          <dd className="ml-auto font-mono text-text-primary">
            {formatNumber(dataset.chunk_count)}
          </dd>
        </div>
      </dl>

      <footer className="mt-auto flex items-center justify-between gap-2 text-xs text-text-disabled">
        <span className="truncate" title={dataset.embedding_model}>
          {dataset.embedding_model || '—'}
        </span>
        <span className="flex items-center gap-1 whitespace-nowrap">
          <span>{t('knowledgeList.updatedAt')}</span>
          <time
            dateTime={
              dataset.update_time
                ? new Date(dataset.update_time).toISOString()
                : undefined
            }
            className="font-mono"
          >
            {formatDate(dataset.update_time)}
          </time>
          <ChevronRight className="size-3 opacity-0 transition group-hover:opacity-100" />
        </span>
      </footer>
    </Card>
  );
}
