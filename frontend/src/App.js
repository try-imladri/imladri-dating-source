import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import ClerkAuthProvider, { RequireClerk } from "@/components/auth/ClerkAuthProvider";
import { ThemeProvider, useTheme } from "@/lib/theme";
import PublicLayout from "@/components/public/PublicLayout";
import RegistrationEntry from "@/components/auth/RegistrationEntry";
import Home from "@/pages/public/Home";
import { AuthFormRoute, SignInPage, SignUpPage } from "@/pages/public/AuthPages";
import { LegacyGoogleReturn, PersonalEntry, ProviderAuthContinuation } from "@/components/auth/PersonalEntry";
import AppShell from "@/components/app/AppShell";
import { RouteIndexing } from "@/pages/public/publicHead";

import { deferredPage } from "@/lib/routePages";

const Plans = deferredPage("plans");
const DatingApp = deferredPage("dating");
const LegacyHub = deferredPage("legacy");
const LegacyPlans = deferredPage("legacyPlans");
const LegacyHowItWorks = deferredPage("static", "LegacyHowItWorks");
const Learn = deferredPage("learn");
const Types = deferredPage("types");
const ArchetypePage = deferredPage("archetype");
const Figures = deferredPage("figures");
const FigurePage = deferredPage("figure");
const Waitlist = deferredPage("waitlist");
const About = deferredPage("static", "About");
const Billing = deferredPage("static", "Billing");
const Privacy = deferredPage("static", "Privacy");
const Terms = deferredPage("static", "Terms");
const HowItWorks = deferredPage("static", "HowItWorks");
const Support = deferredPage("support");
const Dashboard = deferredPage("people");
const Overview = deferredPage("overview");
const PersonalRoute = deferredPage("personal", "PersonalRoute");
const IntimacyPage = deferredPage("intimacy");
const YourTypePage = deferredPage("yourType");
const PersonPage = deferredPage("person");
const Settings = deferredPage("settings");
const Upgrade = deferredPage("upgrade");
const Start = deferredPage("start");
const QuickAdvice = deferredPage("quickAdvice");
const Practice = deferredPage("practice");
const Couples = deferredPage("couples", "Couples");
const CouplesOverview = deferredPage("couples", "CouplesOverview");
const ConversationCards = deferredPage("couples", "ConversationCards");
const CouplesInviteEntry = deferredPage("couplesPartner", "CouplesInviteEntry");
const JoinCouples = deferredPage("couplesPartner", "JoinCouples");
const CouplesPartner = deferredPage("couplesPartner", "CouplesPartner");
const CouplesAnswers = deferredPage("couplesAnswers", "CouplesAnswers");
const CouplesPersonal = deferredPage("couplesPersonal", "CouplesPersonal");
const CouplesCompatibility = deferredPage("couplesCompatibility", "CouplesCompatibility");
const CouplesTogether = deferredPage("couplesTogether", "CouplesTogether");
const CouplesAgreements = deferredPage("couplesTogether", "CouplesAgreements");
const CouplesLearnings = deferredPage("couplesTogether", "CouplesLearnings");
const Admin = deferredPage("admin");

function ThemedToaster() {
  const { theme } = useTheme();
  return <Toaster theme={theme} position="top-center" />;
}

