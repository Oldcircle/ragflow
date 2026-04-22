import { useFetchKnowledgeBaseConfiguration } from '@/hooks/use-knowledge-request';
import { KnowledgeBaseProvider } from '@/pages/dataset/contexts/knowledge-base-context';

import { Outlet } from 'react-router';
import { SideBar } from './sidebar';

export default function DatasetWrapper() {
  const { data, loading } = useFetchKnowledgeBaseConfiguration();

  return (
    <KnowledgeBaseProvider knowledgeBase={data} loading={loading}>
      <article className="grid size-full grid-cols-[auto_1fr] grid-rows-1 bg-bg-base">
        <SideBar dataset={data} />

        <section className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <Outlet />
        </section>
      </article>
    </KnowledgeBaseProvider>
  );
}
