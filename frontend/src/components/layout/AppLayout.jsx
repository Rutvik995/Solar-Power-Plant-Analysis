import React, { useState } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import { createSession } from '../../services/api';

export default function AppLayout() {
  const [mode, setMode] = useState('live'); // 'live' | 'replay'
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const handleNewChat = async () => {
    try {
      const newSess = await createSession({ title: 'New Chat' });
      await queryClient.invalidateQueries({ queryKey: ['sessions'] });
      localStorage.setItem('solar_last_session_id', newSess.id);
      navigate(`/chat/${newSess.id}`);
    } catch (err) {
      console.error('Failed to create new session:', err);
      navigate('/');
    }
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
          <Outlet context={{ mode, setMode, onNewChat: handleNewChat }} />
        </main>
      </div>
    </div>
  );
}
