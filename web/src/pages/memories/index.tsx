import { CardContainer } from '@/components/card-container';
import { EmptyCardType } from '@/components/empty/constant';
import { EmptyAppCard } from '@/components/empty/empty';
import ListFilterBar from '@/components/list-filter-bar';
import { Button } from '@/components/ui/button';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { useTranslate } from '@/hooks/common-hooks';
import { pick } from 'lodash';
import { Plus } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { AddOrEditModal } from './add-or-edit-modal';
import { defaultMemoryFields } from './constants';
import { useFetchMemoryList, useRenameMemory, useSelectFilters } from './hooks';
import { ICreateMemoryProps, IMemory } from './interface';
import { MemoryCard } from './memory-card';

export default function MemoryList() {
  // const { data } = useFetchFlowList();
  const { t } = useTranslate('memories');
  const [addOrEditType, setAddOrEditType] = useState<'add' | 'edit'>('add');
  // const [isEdit, setIsEdit] = useState(false);
  const {
    data: list,
    pagination,
    searchString,
    handleInputChange,
    setPagination,
    refetch: refetchList,
    filterValue,
    handleFilterSubmit,
  } = useFetchMemoryList();

  const {
    openCreateModal,
    showMemoryRenameModal,
    hideMemoryModal,
    searchRenameLoading,
    onMemoryRenameOk,
    initialMemory,
  } = useRenameMemory();

  const onMemoryConfirm = (data: ICreateMemoryProps) => {
    onMemoryRenameOk(data, () => {
      refetchList();
    });
  };
  const openCreateModalFun = useCallback(() => {
    // setIsEdit(false);
    setAddOrEditType('add');
    showMemoryRenameModal(defaultMemoryFields as unknown as IMemory);
  }, [showMemoryRenameModal]);
  const handlePageChange = useCallback(
    (page: number, pageSize?: number) => {
      setPagination({ page, pageSize });
    },
    [setPagination],
  );

  const [searchUrl, setMemoryUrl] = useSearchParams();
  const { filters } = useSelectFilters();
  const isCreate = searchUrl.get('isCreate') === 'true';
  useEffect(() => {
    if (isCreate) {
      openCreateModalFun();
      searchUrl.delete('isCreate');
      setMemoryUrl(searchUrl);
    }
  }, [isCreate, openCreateModalFun, searchUrl, setMemoryUrl]);

  const memoryList = list?.data?.memory_list ?? [];
  const total = list?.data?.total_count ?? 0;
  const hasListContent = memoryList.length > 0 || !!searchString;

  return (
    <>
      {hasListContent ? (
        <article
          className="flex size-full flex-col bg-bg-base"
          data-testid="memory-list"
        >
          <header className="border-b border-border-button bg-bg-component/60 px-8 pb-5 pt-8">
            <div className="mb-4 min-w-0">
              <h1 className="text-[22px] font-semibold tracking-normal text-text-primary">
                {t('memory')}
              </h1>
              <p className="mt-1 text-sm text-text-secondary">
                {total.toLocaleString()} · {t('memory')}
              </p>
            </div>
            <ListFilterBar
              title={null}
              onSearchChange={handleInputChange}
              searchString={searchString}
              filters={filters}
              onChange={handleFilterSubmit}
              value={filterValue}
              className="gap-3"
            >
              <Button onClick={() => openCreateModalFun()}>
                <Plus className="size-[1em]" />
                {t('createMemory')}
              </Button>
            </ListFilterBar>
          </header>

          {memoryList.length ? (
            <>
              <CardContainer className="flex-1 overflow-auto px-8 py-6">
                {memoryList.map((x) => (
                  <MemoryCard
                    key={x.id}
                    data={x}
                    showMemoryRenameModal={() => {
                      setAddOrEditType('edit');
                      showMemoryRenameModal(x);
                    }}
                  />
                ))}
              </CardContainer>

              <footer className="border-t border-border-button bg-bg-component/40 px-8 py-4">
                <RAGFlowPagination
                  {...pick(pagination, 'current', 'pageSize')}
                  total={total}
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
                type={EmptyCardType.Memory}
                onClick={() => openCreateModalFun()}
              />
            </div>
          )}
        </article>
      ) : (
        <article
          className="flex size-full items-center justify-center bg-bg-base"
          data-testid="memory-list"
        >
          <EmptyAppCard
            showIcon
            size="large"
            className="w-[480px] p-14"
            type={EmptyCardType.Memory}
            onClick={() => openCreateModalFun()}
          />
        </article>
      )}
      {/* {openCreateModal && (
        <RenameDialog
          hideModal={hideMemoryRenameModal}
          onOk={onMemoryRenameConfirm}
          initialName={initialMemoryName}
          loading={searchRenameLoading}
          title={<HomeIcon name="memory" width={'24'} />}
        ></RenameDialog>
      )} */}
      {openCreateModal && (
        <AddOrEditModal
          initialMemory={initialMemory}
          isCreate={addOrEditType === 'add'}
          open={openCreateModal}
          loading={searchRenameLoading}
          onClose={hideMemoryModal}
          onSubmit={onMemoryConfirm}
        />
      )}
    </>
  );
}
