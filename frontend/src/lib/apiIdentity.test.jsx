import {act} from 'react';
import {createRoot} from 'react-dom/client';
import axios from 'axios';
import {useApi} from './api';
jest.mock('axios',()=>jest.requireActual('axios/dist/node/axios.cjs'));
jest.mock('@clerk/react',()=>({useAuth:()=>({isLoaded:true,userId:mockUserId,sessionId:mockSessionId,getToken:mockGetToken}),useClerk:()=>mockClerk}));
let mockUserId,mockSessionId,mockGetToken,mockClerk,host,root,currentApi,adapter;
function Capture(){currentApi=useApi();return null;}
const render=()=>act(async()=>root.render(<Capture key={mockUserId+':'+mockSessionId}/>));
const switchTo=(user='fictional-B',session='session-B')=>{mockUserId=user;mockSessionId=session;};
const settle=promise=>promise.then(value=>({value}),error=>({error}));
const flush=()=>act(async()=>{await Promise.resolve();});
beforeEach(()=>{
 global.IS_REACT_ACT_ENVIRONMENT=true;mockUserId='fictional-A';mockSessionId='session-A';mockGetToken=jest.fn(async()=>`token-${mockUserId}`);
 mockClerk={get user(){return {id:mockUserId};},get session(){return {id:mockSessionId};}};
 adapter=jest.fn(async config=>({config,status:200,statusText:'OK',headers:{},data:{actor:mockUserId}}));axios.defaults.adapter=adapter;
 host=document.body.appendChild(document.createElement('div'));root=createRoot(host);
});
afterEach(()=>{act(()=>root.unmount());host.remove();jest.useRealTimers();});
test('an ordinary owner request retains its token and body',async()=>{
 await render();const result=await currentApi.post('/people',{nickname:'synthetic-person-A'});expect(result.data.actor).toBe('fictional-A');expect(adapter).toHaveBeenCalledTimes(1);expect(adapter.mock.calls[0][0].headers.get('Authorization')).toBe('Bearer token-fictional-A');expect(JSON.parse(adapter.mock.calls[0][0].data)).toEqual({nickname:'synthetic-person-A'});
});

test('SDK callback/object refresh for the same session retains the client and uses the fresh token callback',async()=>{
 await render();const first=currentApi;
 mockGetToken=jest.fn(async()=> 'refreshed-token-A');
 mockClerk={user:{id:mockUserId},session:{id:mockSessionId}};
 await render();expect(currentApi).toBe(first);
 await currentApi.get('/me');expect(adapter.mock.calls[0][0].headers.get('Authorization')).toBe('Bearer refreshed-token-A');
});
test('a write waiting for token A cannot use the next account token B',async()=>{
 await render();let release;mockGetToken.mockImplementationOnce(()=>new Promise(resolve=>{release=resolve;}));const result=settle(currentApi.post('/people',{nickname:'synthetic-private-A'}));await flush();expect(typeof release).toBe('function');switchTo();await render();await act(async()=>release('token-fictional-B'));const outcome=await result;expect(adapter).not.toHaveBeenCalled();expect(axios.isCancel(outcome.error)).toBe(true);
});
test('a retained old client cannot issue a later write after account change',async()=>{
 await render();const previous=currentApi;switchTo();await render();const outcome=await settle(previous.post('/people',{nickname:'synthetic-private-A'}));expect(adapter).not.toHaveBeenCalled();expect(axios.isCancel(outcome.error)).toBe(true);
});
test('an unmounted client is inactive even when the same identity remains signed in',async()=>{
 await render();const previous=currentApi;await act(async()=>root.render(null));const outcome=await settle(previous.post('/people',{nickname:'synthetic-private-A'}));expect(adapter).not.toHaveBeenCalled();expect(axios.isCancel(outcome.error)).toBe(true);
});
test('an old in-flight response cannot deliver account A data after switching to B',async()=>{
 await render();let finish;adapter.mockImplementationOnce(config=>new Promise(resolve=>{finish=()=>resolve({config,status:200,statusText:'OK',headers:{},data:{private:'synthetic-A-data'}});}));const result=settle(currentApi.get('/people'));await flush();expect(typeof finish).toBe('function');switchTo();await render();await act(async()=>finish());const outcome=await result;expect(outcome.value).toBeUndefined();expect(axios.isCancel(outcome.error)).toBe(true);expect(adapter).toHaveBeenCalledTimes(1);
});
test('stable getToken still produces a distinct client for each current identity',async()=>{
 await render();const first=currentApi;const stable=mockGetToken;switchTo();await render();expect(mockGetToken).toBe(stable);expect(currentApi).not.toBe(first);await currentApi.get('/me');expect(adapter.mock.calls[0][0].headers.get('Authorization')).toBe('Bearer token-fictional-B');
});
test('session replacement fences an old client even for the same user',async()=>{
 await render();const first=currentApi;switchTo('fictional-A','session-A-replaced');await render();const outcome=await settle(first.post('/people',{nickname:'synthetic-person'}));expect(adapter).not.toHaveBeenCalled();expect(axios.isCancel(outcome.error)).toBe(true);expect(currentApi).not.toBe(first);
});
test('a live Clerk identity mismatch fails closed instead of sending a request',async()=>{
 mockClerk={user:{id:'different-live-user'},session:{id:'different-live-session'}};await render();const outcome=await settle(currentApi.get('/me'));expect(adapter).not.toHaveBeenCalled();expect(axios.isCancel(outcome.error)).toBe(true);
});
test('an old reconnect timer cannot replay a GET with the next account identity',async()=>{
 jest.useFakeTimers();await render();adapter.mockImplementationOnce(config=>Promise.reject({config,response:{status:503,headers:{'x-imladri-reconnecting':'1'}}}));const result=settle(currentApi.get('/people'));await flush();expect(adapter).toHaveBeenCalledTimes(1);switchTo();await render();await act(async()=>jest.advanceTimersByTime(3000));const outcome=await result;expect(adapter).toHaveBeenCalledTimes(1);expect(axios.isCancel(outcome.error)).toBe(true);
});
