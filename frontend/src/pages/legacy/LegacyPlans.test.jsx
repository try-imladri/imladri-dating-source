import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { publicApi } from "@/lib/api";
import Plans from "./LegacyPlans";

jest.mock("react-router-dom", () => {
  global.TextEncoder = require("util").TextEncoder;
  return jest.requireActual("react-router");
});
jest.mock("@/lib/api", () => ({ publicApi: { get: jest.fn() } }));
jest.mock("@/pages/public/catalog", () => ({ useCatalogTitle: jest.fn() }));

let root, host;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  publicApi.get.mockReset();
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
const render = () => act(async () => root.render(<MemoryRouter><Plans /></MemoryRouter>));

test("each API-priced plan summarizes benefits through personality questions, then ends with And many more", async () => {
  publicApi.get.mockResolvedValue({ data: { plans: [
    { id: "plus_100", name: "Plus 100", price_cents: 1200, messages: 100 },
    { id: "plus_250", name: "Plus 250", price_cents: 2000, messages: 250 },
    { id: "plus_500", name: "Plus 500", price_cents: 4000, messages: 500 },
  ] } });
  await render();
  const cards = [...host.querySelectorAll("article")];
  expect(cards).toHaveLength(3);
  for (const [index, card] of cards.entries()) {
    expect(card.querySelector("strong").textContent).toBe(["$12", "$20", "$40"][index]);
    expect(card.textContent).toContain(["100 replies", "250 replies", "500 replies"][index]);
    for (const feature of ["Individual and group coaching", "Personality questionnaires", "Conversation practice", "Tone insights"]) {
      expect(card.textContent).toContain(feature);
    }
    const bullets = [...card.querySelectorAll("li")].map(item => item.textContent);
    expect(bullets).toHaveLength(5);
    expect(bullets.slice(-2)).toEqual(["Personality questionnaires and personal insights", "And many more"]);
    expect(card.textContent).not.toContain("unlimited");
  }
  expect(host.querySelector('[data-testid="plan-inclusion-details"]').textContent).toContain("their own Individual access requires a separate membership");
  expect(host.querySelector('a[href="/billing#message-allowance"]')).not.toBeNull();
  expect(publicApi.get).toHaveBeenCalledWith("/public/plans", expect.objectContaining({ signal: expect.any(AbortSignal) }));
});

test("the cards use the public catalog values and retain retry after a catalog outage", async () => {
  publicApi.get.mockRejectedValueOnce(new Error("offline"));
  await render();
  expect(host.querySelector('[role="alert"]').textContent).toContain("Plans couldn’t load");
  publicApi.get.mockResolvedValueOnce({ data: { plans: [{ id: "current", name: "Current offer", price_cents: 1500, messages: 123 }] } });
  await act(async () => host.querySelector("button").click());
  expect(host.querySelector("strong").textContent).toBe("$15");
  expect(host.querySelector("article").textContent).toContain("123 replies per billing month");
  expect(host.querySelector('[role="alert"]')).toBeNull();
  expect(host.querySelector("article").textContent).toContain("And many more");
});

test("the card-first offer is separately fetched and states its exact trigger and cancellation before charge", async () => {
  publicApi.get.mockImplementation(async path => ({ data: path === "/public/intro-access"
    ? { enabled: true, messages: 10, requires_payment_method: true, automatic_charge: true, authorization_version: "card-first-intro-v1" }
    : { plans: [{ id: "plus_100", name: "Plus 100", price_cents: 1200, messages: 100 }] } }));
  await render();
  const offer = host.querySelector('[data-testid="public-intro-offer"]');
  expect(offer.textContent).toContain("Start with 10 free AI messages");
  expect(offer.textContent).toContain("eligible verified account");
  expect(offer.textContent).toContain("Choose a monthly plan and verify your card first");
  expect(offer.textContent).toContain("After your tenth free AI message is delivered");
  expect(offer.textContent).toContain("Cancel before using all 10");
  expect(offer.textContent).toContain("Monthly plans then renew until canceled");
  expect(offer.textContent).not.toContain("No card is needed");
  expect(offer.querySelector("a").getAttribute("href")).toBe("/sign-up");
  expect(host.querySelector("strong").textContent).toBe("$12");
});

test("an unavailable introductory API hides the offer while paid prices remain available", async () => {
  publicApi.get.mockImplementation(async path => {
    if (path === "/public/intro-access") throw new Error("Unavailable");
    return { data: { plans: [{ id: "plus_100", name: "Plus 100", price_cents: 1200, messages: 100 }] } };
  });
  await render();
  expect(host.querySelector('[data-testid="public-intro-offer"]')).toBeNull();
  expect(host.querySelector("strong").textContent).toBe("$12");
  expect(host.querySelector('[role="alert"]')).toBeNull();
});

test("an incomplete introductory offer cannot promise the card-first introduction", async () => {
  publicApi.get.mockImplementation(async path => ({ data: path === "/public/intro-access"
    ? { enabled: true, messages: 10 }
    : { plans: [{ id: "plus_100", name: "Plus 100", price_cents: 1200, messages: 100 }] } }));
  await render();
  expect(host.querySelector('[data-testid="public-intro-offer"]')).toBeNull();
  expect(host.querySelector("strong").textContent).toBe("$12");
});


test("a historical no-card API response cannot advertise the new automatic-charge flow", async () => {
  publicApi.get.mockImplementation(async path => ({ data: path === "/public/intro-access"
    ? { enabled: true, messages: 10, requires_payment_method: false, automatic_charge: false }
    : { plans: [{ id: "plus_100", name: "Plus 100", price_cents: 1200, messages: 100 }] } }));
  await render();
  expect(host.querySelector('[data-testid="public-intro-offer"]')).toBeNull();
  expect(host.querySelector("strong").textContent).toBe("$12");
});
