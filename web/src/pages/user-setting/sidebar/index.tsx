import { IconFontFill } from '@/components/icon-font';
import { RAGFlowAvatar } from '@/components/ragflow-avatar';
import ThemeSwitch from '@/components/theme-switch';
import { Button } from '@/components/ui/button';
import { Domain } from '@/constants/common';
import { useLogout } from '@/hooks/use-login-request';
import {
  useFetchSystemVersion,
  useFetchUserInfo,
} from '@/hooks/use-user-setting-request';
import { cn } from '@/lib/utils';
import { Routes } from '@/routes';
import { TFunction } from 'i18next';
import {
  ArrowLeft,
  Bot,
  LucideBarChart3,
  LucideBox,
  LucideServer,
  LucideShieldCheck,
  LucideUnplug,
  LucideUser,
  LucideUsers,
} from 'lucide-react';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { useHandleMenuClick } from './hooks';

const menuItems = (t: TFunction) => [
  {
    icon: <LucideServer className="size-4" />,
    label: t('setting.dataSources'),
    key: Routes.DataSource,
    testId: 'settings-nav-data-source',
  },
  {
    icon: <LucideBox className="size-4" />,
    label: t('setting.model'),
    key: Routes.Model,
    testId: 'settings-nav-model-providers',
  },
  {
    icon: <IconFontFill name="mcp" className="size-4" />,
    label: 'MCP',
    key: Routes.Mcp,
    testId: 'settings-nav-mcp',
  },
  {
    icon: <Bot className="size-4" />,
    label: t('setting.botChannels'),
    key: Routes.BotChannels,
    testId: 'settings-nav-bot',
  },
  {
    icon: <LucideShieldCheck className="size-4" />,
    label: t('audit.navLabel'),
    key: Routes.AuditLog,
    testId: 'settings-nav-audit',
  },
  {
    icon: <LucideBarChart3 className="size-4" />,
    label: t('usage.navLabel'),
    key: Routes.Usage,
    testId: 'settings-nav-usage',
  },
  {
    icon: <LucideUsers className="size-4" />,
    label: t('setting.team'),
    key: Routes.Team,
    testId: 'settings-nav-team',
  },
  {
    icon: <LucideUser className="size-4" />,
    label: t('setting.profile'),
    key: Routes.Profile,
    testId: 'settings-nav-profile',
  },
  {
    icon: <LucideUnplug className="size-4" />,
    label: t('setting.api'),
    key: Routes.Api,
    testId: 'settings-nav-api',
  },
];

export function SideBar() {
  const { data: userInfo } = useFetchUserInfo();
  const { handleMenuClick, active: activeItemKey } = useHandleMenuClick();
  const { version, fetchSystemVersion } = useFetchSystemVersion();
  const { t } = useTranslation();

  useEffect(() => {
    if (location.host !== Domain) {
      fetchSystemVersion();
    }
  }, [fetchSystemVersion]);

  const { logout } = useLogout();

  return (
    <aside
      className="flex w-64 shrink-0 flex-col gap-4 overflow-y-auto border-r border-border-button bg-bg-component/60 px-4 py-5"
      data-testid="user-setting-sidebar"
    >
      <Link
        to={Routes.Root}
        className="inline-flex items-center gap-1.5 text-xs text-text-secondary transition hover:text-text-primary"
        data-testid="user-setting-back"
      >
        <ArrowLeft className="size-3" />
        {t('common.back')}
      </Link>

      <section className="flex items-start gap-3">
        <RAGFlowAvatar
          avatar={userInfo?.avatar}
          name={userInfo?.nickname}
          isPerson
          className="size-11 shrink-0"
        />
        <div className="min-w-0 flex-1">
          <h2
            className="truncate text-[15px] font-semibold leading-tight text-text-primary"
            title={userInfo?.nickname}
          >
            {userInfo?.nickname || '—'}
          </h2>
          <p
            className="mt-1 truncate text-[11px] text-text-disabled"
            title={userInfo?.email}
          >
            {userInfo?.email || '—'}
          </p>
        </div>
      </section>

      <nav aria-label={t('setting.setting')}>
        <div className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-text-disabled">
          {t('setting.setting')}
        </div>
        <ul className="space-y-1">
          {menuItems(t).map((item) => {
            const { key, icon, label, testId } = item;
            const active = activeItemKey === key;

            return (
              <li key={key}>
                <button
                  type="button"
                  onClick={handleMenuClick(key)}
                  aria-current={active ? 'page' : undefined}
                  data-testid={testId}
                  className={cn(
                    'group flex h-9 w-full items-center gap-3 rounded-lg px-3 text-sm transition',
                    'text-text-secondary hover:bg-bg-card hover:text-text-primary focus-visible:bg-bg-card focus-visible:text-text-primary focus-visible:outline-none',
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
                    {icon}
                  </span>
                  <span className="truncate">{label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="flex-1" />

      <footer className="space-y-3 border-t border-border-button pt-4">
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-xs text-text-disabled">
            {version || ''}
          </span>
          <ThemeSwitch />
        </div>

        <Button
          block
          size="sm"
          variant="outline"
          onClick={() => logout()}
          data-testid="user-setting-logout"
        >
          {t('setting.logout')}
        </Button>
      </footer>
    </aside>
  );
}
