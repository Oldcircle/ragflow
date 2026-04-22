import { Outlet } from 'react-router';
import { Header } from './components/header';

export function RootLayoutContainer({ children }: React.PropsWithChildren) {
  return (
    <div className="grid size-full grid-cols-[248px_minmax(0,1fr)] bg-bg-base">
      <Header className="min-h-0" />

      <main className="min-h-0 min-w-0 overflow-hidden">{children}</main>
    </div>
  );
}

export default function RootLayout() {
  return (
    <RootLayoutContainer>
      <Outlet />
    </RootLayoutContainer>
  );
}
