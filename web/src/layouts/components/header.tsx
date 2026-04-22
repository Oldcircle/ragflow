import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useChangeLanguage } from '@/hooks/logic-hooks';
import {
  useFetchUserInfo,
  useListTenant,
} from '@/hooks/use-user-setting-request';
import { cn } from '@/lib/utils';
import { TenantRole } from '@/pages/user-setting/constants';
import { Routes } from '@/routes';
import { Bot, LucideChevronDown, LucideCircleHelp, Plus } from 'lucide-react';
import React, { useMemo } from 'react';
import { Link, useLocation } from 'react-router';
import { BellButton } from './bell-button';
import GlobalNavbar from './global-navbar';
import { ProductMark } from './product-mark';
import ThemeButton from './theme-button';

import { supportedLanguages } from '@/locales/config';

export function Header({
  className,
  ...props
}: React.HTMLAttributes<HTMLElement>) {
  const { pathname } = useLocation();

  const changeLanguage = useChangeLanguage();

  const {
    data: { language = 'en', avatar, nickname },
  } = useFetchUserInfo();

  const { data: tenantData } = useListTenant();
  const hasNotification = useMemo(
    () => tenantData?.some((x) => x.role === TenantRole.Invite),
    [tenantData],
  );

  const currentLanguage = supportedLanguages.find((x) => x.code === language);

  // const langItems = LanguageList.map((x) => ({
  //   key: x,
  //   label: <span>{LanguageMap[x as keyof typeof LanguageMap]}</span>,
  // }));

  return (
    <header
      key="app-navbar"
      className={cn(
        'flex h-full min-h-0 flex-col border-r border-border-button bg-bg-component/80 px-4 py-4',
        className,
      )}
      {...props}
    >
      <div className="mb-5 flex items-center">
        <Link
          to={Routes.Root}
          aria-current={pathname === Routes.Root ? 'page' : undefined}
          className="min-w-0 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary/30"
        >
          <ProductMark />
        </Link>
      </div>

      <Button
        asLink
        to={Routes.AgentChat}
        className="mb-5 w-full justify-start border-border-button bg-bg-base text-text-primary hover:bg-bg-card"
        variant="outline"
      >
        <Plus className="size-4 text-accent-primary" />
        新建 Agent 会话
      </Button>

      <GlobalNavbar />

      <div className="mt-6 rounded-xl border border-border-button bg-bg-base p-3">
        <div className="mb-2 flex items-center gap-2 text-sm font-medium text-text-primary">
          <Bot className="size-4 text-accent-primary" />
          Agent v2
        </div>
        <p className="text-xs leading-5 text-text-secondary">
          自主检索、阅读文档并展示工具调用，适合政策、法务、研报等可信问答场景。
        </p>
      </div>

      <div className="flex-1" />

      <div
        className="space-y-2 border-t border-border-button pt-4 text-text-badge"
        data-testid="auth-status"
      >
        <div className="flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                className="min-w-0 flex-1 justify-start gap-1"
                variant="ghost"
              >
                <span className="truncate">{currentLanguage?.displayName}</span>
                <LucideChevronDown className="size-[1em]" />
              </Button>
            </DropdownMenuTrigger>

            <DropdownMenuContent>
              {supportedLanguages.map((x) => (
                <DropdownMenuItem
                  key={x.code}
                  onClick={() => changeLanguage(x.code)}
                >
                  {x.displayName}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <Button
            asLink
            variant="ghost"
            size="icon"
            to="https://ragflow.io/docs/dev/category/user-guides"
            target="_blank"
            rel="noreferrer noopener"
          >
            <LucideCircleHelp className="size-[1em]" />
          </Button>

          <ThemeButton />

          {hasNotification && <BellButton />}
        </div>

        <Link
          to={Routes.UserSetting}
          className="flex min-w-0 items-center gap-3 rounded-xl px-2 py-2 transition hover:bg-bg-card"
          data-testid="settings-entrypoint"
        >
          <RAGFlowAvatar
            name={nickname}
            avatar={avatar}
            isPerson
            className="size-8"
          />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-text-primary">
              {nickname || 'User'}
            </div>
            <div className="truncate text-xs text-text-secondary">
              工作区设置
            </div>
          </div>
        </Link>
      </div>
    </header>
  );
}
