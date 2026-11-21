import { act } from "react";
import { createRoot } from "react-dom/client";
import CouplesAnalytics from "./CouplesAnalytics";

jest.mock("@/lib/api", () => ({ errMsg: e => e.message }));

test("the Couples report separates active pairs, conversions, scheduled memberships and paid years", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const rate = { count: 0, eligible: 0 };
  const report = {
    days: 7, start: "2026-11-02", end: "2026-11-08", completion: rate, pairing: rate, mutual: rate, adoption: rate, return: rate,
    observed_so_far: { completion: {count: 1, eligible: 8}, pairing: {count: 3, eligible: 4},
      mutual: {count: 4, eligible: 8}, adoption: {count: 2, eligible: 5}, return: {count: 0, eligible: 1} },
    milestones: {}, reviews: {}, outcomes: { available: false, minimum: 10 },
    scheduled_returns: {due: 3, pending: 2, eligible: 1, returned: 1, both_returned: 0, both_shared: 0,
      observed_so_far: {eligible: 3, returned: 2, both_returned: 1, both_shared: 1}},
    card_funnel: [{ id: "repair", name: "After a disagreement", started: 6, from_card: 4, from_sample: 2, mutual: 5, agreed: 3, reviewed: 1 }],
    shared_features: { active_pairs: 11, counts: { prompt: 7, moment: 4, positive: 2, learning: 1 } },
    assessment_sharing: { active_pairs: 11, any_sharing_pairs: 6, personality_mutual_pairs: 4, intimacy_mutual_pairs: 2,
      answers: { private: "Private answer must never appear" } },
    connections: { invitations_accepted: 9, invitations_expired: 6, invitations_canceled: 3, pairs_ended: 2 },
    membership: { sponsored_pairs: 11, partner_upgrades: 8, annual_offers: 6, annual_authorizations: 9, annual_payments: 4 },
  };
  const api = { get: jest.fn(async () => ({ data: report })) };
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    await act(async () => root.render(<CouplesAnalytics api={api} days={7} />));
    const cards = [...host.querySelectorAll('[data-testid="couples-admin-funnel"] details')]
      .filter(node => node.querySelector("summary").textContent === "Completed windows");
    expect(cards.map(node => node.parentElement.querySelector(".text-2xl").textContent)).toEqual([
      "1 / 8 observed so far", "3 / 4 observed so far", "4 / 8 observed so far", "2 / 5 observed so far", "0 / 1 observed so far",
    ]);
    expect(cards.every(node => !node.open && node.textContent.includes("0 / 0"))).toBe(true);
    const reviews = [...host.querySelectorAll("h3")].find(el => el.textContent === "Return for the first scheduled check-in").parentElement;
    expect(reviews.textContent).toContain("3 due so far · 2 with a return · 1 with both partners returning");
    expect(reviews.querySelector("details").textContent).toContain("1 completed · 1 with a return");
    const section = host.querySelector('[data-testid="couples-shared-features"]');
    const card = host.querySelector('[data-testid="couples-card-funnel"] tbody tr');
    expect([...card.children].map(cell => cell.textContent)).toEqual(["After a disagreement", "6", "4", "2", "5", "3", "1"]);
    const changes = Array.from(host.querySelectorAll("h3")).find(el => el.textContent === "Connection changes this period").parentElement;
    expect(Array.from(changes.querySelectorAll("dd"), node => node.textContent)).toEqual(["9", "6", "3", "2"]);
    expect(section.textContent).toContain("11 active pairs");
    expect(Array.from(section.querySelectorAll("dt"), node => [node.textContent, node.nextElementSibling.textContent])).toEqual([
      ["Starter conversations", "7"], ["Recent moments", "4"], ["Things that went well", "2"], ["Saved learnings", "1"],
    ]);
    const assessments = host.querySelector('[data-testid="couples-assessment-sharing"]');
    expect([...assessments.querySelectorAll("dd")].map(node => node.textContent)).toEqual(["11", "6", "4", "2"]);
    expect(assessments.textContent).toContain("not compatibility or research participation");
    expect(host.textContent).not.toContain("Private answer must never appear");
    expect(api.get).toHaveBeenCalledWith("/admin/couples/analytics", expect.objectContaining({ params: { days: 7 } }));
    expect(api.get).toHaveBeenCalledTimes(1);
    expect([...host.querySelectorAll("button")].map(node => node.textContent)).toEqual(["Partner journeys", "Starter editor", "Answer history"]);
    const memberships = host.querySelector('[data-testid="couples-membership"]');
    expect([...memberships.querySelectorAll("dd")].map(node => node.textContent)).toEqual(["11", "8", "6", "9", "4"]);
    expect(host.querySelector('a[href^="/admin/activity"]').getAttribute("href")).toBe("/admin/activity?measure=couples_opened&days=7");
    expect(host.querySelector('a[href^="/admin/research"]').getAttribute("href")).toBe("/admin/research?unit=couple");
  } finally {
    act(() => root.unmount());
  }
});
