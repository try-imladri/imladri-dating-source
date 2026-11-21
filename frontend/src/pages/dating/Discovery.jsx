import { useState } from "react";
import { Link } from "react-router-dom";
import { Compass, Heart, SlidersHorizontal, CheckCircle2 } from "lucide-react";
import { useDating, useDatingAction, useDatingResource } from "@/components/dating/DatingContext";
import { ActionFeedback, EmptyState, ErrorState, Loading, MemberProfileModal, PageHeading, ProfileCard } from "@/components/dating/DatingUI";

function IntroductionList({ likes = false }) {
  const { api, data: bootstrap, refresh } = useDating();
  const resource = useDatingResource(likes ? "/dating/likes" : "/dating/discovery");
  const action = useDatingAction();
  const [selected, setSelected] = useState(null), [matched, setMatched] = useState(null);
  const react = (profile, choice) => action.run(() => api.post("/dating/interests", { profile_id: profile.id, choice }), async response => {
    setSelected(null);
    if (response.data.matched) setMatched({ id: response.data.match_id, name: profile.display_name });
    await resource.reload(); await refresh();
  });
  const state = bootstrap.profile?.state;
  const availability = resource.data?.availability;
  const readyProfile = state === "active";
  return <>
    <PageHeading eyebrow={likes ? "An independent choice, on both sides" : "A little more understanding"} title={likes ? "Your likes" : "Discover"} action={<Link className="btn-ghost dating-button dating-button--quiet" to="/dating/profile/preferences"><SlidersHorizontal size={17} /> Your preferences</Link>}>{likes ? "People who have expressed interest. Choose whether you would like to connect." : "Relevant people, understandable reasons, and space to make up your own mind."}</PageHeading>
    {matched && <div className="dating-mutual" role="status"><span className="dating-mutual__icon"><Heart size={24} fill="currentColor" /></span><div><strong>You both chose to connect.</strong><p>You and {matched.name} can start a conversation.</p></div><Link className="btn-lime dating-button dating-button--dark" to={`/dating/messages/${encodeURIComponent(matched.id)}`}>Open conversation</Link><button className="dating-text-button" onClick={() => setMatched(null)}>Dismiss</button></div>}
    <ActionFeedback action={action} />
    {!readyProfile && <div className="dating-status-panel"><CheckCircle2 size={22} /><div><strong>{state === "pending" ? "Your profile is with our review team." : state === "paused" ? "Your dating profile is paused." : "A thoughtful introduction starts with your profile."}</strong><p>{state === "pending" ? "You will become discoverable only after the required review. You can keep refining your preferences while you wait." : state === "paused" ? "You are not appearing in new introductions. You can manage your visibility from Profile." : "Add your essentials and photos, then preview and submit your profile."}</p></div><Link className="dating-text-button" to="/dating/profile">Open my profile</Link></div>}
    {resource.error ? <ErrorState error={resource.error} retry={resource.reload} /> : resource.loading && !resource.data ? <Loading>Finding your introductions…</Loading> : resource.data?.items?.length ? <><div className="dating-results-note"><span>{resource.data.items.length} {likes ? "people to consider" : "introductions to explore"}</span><span>Only what each member chooses to show.</span></div><div className="dating-discovery-grid">{resource.data.items.map(profile => <ProfileCard key={profile.id} profile={profile} onOpen={setSelected} onLike={p => react(p, "like")} onPass={p => react(p, "pass")} busy={action.busy} />)}</div></> : <EmptyState icon={likes ? Heart : Compass} eyebrow={likes ? "Room for the right conversation" : `${bootstrap.launch?.city || "Your city"} · Founding community`} title={likes ? "Interest starts with an introduction." : readyProfile ? "Your community is taking shape." : "Your next chapter starts here."} action={<Link to={likes && readyProfile ? "/dating" : "/dating/profile"} className="btn-lime dating-button dating-button--dark">{likes && readyProfile ? "Explore introductions" : "Review my profile"}</Link>}><p>{availability?.message || (likes ? "When someone eligible expresses interest, you can consider their profile here. A conversation opens only when you both choose it." : readyProfile ? "We’re inviting real people in your area. We will show relevant, eligible members when they are available. We never fill your feed with demo people." : "Complete your essentials and publish your profile after review. Your earlier Imladri records stay private.")}</p></EmptyState>}
    {!likes && <section className="dating-understanding-strip"><div><h3>Who catches your eye</h3><p>Attraction is personal. You always make the choice.</p></div><div><h3>What matters between you</h3><p>Understand shared preferences and differences worth discussing.</p></div><div><h3>A real conversation</h3><p>Independent interest first. Then a chance to meet.</p></div></section>}
    {selected && <MemberProfileModal profile={selected} close={() => setSelected(null)} onInterest={p => react(p, "like")} onPass={p => react(p, "pass")} onReported={resource.reload} />}
  </>;
}

export const Discover = () => <IntroductionList />;
export const Likes = () => <IntroductionList likes />;
