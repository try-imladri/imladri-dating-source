import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import NotificationContent from "./NotificationContent";
import { toast } from "sonner";
import { errMsg } from "@/lib/api";
import { MemberLoadState, useMemberResource } from "@/components/app/MemberResource";

export function NotificationBell({ api, couples = false }) {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [pages, setPages] = useState([null]);
  const endpoint = couples ? "/couples/notifications" : "/notifications";
  const cursor = pages[pages.length - 1];
  const pagePath = cursor ? endpoint + "?" + new URLSearchParams(cursor).toString() : endpoint;
  const { data, error, reload: load } = useMemberResource(api, pagePath);
  useEffect(() => {
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    const timer = setInterval(refreshWhenVisible, 60000);
    window.addEventListener("focus", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [load]);
  const openItem = async (n) => {
    if (!n.read) {
      try { await api.post(`${endpoint}/${n.id}/read`); }
      catch { toast.error("Couldn’t mark the notification as read."); }
    }
    setOpen(false);
    load();
    nav(n.link);
  };
  const readAll = async () => {
    try {
      await api.post(endpoint + "/read-all");
      load();
    } catch (failure) { toast.error(errMsg(failure)); }
  };
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) { if (cursor) setPages([null]); else load(); } }}>
      <PopoverTrigger asChild>
        <button className="relative w-9 h-9 rounded-full border border-[var(--line)] flex items-center justify-center hover:border-[var(--ink)] transition-colors" aria-label={data?.unread ? `Notifications, ${data.unread} unread` : "Notifications"} data-testid="notifications-bell">
          <Bell className="w-4 h-4" />
          {data?.unread > 0 && <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-red-600 text-white text-[10px] font-bold flex items-center justify-center" data-testid="notifications-unread">{data.unread}</span>}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[340px] max-w-[calc(100vw-1rem)] p-0" data-testid="notifications-panel">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--line)]">
          <p className="font-semibold text-sm">Notifications</p>
          <span className="flex-1" />
          <span className="text-[10px] font-mono uppercase text-[var(--muted)] mr-3" title="Alerts never show names or message text" data-testid="notifications-quiet">Quiet: no names</span>
          {data?.unread > 0 && <button onClick={readAll} className="text-xs underline" data-testid="notifications-read-all">Mark all read</button>}
        </div>
        <div className="max-h-[60vh] overflow-y-auto">
          {(!data || error) && <div className="p-4"><MemberLoadState label="notifications" error={error} retry={load} /></div>}
          {data && !error && !data.items.length && <p className="p-4 text-sm text-[var(--muted)]" data-testid="notifications-empty">Nothing new yet.</p>}
          {!error && data?.items.map((n) => (
            <button key={n.id} onClick={() => openItem(n)} className={`w-full text-left px-4 py-3 border-b border-[var(--line)] last:border-0 hover:bg-[var(--surface)] flex gap-3 ${n.read ? "opacity-60" : ""}`} data-testid={`notification-${n.id}`}>
              <NotificationContent item={n} />
            </button>
          ))}
          {!couples && (cursor || data?.next) && <nav aria-label="Inbox pages" className="flex justify-between gap-2 p-3 text-xs">
            <button className="underline disabled:opacity-40" disabled={!cursor || !data} onClick={() => setPages(p => p.slice(0, -1))}>Newer</button>
            <span>Page {pages.length}</span>
            <button className="underline disabled:opacity-40" disabled={!data?.next} onClick={() => setPages(p => [...p, data.next])}>Older</button>
          </nav>}
        </div>
      </PopoverContent>
    </Popover>
  );
}
