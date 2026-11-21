import {act} from "react";
import {createRoot} from "react-dom/client";
import BillingRecords from "./BillingRecords";

let root,host,api;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.body.appendChild(document.createElement('div'));root=createRoot(host);api={get:jest.fn()};});
afterEach(()=>{act(()=>root.unmount());host.remove();delete global.IS_REACT_ACT_ENVIRONMENT;});
const mount=id=>act(async()=>root.render(<BillingRecords key={id} api={api} userId={id}/>));
const open=()=>act(async()=>host.querySelector('button').click());

test('billing evidence loads on demand and distinguishes legacy policy from verified adjustments',async()=>{
  api.get.mockResolvedValue({data:{items:[{id:'invoice-fixture',kind:'purchase',currency:'usd',payment_status:'paid',original_amount_cents:1200,
    original_revenue_cents:1200,refund_dispute_offset_cents:600,net_revenue_cents:600,net_credits:50,adjustment_status:'verified',policy_recorded:false}],pending_reviews:[]}});
  await mount('fixture-member');expect(api.get).not.toHaveBeenCalled();await open();
  expect(api.get.mock.calls[0][0]).toBe('/users/fixture-member/billing-records');
  expect(host.textContent).toContain('Net revenue: $6.00');
  expect(host.textContent).toContain('Purchase acknowledgment not recorded');
  expect(host.textContent).toContain('Verified with Stripe');
});

test('late evidence cannot follow an account switch and the panel remains closed',async()=>{
  let finish;api.get.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  await mount('fixture-a');await open();const signal=api.get.mock.calls[0][1].signal;
  await mount('fixture-b');expect(signal.aborted).toBe(true);
  await act(async()=>finish({data:{items:[{id:'private-old-billing-fixture'}]}}));
  expect(host.textContent).not.toContain('private-old-billing-fixture');
  expect(host.querySelector('button').getAttribute('aria-expanded')).toBe('false');
});

test('missing financial linkage stays visibly review-required without invented totals',async()=>{
  api.get.mockResolvedValue({data:{items:[{id:'legacy-fixture',kind:'renewal',currency:'usd',adjustment_status:'review_required'}],pending_reviews:[{id:'review',reason:'Invoice payment link needs review',opened_at:'now'}],truncated:true}});
  await mount('fixture-member');await open();expect(host.textContent).toContain('Payment review needed');
  expect(host.textContent).toContain('Net revenue: Not recorded');expect(host.textContent).toContain('No retrospective acceptance is assumed');
  expect(host.textContent).toContain('Showing the latest 25 records');
});
