import { EmptyType } from '@/components/empty/constant';
import Empty from '@/components/empty/empty';
import HighLightMarkdown from '@/components/highlight-markdown';
import { FilterButton } from '@/components/list-filter-bar';
import { FilterPopover } from '@/components/list-filter-bar/filter-popover';
import { FilterCollection } from '@/components/list-filter-bar/interface';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { useTestRetrieval } from '@/hooks/use-knowledge-request';
import { ITestingChunk } from '@/interfaces/database/knowledge';
import { cn } from '@/lib/utils';
import { FileText } from 'lucide-react';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

type SimilarityKey = 'similarity' | 'term_similarity' | 'vector_similarity';

const similarityList: Array<{ field: SimilarityKey; tone: string }> = [
  { field: 'similarity', tone: 'bg-accent-primary/10 text-accent-primary' },
  { field: 'term_similarity', tone: 'bg-bg-card text-text-secondary' },
  { field: 'vector_similarity', tone: 'bg-bg-card text-text-secondary' },
];

const similarityLabelKey: Record<SimilarityKey, string> = {
  similarity: 'knowledgeDetails.hybridSimilarity',
  term_similarity: 'knowledgeDetails.termSimilarity',
  vector_similarity: 'knowledgeDetails.vectorSimilarity',
};

function SimilarityChips({ item }: { item: ITestingChunk }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {similarityList.map(({ field, tone }) => {
        const raw = item[field] as number | undefined;
        const value = typeof raw === 'number' ? (raw * 100).toFixed(1) : '—';
        return (
          <span
            key={field}
            className={cn(
              'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium',
              tone,
            )}
            title={t(similarityLabelKey[field])}
          >
            <span className="opacity-70">{t(similarityLabelKey[field])}</span>
            <span className="font-mono">{value}</span>
          </span>
        );
      })}
    </div>
  );
}

type TestingResultProps = Pick<
  ReturnType<typeof useTestRetrieval>,
  | 'data'
  | 'filterValue'
  | 'handleFilterSubmit'
  | 'page'
  | 'pageSize'
  | 'onPaginationChange'
  | 'loading'
>;

export function TestingResult({
  filterValue,
  handleFilterSubmit,
  page,
  pageSize,
  loading,
  onPaginationChange,
  data,
}: TestingResultProps) {
  const { t } = useTranslation();

  const filters: FilterCollection[] = useMemo(() => {
    return [
      {
        field: 'doc_ids',
        label: t('knowledgeDetails.fileLogs'),
        list:
          data.doc_aggs?.map((x) => ({
            id: x.doc_id,
            label: x.doc_name,
            count: x.count,
          })) ?? [],
      },
    ];
  }, [data.doc_aggs, t]);

  const hasResults = (data.chunks?.length ?? 0) > 0;
  const totalCount = data.total ?? 0;

  return (
    <article className="flex size-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border-button px-6 py-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold text-text-primary">
            {t('knowledgeDetails.testResults')}
          </h2>
          {hasResults && (
            <span className="font-mono text-xs text-text-disabled">
              {totalCount.toLocaleString()}
            </span>
          )}
        </div>
        <FilterPopover
          filters={filters}
          onChange={handleFilterSubmit}
          value={filterValue}
        >
          <FilterButton />
        </FilterPopover>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        {hasResults && !loading && (
          <section className="flex flex-col gap-3 px-6 py-5">
            {data.chunks?.map((x, idx) => (
              <article
                key={x.chunk_id}
                className="rounded-lg border border-border-button bg-bg-component/60 p-4"
                data-testid="testing-chunk"
                data-doc-name={x.docnm_kwd}
              >
                <header className="mb-2 flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2 text-xs text-text-secondary">
                    <FileText className="size-3.5 shrink-0 text-accent-primary" />
                    <span
                      className="truncate font-medium text-text-primary"
                      title={x.docnm_kwd}
                    >
                      {x.docnm_kwd || x.doc_name || '—'}
                    </span>
                    <span className="shrink-0 text-text-disabled">
                      #{(page - 1) * pageSize + idx + 1}
                    </span>
                  </div>
                  <SimilarityChips item={x} />
                </header>

                <div className="text-[13px] leading-6 text-text-primary">
                  <HighLightMarkdown>
                    {x.highlight || x.content_with_weight}
                  </HighLightMarkdown>
                </div>
              </article>
            ))}
          </section>
        )}

        {!hasResults && !loading && (
          <div className="flex size-full items-center justify-center p-8">
            <Empty type={EmptyType.SearchData} iconWidth={80}>
              <div className="text-sm text-text-secondary">
                {t(
                  data.isRuned
                    ? 'knowledgeDetails.noTestResultsForRuned'
                    : 'knowledgeDetails.noTestResultsForNotRuned',
                )}
              </div>
            </Empty>
          </div>
        )}
      </div>

      {hasResults && !loading && (
        <footer className="flex items-center justify-end border-t border-border-button bg-bg-component/40 px-6 py-3">
          <RAGFlowPagination
            total={totalCount}
            onChange={onPaginationChange}
            current={page}
            pageSize={pageSize}
          />
        </footer>
      )}
    </article>
  );
}
