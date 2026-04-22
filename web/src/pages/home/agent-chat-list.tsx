import { HomeCard } from '@/components/home-card';
import { MoreButton } from '@/components/more-button';
import { Routes } from '@/routes';
import { useEffect } from 'react';
import { useNavigate } from 'react-router';
import { useSessions } from '../agent-chat/hooks/use-sessions';

export function AgentChatList({
  setListLength,
  setLoading,
}: {
  setListLength: (length: number) => void;
  setLoading?: (loading: boolean) => void;
}) {
  const { data, isLoading } = useSessions();
  const navigate = useNavigate();

  useEffect(() => {
    setListLength(data?.length || 0);
    setLoading?.(isLoading || false);
  }, [data, setListLength, isLoading, setLoading]);

  return (
    <>
      {(data ?? []).slice(0, 10).map((session) => (
        <HomeCard
          key={session.id}
          data={{
            name: session.name,
            description: `${session.kb_ids?.length ?? 0} KB · ${session.max_turns} turns`,
            update_time: session.update_time,
          }}
          onClick={() => navigate(Routes.AgentChat)}
          moreDropdown={<MoreButton />}
        />
      ))}
    </>
  );
}
