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
import { Database, FileText, Layers3, Plus, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { DatasetCard } from './dataset-card';
import { DatasetCreatingDialog } from './dataset-creating-dialog';
import { useSaveKnowledge } from './hooks';
import './styles.css';
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
  const totalDocuments =
    kbs?.reduce((count, dataset) => count + (dataset.document_count || 0), 0) ??
    0;
  const totalChunks =
    kbs?.reduce((count, dataset) => count + (dataset.chunk_count || 0), 0) ?? 0;

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

  return (
    <>
      <article className="knowledge-list-root" data-testid="datasets-list">
        <header className="knowledge-list-header">
          <section className="knowledge-list-hero">
            <div className="min-w-0">
              <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-border-button bg-bg-component px-3 py-1 text-xs font-medium text-text-secondary">
                <ShieldCheck className="size-3.5 text-accent-primary" />
                {t('knowledgeList.assetEyebrow')}
              </div>
              <h1 className="text-[32px] font-semibold leading-tight tracking-normal text-text-primary">
                {t('header.dataset')}
              </h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">
                {t('knowledgeList.assetDescription')}
              </p>
            </div>

            <div className="knowledge-list-stats" aria-label="knowledge stats">
              {[
                {
                  icon: Database,
                  label: t('header.dataset'),
                  value: total_datasets ?? 0,
                },
                {
                  icon: FileText,
                  label: t('knowledgeList.doc'),
                  value: totalDocuments,
                },
                {
                  icon: Layers3,
                  label: t('knowledgeDetails.chunkNumber'),
                  value: totalChunks,
                },
              ].map(({ icon: Icon, label, value }) => (
                <div className="knowledge-list-stat" key={label}>
                  <div className="flex items-center justify-between text-text-secondary">
                    <span className="text-xs">{label}</span>
                    <Icon className="size-4 text-accent-primary" />
                  </div>
                  <div className="mt-2 text-2xl font-semibold text-text-primary">
                    {value}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {(kbs?.length || searchString) && (
            <ListFilterBar
              className="knowledge-list-toolbar"
              searchString={searchString}
              onSearchChange={handleInputChange}
              value={filterValue}
              filters={owners}
              onChange={handleFilterSubmit}
              icon={'datasets'}
              leftPanel={
                <span className="text-sm font-semibold text-text-primary">
                  {t('knowledgeList.assetEyebrow')}
                </span>
              }
            >
              <Button onClick={showModal}>
                <Plus className="size-[1em]" />
                {t('knowledgeList.createKnowledgeBase')}
              </Button>
            </ListFilterBar>
          )}
        </header>

        {kbs?.length ? (
          <>
            <CardContainer className="knowledge-list-grid">
              {kbs.map((dataset) => (
                <DatasetCard
                  dataset={dataset}
                  key={dataset.id}
                  showDatasetRenameModal={showDatasetRenameModal}
                />
              ))}
            </CardContainer>

            <footer className="knowledge-list-footer">
              <RAGFlowPagination
                {...pick(pagination, 'current', 'pageSize')}
                total={total_datasets}
                onChange={handlePageChange}
              />
            </footer>
          </>
        ) : (
          <div className="knowledge-list-empty">
            <EmptyAppCard
              showIcon
              size="large"
              className="w-[480px] p-14"
              isSearch={!!searchString}
              type={EmptyCardType.Dataset}
              onClick={() => showModal()}
            />
            {!searchString && (
              <Button onClick={showModal}>
                <Plus className="size-[1em]" />
                {t('knowledgeList.createKnowledgeBase')}
              </Button>
            )}
          </div>
        )}
      </article>
      {visible && (
        <DatasetCreatingDialog
          hideModal={hideModal}
          onOk={onCreateOk}
          loading={creatingLoading}
        ></DatasetCreatingDialog>
      )}
      {datasetRenameVisible && (
        <RenameDialog
          hideModal={hideDatasetRenameModal}
          onOk={onDatasetRenameOk}
          initialName={initialDatasetName}
          loading={datasetRenameLoading}
        ></RenameDialog>
      )}
    </>
  );
}
