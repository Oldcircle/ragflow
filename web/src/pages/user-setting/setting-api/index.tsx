import ApiContent from '@/components/api-service/chat-overview-modal/api-content';
import { useTranslation } from 'react-i18next';

const ApiPage = () => {
  const { t } = useTranslation();

  return (
    <article
      className="flex size-full min-h-0 w-full flex-col overflow-hidden bg-bg-base"
      data-testid="user-setting-api"
    >
      <header className="shrink-0 border-b border-border-button bg-bg-component/60 px-8 py-5">
        <h1 className="text-[22px] font-semibold leading-tight tracking-normal text-text-primary">
          {t('setting.api')}
        </h1>
      </header>

      <div className="min-h-0 flex-1 overflow-auto px-8 py-6 scrollbar-auto">
        <ApiContent idKey="dialogId" />
      </div>
    </article>
  );
};

export default ApiPage;
