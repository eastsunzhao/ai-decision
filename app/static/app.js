const form = document.querySelector("#chatForm");
const queryInput = document.querySelector("#query");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#sendButton");
const statusBadge = document.querySelector("#statusBadge");
const resultMeta = document.querySelector("#resultMeta");
const confidence = document.querySelector("#confidence");
const modulesUsed = document.querySelector("#modulesUsed");
const skillsUsed = document.querySelector("#skillsUsed");
const taskPanel = document.querySelector("#taskPanel");
const taskPlan = document.querySelector("#taskPlan");
const executionSteps = document.querySelector("#executionSteps");
const citationsPanel = document.querySelector("#citationsPanel");
const citations = document.querySelector("#citations");
const reflectionPanel = document.querySelector("#reflectionPanel");
const reflectionSummary = document.querySelector("#reflectionSummary");
const reflectionTrace = document.querySelector("#reflectionTrace");
const followUpsPanel = document.querySelector("#followUpsPanel");
const followUps = document.querySelector("#followUps");
const evidenceHint = document.querySelector("#evidenceHint");
const evidenceList = document.querySelector("#evidenceList");
const themeButtons = document.querySelectorAll("[data-theme-option]");
const sessionList = document.querySelector("#sessionList");
const refreshSessionsButton = document.querySelector("#refreshSessionsButton");
const newChatButton = document.querySelector("#newChatButton");
const gate = document.querySelector("#directionGate");
const appShell = document.querySelector("#appShell");
const gateButtons = document.querySelectorAll("[data-gate-direction]");
const workspaceTitle = document.querySelector("#workspaceTitle");
const workspaceSubtitle = document.querySelector("#workspaceSubtitle");
const welcomeText = document.querySelector("#welcomeText");
const currentQuestion = document.querySelector("#currentQuestion");
const drawerResizeHandle = document.querySelector("#drawerResizeHandle");
const evidenceDrawer = document.querySelector("#evidenceDrawer");
const directionModal = document.querySelector("#directionModal");
const directionModalClose = document.querySelector("#directionModalClose");
const modalDirectionButtons = document.querySelectorAll("[data-modal-direction]");
const modalCloseTargets = document.querySelectorAll("[data-modal-close]");
const DEFAULT_SCENARIO_ID = "market_trend_analysis";
const DRAWER_WIDTH_KEY = "aiDecisionDrawerWidth";
const DRAWER_MIN_WIDTH = 360;
const DRAWER_MAX_WIDTH = 720;

const directionConfig = {
  competitive: {
    title: "产品竞争分析",
    subtitle: "围绕目标产品、竞品集合、渠道、差异化和风险机会进行系统研究。",
    welcome: "已选择产品竞争分析。请在右侧输入具体产品、竞品或目标市场。",
    placeholder: "例如：请对片仔癀开展系统性的竞争分析",
    query: "请对某产品开展系统性的竞争分析",
  },
  tech: {
    title: "产品技术趋势研究",
    subtitle: "围绕产品所处技术领域、演进阶段、趋势驱动和机会风险进行研究。",
    welcome: "已选择产品技术趋势研究。请在右侧输入产品或技术领域。",
    placeholder: "例如：请对吉利汽车所处的智能驾驶技术领域进行系统性趋势研究",
    query: "请对某产品所处的技术领域进行系统性趋势研究",
  },
};

const state = {
  busy: false,
  citationDetails: [],
  activeSessionId: null,
  selectedDirection: null,
  scenarioId: DEFAULT_SCENARIO_ID,
};

function applyTheme(theme) {
  const selected = theme || localStorage.getItem("aiDecisionTheme") || "system";
  if (selected === "system") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", selected);
  }
  localStorage.setItem("aiDecisionTheme", selected);
  themeButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.themeOption === selected);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(text, busy = false) {
  statusBadge.textContent = text;
  statusBadge.classList.toggle("busy", busy);
  sendButton.disabled = busy;
  state.busy = busy;
}

