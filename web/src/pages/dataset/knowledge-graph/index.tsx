import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { Button } from '@/components/ui/button';
import { useFetchKnowledgeGraph } from '@/hooks/use-knowledge-request';
import { LucideTrash2 } from 'lucide-react';
import React from 'react';
import { useTranslation } from 'react-i18next';
import ForceGraph from './force-graph';
import { useDeleteKnowledgeGraph } from './use-delete-graph';

const KnowledgeGraph: React.FC = () => {
  const { data } = useFetchKnowledgeGraph();
  const { t } = useTranslation();
  const { handleDeleteKnowledgeGraph } = useDeleteKnowledgeGraph();

  return (
    <article
      className="flex min-h-0 flex-1 flex-col"
      data-testid="dataset-knowledge-graph"
    >
      <header className="flex items-center justify-between gap-4 border-b border-border-button bg-bg-component/60 px-6 pb-4 pt-6">
        <div>
          <h1 className="text-[22px] font-semibold leading-tight tracking-normal text-text-primary">
            {t('knowledgeDetails.knowledgeGraph')}
          </h1>
        </div>
        <ConfirmDeleteDialog onOk={handleDeleteKnowledgeGraph}>
          <Button
            variant="outline"
            size="sm"
            data-testid="dataset-knowledge-graph-delete"
          >
            <LucideTrash2 />
            {t('common.delete')}
          </Button>
        </ConfirmDeleteDialog>
      </header>

      <section className="relative min-h-0 flex-1 overflow-hidden">
        <ForceGraph data={data?.graph} show />
      </section>
    </article>
  );
};

export default KnowledgeGraph;
