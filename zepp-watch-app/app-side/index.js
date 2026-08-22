import { MessageBuilder } from "../shared/message";
import { ZEEV_WATCH_URL, ZEEV_WATCH_KEY } from "./config";

const messageBuilder = new MessageBuilder();

// Zeev's watch endpoint (zeev/watch_server.py on the Pi), reached publicly
// through Cloudflare -> bosgame nginx -> Tailscale -> the Pi. This file runs
// inside the Zepp phone app's sandbox (app-side), not on the watch itself, so
// the shared secret lives here (in the gitignored config.js) rather than in
// pages/index.js, which ships to the watch's own limited storage.

async function callZeev(ctx, cmd) {
  try {
    const res = await fetch({
      url: ZEEV_WATCH_URL,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Zeev-Watch-Key": ZEEV_WATCH_KEY,
      },
      body: JSON.stringify({ cmd }),
    });
    const body = typeof res.body === "string" ? JSON.parse(res.body) : res.body;
    if (res.status !== 200 || !body || body.ok !== true) {
      const msg = (body && (body.error || body.message)) || `HTTP ${res.status}`;
      ctx.response({ data: { ok: false, message: String(msg) } });
      return;
    }
    ctx.response({ data: { ok: true, message: String(body.message || "") } });
  } catch (error) {
    ctx.response({ data: { ok: false, message: "Network error reaching Zeev." } });
  }
}

AppSideService({
  onInit() {
    messageBuilder.listen(() => {});

    messageBuilder.on("request", (ctx) => {
      const jsonRpc = messageBuilder.buf2Json(ctx.request.payload);
      if (jsonRpc.method === "PAIR_BLE") {
        return callZeev(ctx, "pair_ble");
      }
      if (jsonRpc.method === "WORLD_NEWS") {
        return callZeev(ctx, "world_news");
      }
      if (jsonRpc.method === "FIND_SMOKEY") {
        return callZeev(ctx, "find_smokey");
      }
      // Every blessing method is BLESSING_<KEY> on the watch side and
      // blessing_<key> on the server -- one generic mapping instead of a
      // growing pile of near-identical if-blocks per blessing.
      if (jsonRpc.method && jsonRpc.method.startsWith("BLESSING_")) {
        return callZeev(ctx, jsonRpc.method.toLowerCase());
      }
    });
  },

  onRun() {},

  onDestroy() {},
});