function clampNumber(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function drawerMaxWidth() {
  return Math.min(DRAWER_MAX_WIDTH, Math.max(DRAWER_MIN_WIDTH, window.innerWidth - 760));
}

function applyDrawerWidth(width, persist = true) {
  const max = drawerMaxWidth();
  const nextWidth = clampNumber(Math.round(width), DRAWER_MIN_WIDTH, max);
  document.documentElement.style.setProperty("--drawer-width", `${nextWidth}px`);
  if (persist) localStorage.setItem(DRAWER_WIDTH_KEY, String(nextWidth));
}

function initDrawerResize() {
  const storedWidth = Number(localStorage.getItem(DRAWER_WIDTH_KEY));
  if (storedWidth) applyDrawerWidth(storedWidth, false);
  if (!drawerResizeHandle || !evidenceDrawer) return;

  drawerResizeHandle.addEventListener("pointerdown", (event) => {
    if (window.innerWidth <= 1180) return;
    event.preventDefault();
    drawerResizeHandle.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing-drawer");
    const startX = event.clientX;
    const startWidth = evidenceDrawer.getBoundingClientRect().width;

    const onPointerMove = (moveEvent) => {
      applyDrawerWidth(startWidth + startX - moveEvent.clientX, false);
    };

    const onPointerUp = () => {
      const finalWidth = evidenceDrawer.getBoundingClientRect().width;
      applyDrawerWidth(finalWidth, true);
      document.body.classList.remove("resizing-drawer");
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp, {once: true});
  });

  window.addEventListener("resize", () => {
    const currentWidth = Number(localStorage.getItem(DRAWER_WIDTH_KEY));
    if (currentWidth) applyDrawerWidth(currentWidth, false);
  });
}

function openDirectionModal() {
  directionModal.classList.remove("hidden");
  directionModal.setAttribute("aria-hidden", "false");
  const firstOption = directionModal.querySelector("[data-modal-direction]");
  firstOption?.focus();
}

function closeDirectionModal() {
  directionModal.classList.add("hidden");
  directionModal.setAttribute("aria-hidden", "true");
  newChatButton.focus();
}

function enterDirection(direction, presetQuery) {
  const config = directionConfig[direction] || directionConfig.competitive;
  state.selectedDirection = direction;
  gate.classList.add("hidden");
  appShell.classList.remove("hidden");
  workspaceTitle.textContent = config.title;
  workspaceSubtitle.textContent = config.subtitle;
  welcomeText.textContent = config.welcome;
  currentQuestion.textContent = "等待输入分析问题";
  queryInput.placeholder = config.placeholder;
  queryInput.value = presetQuery === undefined ? config.query : presetQuery;
  resetWorkspace();
  queryInput.focus();
}

function addMessage(role, text) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const avatar = role === "user" ? "我" : "AI";
  article.innerHTML = `
    <div class="avatar">${avatar}</div>
    <div class="bubble"><p>${escapeHtml(text)}</p></div>
  `;
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

function addAssistantShell() {
  const article = document.createElement("article");
  article.className = "message assistant";
  article.innerHTML = `
    <div class="avatar">AI</div>
    <div class="bubble structured-output">
      <div id="streamText" class="stream-text streaming">正在分析...</div>
      <div id="sectionOutput" class="section-output"></div>
    </div>
  `;
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
  return {
    streamText: article.querySelector("#streamText"),
    sectionOutput: article.querySelector("#sectionOutput"),
  };
}

function renderMeta(data) {
  state.citationDetails = data.citation_details || [];
  resultMeta.classList.remove("hidden");
  confidence.textContent = Number(data.confidence || 0).toFixed(2);
  modulesUsed.textContent = (data.modules_used || []).join(", ") || "-";
  skillsUsed.textContent = (data.skills_used || []).join(", ") || "-";
  if ((data.skills_used || []).length > 0) {
    // Skills are shown in result meta; no separate sidebar mutation needed.
  }

  citations.innerHTML = "";
  if ((data.citations || []).length > 0) {
    citationsPanel.classList.remove("hidden");
    data.citations.forEach((item) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = item;
      chip.addEventListener("click", () => showEvidence([item]));
      citations.appendChild(chip);
    });
  } else {
    citationsPanel.classList.add("hidden");
  }

  followUps.innerHTML = "";
  if ((data.follow_up_questions || []).length > 0) {
    followUpsPanel.classList.remove("hidden");
    data.follow_up_questions.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "followup";
      button.textContent = item;
      button.addEventListener("click", () => {
        queryInput.value = item;
        queryInput.focus();
      });
      followUps.appendChild(button);
    });
  } else {
    followUpsPanel.classList.add("hidden");
  }

  renderReflection(data.reflection || {});
  renderTaskPanel(data.task_plan || [], data.execution_steps || []);
}

