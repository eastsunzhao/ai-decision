function closeMentionMenu() {
  state.mentionMenu = {
    open: false,
    kind: "",
    query: "",
    start: 0,
    end: 0,
    selectedIndex: 0,
    items: [],
  };
  mentionMenuEl.classList.add("hidden");
  mentionMenuEl.innerHTML = "";
}

function addPendingSkill(skillName) {
  if (!ACTIVATABLE_SKILLS.has(skillName)) {
    return;
  }
  state.pendingMentions.skill = skillName;
  if (!ITERATION_BUDGETS.has(state.pendingMentions.iterationBudget)) {
    state.pendingMentions.iterationBudget = "medium";
  }
  renderPendingMentions();
  renderContextChips();
  renderSkillsList();
  renderComposerToolState();
}

function setPendingIterationBudget(value) {
  const budget = String(value || "").trim().toLowerCase().replaceAll("-", "_");
  if (!ITERATION_BUDGETS.has(budget)) {
    return;
  }
  state.pendingMentions.iterationBudget = budget;
  renderPendingMentions();
  renderContextChips();
  renderComposerToolState();
}

function togglePendingSource(source) {
  if (!PREFERRED_DATA_SOURCES.has(source)) {
    return;
  }
  const sources = new Set(state.pendingMentions.sources);
  if (sources.has(source)) {
    sources.delete(source);
  } else {
    sources.add(source);
  }
  state.pendingMentions.sources = [...sources];
  renderPendingMentions();
  renderContextChips();
}

function addPendingFile(path) {
  const normalized = normalizeRelativePath(path);
  if (!normalized) {
    return;
  }
  if (!state.pendingMentions.files.includes(normalized)) {
    state.pendingMentions.files.push(normalized);
  }
  renderPendingMentions();
  renderContextChips();
  renderCenterHeader();
}

function togglePendingFile(path) {
  const normalized = normalizeRelativePath(path);
  if (!normalized) {
    return;
  }
  if (state.pendingMentions.files.includes(normalized)) {
    removePendingMention("file", normalized);
    return;
  }
  addPendingFile(normalized);
}

function clearPendingMentions() {
  resetPendingMentionState();
  closeMentionMenu();
  renderPendingMentions();
  renderContextChips();
  renderSkillsList();
  renderComposerToolState();
  renderCenterHeader();
}

function resetPendingMentionState() {
  state.pendingMentions = { skill: "", sources: [], iterationBudget: "medium", files: [] };
}

async function activateSkill(skillName) {
  if (!currentSession() || !ACTIVATABLE_SKILLS.has(skillName)) {
    return;
  }
  addPendingSkill(skillName);
  updateCurrentSessionLocally({
    ui_state: {
      ...currentUiState(),
      selected_skill: skillName,
      active_path: "",
      center_view: "skill",
      left_tab: "skills",
    },
  });
  renderWorkbench();
}

function removePendingMention(kind, value) {
  if (kind === "skill") {
    state.pendingMentions.skill = "";
    state.pendingMentions.iterationBudget = "medium";
  } else if (kind === "source") {
    state.pendingMentions.sources = state.pendingMentions.sources.filter((item) => item !== value);
  } else if (kind === "effort") {
    state.pendingMentions.iterationBudget = "medium";
  } else if (kind === "file") {
    state.pendingMentions.files = state.pendingMentions.files.filter((item) => item !== value);
  }
  renderPendingMentions();
  renderContextChips();
  renderSkillsList();
  renderComposerToolState();
}

function renderPendingMentions() {
  pendingMentionsEl.innerHTML = "";
  const mentions = currentPendingMentions();
  const chips = [];
  if (mentions.skill) {
    chips.push({ kind: "skill", label: "技能", value: mentions.skill, text: mentions.skill });
  }
  for (const source of mentions.sources) {
    chips.push({ kind: "source", label: "来源", value: source, text: dataSourceLabel(source) });
  }
  for (const file of mentions.files) {
    chips.push({ kind: "file", label: "文件", value: file, text: file });
  }
  pendingMentionsEl.classList.toggle("hidden", chips.length === 0);
  for (const chip of chips) {
    const item = document.createElement("span");
    item.className = "pending-chip";
    item.innerHTML = `
      <span class="pending-chip-label">${escapeHtml(chip.label)}</span>
      <span class="pending-chip-value">${escapeHtml(chip.text)}</span>
      <button type="button" class="pending-chip-remove" aria-label="移除${escapeHtml(chip.label)} ${escapeHtml(chip.text)}">×</button>
    `;
    item.querySelector("button").addEventListener("click", () => removePendingMention(chip.kind, chip.value));
    pendingMentionsEl.appendChild(item);
  }
}

function buildTurnContextPayload() {
  const mentions = currentPendingMentions();
  return {
    skills: mentions.skill ? [mentions.skill] : [],
    preferred_data_sources: mentions.sources,
    iteration_budget: mentions.skill && mentions.skill !== "simple" ? mentions.iterationBudget : "medium",
    files: mentions.files,
  };
}

function renderComposerToolState() {
  const mentions = currentPendingMentions();
  const showEffort = Boolean(mentions.skill && mentions.skill !== "simple");
  agentEffortButtonEl.classList.toggle("hidden", !showEffort);
  if (showEffort) {
    const valueEl = agentEffortButtonEl.querySelector(".effort-button-value");
    if (valueEl) {
      valueEl.textContent = iterationBudgetLabel(mentions.iterationBudget);
    }
    agentEffortButtonEl.title = "选择智能体投入强度";
  }
}

function buildContextChips() {
  return [];
}

function renderContextChips() {
  contextChipsEl.innerHTML = "";
  for (const chip of buildContextChips()) {
    const item = document.createElement("span");
    item.className = "context-chip";
    item.innerHTML = `<span class="context-chip-label">${escapeHtml(chip.label)}</span><span class="context-chip-value">${escapeHtml(chip.value)}</span>`;
    contextChipsEl.appendChild(item);
  }
}

function sessionById(sessionId) {
  return state.sessions.find((session) => session.id === sessionId) || null;
}

function ensureHistory(sessionId) {
  if (!state.histories.has(sessionId)) {
    state.histories.set(sessionId, []);
  }
  return state.histories.get(sessionId);
}

function getSessionActivity(sessionId) {
  const session = sessionById(sessionId);
  const active = state.activeJobs.get(sessionId);
  const isRunning = Boolean(session && session.status === "running");
  const isStopping = Boolean(active && active.stopRequested);
  return { session, active, isRunning, isStopping };
}

function renderComposerAction() {
  const { session, isRunning, isStopping } = getSessionActivity(state.currentSessionId);
  const showStop = Boolean(session && isRunning);
  sendButtonEl.classList.toggle("stop-button", showStop);
  sendButtonEl.querySelector(".send-icon").classList.toggle("hidden", showStop);
  sendButtonEl.querySelector(".stop-icon").classList.toggle("hidden", !showStop);
  sendButtonEl.setAttribute("aria-label", showStop ? "停止会话" : "发送消息");
  sendButtonTextEl.textContent = showStop ? "停止" : "发送";
  sendButtonEl.disabled = !session || (showStop ? isStopping : false);
}
