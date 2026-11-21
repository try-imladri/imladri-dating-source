import { useEffect, useRef, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { UserProfile } from "@clerk/react";
import { toast } from "sonner";
import { ShieldCheck, FileText, Sparkles, Database, CheckCircle2 } from "lucide-react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import ContentImpact from "@/components/app/ContentImpact";
import { clearPracticeTurn } from "@/lib/practiceTurn";
import { clearStartDraft } from "@/lib/startDraft";
import AccountClosure from "@/components/app/AccountClosure";
import { currentPlanName } from "@/components/app/PlanUsage";
import { RecordExportPreference } from "@/components/app/RecordExportPreference";
import AnswerHistoryPreference from "@/components/app/AnswerHistoryPreference";
import QuestionnaireResearchPreference from "@/components/app/QuestionnaireResearchPreference";
import ToneResearchPreference from "@/components/app/ToneResearchPreference";
import YourTypeData from "@/components/app/YourTypeData";
import { IntimacyResearch } from "@/components/app/IntimacyResearch";
import { CouplesAssessmentSharing } from "./CouplesCompatibility";
import CouplesDataPreferences from "@/components/app/CouplesDataPreferences";
import { PrivacyPurposeNavigation, SETTINGS_APPEARANCE, useSettingsNavigation } from "./settings/SettingsNavigation";

export const resize = (file, max = 256) => new Promise((resolve, reject) => {
  const img = new Image();
  const url = URL.createObjectURL(file);
  const fail = () => reject(new Error("This photo couldn’t be opened. Choose another image."));
  img.onerror = () => { URL.revokeObjectURL(url); fail(); };
  img.onload = () => {
    try {
      const c = document.createElement("canvas");
      const s = Math.min(1, max / Math.max(img.width, img.height));
      c.width = Math.max(1, Math.round(img.width * s));
      c.height = Math.max(1, Math.round(img.height * s));
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      resolve(c.toDataURL("image/jpeg", 0.8));
    } catch { fail(); }
    finally { URL.revokeObjectURL(url); }
  };
  img.src = url;
});

export function AvatarPicker({ value, onChange }) {
  return (
    <div className="flex items-center gap-4" data-testid="avatar-picker">
      <div className="w-16 h-16 rounded-full bg-[var(--surface-2)] border border-[var(--line)] overflow-hidden shrink-0">{value && <img src={value} alt="avatar" className="w-full h-full object-cover" />}</div>
      <Input type="file" accept="image/*" onChange={async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        try { onChange(await resize(file)); } catch (error) { toast.error(error.message); }
      }} data-testid="avatar-input" />
    </div>
  );
}

function Row({ I, title, children, testid }) {
  return (
    <div className="card p-5" data-testid={testid}>
      <p className="font-semibold flex items-center gap-2 mb-2"><I className="w-4 h-4" />{title}</p>
      <div className="text-xs text-[var(--muted)] space-y-2">{children}</div>
    </div>
  );
}

function TrendPreference({ me, setMe, api }) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  async function change(event) {
    const enabled = event.target.checked;
    setPending(true);
    setMessage("");
    try {
      const { data } = await api.patch("/me/trends-consent", { enabled, expected_member: me.user_id });
      setMe(previous => previous?.user_id === me.user_id ? { ...previous, everyday_trends_opt_in: data.enabled } : previous);
      setMessage(data.enabled ? "Your choices can now contribute to group statistics." : "Your choices are excluded when reports are next loaded.");
    } catch {
      setMessage("Your preference couldn’t be saved. Please try again.");
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="space-y-2 border-t border-[var(--line)] pt-3">
      <label className="flex items-start gap-2 text-sm text-[var(--ink)] font-semibold">
        <input type="checkbox" className="mt-1 shrink-0" data-testid="settings-trends-opt-in" aria-describedby="settings-trends-description"
          disabled={pending} checked={me.everyday_trends_opt_in === true} onChange={change} />
        Include my choices in group statistics
      </label>
      <p id="settings-trends-description">This includes three everyday quiz choices about speaking up, recharging and weekend plans, plus the follow-up result you select after a read. Reports use fixed choices only. They show a breakdown when each represented choice has at least 10 participating members. You can turn this off any time.</p>
      {message && <p role="status">{message}</p>}
    </div>
  );
}

