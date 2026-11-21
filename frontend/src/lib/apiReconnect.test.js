import { publicApi } from "./api";
jest.mock("axios", () => ({
  create: () => ({ request: jest.fn(), interceptors: { response: { use: jest.fn() } } }),
}));
jest.mock("@clerk/react", () => ({ useAuth: jest.fn() }));

const reconnect = publicApi.interceptors.response.use.mock.calls[0][1];
const warming = method => ({ config: { method }, response: { status: 503, headers: { "x-imladri-reconnecting": "1" } } });

it("retries an unsent read while the origin wakes and honors cancellation", async () => {
  jest.useFakeTimers();
  try {
    publicApi.request.mockResolvedValue({ data: "current" });
    const get = warming("get");
    const recovered = reconnect(get);
    jest.advanceTimersByTime(3000);
    expect(await recovered).toEqual({ data: "current" });
    expect(publicApi.request).toHaveBeenCalledTimes(1);
    const stopped = warming("get");
    const controller = new AbortController();
    stopped.config.signal = controller.signal;
    const pending = reconnect(stopped);
    controller.abort();
    jest.advanceTimersByTime(3000);
    await expect(pending).rejects.toBe(stopped);
    expect(publicApi.request).toHaveBeenCalledTimes(1);
  } finally { jest.useRealTimers(); }
});

it("never replays a write, an upstream 503, or an expired reconnect attempt", async () => {
  const post = warming("post"), gateway = warming("get"), expired = warming("get");
  delete gateway.response.headers["x-imladri-reconnecting"];
  expired.config.reconnectUntil = Date.now() - 1;
  const gone = warming("get");
  gone.response = { status: 410, headers: {} };
  for (const error of [post, gateway, expired, gone]) await expect(reconnect(error)).rejects.toBe(error);
});
