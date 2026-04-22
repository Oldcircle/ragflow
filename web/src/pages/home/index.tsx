import { useFetchNextKnowledgeListByPage } from '@/hooks/use-knowledge-request';
import { PageContainer } from '@/layouts/components/page-container';
import { Routes } from '@/routes';
import {
  Bot,
  Database,
  FileText,
  MessageSquareText,
  Search,
  Sparkles,
} from 'lucide-react';
import { useMemo } from 'react';
import { useNavigate } from 'react-router';
import { Applications } from './applications';
import { Datasets } from './datasets';

const Home = () => {
  const navigate = useNavigate();
  const { kbs, total_datasets } = useFetchNextKnowledgeListByPage();

  const stats = useMemo(() => {
    const documents = kbs.reduce((sum, item) => sum + item.document_count, 0);
    const chunks = kbs.reduce((sum, item) => sum + item.chunk_count, 0);
    const tokens = kbs.reduce((sum, item) => sum + item.token_num, 0);

    return [
      {
        label: '知识库',
        value: total_datasets || kbs.length,
        hint: '团队可用知识空间',
        icon: Database,
      },
      {
        label: '文档',
        value: documents.toLocaleString(),
        hint: '当前可见知识库',
        icon: FileText,
      },
      {
        label: '切片',
        value: chunks.toLocaleString(),
        hint: '用于语义召回',
        icon: Search,
      },
      {
        label: 'Token',
        value: tokens > 1000 ? `${Math.round(tokens / 1000)}k` : tokens,
        hint: '解析文本规模',
        icon: Sparkles,
      },
    ];
  }, [kbs, total_datasets]);

  const quickActions = [
    {
      label: '新建知识库',
      description: '上传文档并开始解析',
      icon: Database,
      onClick: () => navigate(`${Routes.Datasets}?isCreate=true`),
    },
    {
      label: '打开 Agent',
      description: '基于知识库进行多步问答',
      icon: Bot,
      onClick: () => navigate(Routes.AgentChat),
    },
    {
      label: '开始对话',
      description: '使用现有聊天助手',
      icon: MessageSquareText,
      onClick: () => navigate(Routes.Chats),
    },
  ];

  return (
    <PageContainer className="px-6 py-8">
      <article className="mx-auto max-w-7xl">
        <header className="mb-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-border-button bg-bg-component px-3 py-1 text-xs font-medium text-text-secondary">
              <Sparkles className="size-3.5 text-accent-primary" />
              Enterprise knowledge workspace
            </div>
            <h1 className="max-w-3xl text-[40px] font-semibold leading-tight tracking-normal text-text-primary">
              管理企业知识，让 Agent 的每一次回答都有依据
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-7 text-text-secondary">
              汇聚文档、检索、对话、工具调用和引用来源，面向企业内部场景构建可信知识库。
            </p>
          </div>

          <div className="rounded-xl border border-border-button bg-bg-component p-4 shadow-sm">
            <div className="mb-3 text-sm font-semibold text-text-primary">
              快速入口
            </div>
            <div className="space-y-2">
              {quickActions.map(
                ({ label, description, icon: Icon, onClick }) => (
                  <button
                    key={label}
                    className="flex w-full items-center gap-3 rounded-lg border border-transparent px-3 py-3 text-left transition hover:border-border-button hover:bg-bg-card"
                    onClick={onClick}
                    type="button"
                  >
                    <span className="grid size-9 place-items-center rounded-lg bg-accent-primary/10 text-accent-primary">
                      <Icon className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-text-primary">
                        {label}
                      </span>
                      <span className="block truncate text-xs text-text-secondary">
                        {description}
                      </span>
                    </span>
                  </button>
                ),
              )}
            </div>
          </div>
        </header>

        <section className="mb-10 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {stats.map(({ label, value, hint, icon: Icon }) => (
            <div
              key={label}
              className="rounded-xl border border-border-button bg-bg-component p-4 shadow-sm"
            >
              <div className="mb-4 flex items-center justify-between">
                <div className="text-sm font-medium text-text-secondary">
                  {label}
                </div>
                <Icon className="size-4 text-accent-primary" />
              </div>
              <div className="text-3xl font-semibold tracking-normal text-text-primary">
                {value}
              </div>
              <div className="mt-1 text-xs text-text-disabled">{hint}</div>
            </div>
          ))}
        </section>

        <Datasets />
        <Applications />
      </article>
    </PageContainer>
  );
};

export default Home;
