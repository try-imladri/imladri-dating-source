import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Ban, CalendarDays, Flag, Heart, MessageCircle, Send, ShieldCheck, Unlink, X } from "lucide-react";
import { useDating, useDatingAction, useDatingResource } from "@/components/dating/DatingContext";
import { ActionFeedback, AuthorizedPhoto, DateFeedback, EmptyState, ErrorState, Loading, MatchListItem, MemberProfileModal, Modal, PageHeading, SafetyDialog } from "@/components/dating/DatingUI";
import { humanLabel, requestId, safeTextDate } from "@/lib/dating";

export default function DatingMessages() {
  const { matchId } = useParams();
  const matches = useDatingResource("/dating/matches");
  const reloadConnections = matches.reload;
  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) reloadConnections(); }, 12000);
    return () => clearInterval(timer);
  }, [reloadConnections]);
  const selected = matches.data?.items?.find(match => match.id === matchId);
  return <><PageHeading eyebrow="Independent interest. A real conversation." title="Messages">A conversation opens when you both choose to connect. Human messages don’t use an AI allowance.</PageHeading>
    <div className={`dating-messages-layout ${matchId ? "has-conversation" : ""}`}><aside className="dating-match-list" aria-label="Your matches"><div className="dating-match-list-heading"><h2>Your connections</h2><span>{matches.data?.items?.length || 0}</span></div>{matches.error ? <ErrorState error={matches.error} retry={matches.reload} /> : matches.loading && !matches.data ? <Loading>Opening connections…</Loading> : matches.data?.items?.length ? matches.data.items.map(match => <div className={match.id === matchId ? "is-active" : ""} key={match.id}><MatchListItem match={match} /></div>) : <div className="dating-match-list-empty"><Heart size={25} /><p>Your mutual connections will appear here.</p><Link to="/dating">Explore introductions</Link></div>}</aside>
      <section className="dating-conversation-wrap">{matchId ? selected ? <Conversation key={matchId} match={selected} reloadMatches={matches.reload} /> : matches.data && !matches.loading ? <EmptyState icon={MessageCircle} title="This conversation is unavailable." action={<Link className="btn-ghost dating-button dating-button--quiet" to="/dating/messages">All conversations</Link>}><p>It may have ended, or you may no longer have access. Your safety and data controls remain in Profile.</p></EmptyState> : <Loading>Opening conversation…</Loading> : <EmptyState icon={MessageCircle} eyebrow="Let a good conversation begin" title="A connection is a starting point." action={<Link className="btn-lime dating-button dating-button--dark" to="/dating">Explore introductions</Link>}><p>Choose a connection to say hello. You both decide what to share and where the conversation goes.</p></EmptyState>}</section>
    </div></>;
}

