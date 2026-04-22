import { CardContainer } from '@/components/card-container';
import { EmptyCardType } from '@/components/empty/constant';
import { EmptyAppCard } from '@/components/empty/empty';
import ListFilterBar from '@/components/list-filter-bar';
import { RenameDialog } from '@/components/rename-dialog';
import { Button } from '@/components/ui/button';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { useTranslate } from '@/hooks/common-hooks';
import { pick } from 'lodash';
import { Plus } from 'lucide-react';
import { useCallback, useEffect } from 'react';
import { useSearchParams } from 'react-router';
import { useFetchSearchList, useRenameSearch } from './hooks';
import { SearchCard } from './search-card';

export default function SearchList() {
  // const { data } = useFetchFlowList();
  const { t } = useTranslate('search');
  // const [isEdit, setIsEdit] = useState(false);
  const {
    data: list,
    pagination,
    searchString,
    handleInputChange,
    setPagination,
    refetch: refetchList,
  } = useFetchSearchList();

  const {
    openCreateModal,
    showSearchRenameModal,
    hideSearchRenameModal,
    searchRenameLoading,
    onSearchRenameOk,
    initialSearchName,
  } = useRenameSearch();

  // const handleSearchChange = (value: string) => {
  //   console.log(value);
  // };
  const onSearchRenameConfirm = (name: string) => {
    onSearchRenameOk(name, () => {
      refetchList();
    });
  };
  const openCreateModalFun = useCallback(() => {
    // setIsEdit(false);
    showSearchRenameModal();
  }, [showSearchRenameModal]);
  const handlePageChange = useCallback(
    (page: number, pageSize?: number) => {
      setPagination({ page, pageSize });
    },
    [setPagination],
  );

  const [searchUrl, setSearchUrl] = useSearchParams();
  const isCreate = searchUrl.get('isCreate') === 'true';
  useEffect(() => {
    if (isCreate) {
      openCreateModalFun();
      searchUrl.delete('isCreate');
      setSearchUrl(searchUrl);
    }
  }, [isCreate, openCreateModalFun, searchUrl, setSearchUrl]);

  const total = list?.data?.total ?? 0;
  const apps = list?.data?.search_apps ?? [];
  const hasListContent = apps.length > 0 || !!searchString;

  return (
    <>
      {hasListContent ? (
        <article
          className="flex size-full flex-col bg-bg-base"
          data-testid="search-list"
        >
          <header className="border-b border-border-button bg-bg-component/60 px-8 pb-5 pt-8">
            <div className="mb-4 min-w-0">
              <h1 className="text-[22px] font-semibold tracking-normal text-text-primary">
                {t('searchApps')}
              </h1>
              <p className="mt-1 text-sm text-text-secondary">
                {total.toLocaleString()} · {t('searchApps')}
              </p>
            </div>
            <ListFilterBar
              title={null}
              showFilter={false}
              searchString={searchString}
              onSearchChange={handleInputChange}
              className="gap-3"
            >
              <Button
                data-testid="create-search"
                onClick={() => openCreateModalFun()}
              >
                <Plus className="size-[1em]" />
                {t('createSearch')}
              </Button>
            </ListFilterBar>
          </header>

          {apps.length ? (
            <>
              <CardContainer className="flex-1 overflow-auto px-8 py-6">
                {apps.map((x) => (
                  <SearchCard
                    key={x.id}
                    data={x}
                    showSearchRenameModal={() => {
                      showSearchRenameModal(x);
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
                type={EmptyCardType.Search}
                testId="search-empty-create"
              />
            </div>
          )}
        </article>
      ) : (
        <article
          className="flex size-full items-center justify-center bg-bg-base"
          data-testid="search-list"
        >
          <EmptyAppCard
            showIcon
            size="large"
            className="w-[480px] p-14"
            type={EmptyCardType.Search}
            onClick={() => openCreateModalFun()}
            testId="search-empty-create"
          />
        </article>
      )}
      {openCreateModal && (
        <RenameDialog
          hideModal={hideSearchRenameModal}
          onOk={onSearchRenameConfirm}
          initialName={initialSearchName}
          loading={searchRenameLoading}
          title={initialSearchName || t('createSearch')}
        ></RenameDialog>
      )}
    </>
  );
}