function DataPrivacy({ me, setMe, api, purpose, onSelect }) {
  return <div className="settings-pane" data-testid="ai-disclosure">
    <h2 className="font-display font-black uppercase text-2xl">Data &amp; privacy</h2>
    {purpose === "overview" && <div id="data-permissions" className="mt-3 space-y-2 text-sm text-[var(--muted)]">
      <p>Your welcome screen explains saving, personalized advice and optional data uses in one place. Change any optional permission below, or withdraw sharing with your current partner. These controls stay available before you complete the welcome screen.</p>
      <p>Imladri sends your question and relevant details from your profiles and conversations to DeepSeek to generate advice. For screenshots, DeepSeek reads the preview you choose to share. Imladri saves the conversation text you submit and the advice you receive.</p>
      <p data-testid="settings-about-note">Saved answers, traits and patterns can inform coaching when they are relevant. Open “Based on” on a saved read to see which details it used. You can review or change what you’ve shared in <Link to="/app/personal" className="underline">Personal</Link>.</p>
    </div>}
    <PrivacyPurposeNavigation purpose={purpose} onSelect={onSelect} />
    <div className="text-sm text-[var(--muted)] space-y-4" data-testid={`settings-privacy-${purpose}`}>
      {purpose === "overview" && <>
        <h3 className="font-semibold text-[var(--ink)]">Choose the purpose you want to manage</h3>
        <p>Each permission is separate. Opening a section does not turn anything on. Review the notice beside a choice before changing it; you can withdraw a permission from the same section.</p>
        <p>Saving questionnaire answers to your account is always available. Research, staff access and partner sharing have their own controls. Download or clear your app records under Your data.</p>
      </>}
      {purpose === "questionnaires" && (me.role === "member"
        ? <QuestionnaireResearchPreference key={`all-${me.user_id}`} me={me} setMe={setMe} api={api} />
        : <p data-testid="settings-member-only-questionnaires">Questionnaire research participation is available only to member accounts. This account has no participation setting to manage here.</p>)}
      {purpose === "exports" && (me.role === "member" ? <>
        <RecordExportPreference key={`rows-member-${me.user_id}`} scope="member" me={me} setMe={setMe} api={api} />
        <RecordExportPreference key={`rows-couple-${me.user_id}-${me.research_exports?.couple?.pair_id || ""}`} scope="couple" me={me} setMe={setMe} api={api} />
      </> : <p data-testid="settings-member-only-exports">Research download permissions are available only to member accounts. Staff access to research reports does not opt this account into those downloads.</p>)}
      {purpose === "history" && <>
        <h3 className="font-semibold text-[var(--ink)]">Questionnaire</h3>
        <p className="flex items-center gap-2 font-semibold text-[var(--ink)]" data-testid="questionnaire-saving-status"><CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />Answer saving is always on</p>
        <p>Your saved questionnaire answers and answer history are kept in your account, including Individual and Couples assessments. You do not need to submit a separate research contribution to keep your answers.</p>
        <p>Unfinished work stays available for you to resume. Private Couples drafts are not shared with your partner or included in staff answer-history reports.</p>
        <p>Research participation and staff access are separate from saving your own answers.</p>
        <AnswerHistoryPreference key={`answer-history-${me.user_id}`} api={api} me={me} setMe={setMe} />
      </>}
      {purpose === "tone" && (me.role === "member"
        ? <ToneResearchPreference key={`tone-${me.user_id}`} me={me} setMe={setMe} api={api} />
        : <p data-testid="settings-member-only-tone">Conversation tone research participation is available only to member accounts. This account has no participation setting to manage here.</p>)}
      {purpose === "your-type" && <YourTypeData key={`your-type-${me.user_id}`} api={api} owner={me.user_id} />}
      {purpose === "trends" && <TrendPreference key={me.user_id} me={me} setMe={setMe} api={api} />}
      {purpose === "partner" && (me.role === "member" ? <>
        <p>After each partner agrees, assessment sharing stays on for your current pair until changed. Your partner sees only answers and results you choose to share. Private Couples drafts and unsaved edits are excluded.</p>
        <CouplesDataPreferences key={`couples-data-${me.user_id}`} api={api} me={me} setMe={setMe} />
        <CouplesAssessmentSharing key={`assessment-sharing-${me.user_id}`} api={api} me={me} />
      </> : <p>Partner sharing controls are available in your member workspace.</p>)}
    </div>
  </div>;
}

