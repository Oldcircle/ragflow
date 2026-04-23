import { isEmpty } from 'lodash';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import {
  ArrowLeft,
  LucideFolderOpen,
  LucideLogs,
  LucideSettings,
  LucideTextSearch,
  LucideUsers,
} from 'lucide-react';

import { IconFontFill } from '@/components/icon-font';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { useSecondPathName } from '@/hooks/route-hook';
import { useFetchKnowledgeGraph } from '@/hooks/use-knowledge-request';
import { IKnowledge } from '@/interfaces/database/knowledge';
import { cn, formatBytes } from '@/lib/utils';
import { Routes } from '@/routes';
import { formatPureDate } from '@/utils/date';
import { Link, useParams } from 'react-router';

type PropType = {
  refreshCount?: number;
  dataset: IKnowledge;
};

export function SideBar({ dataset: data }: PropType) {
  const pathName = useSecondPathName();
  const { id } = useParams();
  const { data: routerData } = useFetchKnowledgeGraph();
  const { t } = useTranslation();

  const items = useMemo(() => {
    const list: {
      icon: React.ReactNode;
      label: string;
      key: string;
      testId?: string;
    }[] = [
      {
        icon: <LucideFolderOpen className="size-4" />,
        label: t(`knowledgeDetails.subbarFiles`),
        key: Routes.DatasetBase,
        testId: 'dataset-nav-files',
      },
      {
        icon: <LucideTextSearch className="size-4" />,
        label: t(`knowledgeDetails.testing`),
        key: Routes.DatasetTesting,
        testId: 'dataset-nav-testing',
      },
      {
        icon: <LucideLogs className="size-4" />,
        label: t(`knowledgeDetails.overview`),
        key: Routes.DataSetOverview,
        testId: 'dataset-nav-overview',
      },
      {
        icon: <LucideSettings className="size-4" />,
        label: t(`knowledgeDetails.configuration`),
        key: Routes.DataSetSetting,
        testId: 'dataset-nav-setting',
      },
      {
        icon: <LucideUsers className="size-4" />,
        label: t(`knowledgeDetails.members`),
        key: Routes.DataSetMember,
        testId: 'dataset-nav-members',
      },
    ];

    if (!isEmpty(routerData?.graph)) {
      list.push({
        icon: <IconFontFill name="knowledgegraph" className="size-4" />,
        label: t(`knowledgeDetails.knowledgeGraph`),
        key: Routes.KnowledgeGraph,
        testId: 'dataset-nav-graph',
      });
    }

    return list;
  }, [t, routerData]);

  const metrics = [
    {
      label: t('knowledgeList.metricDocuments'),
      value:
        typeof data.doc_num === 'number' ? data.doc_num.toLocaleString() : '—',
    },
    {
      label: t('knowledgeList.metricChunks'),
      value:
        typeof data.chunk_num === 'number'
          ? data.chunk_num.toLocaleString()
          : '—',
    },
    {
      label: t('knowledgeList.totalSize'),
      value: data.size ? formatBytes(data.size) : '—',
    },
  ];

  return (
    <aside
      className="flex w-64 shrink-0 flex-col gap-4 overflow-y-auto border-r border-border-button bg-bg-component/60 px-4 py-5"
      data-testid="dataset-sidebar"
    >
      <Link
        to={Routes.Datasets}
        className="inline-flex items-center gap-1.5 text-xs text-text-secondary transition hover:text-text-primary"
        data-testid="dataset-back"
      >
        <ArrowLeft className="size-3" />
        {t('knowledgeList.backToDatasets')}
      </Link>

      <section className="flex items-start gap-3">
        <RAGFlowAvatar
          avatar={data.avatar}
          name={data.name}
          className="size-11 shrink-0 rounded-lg"
        />
        <div className="min-w-0 flex-1">
          <h2
            className="truncate text-[15px] font-semibold leading-tight text-text-primary"
            title={data.name}
          >
            {data.name || '—'}
          </h2>
          <p className="mt-1 text-[11px] text-text-disabled">
            {t('knowledgeDetails.created')}{' '}
            {data.create_time ? formatPureDate(data.create_time) : '—'}
          </p>
        </div>
      </section>

      {data.description ? (
        <p
          className="line-clamp-3 text-[12px] leading-5 text-text-secondary"
          title={data.description}
        >
          {data.description}
        </p>
      ) : null}

      <dl
        className="rounded-lg border border-border-button bg-bg-base/60 p-3 text-[11px]"
        aria-label={t('knowledgeList.detailSummary')}
      >
        <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-text-disabled">
          {t('knowledgeList.detailSummary')}
        </div>
        <div className="grid grid-cols-3 gap-2">
          {metrics.map(({ label, value }) => (
            <div key={label}>
              <dt className="text-text-disabled">{label}</dt>
              <dd className="mt-0.5 font-mono text-sm font-medium text-text-primary">
                {value}
              </dd>
            </div>
          ))}
        </div>
        {data.embedding_model ? (
          <div
            className="mt-3 truncate text-text-secondary"
            title={data.embedding_model}
          >
            <span className="text-text-disabled">
              {t('knowledgeList.embeddingModel')}:
            </span>{' '}
            {data.embedding_model}
          </div>
        ) : null}
      </dl>

      <nav aria-label={t('knowledgeList.detailNavigate')}>
        <div className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-text-disabled">
          {t('knowledgeList.detailNavigate')}
        </div>
        <ul className="space-y-1">
          {items.map((item) => {
            const active = '/' + pathName === item.key;

            return (
              <li key={item.key}>
                <Link
                  to={`${Routes.DatasetBase}${item.key}/${id}`}
                  aria-current={active ? 'page' : undefined}
                  data-testid={item.testId}
                  className={cn(
                    'group flex h-9 items-center gap-3 rounded-lg px-3 text-sm transition',
                    'text-text-secondary hover:bg-bg-card hover:text-text-primary focus-visible:bg-bg-card focus-visible:text-text-primary',
                    active &&
                      'bg-accent-primary/10 text-text-primary shadow-[inset_2px_0_0_rgb(var(--accent-primary))]',
                  )}
                >
                  <span
                    className={cn(
                      'flex text-text-secondary transition group-hover:text-text-primary',
                      active && 'text-accent-primary',
                    )}
                  >
                    {item.icon}
                  </span>
                  <span className="truncate">{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
