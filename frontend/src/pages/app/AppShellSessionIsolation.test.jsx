import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useOutletContext } from 'react-router-dom';
import AppShell from '@/components/app/AppShell';
import { mainConsentFixture, installConsentCrypto } from '@/testFixtures/mainConsent';
jest.mock('react-router-dom',()=>{global.TextEncoder=require('util').TextEncoder;return jest.requireActual('react-router');});
jest.mock('@clerk/react',()=>({Show:({when,children})=>when==='signed-in'?children:null,useAuth:()=>({userId:mockUserId,isLoaded:true,isSignedIn:true}),useClerk:()=>({signOut:jest.fn()})}));
jest.mock('@/components/app/NotificationBell',()=>({NotificationBell:()=>null}));
jest.mock('@/lib/theme',()=>({ThemeToggle:()=>null}));
jest.mock('@/lib/browserSession',()=>({endBrowserSession:jest.fn(),recordMemberVisit:async()=>{}}));
jest.mock('@/lib/api',()=>({useApi:()=>mockApi,applySignupOrigin:(...args)=>mockApplyOrigin(...args),errMsg:e=>e.message}));
let mockApi,mockUserId,mockApplyOrigin,host,root,restoreConsentCrypto;
const profile=(id,count)=>({user_id:id,role:'member',main_consent:mainConsentFixture(id),onboarded:true,personal_access:true,origin:'direct',usage:{plan:'plus',period_verified:true,messages_remaining:count}});
const client=result=>({get:jest.fn(()=>result),interceptors:{response:{use:jest.fn(),eject:jest.fn()}}});
function Probe(){const {me}=useOutletContext();return <output data-testid="member">{me.user_id}:{me.usage.messages_remaining}</output>;}
const render=()=>act(async()=>root.render(<MemoryRouter initialEntries={['/app/personal']}><Routes><Route path="/app" element={<AppShell/>}><Route path="personal" element={<Probe/>}/><Route path="settings/upgrade" element={<p>Plans</p>}/></Route></Routes></MemoryRouter>));
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;restoreConsentCrypto=installConsentCrypto();mockUserId='fictional-A';mockApplyOrigin=jest.fn(async(_,member)=>member);host=document.body.appendChild(document.createElement('div'));root=createRoot(host);});
afterEach(()=>{act(()=>root.unmount());host.remove();sessionStorage.clear();restoreConsentCrypto();});
test('does not display account A while switched account B profile is pending',async()=>{
 mockApi=client(Promise.resolve({data:profile('fictional-A',91)}));await render();expect(host.textContent).toContain('fictional-A:91');
 let finishB;mockUserId='fictional-B';mockApi=client(new Promise(resolve=>{finishB=resolve;}));await render();
 expect(host.textContent).not.toContain('fictional-A');
 await act(async()=>finishB({data:profile('fictional-B',7)}));expect(host.textContent).toContain('fictional-B:7');
});
test('a late account A me response cannot replace current account B',async()=>{
 let finishA;mockApi=client(new Promise(resolve=>{finishA=resolve;}));await render();
 mockUserId='fictional-B';mockApi=client(Promise.resolve({data:profile('fictional-B',7)}));await render();expect(host.textContent).toContain('fictional-B:7');
 await act(async()=>finishA({data:profile('fictional-A',91)}));
 expect(host.textContent).toContain('fictional-B:7');expect(host.textContent).not.toContain('fictional-A');
});

test('a previous account closure cannot carry into the next account',async()=>{
 mockApi=client(Promise.reject({response:{status:410}}));await render();expect(host.textContent).toContain('Your Personal account is closed');
 mockUserId='fictional-B';mockApi=client(Promise.resolve({data:profile('fictional-B',7)}));await render();expect(host.textContent).toContain('fictional-B:7');expect(host.textContent).not.toContain('account is closed');
});
test('late failure from the previous account cannot replace the current screen',async()=>{
 let failA;mockApi=client(new Promise((_,reject)=>{failA=reject;}));await render();
 mockUserId='fictional-B';mockApi=client(Promise.resolve({data:profile('fictional-B',7)}));await render();
 await act(async()=>failA({response:{status:410}}));expect(host.textContent).toContain('fictional-B:7');expect(host.textContent).not.toContain('account is closed');
});
test('old usage interceptors cannot change the next member balance',async()=>{
 const old=client(Promise.resolve({data:profile('fictional-A',91)}));mockApi=old;await render();const oldResponse=old.interceptors.response.use.mock.calls[0][0];
 mockUserId='fictional-B';mockApi=client(Promise.resolve({data:profile('fictional-B',7)}));await render();
 await act(async()=>oldResponse({data:{usage:{plan:'plus',messages_remaining:91}},config:{url:'/people/x/advice'}}));expect(host.textContent).toContain('fictional-B:7');
});
test('a late old profile never writes origin attribution after identity changes',async()=>{
 let finishA;const old=client(new Promise(resolve=>{finishA=resolve;}));mockApi=old;await render();const signal=old.get.mock.calls[0][1].signal;
 mockUserId='fictional-B';mockApi=client(Promise.resolve({data:profile('fictional-B',7)}));await render();
 await act(async()=>finishA({data:profile('fictional-A',91)}));expect(signal.aborted).toBe(true);expect(mockApplyOrigin.mock.calls.map(([,member])=>member.user_id)).toEqual(['fictional-B']);expect(host.textContent).toContain('fictional-B:7');
});
test('pending origin attribution is aborted and cannot commit old profile data',async()=>{
 let finishOrigin,originApi;mockApplyOrigin.mockImplementation((api,member)=>member.user_id==='fictional-A'?new Promise(resolve=>{finishOrigin=resolve;originApi=api;}):Promise.resolve(member));
 const old=client(Promise.resolve({data:profile('fictional-A',91)}));old.patch=jest.fn(async()=>({data:{}}));mockApi=old;await render();
 mockUserId='fictional-B';mockApi=client(Promise.resolve({data:profile('fictional-B',7)}));await render();await act(async()=>originApi.patch('/me',{origin:'first'}));expect(old.patch.mock.calls[0][2].signal.aborted).toBe(true);
 await act(async()=>finishOrigin(profile('fictional-A',91)));expect(host.textContent).toContain('fictional-B:7');expect(host.textContent).not.toContain('fictional-A');
});
