import { formatDistanceToNow } from "date-fns";

export default function NotificationContent({ item }) {
  return <>
    <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${item.read ? "bg-transparent" : "bg-red-600"}`} />
    <span className="min-w-0">
      <span className="block text-sm font-semibold">{item.title}</span>
      {item.body && <span className="block text-xs text-[var(--muted)] mt-0.5 line-clamp-2">{item.body}</span>}
      <span className="block text-[10px] font-mono text-[var(--muted)] mt-1">{formatDistanceToNow(new Date(item.created_at))} ago</span>
    </span>
  </>;
}
