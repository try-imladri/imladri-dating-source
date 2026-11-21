import {act} from 'react';
import {createRoot} from 'react-dom/client';
import BillingReviews from './BillingReviews';
let root,host;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.body.appendChild(document.createElement('div'));root=createRoot(host);});
afterEach(()=>{act(()=>root.unmount());host.remove();delete global.IS_REACT_ACT_ENVIRONMENT;});
test('unlinked closed-account adjustments stay visible without recreating an account or claiming verified net',async()=>{
  const api={get:jest.fn(async()=>({data:{items:[{id:'review-fixture',user_id:null,reason:'Personal payment link needs reconciliation',charge_id:'ch_fixture',opened_at:'now'}],truncated:true}}))};
  await act(async()=>root.render(<BillingReviews api={api}/>));
  expect(host.textContent).toContain('Payment adjustments need review');
  expect(host.textContent).toContain('Affected net revenue stays unverified');
  expect(host.textContent).toContain('ch_fixture');expect(host.textContent).not.toContain('Account:');
  expect(host.querySelector('button')).toBeNull();
});
test('a failed review lookup does not imply an empty or healthy queue',async()=>{
  const api={get:jest.fn().mockRejectedValue(new Error('offline'))};
  await act(async()=>root.render(<BillingReviews api={api}/>));
  expect(host.querySelector('[role="alert"]').textContent).toContain('couldn’t load');
  expect(host.textContent).not.toContain('No pending');
});
