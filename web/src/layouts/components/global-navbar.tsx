import { useId, useMemo } from 'react';
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
import { supportsCssAnchor } from '@/utils/css-support';

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

const GlobalNavbar = supportsCssAnchor
  ? () => {
      const { t } = useTranslation();
      const { pathname } = useLocation();
      const navbarAnchorNamePrefix = useId().replace(/:/g, '');

      const activePath = useMemo(() => {
        return (
          Object.keys(PathMap).find((x: string) =>
            PathMap[x as keyof typeof PathMap].some((y: string) =>
              pathname.includes(y),
            ),
          ) || pathname
        );
      }, [pathname]);

      const activePathAnchorName = `--${navbarAnchorNamePrefix}${activePath === Routes.Root ? '-root' : activePath.replace('/', '-')}`;

      const hasAnyActive = useMemo(
        () => menuItems.some(({ path }) => path === activePath),
        [activePath],
      );

      return (
        <nav>
          <ul className="relative flex items-center gap-1 rounded-xl border border-border-button bg-bg-component p-1 shadow-sm">
            {menuItems.map(({ path, name, icon: Icon, ...props }) => {
              const isActive = path === activePath;
              const anchorName = `--${navbarAnchorNamePrefix}${path === Routes.Root ? '-root' : path.replace('/', '-')}`;

              return (
                <li key={path} className="relative" style={{ anchorName }}>
                  <Link
                    {...props}
                    to={path}
                    className={cn(
                      'relative z-[1] h-9 px-3 text-sm inline-flex items-center justify-center gap-2',
                      'hover:text-text-primary focus-visible:text-text-primary rounded-lg transition-all text-text-secondary',
                      isActive && '!text-white',
                    )}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    {Icon && <Icon className="size-4 stroke-[1.7]" />}
                    <span>{t(name)}</span>
                  </Link>
                </li>
              );
            })}

            <li
              className={cn(
                'absolute bg-accent-primary rounded-lg opacity-0 shadow-sm',
                'transition-all',
                hasAnyActive && 'opacity-100',
              )}
              role="presentation"
              style={{
                top: 'anchor(top)',
                left: 'anchor(left)',
                width: 'anchor-size(width)',
                height: 'anchor-size(height)',
                positionAnchor: activePathAnchorName,
              }}
            />
          </ul>
        </nav>
      );
    }
  : () => {
      const { t } = useTranslation();
      const { pathname } = useLocation();

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
        <nav>
          <ul className="flex items-center gap-1 rounded-xl border border-border-button bg-bg-component p-1 shadow-sm">
            {menuItems.map(({ path, name, icon: Icon, ...props }) => {
              const isActive = path === activePath;

              return (
                <li key={path}>
                  <Link
                    {...props}
                    to={path}
                    className={cn(
                      'h-9 px-3 text-sm inline-flex items-center justify-center gap-2',
                      'hover:text-text-primary focus-visible:text-text-primary rounded-lg transition-all text-text-secondary',
                      isActive && '!text-white bg-accent-primary shadow-sm',
                    )}
                    aria-label={t(name)}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    {Icon && <Icon className="size-4 stroke-[1.7]" />}
                    <span>{t(name)}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      );
    };

export default GlobalNavbar;
