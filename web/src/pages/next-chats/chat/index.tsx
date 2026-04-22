import { Button } from '@/components/ui/button';
import {
  useFetchSessionList,
  useFetchSessionManually,
  useGetChatSearchParams,
} from '@/hooks/use-chat-request';
import { IClientConversation } from '@/interfaces/database/chat';
import { RootLayoutContainer } from '@/layouts/root-layout';
import { cn } from '@/lib/utils';
import { useMount } from 'ahooks';
import { isEmpty } from 'lodash';
import {
  LucideArrowBigLeft,
  LucideArrowUpRight,
  MessageSquareText,
} from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useHandleClickConversationCard } from '../hooks/use-click-card';
import { ChatSettings } from './app-settings/chat-settings';
import { MultipleChatBox } from './chat-box/next-multiple-chat-box';
import { SingleChatBox } from './chat-box/single-chat-box';
import { Sessions } from './sessions';
import './styles.css';
import { useAddChatBox } from './use-add-box';
import { useSwitchDebugMode } from './use-switch-debug-mode';

export default function Chat() {
  const { t } = useTranslation();
  const [currentConversation, setCurrentConversation] =
    useState<IClientConversation>({} as IClientConversation);

  const { fetchSessionManually } = useFetchSessionManually();

  const { handleConversationCardClick, controller, stopOutputMessage } =
    useHandleClickConversationCard();

  const { isDebugMode, switchDebugMode } = useSwitchDebugMode();
  const { removeChatBox, addChatBox, chatBoxIds, hasSingleChatBox } =
    useAddChatBox(isDebugMode);

  const { conversationId, isNew } = useGetChatSearchParams();

  const { data: dialogList } = useFetchSessionList();

  const currentConversationName = useMemo(() => {
    return (
      dialogList.find((x) => x.id === conversationId)?.name ||
      t('chat.newConversation')
    );
  }, [conversationId, dialogList, t]);

  const fetchConversation: typeof handleConversationCardClick = useCallback(
    async (conversationId, isNew) => {
      if (conversationId && !isNew) {
        const conversation = await fetchSessionManually(conversationId);
        if (!isEmpty(conversation)) {
          setCurrentConversation(conversation);
        }
      }
    },
    [fetchSessionManually],
  );

  const handleSessionClick: typeof handleConversationCardClick = useCallback(
    (conversationId, isNew) => {
      handleConversationCardClick(conversationId, isNew);
      fetchConversation(conversationId, isNew);
    },
    [fetchConversation, handleConversationCardClick],
  );

  useMount(() => {
    fetchConversation(conversationId, isNew === 'true');
  });

  if (isDebugMode) {
    return (
      <RootLayoutContainer>
        <section
          className="chat-workbench-root flex h-full flex-col pb-8 pt-5"
          data-testid="chat-detail-multimodel-root"
        >
          <header className="px-10 pb-5">
            <div className="mb-5">
              <Button
                variant="outline"
                onClick={switchDebugMode}
                data-testid="chat-detail-multimodel-back"
              >
                <LucideArrowBigLeft />
                <span>{t('common.back')}</span>
              </Button>
            </div>

            <span className="text-2xl">
              {t('chat.multipleModels')} ({chatBoxIds.length}/3)
            </span>
          </header>

          <MultipleChatBox
            chatBoxIds={chatBoxIds}
            controller={controller}
            removeChatBox={removeChatBox}
            addChatBox={addChatBox}
            stopOutputMessage={stopOutputMessage}
            conversation={currentConversation}
          ></MultipleChatBox>
        </section>
      </RootLayoutContainer>
    );
  }

  return (
    <RootLayoutContainer>
      <section
        className="chat-workbench-root flex h-full"
        data-testid="chat-detail"
      >
        <Sessions handleConversationCardClick={handleSessionClick}></Sessions>

        <main className="flex min-w-0 flex-1 flex-col">
          <header
            className={cn('chat-workbench-topbar', {
              'border-b-0.5 border-border-button': hasSingleChatBox,
            })}
          >
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-1.5 text-[11px] text-text-disabled">
                <MessageSquareText className="size-3" />
                <span>{t('header.chat')}</span>
              </div>
              <h1 className="truncate text-[15px] font-semibold text-text-primary">
                {currentConversationName}
              </h1>
            </div>

            <Button
              variant="ghost"
              onClick={switchDebugMode}
              data-testid="chat-detail-multimodel-toggle"
            >
              <LucideArrowUpRight />
              {t('chat.multipleModels')}
            </Button>
          </header>

          <div className="min-h-0 flex-1">
            <SingleChatBox
              controller={controller}
              stopOutputMessage={stopOutputMessage}
              conversation={currentConversation}
            />
          </div>
        </main>

        <ChatSettings hasSingleChatBox={hasSingleChatBox}></ChatSettings>
      </section>
    </RootLayoutContainer>
  );
}
