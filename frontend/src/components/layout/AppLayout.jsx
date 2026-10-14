import React, { useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import TopBar from './TopBar';

export default function AppLayout() {
  const [mode, setMode] = useState('replay'); // 'replay' | 'live'
  const [newChatTrigger, setNewChatTrigger] = useState(0);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const handleNewChat = () => {
    setNewChatTrigger((prev) => prev + 1);
  };

  const handleToggleSidebar = () => {
    setIsSidebarOpen((prev) => !prev);
  };

  return (
    <div className="flex min-h-screen bg-[#F8FAFC] text-[#0F172A]">
      <Sidebar
        isOpen={isSidebarOpen}
        onToggle={handleToggleSidebar}
      />
      <div className="flex-1 flex flex-col min-w-0 bg-[#F8FAFC]">
        <TopBar
          mode={mode}
          setMode={setMode}
          onNewChat={handleNewChat}
          isSidebarOpen={isSidebarOpen}
          onToggleSidebar={handleToggleSidebar}
        />
        <main className="flex-1 p-6 overflow-y-auto bg-[#F8FAFC]">
          <Outlet context={{ mode, setMode, newChatTrigger, onNewChat: handleNewChat }} />
        </main>
      </div>
    </div>
  );
}
