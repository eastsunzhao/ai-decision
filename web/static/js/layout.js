function getDividerWidth() {
  if (!appShellEl) {
    return 12;
  }
  const raw = getComputedStyle(appShellEl).getPropertyValue("--divider-width");
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : 12;
}

function getShellContentMetrics() {
  if (!appShellEl) {
    return { contentLeft: 0, contentWidth: 0 };
  }
  const rect = appShellEl.getBoundingClientRect();
  const styles = getComputedStyle(appShellEl);
  const paddingLeft = Number.parseFloat(styles.paddingLeft) || 0;
  const paddingRight = Number.parseFloat(styles.paddingRight) || 0;
  return {
    contentLeft: rect.left + paddingLeft,
    contentWidth: Math.max(0, rect.width - paddingLeft - paddingRight),
  };
}

function getDesktopMinWidths() {
  return window.innerWidth < 1320
    ? { nav: NAV_PANEL_MIN_WIDTH, center: 500, chat: 320 }
    : { nav: NAV_PANEL_MIN_WIDTH, center: 660, chat: 360 };
}

function getPanelAreaWidth() {
  const { contentWidth } = getShellContentMetrics();
  const visibleOptionalPanels = Number(!state.navPanelHidden) + Number(!state.centerPanelHidden);
  const dividerCount = Math.max(0, visibleOptionalPanels);
  return Math.max(0, contentWidth - (getDividerWidth() * dividerCount));
}

function clampDesktopLayout(layout) {
  const total = getPanelAreaWidth();
  const mins = getDesktopMinWidths();
  let nav = Math.max(mins.nav, Math.round(Number(layout?.nav) || mins.nav));
  let center = Math.max(mins.center, Math.round(Number(layout?.center) || mins.center));
  let chat = Math.max(mins.chat, Math.round(Number(layout?.chat) || mins.chat));

  if (state.navPanelHidden && state.centerPanelHidden) {
    return { nav, center, chat: Math.max(mins.chat, total) };
  }

  if (state.navPanelHidden) {
    let overflow = center + chat - total;
    if (overflow > 0) {
      const centerReduction = Math.min(center - mins.center, overflow);
      center -= Math.max(0, centerReduction);
      overflow -= Math.max(0, centerReduction);
    }
    if (overflow > 0) {
      const chatReduction = Math.min(chat - mins.chat, overflow);
      chat -= Math.max(0, chatReduction);
      overflow -= Math.max(0, chatReduction);
    }
    if (overflow > 0) {
      center = Math.max(240, total - chat);
    }
    if (center + chat < total) {
      center += total - center - chat;
    }
    return { nav, center, chat };
  }

  if (state.centerPanelHidden) {
    let overflow = nav + chat - total;
    if (overflow > 0) {
      const chatReduction = Math.min(chat - mins.chat, overflow);
      chat -= Math.max(0, chatReduction);
      overflow -= Math.max(0, chatReduction);
    }
    if (overflow > 0) {
      const navReduction = Math.min(nav - mins.nav, overflow);
      nav -= Math.max(0, navReduction);
      overflow -= Math.max(0, navReduction);
    }
    if (overflow > 0) {
      chat = Math.max(240, total - nav);
    }
    if (nav + chat < total) {
      chat += total - nav - chat;
    }
    return { nav, center, chat };
  }

  let overflow = nav + center + chat - total;

  if (overflow > 0) {
    const reductions = [
      ["center", center - mins.center],
      ["chat", chat - mins.chat],
      ["nav", nav - mins.nav],
    ];
    for (const [key, room] of reductions) {
      if (overflow <= 0) {
        break;
      }
      const amount = Math.min(Math.max(0, room), overflow);
      if (key === "center") {
        center -= amount;
      } else if (key === "chat") {
        chat -= amount;
      } else {
        nav -= amount;
      }
      overflow -= amount;
    }
  }

  if (overflow > 0) {
    const fallbackCenter = Math.max(240, total - nav - chat);
    center = fallbackCenter;
    if (nav + center + chat > total) {
      chat = Math.max(240, total - nav - center);
    }
    if (nav + center + chat > total) {
      nav = Math.max(120, total - center - chat);
    }
  }

  if (overflow < 0) {
    center += Math.abs(overflow);
  }

  return { nav, center, chat };
}

