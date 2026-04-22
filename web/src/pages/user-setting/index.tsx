import { Outlet } from 'react-router';
import { SideBar } from './sidebar';

const UserSetting = () => {
  return (
    <section
      className="grid size-full grid-cols-[auto_1fr] grid-rows-1 bg-bg-base"
      data-testid="user-setting"
    >
      <SideBar />

      <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </div>
    </section>
  );
};

export default UserSetting;
