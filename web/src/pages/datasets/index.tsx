import { CardContainer } from '@/components/card-container';
import { EmptyCardType } from '@/components/empty/constant';
import { EmptyAppCard } from '@/components/empty/empty';
import ListFilterBar from '@/components/list-filter-bar';
import { RenameDialog } from '@/components/rename-dialog';
import { Button } from '@/components/ui/button';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { useFetchNextKnowledgeListByPage } from '@/hooks/use-knowledge-request';
import { useQueryClient } from '@tanstack/react-query';
import { pick } from 'lodash';
import { Database, Layers, Plus, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { DatasetCard } from './dataset-card';
import { DatasetCreatingDialog } from './dataset-creating-dialog';
import { useSaveKnowledge } from './hooks';
import { useRenameDataset } from './use-rename-dataset';
import { useSelectOwners } from './use-select-owners';

export default function Datasets() {
  const { t } = useTranslation();
  const {
    visible,
    hideModal,
    showModal,
    onCreateOk,
    loading: creatingLoading,
  } = useSaveKnowledge();

  const {
    kbs,
    total_datasets,
    pagination,
    setPagination,
    handleInputChange,
    searchString,
    filterValue,
    handleFilterSubmit,
  } = useFetchNextKnowledgeListByPage();

  const owners = useSelectOwners();

  const {
    datasetRenameLoading,
    initialDatasetName,
    onDatasetRenameOk,
    datasetRenameVisible,
    hideDatasetRenameModal,
    showDatasetRenameModal,
  } = useRenameDataset();

  const handlePageChange = useCallback(
    (page: number, pageSize?: number) => {
      setPagination({ page, pageSize });
    },
    [setPagination],
  );
  const [searchUrl, setSearchUrl] = useSearchParams();
  const isCreate = searchUrl.get('isCreate') === 'true';
  const queryClient = useQueryClient();
  useEffect(() => {
    if (isCreate) {
      queryClient.invalidateQueries({ queryKey: ['tenantInfo'] });
      showModal();
      searchUrl.delete('isCreate');
      setSearchUrl(searchUrl);
    }
  }, [isCreate, showModal, searchUrl, setSearchUrl, queryClient]);

  const summary = useMemo(() => {
    const documents = kbs.reduce(
      (sum, item) => sum + (item.document_count ?? 0),
      0,
    );
    const chunks = kbs.reduce((sum, item) => sum + (item.chunk_count ?? 0), 0);
    return [
      {
        key: 'datasets',
        label: t('knowledgeList.welcome'),
        value: total_datasets || kbs.length || 0,
        icon: Sparkles,
      },
      {
        key: 'documents',
        label: t('knowledgeList.metricDocuments'),
        value: documents.toLocaleString(),
        icon: Database,
      },
      {
        key: 'chunks',
        label: t('knowledgeList.metricChunks'),
        value: chunks.toLocaleString(),
        icon: Layers,
      },
    ];
  }, [kbs, total_datasets, t]);

  const hasListContent = !!kbs?.length || !!searchString;

  return (
    <>
      {hasListContent ? (
        <article
          className="flex size-full flex-col bg-bg-base"
          data-testid="datasets-list"
        >
          <header className="border-b border-border-button bg-bg-component/60 px-8 pb-5 pt-8">
            <div className="mb-4 flex items-baseline justify-between gap-4">
              <div className="min-w-0">
                <h1 className="text-[22px] font-semibold tracking-normal text-text-primary">
                  {t('header.dataset')}
                </h1>
                <p className="mt-1 text-sm text-text-secondary">
                  {t('knowledgeList.listSubtitle', {
                    count: total_datasets || kbs.length,
                  })}
                </p>
              </div>
              <dl className="hidden shrink-0 items-center gap-3 md:flex">
                {summary.map(({ key, label, value, icon: Icon }) => (
                  <div
                    key={key}
                    className="flex items-center gap-2 rounded-lg border border-border-button bg-bg-component px-3 py-2"
                  >
                    <Icon className="size-3.5 text-accent-primary" />
                    <dt className="text-xs text-text-secondary">{label}</dt>
                    <dd className="font-mono text-sm font-medium text-text-primary">
                      {value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <ListFilterBar
              title={null}
              searchString={searchString}
              onSearchChange={handleInputChange}
              value={filterValue}
              filters={owners}
              onChange={handleFilterSubmit}
              className="gap-3"
            >
              <Button onClick={showModal} data-testid="datasets-create">
                <Plus className="size-[1em]" />
                {t('knowledgeList.createKnowledgeBase')}
              </Button>
            </ListFilterBar>
          </header>

          {kbs?.length ? (
            <>
              <CardContainer className="flex-1 overflow-auto px-8 py-6">
                {kbs.map((dataset) => (
                  <DatasetCard
                    dataset={dataset}
                    key={dataset.id}
                    showDatasetRenameModal={showDatasetRenameModal}
                  />
                ))}
              </CardContainer>

              <footer className="border-t border-border-button bg-bg-component/40 px-8 py-4">
                <RAGFlowPagination
                  {...pick(pagination, 'current', 'pageSize')}
                  total={total_datasets}
                  onChange={handlePageChange}
                />
              </footer>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center">
              <EmptyAppCard
                showIcon
                size="large"
                className="w-[480px] p-14"
                isSearch
                type={EmptyCardType.Dataset}
                onClick={() => showModal()}
              />
            </div>
          )}
        </article>
      ) : (
        <article
          className="flex size-full items-center justify-center bg-bg-base"
          data-testid="datasets-list"
        >
          <EmptyAppCard
            showIcon
            size="large"
            className="w-[480px] p-14"
            type={EmptyCardType.Dataset}
            onClick={() => showModal()}
          />
        </article>
      )}
      {visible && (
        <DatasetCreatingDialog
          hideModal={hideModal}
          onOk={onCreateOk}
          loading={creatingLoading}
        />
      )}
      {datasetRenameVisible && (
        <RenameDialog
          hideModal={hideDatasetRenameModal}
          onOk={onDatasetRenameOk}
          initialName={initialDatasetName}
          loading={datasetRenameLoading}
        />
      )}
    </>
  );
}