function buildDefaultDesktopLayout() {
  const total = getPanelAreaWidth();
  const mins = getDesktopMinWidths();
  if (!total) {
    return { ...mins };
  }
  const nav = Math.round(total * 0.15);
  const center = Math.round(total * 0.58);
  const chat = total - nav - center;
  return clampDesktopLayout({ nav, center, chat });
}

function applyDesktopLayout(layout, { persist = true } = {}) {
  if (!appShellEl || isMobileLayout()) {
    return;
  }
  const normalized = clampDesktopLayout(layout);
  desktopLayout = normalized;
  appShellEl.classList.add("layout-customized");
  appShellEl.style.setProperty("--nav-panel-width", `${normalized.nav}px`);
  appShellEl.style.setProperty("--center-panel-width", `${normalized.center}px`);
  appShellEl.style.setProperty("--chat-panel-width", `${normalized.chat}px`);
  if (persist) {
    localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(normalized));
  }
}

function syncPanelToggleButton(button, hidden, label) {
  if (!button) {
    return;
  }
  const visible = !hidden;
  button.classList.toggle("pressed", visible);
  button.setAttribute("aria-pressed", visible ? "true" : "false");
  button.setAttribute("aria-label", visible ? `隐藏${label}面板` : `显示${label}面板`);
  button.title = visible ? `隐藏${label}面板` : `显示${label}面板`;
}

function renderPanelToggles() {
  syncPanelToggleButton(toggleNavPanelButtonEl, state.navPanelHidden, "文件");
  syncPanelToggleButton(toggleCenterPanelButtonEl, state.centerPanelHidden, "编辑器");
}

function applyPanelVisibility({ persist = true } = {}) {
  if (!appShellEl) {
    return;
  }
  appShellEl.classList.toggle("nav-collapsed", state.navPanelHidden);
  appShellEl.classList.toggle("center-collapsed", state.centerPanelHidden);
  if (persist) {
    localStorage.setItem(PANEL_VISIBILITY_STORAGE_KEY, JSON.stringify({
      nav: state.navPanelHidden,
      center: state.centerPanelHidden,
    }));
  }
  renderPanelToggles();
  restoreDesktopLayout();
}

function restorePanelVisibility() {
  try {
    const raw = localStorage.getItem(PANEL_VISIBILITY_STORAGE_KEY);
    const visibility = raw ? JSON.parse(raw) : null;
    state.navPanelHidden = Boolean(visibility?.nav);
    state.centerPanelHidden = Boolean(visibility?.center);
  } catch {
    state.navPanelHidden = false;
    state.centerPanelHidden = false;
  }
  if (!localStorage.getItem(PANEL_VISIBILITY_STORAGE_KEY)) {
    state.centerPanelHidden = localStorage.getItem(LEGACY_CENTER_PANEL_HIDDEN_STORAGE_KEY) === "1";
  }
  applyPanelVisibility({ persist: false });
}

function setPanelHidden(panel, hidden, options = {}) {
  const key = panel === "nav" ? "navPanelHidden" : "centerPanelHidden";
  const nextHidden = Boolean(hidden);
  if (state[key] === nextHidden) {
    renderPanelToggles();
    return;
  }
  stopDividerDrag();
  state[key] = nextHidden;
  applyPanelVisibility(options);
}

function setCenterPanelHidden(hidden, options = {}) {
  setPanelHidden("center", hidden, options);
}

function restoreDesktopLayout() {
  if (!appShellEl) {
    return;
  }
  if (isMobileLayout()) {
    appShellEl.classList.remove("layout-customized");
    return;
  }
  let layout = null;
  try {
    const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
    layout = raw ? JSON.parse(raw) : null;
  } catch {
    layout = null;
  }
  applyDesktopLayout(layout || buildDefaultDesktopLayout(), { persist: false });
}

