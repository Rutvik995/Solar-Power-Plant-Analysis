import React, { useState, useEffect, useRef } from 'react';
import { NavLink, useNavigate, useMatch } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  LayoutDashboard,
  MessageSquareText,
  Info,
  Sun,
  PanelLeftClose,
  MoreHorizontal,
  Pencil,
  Trash2,
  Plus,
} from 'lucide-react';
import {
  fetchSessions,
  createSession,
  renameSession,
  deleteSession,
  formatRelativeTime,
} from '../../services/api';

const NAV_ITEMS = [
  {
    name: 'Dashboard',
    path: '/dashboard',
    icon: LayoutDashboard,
  },
  {
    name: 'Chat',
    path: '/',
    icon: MessageSquareText,
  },
  {
    name: 'About',
    path: '/about',
    icon: Info,
  },
];

export default function Sidebar({ isOpen = true, onToggle }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const chatMatch = useMatch('/chat/:sessionId');
  const activeSessionId = chatMatch?.params?.sessionId;

  const [menuOpenId, setMenuOpenId] = useState(null);
  const [renameModalSession, setRenameModalSession] = useState(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [deleteModalSession, setDeleteModalSession] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [renameError, setRenameError] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const menuRef = useRef(null);

  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: fetchSessions,
    staleTime: 0,                // Always fetch fresh — deleted sessions must not re-appear
    refetchOnWindowFocus: true,  // Re-validate when tab regains focus
  });

  // Close context dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpenId(null);
      }
    };
    if (menuOpenId) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [menuOpenId]);

  const handleCreateNewSession = async () => {
    try {
      const newSess = await createSession({ title: 'New Chat' });
      await queryClient.invalidateQueries({ queryKey: ['sessions'] });
      localStorage.setItem('solar_last_session_id', newSess.id);
      navigate(`/chat/${newSess.id}`);
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  };

  const handleOpenRename = (session, e) => {
    e.stopPropagation();
    setRenameModalSession(session);
    setRenameTitle(session.title || 'New Chat');
    setMenuOpenId(null);
  };

  const handleConfirmRename = async () => {
    if (!renameModalSession) return;
    const trimmed = renameTitle.trim();
    if (!trimmed) return;
    setRenameError(null);
    try {
      await renameSession(renameModalSession.id, trimmed);
      // Refetch from server to confirm the rename persisted
      await queryClient.refetchQueries({ queryKey: ['sessions'] });
      setRenameModalSession(null);
    } catch (err) {
      setRenameError(err.message || 'Failed to rename session');
    }
  };

  const handleOpenDelete = (session, e) => {
    e.stopPropagation();
    setDeleteModalSession(session);
    setMenuOpenId(null);
  };

  const handleConfirmDelete = async () => {
    if (!deleteModalSession || deleting) return;
    setDeleteError(null);
    setDeleting(true);
    try {
      await deleteSession(deleteModalSession.id);
      // Refetch from server BEFORE closing modal — confirms the row is gone
      await queryClient.refetchQueries({ queryKey: ['sessions'] });
      if (activeSessionId === deleteModalSession.id) {
        localStorage.removeItem('solar_last_session_id');
        navigate('/');
      }
      setDeleteModalSession(null);
    } catch (err) {
      // Do NOT close the modal — show the error so the user knows it failed
      setDeleteError(err.message || 'Failed to delete session. Please try again.');
    } finally {
      setDeleting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <>
      <aside className="w-[220px] min-w-[220px] max-w-[220px] bg-white border-r border-[#E2E8F0] flex flex-col shrink-0 min-h-screen">
        {/* Brand Header */}
        <div className="h-14 px-4 border-b border-[#E2E8F0] flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-[#EFF6FF] border border-[#2563EB]/20 flex items-center justify-center text-[#2563EB] shrink-0">
              <Sun className="w-4 h-4 text-[#2563EB]" />
            </div>
            <div className="min-w-0">
              <h1 className="text-sm font-semibold tracking-tight text-[#0F172A] truncate">
                Solar Plant AI
              </h1>
              <p className="text-[11px] text-[#64748B] truncate">Analytics & Ops</p>
            </div>
          </div>

          {onToggle && (
            <button
              type="button"
              onClick={onToggle}
              className="p-1 rounded hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#0F172A] transition-colors ml-1 shrink-0"
              title="Collapse sidebar"
            >
              <PanelLeftClose className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Navigation */}
        <nav className="p-3 space-y-1 border-b border-[#E2E8F0]">
          <div className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[#64748B]">
            Menu
          </div>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                    isActive && !activeSessionId
                      ? 'bg-[#EFF6FF] text-[#2563EB]'
                      : 'text-[#64748B] hover:text-[#0F172A] hover:bg-[#F8FAFC]'
                  }`
                }
              >
                <Icon className="w-4 h-4 shrink-0" />
                <span className="truncate">{item.name}</span>
              </NavLink>
            );
          })}
        </nav>

        {/* History Sidebar: sessions list */}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          <div className="px-3 pt-3 pb-1 flex items-center justify-between">
            <span className="px-1 text-[10px] font-semibold uppercase tracking-wider text-[#64748B]">
              History
            </span>
            <button
              type="button"
              onClick={handleCreateNewSession}
              className="p-1 rounded hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#2563EB] transition-colors"
              title="Create new chat"
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
            {isLoading ? (
              <div className="p-3 text-xs text-[#64748B] text-center">Loading chats...</div>
            ) : sessions.length === 0 ? (
              <div className="p-3 text-xs text-[#64748B] text-center">No chats yet</div>
            ) : (
              sessions.map((sess) => {
                const isActive = activeSessionId === sess.id;
                const isMenuOpen = menuOpenId === sess.id;

                return (
                  <div
                    key={sess.id}
                    className={`group relative flex items-center justify-between rounded-lg px-2.5 py-2 cursor-pointer transition-colors text-xs ${
                      isActive
                        ? 'bg-[#EFF6FF] text-[#2563EB] font-medium'
                        : 'text-[#0F172A] hover:bg-[#F8FAFC]'
                    }`}
                    onClick={() => {
                      localStorage.setItem('solar_last_session_id', sess.id);
                      navigate(`/chat/${sess.id}`);
                    }}
                  >
                    <div className="min-w-0 flex-1 pr-2">
                      <div className="truncate font-medium text-[13px] leading-snug">
                        {sess.title || 'New Chat'}
                      </div>
                      <div className="text-[10px] text-[#64748B] mt-0.5 truncate">
                        {formatRelativeTime(sess.updated_at || sess.created_at)}
                      </div>
                    </div>

                    {/* Three dots menu button */}
                    <div className="shrink-0 relative">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setMenuOpenId(isMenuOpen ? null : sess.id);
                        }}
                        className={`p-1 rounded hover:bg-white/80 transition-colors ${
                          isMenuOpen || isActive
                            ? 'opacity-100 text-[#0F172A]'
                            : 'opacity-0 group-hover:opacity-100 text-[#64748B]'
                        }`}
                        title="Session options"
                      >
                        <MoreHorizontal className="w-3.5 h-3.5" />
                      </button>

                      {/* Dropdown menu */}
                      {isMenuOpen && (
                        <div
                          ref={menuRef}
                          onClick={(e) => e.stopPropagation()}
                          className="absolute right-0 top-6 z-30 w-32 bg-white rounded-lg border border-[#E2E8F0] shadow-sm py-1 text-xs"
                        >
                          <button
                            type="button"
                            onClick={(e) => handleOpenRename(sess, e)}
                            className="w-full px-3 py-1.5 flex items-center gap-2 hover:bg-[#F8FAFC] text-[#0F172A] text-left transition-colors"
                          >
                            <Pencil className="w-3.5 h-3.5 text-[#64748B]" />
                            <span>Rename</span>
                          </button>
                          <button
                            type="button"
                            onClick={(e) => handleOpenDelete(sess, e)}
                            className="w-full px-3 py-1.5 flex items-center gap-2 hover:bg-[#FEF2F2] text-[#DC2626] text-left transition-colors"
                          >
                            <Trash2 className="w-3.5 h-3.5 text-[#DC2626]" />
                            <span>Delete</span>
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </aside>

      {/* Confirmation Dialog for Session Deletion */}
      {deleteModalSession && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-[1px] p-4">
          <div className="bg-white rounded-lg border border-[#E2E8F0] max-w-sm w-full p-5 space-y-4 shadow-sm animate-in fade-in zoom-in-95">
            <div className="space-y-1.5">
              <h4 className="text-sm font-semibold text-[#0F172A]">Delete chat session?</h4>
              <p className="text-xs text-[#64748B] leading-relaxed">
                Are you sure you want to delete <span className="font-medium text-[#0F172A]">"{deleteModalSession.title}"</span>? All messages and traces in this session will be permanently deleted.
              </p>
              {deleteError && (
                <p className="text-xs text-[#DC2626] bg-[#FEF2F2] border border-[#DC2626]/20 rounded px-2 py-1.5">
                  {deleteError}
                </p>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => { setDeleteModalSession(null); setDeleteError(null); }}
                disabled={deleting}
                className="px-3 py-1.5 text-xs font-medium rounded-lg border border-[#E2E8F0] hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#0F172A] transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDelete}
                disabled={deleting}
                className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[#DC2626] hover:bg-[#B91C1C] text-white transition-colors disabled:opacity-60"
              >
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal Dialog for Session Rename */}
      {renameModalSession && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-[1px] p-4">
          <div className="bg-white rounded-lg border border-[#E2E8F0] max-w-sm w-full p-5 space-y-4 shadow-sm animate-in fade-in zoom-in-95">
            <div className="space-y-2">
              <h4 className="text-sm font-semibold text-[#0F172A]">Rename session</h4>
              <input
                type="text"
                value={renameTitle}
                onChange={(e) => setRenameTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleConfirmRename();
                  if (e.key === 'Escape') { setRenameModalSession(null); setRenameError(null); }
                }}
                autoFocus
                className="w-full px-3 py-1.5 text-xs border border-[#E2E8F0] rounded-lg focus:outline-none focus:border-[#2563EB] text-[#0F172A]"
              />
              {renameError && (
                <p className="text-xs text-[#DC2626]">{renameError}</p>
              )}
            </div>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => { setRenameModalSession(null); setRenameError(null); }}
                className="px-3 py-1.5 text-xs font-medium rounded-lg border border-[#E2E8F0] hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#0F172A] transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmRename}
                className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white transition-colors"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
