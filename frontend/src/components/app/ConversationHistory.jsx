import { useMemo, useState } from "react";
import { useMemberResource } from "@/components/app/MemberResource";

const PAGE_SIZE = 30;

export function useConversationPage(api, endpoint) {
  const [cursors, setCursors] = useState([]);
  const query = new URLSearchParams({ limit: String(PAGE_SIZE + 1) });
  if (cursors.length) {
    const cursor = cursors[cursors.length - 1];
    query.set("before_time", cursor.created_at);
    query.set("before_id", cursor.id);
  }
  const path = endpoint + (endpoint.includes("?") ? "&" : "?") + query.toString();
  const { data, error, reload } = useMemberResource(api, path);
  const rows = useMemo(() => data?.slice(-PAGE_SIZE), [data]);
  const older = data?.length > PAGE_SIZE ? () => setCursors([...cursors, { created_at: rows[0].created_at, id: rows[0].id }]) : null;
  const newer = cursors.length ? () => setCursors(cursors.slice(0, -1)) : null;
  const latest = () => cursors.length ? setCursors([]) : reload();
  return { rows, error, reload, latest, older, newer };
}

export function ConversationPaging({ older, newer }) {
  if (!older && !newer) return null;
  return <nav aria-label="Conversation pages" className="flex gap-4 text-sm">
    {older && <button type="button" className="underline" onClick={older}>Earlier conversations</button>}
    {newer && <button type="button" className="underline" onClick={newer}>Newer conversations</button>}
  </nav>;
}
