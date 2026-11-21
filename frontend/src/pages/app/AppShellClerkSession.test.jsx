import {act,StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {MemoryRouter,Route,Routes,useOutletContext} from 'react-router-dom';
import axios from 'axios';
import AppShell from '@/components/app/AppShell';
import { mainConsentFixture, installConsentCrypto } from '@/testFixtures/mainConsent';
jest.mock('react-router-dom',()=>{global.TextEncoder=require('util').TextEncoder;return jest.requireActual('react-router');});
jest.mock('axios',()=>jest.requireActual('axios/dist/node/axios.cjs'));
jest.mock('@clerk/react',()=>({
 Show:({when,children})=>when==='signed-in'?children:null,
 useAuth:()=>({isLoaded:true,isSignedIn:true,userId:mockUserId,sessionId:mockSessionId,getToken:mockGetToken}),
 useClerk:()=>mockClerk
}));
jest.mock('@/components/app/NotificationBell',()=>({NotificationBell:()=>null}));
jest.mock('@/lib/theme',()=>({ThemeToggle:()=>null}));
jest.mock('@/lib/browserSession',()=>({endBrowserSession:jest.fn(),recordMemberVisit:async()=>{}}));
let mockUserId,mockSessionId,mockGetToken,mockClerk,host,root,adapter,restoreConsentCrypto;
function Probe(){const {me}=useOutletContext();return <output data-testid="member">{me.user_id}:{me.usage.messages_remaining}</output>;}
const render=()=>act(async()=>root.render(<MemoryRouter initialEntries={['/app/personal']}><Routes><Route path="/app" element={<AppShell/>}><Route path="personal" element={<Probe/>}/></Route></Routes></MemoryRouter>));
beforeEach(()=>{
 global.IS_REACT_ACT_ENVIRONMENT=true;restoreConsentCrypto=installConsentCrypto();mockUserId='fictional-A';mockSessionId='session-A';mockGetToken=jest.fn(async()=>`synthetic-token-${mockUserId}`);mockClerk={get user(){return {id:mockUserId};},get session(){return {id:mockSessionId};},signOut:jest.fn()};
 adapter=jest.fn(async config=>({config,status:200,statusText:'OK',headers:{},data:{user_id:mockUserId,role:'member',main_consent:mainConsentFixture(mockUserId),origin:'direct',onboarded:true,personal_access:true,usage:{plan:'plus',period_verified:true,messages_remaining:mockUserId==='fictional-A'?91:7}}}));axios.defaults.adapter=adapter;
 host=document.body.appendChild(document.createElement('div'));root=createRoot(host);
});
afterEach(()=>{act(()=>root.unmount());host.remove();restoreConsentCrypto();});
test('stable SDK getToken does not retain account A after auth user/session changes (real useApi and Axios)',async()=>{
 await render();expect(host.textContent).toContain('fictional-A:91');const stableToken=mockGetToken;
 mockUserId='fictional-B';mockSessionId='session-B';await render();
 expect(mockGetToken).toBe(stableToken);expect(host.textContent).toContain('fictional-B:7');expect(host.textContent).not.toContain('fictional-A');
 expect(adapter.mock.calls.at(-1)[0].headers.get('Authorization')).toBe('Bearer synthetic-token-fictional-B');
});

test('an origin write waiting for a token is canceled before a changed session can send it (real Axios)',async()=>{
 // The first two tokens serve /me and the parallel own-profile read. Hold
 // the third token, which belongs specifically to the original origin write.
 let releaseToken;let calls=0;mockGetToken.mockImplementation(()=>{calls+=1;return calls===3?new Promise(resolve=>{releaseToken=resolve;}):Promise.resolve(`synthetic-token-${mockUserId}`);});
 adapter.mockImplementation(async config=>({config,status:200,statusText:'OK',headers:{},data:{user_id:mockUserId,role:'member',main_consent:mainConsentFixture(mockUserId),origin:mockUserId==='fictional-A'?null:'direct',onboarded:true,personal_access:true,usage:{plan:'plus',period_verified:true,messages_remaining:mockUserId==='fictional-A'?91:7}}}));
 await render();expect(typeof releaseToken).toBe('function');mockUserId='fictional-B';mockSessionId='session-B';await render();expect(host.textContent).toContain('fictional-B:7');
 await act(async()=>releaseToken('synthetic-token-fictional-B'));expect(adapter.mock.calls.map(([config])=>config.method)).toEqual(['get','get','get','get']);
 expect(adapter.mock.calls.map(([config])=>config.url).sort()).toEqual(['/me','/me','/me/assessments','/me/assessments']);expect(host.textContent).toContain('fictional-B:7');
});

test('the actual StrictMode entry can finish loading after cleanup and remount',async()=>{
 await act(async()=>root.render(<StrictMode><MemoryRouter initialEntries={['/app/personal']}><Routes><Route path="/app" element={<AppShell/>}><Route path="personal" element={<Probe/>}/></Route></Routes></MemoryRouter></StrictMode>));
 expect(host.textContent).toContain('fictional-A:91');expect(host.textContent).not.toContain("Couldn't load your account");
});
