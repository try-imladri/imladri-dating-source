import { useEffect } from "react";
import { useAuth, useClerk } from "@clerk/react";
import { hasBrowserSession, rememberPreference } from "../../lib/browserSession";

// "Remember me" off: end the session once the browser has been closed.
export function RememberGuard() {
  const { isSignedIn } = useAuth();
  const { signOut } = useClerk();
  useEffect(() => {
    if (isSignedIn && !rememberPreference() && !hasBrowserSession()) {
      signOut({ redirectUrl: "/sign-in" });
    }
  }, [isSignedIn, signOut]);
  return null;
}
