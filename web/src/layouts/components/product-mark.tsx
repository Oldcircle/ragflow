import { cn } from '@/lib/utils';

import zhiyuanMark from '@/assets/zhiyuan-mark.png';

type ProductMarkProps = {
  className?: string;
  showSubtitle?: boolean;
  size?: 'sm' | 'md' | 'lg';
};

const sizeMap = {
  sm: {
    mark: 'size-8',
    title: 'text-sm',
    subtitle: 'text-[10px]',
  },
  md: {
    mark: 'size-10',
    title: 'text-base',
    subtitle: 'text-xs',
  },
  lg: {
    mark: 'size-12',
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
      <img
        src={zhiyuanMark}
        alt=""
        aria-hidden="true"
        className={cn('object-contain shrink-0 select-none', s.mark)}
        draggable={false}
      />
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
