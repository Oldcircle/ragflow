import { isEmpty } from 'lodash';

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import {
  Database,
  FileText,
  LucideFolderOpen,
  LucideLogs,
  LucideSettings,
  LucideTextSearch,
  Scale,
} from 'lucide-react';

import { IconFontFill } from '@/components/icon-font';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Button } from '@/components/ui/button';
import { useSecondPathName } from '@/hooks/route-hook';
import { useFetchKnowledgeGraph } from '@/hooks/use-knowledge-request';
import { cn, formatBytes } from '@/lib/utils';
import { Routes } from '@/routes';
import { formatPureDate } from '@/utils/date';

import { IKnowledge } from '@/interfaces/database/knowledge';
import { useParams } from 'react-router';

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
    const list = [
      {
        icon: <LucideFolderOpen className="size-[1em]" />,
        label: t(`knowledgeDetails.subbarFiles`),
        key: Routes.DatasetBase,
      },
      {
        icon: <LucideTextSearch className="size-[1em]" />,
        label: t(`knowledgeDetails.testing`),
        key: Routes.DatasetTesting,
      },
      {
        icon: <LucideLogs className="size-[1em]" />,
        label: t(`knowledgeDetails.overview`),
        key: Routes.DataSetOverview,
      },
      {
        icon: <LucideSettings className="size-[1em]" />,
        label: t(`knowledgeDetails.configuration`),
        key: Routes.DataSetSetting,
      },
    ];

    if (!isEmpty(routerData?.graph)) {
      list.push({
        icon: <IconFontFill name="knowledgegraph" className="size-[1em]" />,
        label: t(`knowledgeDetails.knowledgeGraph`),
        key: Routes.KnowledgeGraph,
      });
    }

    return list;
  }, [t, routerData]);

  return (
    <aside className="dataset-sidebar">
      <header className="dataset-sidebar-header">
        <div className="mb-4 flex items-center gap-2 text-[11px] font-medium uppercase text-text-disabled">
          <Database className="size-3.5 text-accent-primary" />
          {t('knowledgeDetails.assetEyebrow')}
        </div>

        <div className="flex items-start gap-3">
          <RAGFlowAvatar
            avatar={data.avatar}
            name={data.name}
            className="size-12 rounded-lg"
          />

          <div className="min-w-0 flex-1">
            <h3 className="line-clamp-2 text-base font-semibold leading-5 text-text-primary">
              {data.name}
            </h3>

            <div className="mt-2 text-xs text-text-secondary">
              {t('knowledgeDetails.created')} {formatPureDate(data.create_time)}
            </div>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-2">
          <div className="dataset-sidebar-stat">
            <FileText className="size-3.5 text-accent-primary" />
            <span>{data.doc_num}</span>
            <small>{t('knowledgeList.doc')}</small>
          </div>
          <div className="dataset-sidebar-stat">
            <Scale className="size-3.5 text-accent-primary" />
            <span>{formatBytes(data.size)}</span>
            <small>{t('knowledgeDetails.fileSize')}</small>
          </div>
        </div>
      </header>

      <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1.5">
          {items.map((item) => {
            const active = '/' + pathName === item.key;

            return (
              <li key={item.key}>
                <Button
                  asLink
                  block
                  variant="ghost"
                  className={cn(
                    'dataset-sidebar-link justify-start gap-2.5',
                    active && 'dataset-sidebar-link-active',
                  )}
                  to={`${Routes.DatasetBase}${item.key}/${id}`}
                >
                  {item.icon}
                  <span>{item.label}</span>
                </Button>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
