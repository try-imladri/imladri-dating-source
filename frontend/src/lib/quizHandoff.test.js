import { keepQuizHandoff, readQuizHandoff } from "./quizHandoff";

test("unavailable, expired and malformed quiz results leave no handoff", () => {
  const key = "imladri:public-quiz:handoff";
  expect(keepQuizHandoff({name:"Missing slug"})).toBe(false);
  expect(keepQuizHandoff({slug:"abc",name:" "})).toBe(false);
  expect(keepQuizHandoff({slug:"abc",name:"ABC"})).toBe(true);
  expect(readQuizHandoff()?.name).toBe("ABC");
  for (const data of [null,{name:"ABC",until:Date.now()+1000},
        {slug:"abc",name:"ABC",until:Date.now()-1},{slug:"abc",name:"ABC",until:String(Date.now()+1000)}]) {
    sessionStorage.setItem(key,JSON.stringify(data));
    expect(readQuizHandoff()).toBeNull();
    expect(sessionStorage.getItem(key)).toBeNull();
  }
});
