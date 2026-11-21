import { act } from "react";
import { createRoot } from "react-dom/client";
import OperationsStatus from "./OperationsStatus";
import { ActivityFeed } from "./ActivityFeed";

let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.body.appendChild(document.createElement("div"));
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("service checks distinguish a missing first check, stale data, a failed check, and a skipped one", async () => {
  const get = jest.fn().mockResolvedValueOnce({ data: { checked_at: null, checks: {}, billing_enabled: false } })
    .mockResolvedValueOnce({ data: { checked_at: "2026-11-08T12:00:00Z", stale: true, billing_enabled: true,
      checks: { deepseek: true, stripe: false, ai_budget: true, partner_checkouts: false } } });
  await act(async () => root.render(<OperationsStatus api={{get}} />));
  expect(host.textContent).toContain("Paused in this deployment");
  expect(host.textContent).toContain("Waiting for the first");
  await act(async () => host.querySelector("button").click());
  expect(host.textContent).toContain("Check overdue");
  expect(host.textContent).toContain("Stripe configuration and priceNeeds attention");
  expect(host.textContent).toContain("Paid invoice reconciliationNot checked");
  expect(host.textContent).toContain("Invited-partner checkout reviewNeeds attention");
});

test("live activity requests the selected category and shows a refresh failure", async () => {
  const counts = {signup: 1, chat: 100, group: 0, practice: 0};
  const get = jest.fn(async (_, {params}) => {
    if (params.kind === "group") throw new Error("Offline");
    return { data: {last24: counts, items: params.kind === "signup" ? [
      {type: "signup", user: "Test account", at: "2026-11-08T12:00:00Z", detail: "New account"}
    ] : []} };
  });
  await act(async () => root.render(<ActivityFeed api={{get}} />));
  await act(async () => host.querySelector('[data-testid="activity-filter-signup"]').click());
  expect(host.querySelector('[data-testid="activity-list"]').textContent).toContain("New account");
  await act(async () => host.querySelector('[data-testid="activity-filter-group"]').click());
  expect(host.querySelector('[role="alert"]').textContent).toContain("couldn’t refresh");
  expect(host.textContent).not.toContain("Nothing yet");
});

test("the Couples filter requests and renders combined hourly counts without a member name", async () => {
  const period = { type: "couples", at: "2026-11-08T13:00:00+00:00", visits: 2, samples: 1 };
  const get = jest.fn(async () => ({ data: { last24: { signup: 0, chat: 0, group: 0, practice: 0, couples: 3 }, items: [period] } }));
  await act(async () => root.render(<ActivityFeed api={{get}} />));
  expect(get.mock.calls[0][1].params).toEqual({kind: "all", include_couples: true});
  await act(async () => host.querySelector('[data-testid="activity-filter-couples"]').click());
  expect(get.mock.calls.at(-1)[1].params).toEqual({kind: "couples", include_couples: true});
  const item = host.querySelector('[data-testid="activity-item"]');
  expect(item.textContent).toContain("2 visits · 1 sample completed");
  expect(item.textContent).toContain("13:00 UTC");
  expect(host.querySelector('[data-testid="activity-count-couples"]').textContent).toContain("3 /24h");
});
