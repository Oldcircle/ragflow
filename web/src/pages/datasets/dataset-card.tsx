import { MoreButton } from '@/components/more-button';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { SharedBadge } from '@/components/shared-badge';
import { Card, CardContent } from '@/components/ui/card';
import { useNavigatePage } from '@/hooks/logic-hooks/navigate-hooks';
import { IDataset } from '@/interfaces/database/dataset';
import { formatDate } from '@/utils/date';
import { t } from 'i18next';
import { ChevronRight, Database, FileText, Layers3 } from 'lucide-react';
import { KeyboardEvent, MouseEvent } from 'react';
import { DatasetDropdown } from './dataset-dropdown';
import { useRenameDataset } from './use-rename-dataset';

export type DatasetCardProps = {
  dataset: IDataset;
} & Pick<ReturnType<typeof useRenameDataset>, 'showDatasetRenameModal'>;

export function DatasetCard({
  dataset,
  showDatasetRenameModal,
}: DatasetCardProps) {
  const { navigateToDataset } = useNavigatePage();
  const openDataset = navigateToDataset(dataset.id);
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openDataset();
    }
  };
  const stopPropagation = (event: MouseEvent) => {
    event.stopPropagation();
  };

  return (
    <article
      className="knowledge-card group"
      data-testid="dataset-card"
      data-dataset-name={dataset.name}
      role="button"
      tabIndex={0}
      onClick={openDataset}
      onKeyDown={handleKeyDown}
    >
      <header className="flex items-start gap-3">
        <RAGFlowAvatar
          className="size-10 rounded-lg"
          avatar={dataset.avatar}
          name={dataset.name}
        />

        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-semibold leading-6 text-text-primary">
            {dataset.name}
          </h2>
          <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-text-secondary">
            <Database className="size-3.5 text-accent-primary" />
            <span className="truncate">{dataset.embedding_model}</span>
          </div>
        </div>

        <div onClick={stopPropagation}>
          <DatasetDropdown
            showDatasetRenameModal={showDatasetRenameModal}
            dataset={dataset}
          >
            <MoreButton></MoreButton>
          </DatasetDropdown>
        </div>
      </header>

      <p className="mt-4 line-clamp-2 min-h-10 text-sm leading-5 text-text-secondary">
        {dataset.description || t('knowledgeList.cardDescription')}
      </p>

      <section className="mt-5 grid grid-cols-3 gap-2">
        {[
          {
            icon: FileText,
            label: t('knowledgeList.doc'),
            value: dataset.document_count,
          },
          {
            icon: Layers3,
            label: t('knowledgeDetails.chunkNumber'),
            value: dataset.chunk_count,
          },
          {
            icon: Database,
            label: t('knowledgeDetails.chunkMethod'),
            value: dataset.chunk_method,
          },
        ].map(({ icon: Icon, label, value }) => (
          <div className="knowledge-card-metric" key={label}>
            <div className="mb-1 flex items-center gap-1 text-[11px] text-text-disabled">
              <Icon className="size-3" />
              <span className="truncate">{label}</span>
            </div>
            <div className="truncate text-sm font-semibold text-text-primary">
              {value}
            </div>
          </div>
        ))}
      </section>

      <footer className="mt-5 flex items-center justify-between gap-3 border-t border-border-button pt-3 text-xs text-text-secondary">
        <span className="truncate">{formatDate(dataset.update_time)}</span>
        <SharedBadge>{dataset.nickname}</SharedBadge>
      </footer>
    </article>
  );
}

export function SeeAllCard() {
  const { navigateToDatasetList } = useNavigatePage();

  return (
    <Card
      className="w-full flex-none h-full cursor-pointer"
      onClick={() => navigateToDatasetList({ isCreate: false })}
    >
      <CardContent className="p-2.5 pt-1 w-full h-full flex items-center justify-center gap-1.5 text-text-secondary">
        {t('common.seeAll')} <ChevronRight className="size-4" />
      </CardContent>
    </Card>
  );
}
