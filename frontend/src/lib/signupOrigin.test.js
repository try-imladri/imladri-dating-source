import { applySignupOrigin, setOrigin } from "./api";
import { storeCouplesInvite, clearCouplesInvite } from "./couplesInvite";
jest.mock("axios", () => ({ create: () => ({ interceptors: { response: { use: jest.fn() } } }) }));
jest.mock("@clerk/react", () => ({ useAuth: jest.fn() }));

test("a signup retains the selected entry page through a failed save and keeps an existing member's attribution", async () => {
  const api = { patch: jest.fn().mockRejectedValueOnce(new Error("temporarily offline"))
    .mockResolvedValueOnce({ data: { origin: "demo" } })
    .mockResolvedValueOnce({ data: { origin: "direct" } }) };
  const fresh = { origin: "", usage: { plan: "none" } };
  setOrigin("demo");
  await expect(applySignupOrigin(api, fresh)).rejects.toThrow("temporarily offline");
  expect(await applySignupOrigin(api, fresh)).toEqual({ ...fresh, origin: "demo" });
  expect(api.patch.mock.calls.slice(0, 2)).toEqual([["/me", { origin: "demo" }], ["/me", { origin: "demo" }]]);
  setOrigin("how-it-works");
  expect(await applySignupOrigin(api, { origin: "home" })).toEqual({ origin: "home" });
  expect((await applySignupOrigin(api, fresh)).origin).toBe("direct");
});

test("an invited signup reports only the entry page and preserves an existing member’s origin", async () => {
  const api = { patch: jest.fn(async (_, body) => ({ data: body })) };
  setOrigin("how-it-works");
  storeCouplesInvite("private-invitation".repeat(3));
  try {
    expect((await applySignupOrigin(api, {})).origin).toBe("couples/invite");
    expect((await applySignupOrigin(api, { origin: "demo" })).origin).toBe("demo");
    expect(api.patch.mock.calls).toEqual([["/me", { origin: "couples/invite" }]]);
  } finally {
    clearCouplesInvite();
  }
});
