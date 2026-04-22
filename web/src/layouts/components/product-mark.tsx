import { cn } from '@/lib/utils';

type ProductMarkProps = {
  className?: string;
  showSubtitle?: boolean;
  size?: 'sm' | 'md' | 'lg';
};

const sizeMap = {
  sm: {
    mark: 'size-8 rounded-lg text-sm',
    title: 'text-sm',
    subtitle: 'text-[10px]',
  },
  md: {
    mark: 'size-10 rounded-xl text-base',
    title: 'text-base',
    subtitle: 'text-xs',
  },
  lg: {
    mark: 'size-12 rounded-xl text-lg',
    title: 'text-xl',
    subtitle: 'text-sm',
  },
};

export function ProductMark({
  className,
  showSubtitle = true,
  size = 'md',
}: ProductMarkProps) {
  const s = sizeMap[size];

  return (
    <div className={cn('inline-flex items-center gap-3 min-w-0', className)}>
      <div
        className={cn(
          'grid place-items-center bg-accent-primary text-white font-semibold shadow-sm',
          s.mark,
        )}
        aria-hidden="true"
      >
        知
      </div>
      <div className="min-w-0">
        <div
          className={cn(
            'font-semibold leading-tight tracking-normal text-text-primary',
            s.title,
          )}
        >
          知源
        </div>
        {showSubtitle && (
          <div
            className={cn(
              'mt-0.5 truncate leading-tight text-text-secondary',
              s.subtitle,
            )}
          >
            企业知识库
          </div>
        )}
      </div>
    </div>
  );
}
