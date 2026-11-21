import { act } from "react";
import { createRoot } from "react-dom/client";
import { useSignUp } from "@clerk/react/legacy";
import { Navigate } from "react-router-dom";
import { useConfig } from "@/components/public/PublicLayout";
import { pendingCouplesInvite } from "@/lib/couplesInvite";
import RegistrationEntry from "./RegistrationEntry";
import { publicApi } from "@/lib/api";

jest.mock("@clerk/react/legacy", () => ({ useSignUp: jest.fn() }), { virtual: true });
jest.mock("react-router-dom", () => ({ Navigate: jest.fn(() => null) }), { virtual: true });
jest.mock("@/components/public/PublicLayout", () => ({ useConfig: jest.fn() }));
jest.mock("@/lib/couplesInvite", () => ({ pendingCouplesInvite: jest.fn() }));
jest.mock("@/lib/api", () => ({ publicApi: { post: jest.fn() } }));

test("only fresh signup attempts are routed to a closed registration waitlist", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  const root = createRoot(host);
  const render = () => act(async () => root.render(<RegistrationEntry>Registration form</RegistrationEntry>));
  useSignUp.mockReturnValue({ isLoaded: true, signUp: {} });
  try {
    useConfig.mockReturnValue({});
    await render();
    expect(host.textContent).toContain("Checking registration");
    const retry = jest.fn();
    useConfig.mockReturnValue({ configError: true, retry });
    await render();
    host.querySelector("button").click();
    expect(retry).toHaveBeenCalled();
    useConfig.mockReturnValue({ waitlist_mode: true });
    await render();
    expect(Navigate.mock.calls.at(-1)[0]).toMatchObject({ to: "/waitlist?origin=sign-up", replace: true });
    for (const path of ["/sign-up?__clerk_ticket=", "/sign-up#__clerk_ticket=bogus"]) {
      window.history.replaceState(null, "", path);
      await render();
      expect(host.textContent).not.toContain("Registration form");
    }
    pendingCouplesInvite.mockReturnValue("bogus");
    publicApi.post.mockResolvedValue({ data: { eligible: false } });
    await render();
    expect(host.textContent).not.toContain("Registration form");
    pendingCouplesInvite.mockReturnValue("valid");
    publicApi.post.mockResolvedValue({ data: { eligible: true } });
    await render();
    expect(host.textContent).toBe("Registration form");
    pendingCouplesInvite.mockReturnValue("");
    useSignUp.mockReturnValue({ isLoaded: true, signUp: { id: "already-verifying" } });
    await render();
    expect(host.textContent).toBe("Registration form");
    useSignUp.mockReturnValue({ isLoaded: true, signUp: {} });
    useConfig.mockReturnValue({ waitlist_mode: false });
    await render();
    expect(host.textContent).toBe("Registration form");
  } finally { await act(async () => root.unmount()); delete global.IS_REACT_ACT_ENVIRONMENT; }
});
