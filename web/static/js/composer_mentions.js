function handleMessageInputKeydown(event) {
  if (state.mentionMenu.open) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      const count = state.mentionMenu.items.length || 1;
      state.mentionMenu.selectedIndex = (state.mentionMenu.selectedIndex + delta + count) % count;
      renderMentionMenu();
      return;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      const item = state.mentionMenu.items[state.mentionMenu.selectedIndex];
      if (item) {
        selectMentionItem(item);
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeMentionMenu();
      return;
    }
  }
  if (event.key !== "Enter" || event.ctrlKey || messageInputComposing || event.isComposing) {
    return;
  }
  event.preventDefault();
  composerEl.requestSubmit();
}

function mentionItemMatches(item, query) {
  if (!query) {
    return true;
  }
  const haystack = `${item.label} ${item.value} ${item.summary || ""}`.toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function buildMentionItems(kind, query = "") {
  const items = [];
  if (kind === "skill" || kind === "skill-button") {
    for (const skill of state.skills) {
      if (!skill.activatable || !ACTIVATABLE_SKILLS.has(skill.name)) {
        continue;
      }
      const item = {
        type: "skill",
        value: skill.name,
        label: skill.name,
        summary: skill.summary || "技能",
        group: "技能",
      };
      if (mentionItemMatches(item, query)) {
        items.push(item);
      }
    }
    return items;
  }
  if (kind === "effort-button") {
    for (const option of ITERATION_BUDGET_OPTIONS) {
      const item = {
        type: "effort",
        value: option.value,
        label: option.label,
        summary: option.summary,
        group: "智能体强度",
      };
      if (mentionItemMatches(item, query)) {
        items.push(item);
      }
    }
    return items;
  }
  for (const source of DATA_SOURCE_OPTIONS) {
    const item = {
      type: "source",
      value: source.value,
      label: source.label,
      summary: source.summary,
      group: "来源",
    };
    if (mentionItemMatches(item, query)) {
      items.push(item);
    }
  }
  for (const file of flattenWorkspaceFiles()) {
    const item = {
      type: "file",
      value: file.path,
      label: file.name,
      summary: file.path,
      group: "文件",
    };
    if (mentionItemMatches(item, query)) {
      items.push(item);
    }
  }
  return items;
}

function openMentionMenu(kind, query = "", start = messageInputEl.selectionStart, end = messageInputEl.selectionEnd) {
  const items = buildMentionItems(kind, query);
  state.mentionMenu = {
    open: true,
    kind,
    query,
    start,
    end,
    selectedIndex: 0,
    items,
  };
  renderMentionMenu();
}

function renderMentionMenu() {
  const menu = state.mentionMenu;
  if (!menu.open) {
    closeMentionMenu();
    return;
  }
  mentionMenuEl.classList.remove("hidden");
  if (!menu.items.length) {
    mentionMenuEl.innerHTML = '<div class="mention-empty">没有匹配项</div>';
    return;
  }
  mentionMenuEl.innerHTML = "";
  let previousGroup = "";
  menu.items.forEach((item, index) => {
    if (item.group !== previousGroup) {
      const group = document.createElement("div");
      group.className = "mention-group";
      group.textContent = item.group;
      mentionMenuEl.appendChild(group);
      previousGroup = item.group;
    }
    const selected = index === menu.selectedIndex;
    const checked = (item.type === "source" && state.pendingMentions.sources.includes(item.value))
      || (item.type === "effort" && state.pendingMentions.iterationBudget === item.value)
      || (item.type === "file" && state.pendingMentions.files.includes(item.value));
    const row = document.createElement("button");
    row.type = "button";
    row.className = `mention-item${selected ? " selected" : ""}`;
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", selected ? "true" : "false");
    row.innerHTML = `
      <span class="mention-item-main">
        <span class="mention-item-name">${escapeHtml(item.label)}</span>
        <span class="mention-item-summary">${escapeHtml(item.summary || item.value)}</span>
      </span>
      ${checked ? '<span class="mention-item-check">✓</span>' : ""}
    `;
    row.addEventListener("mouseenter", () => {
      if (state.mentionMenu.selectedIndex === index) {
        return;
      }
      state.mentionMenu.selectedIndex = index;
      renderMentionMenu();
    });
    row.addEventListener("click", () => selectMentionItem(item));
    mentionMenuEl.appendChild(row);
  });
}

function replaceMentionTrigger() {
  const { start, end } = state.mentionMenu;
  const value = messageInputEl.value;
  messageInputEl.value = `${value.slice(0, start)}${value.slice(end)}`.replace(/\s{2,}/g, " ");
  const nextPos = start;
  messageInputEl.setSelectionRange(nextPos, nextPos);
  resizeMessageInput();
}

function selectMentionItem(item) {
  if (item.type === "skill") {
    addPendingSkill(item.value);
  } else if (item.type === "source") {
    togglePendingSource(item.value);
  } else if (item.type === "effort") {
    setPendingIterationBudget(item.value);
  } else if (item.type === "file") {
    addPendingFile(item.value);
  }
  if (!state.mentionMenu.kind.endsWith("-button")) {
    replaceMentionTrigger();
  }
  if (state.mentionMenu.kind !== "source-button") {
    closeMentionMenu();
  } else {
    openMentionMenu("source-button");
  }
  messageInputEl.focus();
}

function updateMentionMenuFromInput() {
  const caret = messageInputEl.selectionStart;
  const before = messageInputEl.value.slice(0, caret);
  const match = before.match(/(^|\s)([$@])([^\s$@]*)$/);
  if (!match) {
    if (!state.mentionMenu.kind.endsWith("-button")) {
      closeMentionMenu();
    }
    return;
  }
  const trigger = match[2];
  const query = match[3] || "";
  const start = caret - trigger.length - query.length;
  const kind = trigger === "$" ? "skill" : "mention";
  openMentionMenu(kind, query, start, caret);
}

function showSkillPicker() {
  openMentionMenu("skill-button", "", messageInputEl.selectionStart, messageInputEl.selectionEnd);
}

function showSourcePicker() {
  openMentionMenu("source-button", "", messageInputEl.selectionStart, messageInputEl.selectionEnd);
}

function showEffortPicker() {
  openMentionMenu("effort-button", "", messageInputEl.selectionStart, messageInputEl.selectionEnd);
}

function handleDocumentKeydown(event) {
  const isSaveShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s";
  if (!isSaveShortcut) {
    return;
  }
  const doc = state.currentDocument;
  if (!doc || doc.type !== "file" || !doc.file.editable) {
    return;
  }
  event.preventDefault();
  saveCurrentFile().catch((error) => {
    centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
  });
}

function resizeMessageInput() {
  messageInputEl.style.height = "auto";
  messageInputEl.style.height = `${Math.min(messageInputEl.scrollHeight, 220)}px`;
}

async function handlePreferredDataSourceChange() {
  showSourcePicker();
}