export default function App() {
  return (
    <ThemeProvider>
    <BrowserRouter>
      <RouteIndexing />
      <ClerkAuthProvider>
        <Routes>
          <Route path="/couples/invite" element={<CouplesInviteEntry />} />
          <Route element={<PublicLayout />}>
            <Route path="/" element={<Home />} />
            <Route path="/plans" element={<Plans />} />
            <Route path="/learn" element={<Learn />} />
            <Route path="/legacy" element={<LegacyHub />} />
            <Route path="/legacy/plans" element={<LegacyPlans />} />
            <Route path="/legacy/how-it-works" element={<LegacyHowItWorks />} />
            <Route path="/types" element={<Types />} />
            <Route path="/types/:slug" element={<ArchetypePage />} />
            <Route path="/figures" element={<Figures />} />
            <Route path="/figures/:slug" element={<FigurePage />} />
            <Route path="/waitlist" element={<Waitlist />} />
            <Route path="/about" element={<About />} />
            <Route path="/privacy" element={<Privacy />} />
            <Route path="/terms" element={<Terms />} />
            <Route path="/billing" element={<Billing />} />
            <Route path="/support" element={<Support />} />
            <Route path="/how-it-works" element={<HowItWorks />} />
            <Route element={<RequireClerk />}>
              <Route path="/sign-in" element={<AuthFormRoute signedInPath="/dating"><SignInPage /></AuthFormRoute>} />
              <Route path="/sign-in/*" element={<ProviderAuthContinuation />} />
              <Route path="/sign-up" element={<AuthFormRoute signedInPath="/dating"><RegistrationEntry><SignUpPage /></RegistrationEntry></AuthFormRoute>} />
              <Route path="/sign-up/*" element={<ProviderAuthContinuation signUp />} />
            </Route>
            <Route path="/login" element={<Navigate to="/sign-in" replace />} />
            <Route path="/signup" element={<Navigate to="/sign-up" replace />} />
          </Route>
          <Route element={<RequireClerk />}>
            <Route path="/dating/*" element={<DatingApp />} />
            <Route path="/sso-callback" element={<LegacyGoogleReturn />} />
            <Route path="/app" element={<PersonalEntry><AppShell /></PersonalEntry>}>
              <Route index element={<Overview />} />
              <Route path="people" element={<Dashboard />} />
              <Route path="personal" element={<PersonalRoute />} />
              <Route path="personality" element={<Navigate to="/app/personal?tab=results" replace />} />
              <Route path="intimacy" element={<IntimacyPage />} />
              <Route path="your-type" element={<YourTypePage />} />
              <Route path="about" element={<Navigate to="/app/personal" replace />} />
              <Route path="start" element={<Start />} />
              <Route path="quick-advice" element={<QuickAdvice />} />
              <Route path="practice" element={<Practice />} />
              <Route path="couples" element={<Couples />}>
                <Route index element={<CouplesOverview />} />
                <Route path="personal" element={<CouplesPersonal />} />
                <Route path="quick-advice" element={<QuickAdvice withinCouples />} />
                <Route path="personality" element={<Navigate to="/app/couples/personal?tab=results" replace />} />
                <Route path="intimacy" element={<IntimacyPage withinCouples />} />
                <Route path="compatibility" element={<CouplesCompatibility />} />
                <Route path="answers" element={<CouplesAnswers />} />
                <Route path="together" element={<CouplesTogether />} />
                <Route path="agreements" element={<CouplesAgreements />} />
                <Route path="what-works" element={<CouplesLearnings />} />
                <Route path="partner" element={<CouplesPartner />} />
                <Route path="join" element={<JoinCouples />} />
                <Route path="conversation-cards" element={<ConversationCards />} />
                <Route path="*" element={<Navigate to="/app/couples" replace />} />
              </Route>
              <Route path="person/:id" element={<PersonPage />} />
              <Route path="settings" element={<Settings />} />
              <Route path="settings/upgrade" element={<Upgrade />} />
              <Route path="upgrade" element={<Upgrade />} />
            </Route>
            <Route path="/upgrade" element={<Navigate to="/app/settings/upgrade" replace />} />
            <Route path="/admin" element={<AppShell admin />}>
              <Route index element={<Admin />} />
              <Route path="tiers" element={<Navigate to="/admin/users" replace />} />
              <Route path="couples-pilot" element={<Navigate to="/admin/couples" replace />} />
              <Route path="couples/pilot" element={<Navigate to="/admin/couples" replace />} />
              <Route path=":section" element={<Admin />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <ThemedToaster />
      </ClerkAuthProvider>
    </BrowserRouter>
    </ThemeProvider>
  );
}
