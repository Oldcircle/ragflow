import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router';

import {
  Bot,
  BrainCircuit,
  Database,
  FolderOpen,
  Home,
  MessageSquareText,
  Search,
  Workflow,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import { Routes } from '@/routes';

const PathMap = {
  [Routes.Datasets]: [Routes.Datasets, Routes.DatasetBase],
  [Routes.Chats]: [Routes.Chats, Routes.Chat],
  [Routes.AgentChat]: [Routes.AgentChat],
  [Routes.Searches]: [Routes.Searches, Routes.Search],
  [Routes.Agents]: [Routes.Agents, Routes.AgentTemplates],
  [Routes.Memories]: [Routes.Memories, Routes.Memory, Routes.MemoryMessage],
  [Routes.Files]: [Routes.Files],
} as const;

const menuItems = [
  { path: Routes.Root, name: 'header.home', icon: Home },
  { path: Routes.Datasets, name: 'header.dataset', icon: Database },
  {
    path: Routes.Chats,
    name: 'header.chat',
    icon: MessageSquareText,
    'data-testid': 'nav-chat',
  },
  {
    path: Routes.AgentChat,
    name: 'header.agentChat',
    icon: Bot,
    'data-testid': 'nav-agent-chat',
  },
  {
    path: Routes.Searches,
    name: 'header.search',
    icon: Search,
    'data-testid': 'nav-search',
  },
  {
    path: Routes.Agents,
    name: 'header.flow',
    icon: Workflow,
    'data-testid': 'nav-agent',
  },
  { path: Routes.Memories, name: 'header.memories', icon: BrainCircuit },
  { path: Routes.Files, name: 'header.fileManager', icon: FolderOpen },
];

const primaryItems = menuItems.slice(0, 5);
const buildItems = menuItems.slice(5);

function NavSection({
  title,
  items,
  activePath,
}: {
  title: string;
  items: typeof menuItems;
  activePath: string;
}) {
  const { t } = useTranslation();

  return (
    <div>
      <div className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-text-disabled">
        {title}
      </div>
      <ul className="space-y-1">
        {items.map(({ path, name, icon: Icon, ...props }) => {
          const isActive = path === activePath;

          return (
            <li key={path}>
              <Link
                {...props}
                to={path}
                className={cn(
                  'group flex h-9 items-center gap-3 rounded-lg px-3 text-sm transition',
                  'text-text-secondary hover:bg-bg-card hover:text-text-primary focus-visible:bg-bg-card focus-visible:text-text-primary',
                  isActive &&
                    'bg-accent-primary/10 text-text-primary shadow-[inset_2px_0_0_rgb(var(--accent-primary))]',
                )}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon
                  className={cn(
                    'size-4 stroke-[1.7] text-text-secondary transition group-hover:text-text-primary',
                    isActive && 'text-accent-primary',
                  )}
                />
                <span className="truncate">{t(name)}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const GlobalNavbar = () => {
  const { pathname } = useLocation();
  const { t } = useTranslation();

  const activePath = useMemo(() => {
    return (
      Object.keys(PathMap).find((x: string) =>
        PathMap[x as keyof typeof PathMap].some((y: string) =>
          pathname.includes(y),
        ),
      ) || pathname
    );
  }, [pathname]);

  return (
    <nav className="space-y-6">
      <NavSection
        title={t('header.workspaceGroup')}
        items={primaryItems}
        activePath={activePath}
      />
      <NavSection
        title={t('header.buildGroup')}
        items={buildItems}
        activePath={activePath}
      />
    </nav>
  );
};

export default GlobalNavbar;
