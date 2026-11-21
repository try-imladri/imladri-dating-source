import { readAssessmentProfile } from "./assessmentResource";
import { trackProfileWrite } from "./profileWriteBarrier";

test("one fresh owner-bound assessment read replaces two separate reads", async () => {
  const api = { get: jest.fn(async () => ({data:{owner_id:"owner",quiz:{},about:{}}})) };
  await readAssessmentProfile(api, "owner"); await readAssessmentProfile(api, "owner");
  expect(api.get.mock.calls.map(([path])=>path)).toEqual(["/me/assessments","/me/assessments"]);
  expect(api.get.mock.calls[0][1].headers["X-Questionnaire-Owner"]).toBe("owner");
});
test("navigation waits for the owner's actual save, without sharing data between owners", async () => {
  let release; const api={get:jest.fn(async()=>({data:{owner_id:"owner"}}))};
  trackProfileWrite(api,"owner",new Promise(resolve=>{release=resolve;}));
  const pending=readAssessmentProfile(api,"owner"); await Promise.resolve(); expect(api.get).not.toHaveBeenCalled();
  release(); await pending; expect(api.get).toHaveBeenCalledTimes(1);
  await expect(readAssessmentProfile(api,"other")).rejects.toThrow(/account changed/);
});
test("only a missing legacy route falls back; authorization and outages are not retried", async()=>{
  const api={get:jest.fn(async path=>{
    if(path==="/me/assessments") throw {response:{status:404}};
    return {data:path==="/quiz"?{version:1}:{about:{answers:{}}}};
  })};
  expect((await readAssessmentProfile(api,"owner")).quiz.version).toBe(1);
  for(const status of [401,403,409,500,503]) {
    api.get.mockClear();api.get.mockRejectedValue({response:{status}});
    await expect(readAssessmentProfile(api,"owner")).rejects.toEqual({response:{status}});
    expect(api.get).toHaveBeenCalledTimes(1);
  }
});
