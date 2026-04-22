import { useTestRetrieval } from '@/hooks/use-knowledge-request';
import { t } from 'i18next';
import { SearchCheck } from 'lucide-react';
import { useState } from 'react';
import TestingForm from './testing-form';
import { TestingResult } from './testing-result';

export default function RetrievalTesting() {
  const {
    loading,
    setValues,
    refetch,
    data,
    onPaginationChange,
    page,
    pageSize,
    handleFilterSubmit,
    filterValue,
  } = useTestRetrieval();

  const [count] = useState(1);

  return (
    <article className="dataset-testing-panel">
      <header className="dataset-panel-header border-b border-border-button">
        <div className="mb-1 flex items-center gap-2 text-xs font-medium text-text-disabled">
          <SearchCheck className="size-3.5 text-accent-primary" />
          {t('knowledgeDetails.retrievalLab')}
        </div>
        <h1 className="text-xl font-semibold leading-normal text-text-primary">
          {t('knowledgeDetails.retrievalTesting')}
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          {t('knowledgeDetails.testingDescription')}
        </p>
      </header>

      {count === 1 ? (
        <div className="grid min-h-0 flex-1 grid-cols-2 divide-x divide-border-button overflow-hidden">
          <article className="flex size-full flex-1 flex-col">
            <header className="px-5 py-3">
              <h2 className="font-semibold text-base leading-8">
                {t('knowledgeDetails.testSetting')}
              </h2>
            </header>

            <div className="flex-1 h-0">
              <TestingForm
                loading={loading}
                setValues={setValues}
                refetch={refetch}
              />
            </div>
          </article>

          <div className="min-w-0 flex-1">
            <TestingResult
              data={data}
              page={page}
              loading={loading}
              pageSize={pageSize}
              filterValue={filterValue}
              handleFilterSubmit={handleFilterSubmit}
              onPaginationChange={onPaginationChange}
            />
          </div>
        </div>
      ) : (
        <div className="flex gap-2 p-0">
          <div className="flex-1">
            <TestingForm
              loading={loading}
              setValues={setValues}
              refetch={refetch}
            ></TestingForm>
            <TestingResult
              data={data}
              page={page}
              loading={loading}
              pageSize={pageSize}
              filterValue={filterValue}
              handleFilterSubmit={handleFilterSubmit}
              onPaginationChange={onPaginationChange}
            ></TestingResult>
          </div>
          <div className="flex-1">
            <TestingForm
              loading={loading}
              setValues={setValues}
              refetch={refetch}
            ></TestingForm>
            <TestingResult
              data={data}
              page={page}
              loading={loading}
              pageSize={pageSize}
              filterValue={filterValue}
              handleFilterSubmit={handleFilterSubmit}
              onPaginationChange={onPaginationChange}
            ></TestingResult>
          </div>
        </div>
      )}
    </article>
  );
}
