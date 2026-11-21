import { clearStartDraft, readStartDraft, storeStartDraft } from "./startDraft";

const member = { user_id: "member-a", created_at: "2026-01-01", content_revision: 2 };
const draft = { name: "Sam", lines: [{ speaker: "unknown", sure: false, text: "Lunch?" }], hadImage: true };

afterEach(() => { clearStartDraft(); jest.restoreAllMocks(); });

test.each([
  { ...member, user_id: "member-b" },
  { ...member, created_at: "2026-11-08" },
  { ...member, content_revision: 3 },
])("changing identity, account or content version discards the draft", other => {
  storeStartDraft(member, draft);
  expect(readStartDraft(member)).toEqual(draft);
  expect(readStartDraft(other)).toEqual({});
  expect(readStartDraft(member)).toEqual({});
});

test("an interrupted read retains its request key until the draft expires", () => {
  const clock = jest.spyOn(Date, "now").mockReturnValue(1000);
  const pending = { ...draft, request: { key: "one-request", signature: "{}" } };
  storeStartDraft(member, pending);
  clock.mockReturnValue(1000 + 3599999);
  expect(readStartDraft(member)).toEqual(pending);
  clock.mockReturnValue(1000 + 3600000);
  expect(readStartDraft(member)).toEqual({});
});