export default function Settings() {
  const { me, setMe, api } = useOutletContext();
  const navigation = useSettingsNavigation();
  const [exporting, setExporting] = useState(false);
  const pendingExport = useRef(null);
  const pendingWipe = useRef(null);
  useEffect(() => {
    setExporting(false);
    return () => {
      pendingExport.current?.abort();
      pendingExport.current = null;
      pendingWipe.current?.abort();
      pendingWipe.current = null;
    };
  }, [me.user_id]);
  const exportData = async () => {
    if (pendingExport.current) return;
    const controller = new AbortController();
    pendingExport.current = controller;
    setExporting(true);
    try {
      const { data } = await api.get("/me/export", { responseType: "blob", signal: controller.signal, headers: {"X-Settings-Member": me.user_id} });
      if (controller.signal.aborted) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(data);
      a.download = "imladri-export.json";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    } catch {
      if (!controller.signal.aborted) toast.error("Your export could not be downloaded. Please try again.");
    } finally {
      if (pendingExport.current === controller) {
        pendingExport.current = null;
        setExporting(false);
      }
    }
  };
  const wipe = async () => {
    if (pendingWipe.current) return;
    const controller = new AbortController();
    pendingWipe.current = controller;
    try {
      await api.delete("/me", {signal: controller.signal, headers: {"X-Settings-Member": me.user_id}});
      if (controller.signal.aborted) return;
      clearStartDraft(); clearPracticeTurn();
      toast.success("Earlier tools content cleared");
      window.location.href = "/app/start";
    } catch {
      if (!controller.signal.aborted) toast.error("Your content could not be cleared. Please try again.");
    } finally {
      if (pendingWipe.current === controller) pendingWipe.current = null;
    }
  };
  return (
    <div className="settings-workspace space-y-5" data-testid="settings-page">
      <h1 className="font-display font-black uppercase text-4xl">Settings</h1>
      <section data-testid="settings-account" className="min-w-0">
        <p className="text-sm text-[var(--muted)] mb-4">Manage your profile, sign-in security and app preferences from the menu.</p>
        {navigation.pane === "security" && <p className="text-sm text-[var(--muted)] mb-4" data-testid="settings-password-help">When adding or updating a password, use the eye icon beside each field to show or hide what you type. Your existing password cannot be displayed.</p>}
        <div className="clerk-profile">{!navigation.legacy && <UserProfile key={me.user_id} routing="hash" appearance={SETTINGS_APPEARANCE}>
          <UserProfile.Page label="account" />
          <UserProfile.Page label="security" />
          <UserProfile.Page label="Plan" url="plan" labelIcon={<Sparkles size={16} />}>
            <div className="settings-pane"><Row I={Sparkles} title="Your plan" testid="settings-plan">
          <p><span className="text-[var(--ink)] font-semibold">{currentPlanName(me.usage, me.membership_plan_id) || (me.usage?.plan === "plus" ? "Awaiting billing confirmation" : "No active subscription")}</span> · <Link to="/app/settings/upgrade" className="underline" data-testid="settings-plan-link">View plans</Link></p>
        </Row></div>
          </UserProfile.Page>
          <UserProfile.Page label="Data & privacy" url="privacy" labelIcon={<ShieldCheck size={16} />}>
            <DataPrivacy key={me.user_id} me={me} setMe={setMe} api={api} purpose={navigation.purpose} onSelect={navigation.selectPurpose} />
          </UserProfile.Page>
          <UserProfile.Page label="Your data" url="data" labelIcon={<Database size={16} />}>
        <div className="settings-pane card p-5 space-y-3" data-testid="settings-data-actions">
          <p className="font-display font-black uppercase text-lg leading-none">Your data</p>
          <p className="text-xs text-[var(--muted)]">Download your app records, including group conversations, quiz answers, any submitted research contribution and billing history.</p>
          <div className="flex flex-wrap gap-2">
          <button onClick={exportData} disabled={exporting} className="btn-ghost px-5 py-2 text-sm" data-testid="settings-export">{exporting ? "Preparing export…" : "Export everything"}</button>
          <AlertDialog key={`clear-${me.user_id}`}>
            <AlertDialogTrigger asChild><button className="px-5 py-2 text-sm rounded-full border border-[var(--crimson)] text-[var(--crimson)]" data-testid="settings-delete-all">Clear saved content</button></AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader><AlertDialogTitle>Clear earlier tools content?</AlertDialogTitle><AlertDialogDescription>This permanently erases your earlier profiles, AI conversations, group chats, memories, quiz answers, research contribution and advice. Manage your dating profile, matches and messages in Dating → Profile. Your login, reply allowance and any paid subscription stay active.</AlertDialogDescription></AlertDialogHeader>
              <ContentImpact api={api} action="clear" />
              <AlertDialogFooter><AlertDialogCancel>Keep content</AlertDialogCancel><AlertDialogAction onClick={wipe} data-testid="settings-delete-confirm">Clear content</AlertDialogAction></AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <AccountClosure key={`close-${me.user_id}`} memberId={me.user_id} api={api} closing={me.closing} />
          </div>
          <details className="pt-2 text-sm" data-testid="settings-earlier-contribution">
            <summary className="cursor-pointer underline">Manage an earlier research contribution</summary>
            <IntimacyResearch key={`research-${me.user_id}`} api={api} />
          </details>
        </div>
          </UserProfile.Page>
          <UserProfile.Page label="Legal & help" url="legal" labelIcon={<FileText size={16} />}>
            <div className="settings-pane"><Row I={FileText} title="Legal & help" testid="settings-legal">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <Link to="/how-it-works" className="underline" data-testid="settings-link-how">How it works</Link>
            <Link to="/privacy" className="underline" data-testid="settings-link-privacy">Privacy policy</Link>
            <Link to="/terms" className="underline" data-testid="settings-link-terms">Terms</Link>
            <Link to="/support" className="underline" data-testid="settings-link-support">Support</Link>
          </div>
        </Row></div>
          </UserProfile.Page>
        </UserProfile>}</div>
      </section>
    </div>
  );
}