function renderTaskPanel(plan, steps) {
  if (plan.length === 0 && steps.length === 0) {
    taskPanel.classList.add("hidden");
    return;
  }
  taskPanel.classList.remove("hidden");
  taskPlan.innerHTML = "";
  plan.forEach((item) => {
    const node = document.createElement("details");
    node.className = `task-item ${item.status || "pending"}`;
    node.open = item.status === "running";
    node.innerHTML = `<summary><strong>${escapeHtml(item.name || item.id)}</strong></summary><span>${escapeHtml(item.description || "")}</span>`;
    taskPlan.appendChild(node);
  });

  executionSteps.innerHTML = "";
  steps.forEach((item, index) => appendStep(item.message || item.name || "-", item.status || "completed", index === steps.length - 1));
}

function appendStep(message, status = "running", forceOpen = true) {
  taskPanel.classList.remove("hidden");
  const node = document.createElement("details");
  node.className = `step-item ${status}`;
  node.open = forceOpen && status === "running";
  node.innerHTML = `<summary>${escapeHtml(message)}</summary><span>${escapeHtml(status)}</span>`;
  executionSteps.appendChild(node);
  executionSteps.scrollTop = executionSteps.scrollHeight;
}

function renderSections(sections, container, finalAnswer = "") {
  container.innerHTML = "";
  (sections || []).forEach((section) => {
    const block = document.createElement("details");
    block.className = "answer-section";
    block.innerHTML = `
      <summary class="section-title">
        <span>${escapeHtml(section.title || "-")}</span>
        <small>展开 / 查看引用</small>
      </summary>
      <div class="markdown-content">${markdownToHtml(section.summary || "")}</div>
      ${renderTable(section.columns || [], section.rows || [])}
    `;
    block.querySelector(".section-title").addEventListener("click", () => showEvidence(section.citations || []));
    container.appendChild(block);
  });
  if (finalAnswer) {
    const finalBlock = document.createElement("section");
    finalBlock.className = "final-answer";
    finalBlock.innerHTML = `
      <div class="final-kicker">最终推荐答案</div>
      <h3>分析结论</h3>
      <div class="markdown-content">${markdownToHtml(finalAnswer)}</div>
    `;
    container.appendChild(finalBlock);
  }
}

