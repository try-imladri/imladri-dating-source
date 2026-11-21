import { createContext, useContext, useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { ClerkProvider } from "@clerk/react";
import { dark } from "@clerk/ui/themes";
import { useTheme } from "@/lib/theme";
import { BACKEND_URL } from "@/lib/api";
import { RememberGuard } from "@/components/auth/RememberGuard";
import { readClerkRuntimeConfig } from "@/lib/clerkRuntimeConfig";
import { preloadRoute } from "@/lib/routePages";

const ClerkAvailable = createContext(false);
export const useClerkAvailable = () => useContext(ClerkAvailable);

export function RequireClerk() {
  if (useClerkAvailable()) return <Outlet />;
  return (
    <div className="p-6">
      <p>Sign-in is unavailable right now. Refresh the page to try again.</p>
      <Link to="/" className="underline">Back to home</Link>
    </div>
  );
}

// Brand Clerk here once: it styles sign-in, sign-up and the user menu.
const appearanceFor = (mode) => mode === "dark" ? {
  theme: dark,
  captcha: { theme: "dark" },
  variables: {
    colorPrimary: "#FAFAFA",
    colorPrimaryForeground: "#000000",
    colorBackground: "#0E0E0E",
    colorForeground: "#FAFAFA",
    colorInput: "#1A1A1A",
    colorInputForeground: "#FAFAFA",
    colorBorder: "#262626",
    fontFamily: "'Plus Jakarta Sans', sans-serif",
    borderRadius: "0.75rem",
  },
} : {
  variables: {
    colorPrimary: "#0A0A0A",
    colorPrimaryForeground: "#FFFFFF",
    colorBackground: "#FFFFFF",
    colorForeground: "#0A0A0A",
    fontFamily: "'Plus Jakarta Sans', sans-serif",
    borderRadius: "0.75rem",
  },
};

// Starts Clerk from the backend's runtime config, so deploy's live keys need no rebuild.
// Must render inside <BrowserRouter> (see step 3) so Clerk navigates without reloading.
export default function ClerkAuthProvider({ children }) {
  const { pathname } = useLocation();
  if (!/^\/(?:dating|legacy|app|admin|upgrade|sign-in|sign-up|sso-callback)(?:\/|$)/.test(pathname)) return children;
  return <ClerkSession>{children}</ClerkSession>;
}

// Clerk changes only this link on public catalog pages, so loading the provider cannot reset a demo or form.
export function PublicSessionAction({ children, fallback }) {
  const { pathname } = useLocation();
  const available = useClerkAvailable();
  if (available) return children;
  if (!/^\/(?:$|(?:learn|types|figures|plans|waitlist)(?:\/|$))/.test(pathname)) return fallback;
  return <ClerkSession fallback={fallback}>{children}</ClerkSession>;
}

function ClerkSession({ children, fallback }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { theme } = useTheme();
  const [config, setConfig] = useState(null);
  // Download the destination's code while Clerk establishes the session.
  useEffect(() => { void preloadRoute(pathname); }, [pathname]);

  // Retries while the backend restarts (e.g. right after provisioning): the app's
  // Clerk components crash if they ever render without <ClerkProvider>.
  useEffect(() => {
    let cancelled = false;
    let retry;
    const reconnectUntil = Date.now() + 120_000;
    const load = async (attempt = 0) => {
      let reconnecting = false;
      try {
        const response = await readClerkRuntimeConfig(`${BACKEND_URL}/api/clerk/config`);
        reconnecting = response.reconnecting;
        if (response.ok) {
          const cfg = response.config;
          if (cancelled) return;
          if (cfg?.enabled && typeof cfg.publishable_key === "string" && cfg.publishable_key) {
            setConfig(cfg);
            return;
          }
          if (cfg?.enabled === false) setConfig(false);
        }
      } catch {}
      if (cancelled) return;
      const delay = reconnecting ? 3000 : 1500;
      if ((!reconnecting && attempt >= 20) || Date.now() + delay > reconnectUntil) return setConfig(false);
      retry = setTimeout(() => load(attempt + 1), delay);
    };
    load();
    return () => { cancelled = true; clearTimeout(retry); };
  }, []);

  if (config === null) return fallback ?? <p className="p-6 text-sm text-[var(--muted)]" role="status">Connecting to sign-in…</p>;
  if (config === false) return fallback ?? children;
  return (
    <ClerkProvider
      publishableKey={config.publishable_key}
      proxyUrl={config.proxy_url || undefined}
      appearance={{ ...appearanceFor(theme), layout: { unsafe_disableDevelopmentModeWarnings: true } }}
      routerPush={(to) => navigate(to)}
      routerReplace={(to) => navigate(to, { replace: true })}
      signInUrl="/sign-in"
      signUpUrl="/sign-up"
      signInFallbackRedirectUrl="/dating"
      signUpFallbackRedirectUrl="/dating"
      afterSignOutUrl="/"
    >
      <ClerkAvailable.Provider value={true}>
        {children}
        <RememberGuard />
      </ClerkAvailable.Provider>
    </ClerkProvider>
  );
}
