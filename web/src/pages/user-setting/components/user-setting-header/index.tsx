import Spotlight from '@/components/spotlight';
import { cn } from '@/lib/utils';
import { PropsWithChildren } from 'react';

export function Title({ children }: PropsWithChildren) {
  return (
    <span className="text-[22px] font-semibold leading-tight tracking-normal text-text-primary">
      {children}
    </span>
  );
}

type ProfileSettingWrapperCardProps = {
  header: React.ReactNode;
  className?: string;
  contentClassName?: string;
} & PropsWithChildren;

export function ProfileSettingWrapperCard({
  header,
  children,
  className,
  contentClassName,
}: ProfileSettingWrapperCardProps) {
  return (
    <article
      className={cn(
        'relative flex size-full min-h-0 w-full flex-col overflow-hidden bg-bg-base',
        className,
      )}
    >
      <header className="shrink-0 border-b border-border-button bg-bg-component/60 px-8 py-5">
        {header}
      </header>

      <div
        className={cn(
          'min-h-0 flex-1 overflow-auto scrollbar-auto',
          contentClassName,
        )}
      >
        {children}
      </div>

      <Spotlight />
    </article>
  );
}