function handleDividerDrag(event) {
  if (!activeDividerDrag || !appShellEl) {
    return;
  }

  const dividerWidth = getDividerWidth();
  const { contentLeft } = getShellContentMetrics();
  const total = getPanelAreaWidth();
  const mins = getDesktopMinWidths();
  const pointerX = event.clientX - contentLeft;
  const nextLayout = { ...activeDividerDrag.layout };

  if (state.navPanelHidden && activeDividerDrag.side === "right") {
    nextLayout.center = Math.min(
      Math.max(pointerX - (dividerWidth / 2), mins.center),
      Math.max(mins.center, total - mins.chat),
    );
    nextLayout.chat = total - nextLayout.center;
    activeDividerDrag.layout = clampDesktopLayout(nextLayout);
    applyDesktopLayout(activeDividerDrag.layout, { persist: false });
    return;
  }

  if (state.centerPanelHidden && activeDividerDrag.side === "left") {
    const minChat = getDesktopMinWidths().chat;
    const maxNav = total - minChat;
    nextLayout.nav = Math.min(
      Math.max(pointerX - (dividerWidth / 2), mins.nav),
      Math.max(mins.nav, maxNav),
    );
    nextLayout.chat = total - nextLayout.nav;
    activeDividerDrag.layout = clampDesktopLayout(nextLayout);
    applyDesktopLayout(activeDividerDrag.layout, { persist: false });
    return;
  }

  if (activeDividerDrag.side === "left") {
    const maxNav = total - nextLayout.chat - mins.center;
    nextLayout.nav = Math.min(
      Math.max(pointerX - (dividerWidth / 2), mins.nav),
      Math.max(mins.nav, maxNav),
    );
    nextLayout.center = total - nextLayout.chat - nextLayout.nav;
  } else {
    const maxCenter = total - nextLayout.nav - mins.chat;
    nextLayout.center = Math.min(
      Math.max(pointerX - nextLayout.nav - dividerWidth - (dividerWidth / 2), mins.center),
      Math.max(mins.center, maxCenter),
    );
    nextLayout.chat = total - nextLayout.nav - nextLayout.center;
  }

  activeDividerDrag.layout = clampDesktopLayout(nextLayout);
  applyDesktopLayout(activeDividerDrag.layout, { persist: false });
}

function stopDividerDrag() {
  if (!activeDividerDrag) {
    return;
  }
  document.body.classList.remove("is-resizing");
  activeDividerDrag.element.classList.remove("is-dragging");
  applyDesktopLayout(activeDividerDrag.layout, { persist: true });
  window.removeEventListener("mousemove", handleDividerDrag);
  window.removeEventListener("mouseup", stopDividerDrag);
  activeDividerDrag = null;
}

function startDividerDrag(side, element, event) {
  if (isMobileLayout() || !appShellEl) {
    return;
  }
  if ((side === "left" && state.navPanelHidden) || (side === "right" && state.centerPanelHidden)) {
    return;
  }
  event.preventDefault();
  restoreDesktopLayout();
  activeDividerDrag = {
    side,
    element,
    layout: desktopLayout ? { ...desktopLayout } : buildDefaultDesktopLayout(),
  };
  document.body.classList.add("is-resizing");
  element.classList.add("is-dragging");
  window.addEventListener("mousemove", handleDividerDrag);
  window.addEventListener("mouseup", stopDividerDrag);
}

function setMobilePanel(panel) {
  document.body.dataset.mobilePanel = panel;
}

function formatTimestamp(value) {
  if (!value) {
    return "";
  }
  return new Date(value).toLocaleString();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function randomClientId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    try {
      return crypto.randomUUID();
    } catch {
      // Ignore and fall through.
    }
  }
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const DATA_SOURCE_OPTIONS = [
  { value: "web", label: "互联网", summary: "使用 web_search / web_fetch，搜索全网资料" },
  { value: "mid_platform", label: "中台数据", summary: "内部报告式检索" },
  { value: "forum", label: "久谦论坛", summary: "论坛研究和会议纪要" },
  { value: "news", label: "实时全球新闻", summary: "实时全球新闻摘要，延迟小于10分钟" },
];

const ITERATION_BUDGET_OPTIONS = [
  { value: "low", label: "低", summary: "允许约 3 次迭代" },
  { value: "medium", label: "中", summary: "允许约 7 次迭代" },
  { value: "high", label: "高", summary: "允许约 11 次迭代" },
  { value: "extra_high", label: "极高", summary: "允许约 15 次迭代" },
];

function dataSourceLabel(value) {
  return (DATA_SOURCE_OPTIONS.find((item) => item.value === value) || {}).label || value;
}

function iterationBudgetLabel(value) {
  return (ITERATION_BUDGET_OPTIONS.find((item) => item.value === value) || {}).label || value;
}
