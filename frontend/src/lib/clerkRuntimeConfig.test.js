import { readClerkRuntimeConfig } from "./clerkRuntimeConfig";
const ok=()=>({ok:true,json:async()=>({enabled:true,publishable_key:"public_fixture"})});
test("public configuration is coalesced and reused briefly, with expiry",async()=>{
  jest.useFakeTimers();const fetcher=jest.fn(async()=>ok());
  await Promise.all([readClerkRuntimeConfig("/fixture-a",fetcher),readClerkRuntimeConfig("/fixture-a",fetcher)]);
  await readClerkRuntimeConfig("/fixture-a",fetcher);expect(fetcher).toHaveBeenCalledTimes(1);
  jest.advanceTimersByTime(60001);await readClerkRuntimeConfig("/fixture-a",fetcher);expect(fetcher).toHaveBeenCalledTimes(2);
  jest.useRealTimers();
});
test("failures and disabled configuration are not cached",async()=>{
  const fetcher=jest.fn(async()=>({ok:false,status:503,headers:{get:()=>"1"}}));
  const response=await readClerkRuntimeConfig("/fixture-b",fetcher);expect(response.reconnecting).toBe(true);
  fetcher.mockResolvedValue({ok:true,json:async()=>({enabled:false})});
  await readClerkRuntimeConfig("/fixture-b",fetcher);await readClerkRuntimeConfig("/fixture-b",fetcher);
  expect(fetcher).toHaveBeenCalledTimes(3);
});
