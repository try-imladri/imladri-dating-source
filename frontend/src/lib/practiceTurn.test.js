import { clearPracticeTurn, readPracticeTurn, storePracticeTurn } from "./practiceTurn";

const member = { user_id: "member", created_at: "2026-01-01" };
beforeEach(() => sessionStorage.clear());

it("recovers parallel rehearsals and ignores a late acknowledgement for a replaced turn", () => {
  storePracticeTurn(member, "one", {key: "first"});
  storePracticeTurn(member, "two", {key: "second"});
  storePracticeTurn(member, "one", {key: "replacement"});
  clearPracticeTurn(member, "one", "first");
  expect(readPracticeTurn(member, "one")).toEqual({key: "replacement"});
  clearPracticeTurn(member, "one", "replacement");
  expect(readPracticeTurn(member, "two")).toEqual({key: "second"});
  expect(readPracticeTurn(member, "one")).toBeNull();
});

it("expires pending messages on erasure, account switch, and account closure", () => {
  storePracticeTurn(member, "one", {key: "private"});
  expect(readPracticeTurn({...member, content_revision: 1}, "one")).toBeNull();
  expect(readPracticeTurn(member, "one")).toBeNull();
  storePracticeTurn(member, "two", {key: "other"});
  expect(readPracticeTurn({...member, user_id: "next"}, "two")).toBeNull();
  storePracticeTurn(member, "one", {key: "last"});
  clearPracticeTurn();
  expect(sessionStorage.length).toBe(0);
});
