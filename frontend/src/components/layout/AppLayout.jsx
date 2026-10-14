import React, { useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import TopBar from './TopBar';

export default function AppLayout() {
  const [mode, setMode] = useState('replay'); // 'replay' | 'live'
  const [newChatTrigger, setNewChatTrigger] = useState(0);

  const handleNewChat = () => {
    setNewChatTrigger((prev) => prev + 1);
  };

  return (
    <div className="flex min-h-screen bg-[#F8FAFC] text-[#0F172A]">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 bg-[#F8FAFC]">
        <TopBar mode={mode} setMode={setMode} onNewChat={handleNewChat} />
        <main className="flex-1 p-6 overflow-y-auto bg-[#F8FAFC]">
          <Outlet context={{ mode, setMode, newChatTrigger, onNewChat: handleNewChat }} />
        </main>
      </div>
    </div>
  );
}
