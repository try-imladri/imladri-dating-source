import { publicApi } from "./api";
import { getCachedPublicCatalog, invalidatePublicCatalog, isPublicCatalogPath, loadPublicCatalog, prefetchPublicCatalog, PUBLIC_CATALOG_TTL_MS } from "./publicCatalogCache";

jest.mock("./api", () => ({ publicApi: { get: jest.fn() } }));

const archetypes = "/public/archetypes";
const figures = "/public/figures";
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
beforeEach(() => {
  invalidatePublicCatalog(archetypes);
  invalidatePublicCatalog(figures);
  publicApi.get.mockReset();
});
afterEach(() => {
  invalidatePublicCatalog(archetypes);
  invalidatePublicCatalog(figures);
  jest.restoreAllMocks();
});

test.each(["/public/config", "/public/archetypes/released", "/public/figures/example", "/public/figures?q=one", "/public/figures/", "/research", "/billing", "/me"])(
  "the shared loader rejects non-directory path %s without making a request", async path => {
    expect(isPublicCatalogPath(path)).toBe(false);
    await expect(loadPublicCatalog(path)).rejects.toThrow("Not a public catalog directory");
    await expect(prefetchPublicCatalog(path)).resolves.toBeUndefined();
    expect(publicApi.get).not.toHaveBeenCalled();
    expect(getCachedPublicCatalog(path)).toBeUndefined();
  },
);

test("prefetch and mounted views share one request while one aborted view leaves the others intact", async () => {
  const request = deferred();
  const controller = new AbortController();
  const data = [{ slug: "released" }];
  publicApi.get.mockReturnValue(request.promise);
  const prefetched = prefetchPublicCatalog(archetypes);
  const abandoned = loadPublicCatalog(archetypes, { signal: controller.signal });
  const abortedView = expect(abandoned).rejects.toMatchObject({ name: "AbortError" });
  const mounted = loadPublicCatalog(archetypes);
  controller.abort();
  await abortedView;
  expect(publicApi.get).toHaveBeenCalledTimes(1);
  expect(publicApi.get).toHaveBeenCalledWith(archetypes);
  request.resolve({ data });
  await expect(mounted).resolves.toBe(data);
  await expect(prefetched).resolves.toBe(data);
  expect(getCachedPublicCatalog(archetypes)).toBe(data);
});

test("an already-aborted view starts no request", async () => {
  const controller = new AbortController();
  controller.abort();
  await expect(loadPublicCatalog(figures, { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
  expect(publicApi.get).not.toHaveBeenCalled();
});

test("TTL begins at response completion, reads do not extend it, and expired data is withheld", async () => {
  let now = 1_000;
  jest.spyOn(Date, "now").mockImplementation(() => now);
  const initial = deferred();
  const refreshed = deferred();
  const data = [{ slug: "old" }];
  publicApi.get.mockReturnValueOnce(initial.promise).mockReturnValueOnce(refreshed.promise);
  const first = loadPublicCatalog(figures);
  now = 10_000;
  initial.resolve({ data });
  await first;
  now += PUBLIC_CATALOG_TTL_MS - 1;
  expect(getCachedPublicCatalog(figures)).toBe(data);
  await expect(loadPublicCatalog(figures)).resolves.toBe(data);
  expect(publicApi.get).toHaveBeenCalledTimes(1);
  now += 1;
  expect(getCachedPublicCatalog(figures)).toBeUndefined();
  const second = loadPublicCatalog(figures);
  expect(publicApi.get).toHaveBeenCalledTimes(2);
  expect(getCachedPublicCatalog(figures)).toBeUndefined();
  refreshed.resolve({ data: [] });
  await expect(second).resolves.toEqual([]);
});

test("errors are not cached and a failed prefetch permits a successful retry", async () => {
  publicApi.get.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ data: [] });
  await expect(prefetchPublicCatalog(archetypes)).resolves.toBeUndefined();
  expect(getCachedPublicCatalog(archetypes)).toBeUndefined();
  await expect(loadPublicCatalog(archetypes)).resolves.toEqual([]);
  expect(publicApi.get).toHaveBeenCalledTimes(2);
});

test("invalidation starts a new request and a late prior success cannot replace its result", async () => {
  const old = deferred();
  const latest = [{ slug: "current" }];
  publicApi.get.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ data: latest });
  const priorRequest = loadPublicCatalog(archetypes);
  invalidatePublicCatalog(archetypes);
  await expect(loadPublicCatalog(archetypes)).resolves.toBe(latest);
  old.resolve({ data: [{ slug: "withdrawn" }] });
  await priorRequest;
  expect(getCachedPublicCatalog(archetypes)).toBe(latest);
  expect(publicApi.get).toHaveBeenCalledTimes(2);
});