function Conversation({ match, reloadMatches }) {
  const { api, data, refresh } = useDating(); const navigate = useNavigate();
  const path = `/dating/matches/${encodeURIComponent(match.id)}`;
  const resource = useDatingResource(`${path}/messages`);
  const reloadMessages = resource.reload;
  const send = useDatingAction(), olderAction = useDatingAction(), endAction = useDatingAction();
  const [text, setText] = useState(""), [older, setOlder] = useState([]), [cursor, setCursor] = useState(null);
  const [profile, setProfile] = useState(false), [safety, setSafety] = useState(null), [ending, setEnding] = useState(false), [sharing, setSharing] = useState(false), [feedback, setFeedback] = useState(false);
  const retry = useRef(null), end = useRef(null), scrollArea = useRef(null), readAt = useRef("");
  const person = match.profile;
  const messages = useMemo(() => {
    const unique = new Map([...older, ...(resource.data?.items || [])].map(message => [message.id, message]));
    return [...unique.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  }, [older, resource.data]);
  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) reloadMessages(); }, 12000);
    return () => clearInterval(timer);
  }, [reloadMessages]);
  const lastId = messages.at(-1)?.id;
  useEffect(() => {
    if (!lastId) return;
    const area = scrollArea.current;
    if (area && (area.scrollHeight - area.scrollTop - area.clientHeight < 200 || messages.at(-1)?.sender_id === data.member.user_id)) end.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    if (readAt.current !== lastId && !document.hidden) {
      readAt.current = lastId;
      api.post(`${path}/read`).then(reloadMatches).catch(() => { readAt.current = ""; });
    }
  }, [lastId, api, path, reloadMatches, messages, data.member.user_id]);
  const submit = e => {
    e.preventDefault(); const outgoing = text.trim(); if (!outgoing || send.busy) return;
    // Reuse the same id after an uncertain response so retrying cannot duplicate a message.
    if (!retry.current || retry.current.text !== outgoing) retry.current = { text: outgoing, request_id: requestId() };
    send.run(() => api.post(`${path}/messages`, retry.current), async response => {
      setOlder(previous => [...previous, response.data.message]); setText(current => current.trim() === outgoing ? "" : current); retry.current = null;
      await resource.reload(); await reloadMatches(); end.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    });
  };
  const loadOlder = () => olderAction.run(() => api.get(`${path}/messages`, { params: { cursor: cursor || resource.data?.next_cursor } }), response => {
    setOlder(previous => [...previous, ...(resource.data?.items || []), ...response.data.items]); setCursor(response.data.next_cursor || "end");
  });
  const ended = async () => { await reloadMatches(); await refresh(); navigate("/dating/messages", { replace: true }); };
  return <div className="dating-conversation"><header className="dating-conversation-heading"><Link to="/dating/messages" className="dating-icon-button dating-back-to-matches" aria-label="Back to conversations"><ArrowLeft size={20} /></Link><button className="dating-conversation-person" onClick={() => setProfile(true)}><AuthorizedPhoto photo={person.photos?.[0]} alt="" className="dating-match-avatar" /><span><strong>{person.display_name}</strong><span>{person.city} · {humanLabel(person.intent)}</span></span></button><button className="dating-text-button" onClick={() => setSharing(true)}><ShieldCheck size={17} /><span>Sharing</span></button></header>
    <div className="dating-conversation-toolbar"><button onClick={() => setFeedback(true)}><CalendarDays size={15} /> Date reflection</button><button onClick={() => setSafety("report")}><Flag size={15} /> Report</button><button onClick={() => setSafety("block")}><Ban size={15} /> Block</button><button onClick={() => setEnding(true)}><Unlink size={15} /> Unmatch</button></div>
    <div className="dating-message-stream" ref={scrollArea} aria-label={`Messages with ${person.display_name}`}>
      {resource.data?.next_cursor && cursor !== "end" && <button className="dating-text-button dating-load-older" disabled={olderAction.busy} onClick={loadOlder}>{olderAction.busy ? "Loading…" : "Load earlier messages"}</button>}<ActionFeedback action={olderAction} />
      <div className="dating-chat-start"><Heart size={22} /><strong>You both chose to connect.</strong><p>{person.conversation_starter || "Say hello with a question about something that caught your attention."}</p></div>
      {resource.error ? <ErrorState error={resource.error} retry={resource.reload} /> : resource.loading && !resource.data ? <Loading>Opening messages…</Loading> : <ol className="dating-message-list">{messages.map(message => <li key={message.id} className={message.sender_id === data.member.user_id ? "is-mine" : "is-theirs"}><p>{message.text}</p><time dateTime={message.created_at}>{message.sender_id === data.member.user_id ? "You · " : `${person.display_name} · `}{safeTextDate(message.created_at)}</time></li>)}</ol>}<div ref={end} />
    </div>
    <form className="dating-message-composer" onSubmit={submit}><label htmlFor={`message-${match.id}`} className="dating-sr-only">Message {person.display_name}</label><textarea id={`message-${match.id}`} rows={2} value={text} maxLength={3000} onChange={e => { setText(e.target.value); send.clear(); }} placeholder={`Say hello to ${person.display_name}…`} /><button className="btn-lime dating-button dating-button--dark" disabled={!text.trim() || send.busy}><Send size={18} /><span>{send.busy ? "Sending…" : "Send"}</span></button><ActionFeedback action={send} /><p className="dating-fine">Keep financial details private. You can report, block or end a connection at any time.</p></form>
    {profile && <MemberProfileModal profile={person} close={() => setProfile(false)} onReported={ended} />}
    {safety && <SafetyDialog mode={safety} matchId={match.id} profile={person} close={() => setSafety(null)} onDone={() => { const blocked = safety === "block"; setSafety(null); if (blocked) ended(); }} />}
    {feedback && <DateFeedback matchId={match.id} close={() => setFeedback(false)} />}
    {sharing && <Sharing match={match} close={() => setSharing(false)} />}
    {ending && <Modal title={`End your connection with ${person.display_name}?`} close={() => setEnding(false)}><p>This closes your conversation and revokes selected answer sharing for this connection. The other person cannot continue messaging you.</p><ActionFeedback action={endAction} /><div className="dating-button-row"><button className="btn-ghost dating-button dating-button--quiet" onClick={() => setEnding(false)}>Keep connection</button><button className="btn-lime dating-button dating-button--dark" disabled={endAction.busy} onClick={() => endAction.run(() => api.post(`${path}/unmatch`), ended)}>{endAction.busy ? "Ending…" : "Unmatch"}</button></div></Modal>}
  </div>;
}

