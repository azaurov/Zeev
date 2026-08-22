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

// Blessings is a nav button, not a direct command -- it opens a submenu of
// its own so more blessings can be added later without crowding the
// top-level menu. Order matches the physical blessing card Alex
// photographed (2026-08-22).
const BLESSINGS = [
  { method: "BLESSING_NETILAS_YADAYIM", label: "Netilas Yadayim" },
  { method: "BLESSING_HAMOTZI", label: "Hamotzi" },
  { method: "BLESSING_MEZONOS", label: "Mezonos" },
  { method: "BLESSING_HAGOFEN", label: "Hagofen" },
  { method: "BLESSING_HOAYTZ", label: "Hoaytz" },
  { method: "BLESSING_HOADOMO", label: "Hoadomo" },
  { method: "BLESSING_SHEHAKOL", label: "Shehakol" },
  { method: "BLESSING_BRICH", label: "Brich" },
  { method: "BLESSING_MODEH_ANI", label: "Modeh Ani" },
  { method: "BLESSING_SHEMA", label: "Shema" },
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

  showTitle(text) {
    const title = hmUI.createWidget(hmUI.widget.TEXT, {
      x: 0,
      y: px(56),
      w: DEVICE_WIDTH,
      h: px(60),
      color: TEXT_COLOR,
      text_size: px(40),
      align_h: hmUI.align.CENTER_H,
      text,
    });
    this.state.widgets.push(title);
  },

  showButtonList(buttons) {
    // Button height/gap scale to fit the button count in the space below the
    // title, up to 3-4 buttons -- the original fixed 88px/20px sizing was
    // tuned for exactly 3 buttons and a 4th (Blessings) would run off the
    // bottom of a round 466px face. Capped at 88px so 2-3 buttons don't grow
    // oversized. Below that, a MIN_BTN_H floor takes over instead of
    // continuing to shrink -- the Blessings submenu has 10 entries, and
    // squeezing all of them into one screen would make every button a few
    // px tall and unusable. The watch supports swipe-scroll (confirmed by
    // Alex), so content is allowed to run past the visible screen instead.
    const btnW = px(360);
    const startY = px(150);
    const bottomMargin = px(40);
    const gap = px(20);
    const minBtnH = px(72);
    const available = DEVICE_HEIGHT - startY - bottomMargin;
    const n = buttons.length;
    const btnH = Math.min(px(88), Math.max(minBtnH, (available - gap * (n - 1)) / n));

    buttons.forEach((b, i) => {
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
        text: b.label,
        click_func: b.onClick,
      });
      this.state.widgets.push(btn);
    });
  },

  showMenu() {
    this.clearWidgets();
    this.showTitle("Zeev");
    this.showButtonList([
      ...COMMANDS.map((cmd) => ({
        label: cmd.label,
        onClick: () => this.runCommand(cmd, () => this.showMenu()),
      })),
      { label: "Blessings", onClick: () => this.showBlessingsMenu() },
    ]);
  },

  showBlessingsMenu() {
    this.clearWidgets();
    this.showTitle("Blessings");
    this.showButtonList([
      ...BLESSINGS.map((cmd) => ({
        label: cmd.label,
        onClick: () => this.runCommand(cmd, () => this.showBlessingsMenu()),
      })),
      { label: "Back", onClick: () => this.showMenu() },
    ]);
  },

  runCommand(cmd, backTo) {
    this.backTo = backTo || (() => this.showMenu());
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

    // Default request timeout (60s) can be too short for find_smokey, which
    // sweeps up to 3 cameras sequentially (~25-40s each on a slow vision
    // response) -- 120s covers a worst-case sweep with margin.
    this.messageBuilder
      .request({ method: cmd.method }, { timeout: 120000 })
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
      click_func: () => (this.backTo || (() => this.showMenu()))(),
    });
    this.state.widgets.push(back);
  },

  onDestroy() {
    this.clearWidgets();
  },
});
