import { DEVICE_WIDTH, DEVICE_HEIGHT } from "../utils/config/device";

const BG_COLOR = 0x000000;
const BUTTON_COLOR = 0x2a2a2a;
const BUTTON_PRESS_COLOR = 0x444444;
const TEXT_COLOR = 0xffffff;
const DIM_COLOR = 0x888888;

// A very long world-news digest can't usefully fit a round watch face; cap it
// so the reply is at least glanceable instead of clipped mid-sentence by the
// widget's fixed height.
const MAX_MESSAGE_CHARS = 500;

const COMMANDS = [
  { method: "PAIR_BLE", label: "Pair Headphones" },
  { method: "WORLD_NEWS", label: "World News" },
  { method: "FIND_SMOKEY", label: "Find Smokey" },
];

Page({
  state: {
    widgets: [],
  },
  build() {
    this.messageBuilder = getApp()._options.globalData.messageBuilder;
    this.showMenu();
  },

  clearWidgets() {
    (this.state.widgets || []).forEach((w) => hmUI.deleteWidget(w));
    this.state.widgets = [];
  },

  showMenu() {
    this.clearWidgets();
    const title = hmUI.createWidget(hmUI.widget.TEXT, {
      x: 0,
      y: px(56),
      w: DEVICE_WIDTH,
      h: px(60),
      color: TEXT_COLOR,
      text_size: px(40),
      align_h: hmUI.align.CENTER_H,
      text: "Zeev",
    });
    this.state.widgets.push(title);

    const btnW = px(360);
    const btnH = px(88);
    const gap = px(20);
    const startY = px(150);

    COMMANDS.forEach((cmd, i) => {
      const btn = hmUI.createWidget(hmUI.widget.BUTTON, {
        x: (DEVICE_WIDTH - btnW) / 2,
        y: startY + i * (btnH + gap),
        w: btnW,
        h: btnH,
        radius: px(16),
        text_size: px(30),
        normal_color: BUTTON_COLOR,
        press_color: BUTTON_PRESS_COLOR,
        color: TEXT_COLOR,
        text: cmd.label,
        click_func: () => this.runCommand(cmd),
      });
      this.state.widgets.push(btn);
    });
  },

  runCommand(cmd) {
    this.clearWidgets();
    const status = hmUI.createWidget(hmUI.widget.TEXT, {
      x: px(40),
      y: (DEVICE_HEIGHT - px(60)) / 2,
      w: DEVICE_WIDTH - px(80),
      h: px(60),
      color: DIM_COLOR,
      text_size: px(32),
      align_h: hmUI.align.CENTER_H,
      align_v: hmUI.align.CENTER_V,
      text: "Working...",
    });
    this.state.widgets.push(status);

    this.messageBuilder
      .request({ method: cmd.method })
      .then((data) => {
        const ok = !!(data && data.ok);
        const message = (data && data.message) || "No response.";
        this.showResult(ok, message);
      })
      .catch(() => {
        this.showResult(false, "Couldn't reach Zeev.");
      });
  },

  showResult(ok, message) {
    this.clearWidgets();
    let text = message;
    if (text.length > MAX_MESSAGE_CHARS) {
      text = text.slice(0, MAX_MESSAGE_CHARS) + "...";
    }

    const status = hmUI.createWidget(hmUI.widget.TEXT, {
      x: px(30),
      y: px(70),
      w: DEVICE_WIDTH - px(60),
      h: px(50),
      color: ok ? 0x5ec26a : 0xe05a5a,
      text_size: px(28),
      align_h: hmUI.align.CENTER_H,
      text: ok ? "Done" : "Failed",
    });
    this.state.widgets.push(status);

    const body = hmUI.createWidget(hmUI.widget.TEXT, {
      x: px(40),
      y: px(130),
      w: DEVICE_WIDTH - px(80),
      h: DEVICE_HEIGHT - px(260),
      color: TEXT_COLOR,
      text_size: px(26),
      align_h: hmUI.align.CENTER_H,
      text_style: hmUI.text_style.WRAP,
      text: text,
    });
    this.state.widgets.push(body);

    const backW = px(280);
    const back = hmUI.createWidget(hmUI.widget.BUTTON, {
      x: (DEVICE_WIDTH - backW) / 2,
      y: DEVICE_HEIGHT - px(120),
      w: backW,
      h: px(80),
      radius: px(16),
      text_size: px(30),
      normal_color: BUTTON_COLOR,
      press_color: BUTTON_PRESS_COLOR,
      color: TEXT_COLOR,
      text: "Back",
      click_func: () => this.showMenu(),
    });
    this.state.widgets.push(back);
  },

  onDestroy() {
    this.clearWidgets();
  },
});
