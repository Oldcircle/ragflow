import { useTestRetrieval } from '@/hooks/use-knowledge-request';
import { useTranslation } from 'react-i18next';
import TestingForm from './testing-form';
import { TestingResult } from './testing-result';

export default function RetrievalTesting() {
  const { t } = useTranslation();
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

  return (
    <article
      className="flex min-h-0 flex-1 flex-col"
      data-testid="dataset-testing"
    >
      <header className="border-b border-border-button bg-bg-component/60 px-6 pb-5 pt-6">
        <h1 className="text-[22px] font-semibold leading-tight tracking-normal text-text-primary">
          {t('knowledgeDetails.retrievalTesting')}
        </h1>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">
          {t('knowledgeDetails.testingDescription')}
        </p>
      </header>

      <section className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[420px_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col border-b border-border-button bg-bg-component/40 lg:border-b-0 lg:border-r">
          <header className="border-b border-border-button px-6 py-3">
            <h2 className="text-sm font-semibold text-text-primary">
              {t('knowledgeDetails.testSetting')}
            </h2>
          </header>

          <div className="min-h-0 flex-1 overflow-auto">
            <TestingForm
              loading={loading}
              setValues={setValues}
              refetch={refetch}
            />
          </div>
        </aside>

        <section
          className="flex min-h-0 flex-col"
          data-testid="dataset-testing-results"
        >
          <TestingResult
            data={data}
            page={page}
            loading={loading}
            pageSize={pageSize}
            filterValue={filterValue}
            handleFilterSubmit={handleFilterSubmit}
            onPaginationChange={onPaginationChange}
          />
        </section>
      </section>
    </article>
  );
}