export function Sharing({ match, close }) {
  const { api, data } = useDating();
  const path = `/dating/matches/${encodeURIComponent(match.id)}/sharing`;
  const resource = useDatingResource(path); const action = useDatingAction();
  const reloadSharing = resource.reload;
  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) reloadSharing(); }, 12000);
    return () => clearInterval(timer);
  }, [reloadSharing]);
  const [selected, setSelected] = useState([]), [consent, setConsent] = useState(false);
  const answers = useMemo(() => ["personality", "intimacy"].flatMap(scope => (data.catalog?.[scope] || []).flatMap(question => {
    const answer = data.preferences?.[scope]?.[question.id]?.[scope === "personality" ? "self" : "answer"];
    return answer ? [{ scope, question_id: question.id, answer, label: question.label, key: `${scope}:${question.id}` }] : [];
  })), [data.catalog, data.preferences]);
  const revision = resource.data?.sharing?.revision;
  const savedSelection = JSON.stringify({ revision, keys: (resource.data?.sharing?.mine || []).map(answer => `${answer.scope}:${answer.question_id}`) });
  useEffect(() => { setSelected(JSON.parse(savedSelection).keys); setConsent(false); }, [savedSelection]);
  const save = e => {
    e.preventDefault(); if (!consent || !selected.length) return;
    action.run(() => api.post(path, { answers: answers.filter(answer => selected.includes(answer.key)).map(({ scope, question_id, answer }) => ({ scope, question_id, answer })), expected_revision: revision || 0 }), async () => { setConsent(false); await resource.reload(); });
  };
  const revoke = () => action.run(() => api.delete(path), async () => { setSelected([]); setConsent(false); await resource.reload(); });
  return <Modal title={`Share with ${match.profile.display_name}`} close={close} wide>{resource.error ? <ErrorState error={resource.error} retry={resource.reload} /> : resource.loading && !resource.data ? <Loading>Opening sharing choices…</Loading> : <form className="dating-form" onSubmit={save}><p>Choose the exact answers you would like to share in this connection. Public profiles never show these answers. Intimacy matching and sharing are separate choices.</p><div className="dating-note"><ShieldCheck size={20} /><p>Selected answers become visible only after both people actively choose to share. You can revoke your sharing. Your partner may still remember or retain information they have already seen.</p></div>{answers.length ? <fieldset className="dating-share-choices"><legend>My saved answers</legend>{answers.map(answer => <label className="dating-share-answer" key={answer.key}><input type="checkbox" checked={selected.includes(answer.key)} onChange={e => { setSelected(previous => e.target.checked ? [...previous, answer.key] : previous.filter(key => key !== answer.key)); setConsent(false); }} /><span><span className="dating-private-label">{answer.scope === "intimacy" ? "Intimacy" : "Everyday preference"}</span><strong>{answer.label}</strong><span>{humanLabel(answer.answer)}</span></span></label>)}</fieldset> : <p className="dating-note">You have no saved answers to share. You can add optional answers in <Link to="/dating/profile/personality" onClick={close}>Profile</Link>.</p>}
      <label className="dating-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} data-testid="dating-share-consent" /><span>I choose to share only the selected answers with {match.profile.display_name}, once we have both opted in.</span></label><ActionFeedback action={action} success="Your sharing choices have been updated." /><div className="dating-button-row"><button className="btn-lime dating-button dating-button--dark" disabled={!consent || !selected.length || action.busy} data-testid="dating-share-save">{action.busy ? "Updating…" : "Share selected answers"}</button>{resource.data?.sharing?.mine?.length > 0 && <button type="button" className="btn-ghost dating-button dating-button--quiet" disabled={action.busy} onClick={revoke} data-testid="dating-share-revoke"><X size={17} /> Revoke my sharing</button>}</div>
      <section className="dating-partner-shares"><h3>{match.profile.display_name}’s shared answers</h3>{resource.data?.sharing?.partner?.length > 0 ? resource.data.sharing.partner.map(answer => <div className="dating-partner-answer" key={`${answer.scope}:${answer.question_id}`}><span className="dating-private-label">{answer.scope === "intimacy" ? "Intimacy" : "Everyday preference"}</span><strong>{answer.label}</strong><p>{humanLabel(answer.answer)}</p></div>) : <p className="dating-muted">No answers are visible. Both people must actively choose to share before selected answers appear.</p>}<p className="dating-fine">Sharing an answer is not consent to an activity. Ask, listen and respect a person’s current boundaries.</p></section>
    </form>}</Modal>;
}