function markdownToHtml(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      html.push("</ul>");
      listOpen = false;
    }
  };

  lines.forEach((line) => {
    const raw = line.trim();
    if (!raw) {
      closeList();
      return;
    }
    if (/^---+$/.test(raw)) {
      closeList();
      html.push("<hr />");
      return;
    }
    const heading = raw.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 2, 6);
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      return;
    }
    const listItem = raw.match(/^[-*]\s+(.+)$/);
    if (listItem) {
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${inlineMarkdown(listItem[1])}</li>`);
      return;
    }
    closeList();
    html.push(`<p>${inlineMarkdown(raw)}</p>`);
  });
  closeList();
  return html.join("");
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function renderTable(columns, rows) {
  if (!columns.length || !rows.length) return "";
  const head = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
  const body = rows
    .map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? "")}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function showEvidence(citationIds) {
  const ids = new Set(citationIds || []);
  const docs = state.citationDetails.filter((doc) => ids.size === 0 || ids.has(doc.id));
  evidenceHint.textContent = docs.length ? `当前展示 ${docs.length} 条引用语料。` : "未找到对应引用语料。";
  evidenceList.innerHTML = "";
  docs.forEach((doc) => {
    const node = document.createElement("article");
    node.className = "evidence-card";
    node.innerHTML = `
      <strong>${escapeHtml(doc.title || doc.id)}</strong>
      <span>${escapeHtml(doc.id)} · ${escapeHtml(doc.source || "unknown")}</span>
      <p>${escapeHtml(doc.text || "")}</p>
    `;
    evidenceList.appendChild(node);
  });
}

function renderReflection(reflection) {
  const trace = reflection.trace || [];
  const relatedEntities = reflection.related_entities || [];
  const coverage = reflection.evidence_coverage || {};

  if (!reflection.iterations && trace.length === 0 && relatedEntities.length === 0) {
    reflectionPanel.classList.add("hidden");
    return;
  }

  reflectionPanel.classList.remove("hidden");
  const covered = Object.values(coverage).filter(Boolean).length;
  const total = Object.keys(coverage).length;
  reflectionSummary.innerHTML = `
    <div><span>迭代次数</span><strong>${escapeHtml(reflection.iterations || 0)}</strong></div>
    <div><span>关联实体</span><strong>${escapeHtml(relatedEntities.join("、") || "-")}</strong></div>
    <div><span>证据覆盖</span><strong>${escapeHtml(total ? `${covered}/${total}` : "-")}</strong></div>
  `;

  reflectionTrace.innerHTML = "";
  trace.forEach((item) => {
    const node = document.createElement("div");
    node.className = "trace-item";
    node.innerHTML = `
      <span>第 ${escapeHtml(item.iteration)} 轮 · 新增文档 ${escapeHtml(item.new_doc_count)}</span>
      <strong>${escapeHtml((item.entities || []).join("、") || "-")}</strong>
      <span>停止/状态：${escapeHtml(item.stop_reason || "-")}</span>
    `;
    reflectionTrace.appendChild(node);
  });
}

async function loadSessions() {
  try {
    const response = await fetch("/api/sessions");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderSessions(data.sessions || []);
  } catch (error) {
    sessionList.innerHTML = `<div class="empty-state">历史会话加载失败</div>`;
  }
}

function renderSessions(sessions) {
  sessionList.innerHTML = "";
  if (!sessions.length) {
    sessionList.innerHTML = `
      <div class="session-label">当前</div>
      <div class="session-label muted-label">暂无更多</div>
    `;
    return;
  }
  const currentSession = sessions.find((item) => item.session_id === state.activeSessionId);
  if (currentSession) {
    sessionList.appendChild(createSessionLabel("当前"));
    sessionList.appendChild(createSessionButton(currentSession));
  }
  sessionList.appendChild(createSessionLabel("今天"));
  sessions.forEach((item) => {
    if (item.session_id !== state.activeSessionId) {
      sessionList.appendChild(createSessionButton(item));
    }
  });
  sessionList.appendChild(createSessionLabel("暂无更多", "muted-label"));
}

function createSessionLabel(text, extraClass = "") {
  const label = document.createElement("div");
  label.className = `session-label ${extraClass}`.trim();
  label.textContent = text;
  return label;
}

function createSessionButton(item) {
  const row = document.createElement("div");
  row.className = `session-row ${item.session_id === state.activeSessionId ? "active" : ""}`;
  row.innerHTML = `
    <button type="button" class="session-item">
      <strong>${escapeHtml(item.title || item.query || "未命名分析")}</strong>
      <span>${escapeHtml(formatSessionTime(item.created_at))}</span>
    </button>
    <button type="button" class="session-delete" title="删除对话" aria-label="删除对话">×</button>
  `;
  row.querySelector(".session-item").addEventListener("click", () => loadSessionDetail(item.session_id));
  row.querySelector(".session-delete").addEventListener("click", (event) => {
    event.stopPropagation();
    deleteSession(item.session_id);
  });
  return row;
}

function formatSessionTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}

async function loadSessionDetail(sessionId) {
  const response = await fetch(`/api/sessions/${sessionId}`);
  if (!response.ok) return;
  const session = await response.json();
  state.activeSessionId = sessionId;
  resetWorkspace();
  currentQuestion.textContent = session.query || "历史分析";
  const assistant = addAssistantShell();
  const data = session.response || {};
  assistant.streamText.textContent = data.answer ? "已加载历史分析结果。" : "该历史会话没有可展示结果。";
  assistant.streamText.classList.remove("streaming");
  renderSections(data.answer_sections || [], assistant.sectionOutput, data.answer || "");
  renderMeta(data);
  showEvidence([]);
  loadSessions();
}

async function deleteSession(sessionId) {
  const response = await fetch(`/api/sessions/${sessionId}`, {method: "DELETE"});
  if (!response.ok) return;
  if (state.activeSessionId === sessionId) {
    state.activeSessionId = null;
    resetWorkspace();
    currentQuestion.textContent = "等待输入分析问题";
  }
  await loadSessions();
}

function resetWorkspace() {
  messages.innerHTML = "";
  taskPanel.classList.add("hidden");
  resultMeta.classList.add("hidden");
  reflectionPanel.classList.add("hidden");
  citationsPanel.classList.add("hidden");
  followUpsPanel.classList.add("hidden");
  evidenceList.innerHTML = "";
  evidenceHint.textContent = "点击报告中的分类或引用，查看对应语料。";
}

async function submitQuestion(event) {
  event.preventDefault();
  if (state.busy) return;

  const query = queryInput.value.trim();
  if (!query) return;

  currentQuestion.textContent = query;
  queryInput.value = "";
  setStatus("分析中", true);
  taskPanel.classList.remove("hidden");
  taskPlan.innerHTML = "";
  executionSteps.innerHTML = "";
  const assistant = addAssistantShell();

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        scenario_id: state.scenarioId,
        query,
      }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    await readStream(response, assistant);
    await loadSessions();
    setStatus("就绪", false);
  } catch (error) {
    assistant.streamText.classList.remove("streaming");
    addMessage("assistant", `请求失败：${error.message}`);
    setStatus("异常", false);
  }
}

async function readStream(response, assistant) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    chunks.forEach((chunk) => {
      const line = chunk.split("\n").find((item) => item.startsWith("data: "));
      if (!line) return;
      const event = JSON.parse(line.slice(6));
      handleStreamEvent(event, assistant);
    });
  }
}

function handleStreamEvent(event, assistant) {
  if (event.type === "step") {
    assistant.streamText.textContent = event.message || "执行中...";
    collapseRunningSteps();
    appendStep(event.message || event.name, event.status || "running", event.status === "running");
    const plan = event.state?.task_plan || [];
    if (plan.length) renderTaskPanel(plan, event.state?.execution_steps || []);
    return;
  }

  if (event.type === "final") {
    const data = event.data || {};
    state.activeSessionId = data.session_id || null;
    assistant.streamText.classList.remove("streaming");
    assistant.streamText.textContent = data.answer ? "分析完成，分类结果如下。" : "未返回答案。";
    renderSections(data.answer_sections || [], assistant.sectionOutput, data.answer || "");
    renderMeta(data);
    showEvidence([]);
  }
}

function collapseRunningSteps() {
  executionSteps.querySelectorAll("details.step-item[open]").forEach((node) => {
    node.open = false;
  });
}

form.addEventListener("submit", submitQuestion);
refreshSessionsButton.addEventListener("click", loadSessions);
newChatButton.addEventListener("click", () => {
  openDirectionModal();
});
gateButtons.forEach((button) => {
  button.addEventListener("click", () => {
    enterDirection(button.dataset.gateDirection, button.dataset.directionQuery || "");
  });
});
modalDirectionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.activeSessionId = null;
    state.selectedDirection = null;
    closeDirectionModal();
    enterDirection(button.dataset.modalDirection, button.dataset.directionQuery || "");
    loadSessions();
  });
});
modalCloseTargets.forEach((target) => {
  target.addEventListener("click", closeDirectionModal);
});
directionModalClose.addEventListener("click", closeDirectionModal);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !directionModal.classList.contains("hidden")) {
    closeDirectionModal();
  }
});
themeButtons.forEach((button) => {
  button.addEventListener("click", () => applyTheme(button.dataset.themeOption));
});
queryInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    form.requestSubmit();
  }
});

applyTheme();
initDrawerResize();
enterDirection("competitive", "");
loadSessions();
