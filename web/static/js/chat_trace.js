const SUBAGENT_ACCENT_PALETTE = [
  "#7c3aed",
  "#ea580c",
  "#dc2626",
  "#0891b2",
  "#c026d3",
  "#ca8a04",
  "#2563eb",
  "#16a34a",
  "#9333ea",
  "#be123c",
];

function subagentAccentColor(subagentId, paletteIndex = null) {
  const palette = SUBAGENT_ACCENT_PALETTE;
  if (Number.isInteger(paletteIndex) && paletteIndex >= 0) {
    return palette[paletteIndex % palette.length];
  }
  const key = String(subagentId || "").trim();
  if (!key) {
    return palette[0];
  }
  let hash = 0;
  for (let index = 0; index < key.length; index += 1) {
    hash = ((hash * 31) + key.charCodeAt(index)) >>> 0;
  }
  return palette[hash % palette.length];
}

function subagentAccentFromMap(subagentId, colorMap) {
  const key = String(subagentId || "unknown").trim() || "unknown";
  if (colorMap instanceof Map) {
    if (!colorMap.has(key)) {
      colorMap.set(key, subagentAccentColor(key, colorMap.size));
    }
    return colorMap.get(key);
  }
  return subagentAccentColor(key);
}

function traceAgentAccentColor(agentKey) {
  return agentKey === "main" ? "#2563eb" : subagentAccentColor(agentKey.replace(/^subagent:/, ""));
}

function isSubagentTraceItem(item) {
  return Boolean(item && typeof item.type === "string" && item.type.startsWith("subagent_"));
}

function traceItemMeta(item) {
  return item && item.meta && typeof item.meta === "object" ? item.meta : {};
}

function traceLabel(item) {
  const meta = traceItemMeta(item);
  const subagentId = typeof meta.subagent_id === "string" ? meta.subagent_id.trim() : "";
  const subagentLabel = typeof meta.label === "string" && meta.label.trim()
    ? meta.label.trim()
    : subagentId
      ? subagentId
      : "";
  const subagentPrefix = subagentLabel ? `子智能体 · ${subagentLabel}` : "子智能体";
  const toolName = typeof meta.tool_name === "string" && meta.tool_name.trim() ? meta.tool_name.trim() : "tool";
  return item.type === "tool_hint"
    ? "工具调用"
    : item.type === "tool_result"
      ? "工具结果"
      : item.type === "tool_call_start"
        ? "工具调用开始"
        : item.type === "tool_call_end"
          ? "工具调用结束"
          : item.type === "tool_call_output"
            ? "工具输出"
            : item.type === "retry_wait"
              ? "正在重试"
              : item.type === "subagent_start"
                ? `${subagentPrefix} · 已启动`
                : item.type === "subagent_progress"
                  ? `${subagentPrefix} · 进展`
                  : item.type === "subagent_tool_start"
                    ? `${subagentPrefix} · ${toolName} 已启动`
                    : item.type === "subagent_tool_end"
                      ? `${subagentPrefix} · ${toolName} ${meta.status === "error" ? "失败" : "完成"}`
                      : item.type === "subagent_done"
                        ? `${subagentPrefix} · 完成`
                        : item.type === "subagent_caveat"
                          ? `${subagentPrefix} · 注意事项`
                          : item.type === "subagent_error"
                            ? `${subagentPrefix} · 错误`
                            : item.type === "subagent_barrier"
                              ? "子智能体 · 同步点"
                              : "进展";
}

function traceShortLabel(item) {
  const meta = traceItemMeta(item);
  const toolName = typeof meta.tool_name === "string" && meta.tool_name.trim() ? meta.tool_name.trim() : "tool";
  return item.type === "subagent_start"
    ? "已启动"
    : item.type === "subagent_progress"
      ? "进展"
      : item.type === "subagent_tool_start"
        ? `${toolName} 已启动`
        : item.type === "subagent_tool_end"
          ? `${toolName} ${meta.status === "error" ? "失败" : "完成"}`
          : item.type === "subagent_done"
            ? "完成"
            : item.type === "subagent_caveat"
              ? "注意事项"
              : item.type === "subagent_error"
                ? "错误"
                : item.type === "subagent_barrier"
                  ? "同步点"
                  : traceLabel(item);
}

function subagentDisplayName(item, fallback = "子智能体") {
  const meta = traceItemMeta(item);
  const label = typeof meta.label === "string" && meta.label.trim() ? meta.label.trim() : "";
  const subagentId = typeof meta.subagent_id === "string" && meta.subagent_id.trim() ? meta.subagent_id.trim() : "";
  return label || subagentId || fallback;
}

function traceAgentInfoFromItem(item, subagentColorMap = null) {
  const meta = traceItemMeta(item);
  if (isSubagentTraceItem(item)) {
    const subagentId = typeof meta.subagent_id === "string" && meta.subagent_id.trim()
      ? meta.subagent_id.trim()
      : "unknown";
    return {
      key: `subagent:${subagentId}`,
      id: subagentId,
      role: "subagent",
      label: subagentDisplayName(item, subagentId),
      accent: subagentAccentFromMap(subagentId, subagentColorMap),
    };
  }
  return {
    key: "main",
    id: "main",
    role: "main",
    label: "久谦研究AI",
    accent: traceAgentAccentColor("main"),
  };
}

function isTraceAgentRecentlyUpdated(agentKey) {
  const updatedAt = state.recentTraceAgents.get(agentKey);
  if (!updatedAt) {
    return false;
  }
  if (Date.now() - updatedAt > TRACE_UPDATE_HIGHLIGHT_MS) {
    state.recentTraceAgents.delete(agentKey);
    return false;
  }
  return true;
}

function markTraceAgentUpdated(item) {
  const info = traceAgentInfoFromItem(item);
  const updatedAt = Date.now();
  state.recentTraceAgents.set(info.key, updatedAt);
  window.setTimeout(() => {
    if (state.recentTraceAgents.get(info.key) !== updatedAt) {
      return;
    }
    state.recentTraceAgents.delete(info.key);
    renderAgentTraceControls();
    renderMessages();
  }, TRACE_UPDATE_HIGHLIGHT_MS + 80);
}

function createTraceElement(item, options = {}) {
  const row = document.createElement("article");
  row.className = `timeline-event ${item.type}`;
  if (options.compact) {
    row.classList.add("compact-trace-event");
  }
  const meta = item.meta && typeof item.meta === "object" ? item.meta : {};
  const traceStatus = typeof meta.status === "string" ? meta.status.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-") : "";
  if (traceStatus) {
    row.classList.add(`status-${traceStatus}`);
  }
  const subagentId = typeof meta.subagent_id === "string" ? meta.subagent_id.trim() : "";
  if (item.type.startsWith("subagent_")) {
    row.classList.add("subagent-event");
    row.style.setProperty("--subagent-accent", options.accent || subagentAccentColor(subagentId));
  }
  row.innerHTML = `
    <div class="timeline-meta">
      <span class="timeline-label">${escapeHtml(traceLabel(item))}</span>
      <span class="timeline-time">${escapeHtml(formatTimestamp(item.created_at))}</span>
    </div>
    <pre class="trace-text"></pre>
  `;
  row.querySelector(".trace-text").textContent = item.text || "";
  return row;
}

function formatToolArgValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value.replace(/\s+/g, " ").trim();
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "[]";
    }
    const preview = value
      .slice(0, 3)
      .map((item) => formatToolArgValue(item))
      .filter(Boolean)
      .join(", ");
    return value.length > 3 ? `[${preview}, ...]` : `[${preview}]`;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (entries.length === 0) {
      return "{}";
    }
    const preview = entries
      .slice(0, 3)
      .map(([key, item]) => `${key}: ${formatToolArgValue(item)}`)
      .join(", ");
    return entries.length > 3 ? `{${preview}, ...}` : `{${preview}}`;
  }
  return String(value);
}

function formatToolArgsPreview(args, fallback = "", maxLines = 3, maxValueChars = 120) {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return fallback || "";
  }
  const lines = Object.entries(args).slice(0, maxLines).map(([key, value]) => {
    let rendered = formatToolArgValue(value);
    if (rendered.length > maxValueChars) {
      rendered = `${rendered.slice(0, maxValueChars).trimEnd()}...`;
    }
    return `${key}: ${rendered}`;
  });
  const remaining = Object.keys(args).length - lines.length;
  if (remaining > 0 && lines.length > 0) {
    lines[lines.length - 1] = `${lines[lines.length - 1]} (+${remaining} more)`;
  }
  return lines.join("\n");
}

function createToolCallElement(block) {
  const startMeta = block.start && block.start.meta && typeof block.start.meta === "object" ? block.start.meta : {};
  const endMeta = block.end && block.end.meta && typeof block.end.meta === "object" ? block.end.meta : {};
  const toolName = block.toolName || startMeta.tool_name || endMeta.tool_name || "tool";
  const status = endMeta.status || (block.end ? "ok" : "running");
  const durationMs = Number.isFinite(Number(endMeta.duration_ms)) ? Number(endMeta.duration_ms) : null;
  const commandPreview = formatToolArgsPreview(
    startMeta.args,
    typeof startMeta.args_preview === "string" ? startMeta.args_preview : "",
  );
  const headerParts = [toolName, status];
  if (durationMs !== null) {
    headerParts.push(`${durationMs} ms`);
  }

  const isResultBlock = Boolean(block.end);
  const row = document.createElement("details");
  row.className = `timeline-event tool-call-group ${isResultBlock ? "tool-call-result-group" : "tool-call-invocation-group"}`;
  row.open = false;
  row.innerHTML = `
    <summary class="tool-call-summary">
      <div class="timeline-meta">
        <span class="timeline-label">${escapeHtml(`${isResultBlock ? "工具结果" : "工具调用"} · ${headerParts.join(" · ")}`)}</span>
        <span class="timeline-time">${escapeHtml(formatTimestamp((block.end || block.start || {}).created_at))}</span>
      </div>
      ${commandPreview ? '<pre class="trace-text tool-call-command"></pre>' : ""}
    </summary>
    <pre class="trace-text tool-call-output"></pre>
  `;
  const commandEl = row.querySelector(".tool-call-command");
  if (commandEl) {
    commandEl.textContent = commandPreview;
    commandEl.addEventListener("click", (event) => {
      event.preventDefault();
      row.open = !row.open;
    });
  }
  const outputLines = [];
  for (const outputItem of block.outputs) {
    const meta = outputItem.meta && typeof outputItem.meta === "object" ? outputItem.meta : {};
    const stream = typeof meta.stream === "string" ? meta.stream : "stdout";
    const chunk = outputItem.text || "";
    if (chunk.trim()) {
      outputLines.push(`[${stream}] ${chunk}`);
    }
  }
  if (outputLines.length === 0 && typeof endMeta.result_preview === "string" && endMeta.result_preview.trim()) {
    outputLines.push(`[result] ${endMeta.result_preview}`);
  }
  if (outputLines.length === 0 && typeof endMeta.error === "string" && endMeta.error.trim()) {
    outputLines.push(`[error] ${endMeta.error}`);
  }
  if (outputLines.length === 0 && !block.end) {
    outputLines.push("运行中...");
  }
  row.querySelector(".tool-call-output").textContent = outputLines.join("\n");
  return row;
}

function createAgentTraceGroupElement(block) {
  const latest = block.latest || block.items[block.items.length - 1];
  const latestMeta = traceItemMeta(latest);
  const status = latestMeta.status || (latest.type === "subagent_done" ? "ok" : latest.type === "subagent_caveat" ? "completed_with_caveats" : latest.type === "subagent_error" ? "error" : "running");
  const expanded = state.expandedTraceAgents.has(block.agentKey);
  const recentlyUpdated = isTraceAgentRecentlyUpdated(block.agentKey);
  const row = document.createElement("details");
  row.className = `timeline-event agent-trace-group ${block.role === "subagent" ? "subagent-group" : "main-agent-group"} ${status === "error" || status === "failed" ? "is-error" : ""} ${status === "ok" || status === "completed" || status === "completed_with_caveats" ? "is-done" : ""} ${recentlyUpdated ? "recently-updated" : ""}`;
  row.style.setProperty("--agent-accent", block.accent);
  row.style.setProperty("--subagent-accent", block.accent);
  row.open = expanded;
  row.innerHTML = `
    <summary class="agent-trace-summary">
      <div class="agent-trace-heading">
        <span class="agent-trace-title">${escapeHtml(`${block.role === "main" ? "智能体" : "子智能体"} · ${block.label}`)}</span>
        <span class="agent-trace-count">${block.items.length}</span>
      </div>
      <div class="agent-trace-latest">
        <span class="agent-trace-latest-label">${escapeHtml(block.role === "main" ? traceLabel(latest) : traceShortLabel(latest))}</span>
        <span class="agent-trace-latest-time">${escapeHtml(formatTimestamp(latest.created_at))}</span>
      </div>
      <pre class="trace-text agent-trace-latest-text"></pre>
    </summary>
    <div class="agent-trace-history"></div>
  `;
  row.querySelector(".agent-trace-latest-text").textContent = latest.text || "";
  const historyEl = row.querySelector(".agent-trace-history");
  const detailBlocks = buildTraceBlocks(block.items, block.messageId, { groupMain: false, groupSubagents: false });
  for (const detailBlock of detailBlocks) {
    if (detailBlock.kind === "tool_call") {
      const hasOutput = Array.isArray(detailBlock.outputs) && detailBlock.outputs.some((outputItem) => String(outputItem?.text || "").trim());
      const endMeta = detailBlock.end && detailBlock.end.meta && typeof detailBlock.end.meta === "object" ? detailBlock.end.meta : {};
      const hasEndPreview = String(endMeta.result_preview || endMeta.error || "").trim();
      if (detailBlock.start || detailBlock.end || hasOutput || hasEndPreview) {
        historyEl.appendChild(createToolCallElement(detailBlock));
      }
    } else {
      historyEl.appendChild(createTraceElement(detailBlock.item, { compact: true, accent: block.accent }));
    }
  }
  row.addEventListener("toggle", () => {
    if (row.open) {
      state.expandedTraceAgents.add(block.agentKey);
    } else {
      state.expandedTraceAgents.delete(block.agentKey);
    }
    renderAgentTraceControls();
  });
  return row;
}

function buildTraceBlocks(traceItems, messageId = "", options = {}) {
  const groupMain = options.groupMain !== false;
  const groupSubagents = options.groupSubagents !== false;
  const blocks = [];
  const toolCallBlocks = new Map();
  const agentBlocks = new Map();
  const subagentColorMap = new Map();
  (traceItems || []).forEach((item, index) => {
    if (!item || typeof item !== "object") {
      return;
    }
    const meta = item.meta && typeof item.meta === "object" ? item.meta : {};
    const agentInfo = traceAgentInfoFromItem(item, subagentColorMap);
    if ((agentInfo.role === "subagent" && groupSubagents) || (agentInfo.role === "main" && groupMain)) {
      let block = agentBlocks.get(agentInfo.key);
      if (!block) {
        block = {
          kind: "agent_trace_group",
          order: index,
          key: `${messageId || "message"}:${agentInfo.key}`,
          messageId,
          agentKey: agentInfo.key,
          id: agentInfo.id,
          role: agentInfo.role,
          label: agentInfo.label,
          accent: agentInfo.accent,
          items: [],
          latest: null,
        };
        agentBlocks.set(agentInfo.key, block);
        blocks.push(block);
      }
      if (!block.label || block.label === block.id) {
        block.label = agentInfo.label || block.label || block.id;
      }
      block.items.push(item);
      block.latest = item;
      return;
    }
    const toolCallId = typeof meta.tool_call_id === "string" ? meta.tool_call_id : "";
    if (TOOL_CALL_TRACE_TYPES.has(item.type) && toolCallId) {
      let block = toolCallBlocks.get(toolCallId);
      if (!block) {
        block = {
          kind: "tool_call",
          order: index,
          id: toolCallId,
          start: null,
          end: null,
          outputs: [],
          toolName: typeof meta.tool_name === "string" ? meta.tool_name : "",
        };
        toolCallBlocks.set(toolCallId, block);
        blocks.push(block);
      }
      if (!block.toolName && typeof meta.tool_name === "string") {
        block.toolName = meta.tool_name;
      }
      if (item.type === "tool_call_start") {
        block.start = item;
      } else if (item.type === "tool_call_end") {
        block.end = item;
      } else {
        block.outputs.push(item);
      }
      return;
    }
    blocks.push({ kind: "trace", order: index, item });
  });
  blocks.sort((a, b) => a.order - b.order);
  return blocks;
}

function artifactKindLabel(kind) {
  return kind === "modified" ? "已修改" : "已创建";
}

function artifactsFromMessage(message) {
  const meta = message && message.meta && typeof message.meta === "object" ? message.meta : {};
  const artifacts = Array.isArray(meta.artifacts) ? meta.artifacts : [];
  return artifacts
    .map((artifact) => {
      const path = normalizeRelativePath(artifact?.path || "");
      if (!path || !isDownloadablePath(path)) {
        return null;
      }
      return {
        label: String(artifact.label || basenameFromPath(path)),
        path,
        kind: String(artifact.kind || "created"),
        size: Number.isFinite(Number(artifact.size)) ? Math.max(0, Number(artifact.size)) : null,
      };
    })
    .filter(Boolean);
}

function createMessageArtifactsElement(message) {
  if (message.role !== "assistant") {
    return null;
  }
  const artifacts = artifactsFromMessage(message);
  if (artifacts.length === 0) {
    return null;
  }
  const session = currentSession();
  if (!session) {
    return null;
  }
  const meta = message.meta && typeof message.meta === "object" ? message.meta : {};
  const wrapper = document.createElement("div");
  wrapper.className = "message-artifacts";

  const title = document.createElement("div");
  title.className = "message-artifacts-title";
  title.textContent = "生成的文件";
  wrapper.appendChild(title);

  const list = document.createElement("div");
  list.className = "message-artifacts-list";
  artifacts.forEach((artifact) => {
    const item = document.createElement("div");
    item.className = "artifact-chip";

    const previewButton = document.createElement("button");
    previewButton.type = "button";
    previewButton.className = "artifact-chip-preview";
    previewButton.title = `打开 ${artifact.path}`;
    previewButton.addEventListener("click", () => {
      openFile(artifact.path).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
    });

    const label = document.createElement("span");
    label.className = "artifact-chip-label";
    label.textContent = artifact.label;

    const details = document.createElement("span");
    details.className = "artifact-chip-meta";
    details.textContent = [
      artifactKindLabel(artifact.kind),
      artifact.size === null ? "" : `${artifact.size} 字节`,
    ].filter(Boolean).join(" · ");

    previewButton.appendChild(label);
    previewButton.appendChild(details);

    const divider = document.createElement("span");
    divider.className = "artifact-chip-divider";
    divider.textContent = "|";
    divider.setAttribute("aria-hidden", "true");

    const downloadLink = document.createElement("a");
    downloadLink.className = "artifact-chip-download";
    downloadLink.href = userScopedUrl(`/api/workspace/${encodeURIComponent(session.id)}/download?path=${encodeURIComponent(artifact.path)}`);
    downloadLink.title = `下载 ${artifact.path}`;
    downloadLink.setAttribute("download", "");
    downloadLink.setAttribute("aria-label", `下载 ${artifact.label}`);
    downloadLink.innerHTML = `
      <svg class="artifact-chip-download-icon" aria-hidden="true" viewBox="0 0 24 24">
        <path d="M12 4v10"></path>
        <path d="M8 10l4 4 4-4"></path>
        <path d="M5 19h14"></path>
      </svg>
    `;

    item.appendChild(previewButton);
    item.appendChild(divider);
    item.appendChild(downloadLink);
    list.appendChild(item);
  });
  wrapper.appendChild(list);

  if (meta.artifacts_truncated) {
    const note = document.createElement("div");
    note.className = "message-artifacts-note";
    note.textContent = "正在显示首批生成文件。";
    wrapper.appendChild(note);
  }

  return wrapper;
}

function createMessageElement(message) {
  const fragment = templateEl.content.cloneNode(true);
  const card = fragment.querySelector(".message-card");
  const roleEl = fragment.querySelector(".message-role");
  const timeEl = fragment.querySelector(".message-time");
  const bodyEl = fragment.querySelector(".message-body");

  card.classList.add(message.role === "user" ? "user-message" : "assistant-message");
  if (message.status === "error") {
    card.classList.add("error-message");
  }

  roleEl.textContent = message.role === "user" ? "用户消息" : "久谦研究AI";
  timeEl.textContent = formatTimestamp(message.created_at);

  if (message.role === "assistant" && message.content) {
    bodyEl.innerHTML = renderMarkdown(message.content);
  } else {
    bodyEl.textContent = message.content || (message.role === "assistant" ? "处理中..." : "");
  }
  const artifactsEl = createMessageArtifactsElement(message);
  if (artifactsEl) {
    card.appendChild(artifactsEl);
  }
  return fragment;
}

function renderMessages() {
  const scrollSnapshot = captureScrollPosition(messagesEl);
  messagesEl.innerHTML = "";
  const history = ensureHistory(state.currentSessionId);
  if (history.length === 0) {
    messagesEl.innerHTML = '<div class="empty-chat-state">请让助手分析当前文件，或继续当前研究线索。</div>';
    restoreScrollPosition(messagesEl, scrollSnapshot);
    return;
  }

  for (const message of history) {
    if (message.role === "assistant" && Array.isArray(message.trace) && message.trace.length > 0) {
      const blocks = buildTraceBlocks(message.trace, message.id || message.job_id || "");
      for (const block of blocks) {
        if (block.kind === "tool_call") {
          const hasOutput = Array.isArray(block.outputs) && block.outputs.some((outputItem) => String(outputItem?.text || "").trim());
          const endMeta = block.end && block.end.meta && typeof block.end.meta === "object" ? block.end.meta : {};
          const hasEndPreview = String(endMeta.result_preview || endMeta.error || "").trim();
          if (block.start || block.end || hasOutput || hasEndPreview) {
            messagesEl.appendChild(createToolCallElement(block));
          }
        } else if (block.kind === "agent_trace_group") {
          messagesEl.appendChild(createAgentTraceGroupElement(block));
        } else {
          messagesEl.appendChild(createTraceElement(block.item));
        }
      }
    }
    messagesEl.appendChild(createMessageElement(message));
  }

  restoreScrollPosition(messagesEl, scrollSnapshot, { stickToBottom: true });
}

function sessionMessageCount(session) {
  const value = Number(session?.message_count);
  if (Number.isFinite(value)) {
    return Math.max(0, value);
  }
  const history = session?.id ? state.histories.get(session.id) : null;
  return Array.isArray(history) ? history.length : 0;
}

function isBlankNewSession(session) {
  if (!session) {
    return false;
  }
  const status = String(session.status || "idle").trim().toLowerCase();
  const title = String(session.title || "New Chat").trim();
  return status === "idle" && title === "New Chat" && sessionMessageCount(session) === 0;
}

function sessionStatusLabel(sessionOrStatus) {
  const normalized = String(sessionOrStatus?.status || sessionOrStatus || "idle").trim().toLowerCase();
  if (normalized === "running") {
    return "运行中";
  }
  if (normalized === "error") {
    return "错误";
  }
  const session = sessionOrStatus && typeof sessionOrStatus === "object" ? sessionOrStatus : null;
  return sessionMessageCount(session) <= 0 ? "就绪" : "完成";
}

function renderSessionHistory() {
  if (!sessionHistoryButtonEl || !sessionHistoryPopoverEl) {
    return;
  }
  sessionHistoryButtonEl.classList.toggle("pressed", state.sessionHistoryOpen);
  sessionHistoryButtonEl.setAttribute("aria-expanded", state.sessionHistoryOpen ? "true" : "false");
  sessionHistoryPopoverEl.classList.toggle("hidden", !state.sessionHistoryOpen);
  if (!state.sessionHistoryOpen) {
    return;
  }

  if (state.sessions.length === 0) {
    sessionHistoryPopoverEl.innerHTML = '<div class="session-history-empty">暂无会话。</div>';
    return;
  }

  const items = state.sessions.map((session) => {
    const status = String(session.status || "idle").trim().toLowerCase() || "idle";
    const title = session.title === "New Chat" ? "新对话" : (session.title || "新对话");
    const isCurrent = session.id === state.currentSessionId;
    const isRunning = status === "running";
    return `
      <div class="session-history-row${isCurrent ? " active" : ""}" role="none">
        <button class="session-history-item" type="button" role="menuitem" data-session-id="${escapeHtml(session.id)}">
          <span class="session-history-dot status-${escapeHtml(status)}" aria-hidden="true"></span>
          <span class="session-history-main">
            <span class="session-history-title">${escapeHtml(title)}</span>
            <span class="session-history-meta"><span class="session-history-state status-${escapeHtml(status)}">${escapeHtml(sessionStatusLabel(session))}</span>${session.updated_at ? ` · ${escapeHtml(formatTimestamp(session.updated_at))}` : ""}</span>
          </span>
        </button>
        <button class="session-history-delete" type="button" data-session-id="${escapeHtml(session.id)}" aria-label="删除会话 ${escapeHtml(title)}" title="${isRunning ? "无法删除运行中的会话" : "删除会话"}"${isRunning ? " disabled" : ""}>
          <svg aria-hidden="true" viewBox="0 0 24 24">
            <path d="M3 6h18"></path>
            <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
            <path d="M10 11v6"></path>
            <path d="M14 11v6"></path>
          </svg>
        </button>
      </div>
    `;
  }).join("");
  sessionHistoryPopoverEl.innerHTML = `<div class="session-history-list">${items}</div>`;
}

function collectTraceAgentsForCurrentSession() {
  const history = ensureHistory(state.currentSessionId);
  const agents = new Map();
  const subagentColorMap = new Map();
  history.forEach((message) => {
    if (message.role !== "assistant" || !Array.isArray(message.trace)) {
      return;
    }
    message.trace.forEach((item, index) => {
      if (!item || typeof item !== "object") {
        return;
      }
      const info = traceAgentInfoFromItem(item, subagentColorMap);
      let agent = agents.get(info.key);
      if (!agent) {
        agent = {
          ...info,
          count: 0,
          firstOrder: agents.size,
          latest: null,
        };
        agents.set(info.key, agent);
      }
      agent.count += 1;
      agent.latest = item;
      if (info.role === "subagent" && (!agent.label || agent.label === agent.id)) {
        agent.label = info.label;
      }
      agent.lastIndex = index;
    });
  });
  return [...agents.values()].sort((left, right) => {
    if (left.key === "main") {
      return -1;
    }
    if (right.key === "main") {
      return 1;
    }
    return left.firstOrder - right.firstOrder;
  });
}

function renderAgentTraceControls() {
  if (!agentTraceControlsEl) {
    return;
  }
  const agents = collectTraceAgentsForCurrentSession();
  if (agents.length === 0) {
    agentTraceControlsEl.innerHTML = "";
    agentTraceControlsEl.classList.add("hidden");
    agentTraceControlsEl.classList.remove("is-overflowing");
    return;
  }
  agentTraceControlsEl.classList.remove("hidden");
  const chipMarkup = agents.map((agent) => {
    const expanded = state.expandedTraceAgents.has(agent.key);
    const recentlyUpdated = isTraceAgentRecentlyUpdated(agent.key);
    const latestLabel = agent.latest ? (agent.role === "main" ? traceLabel(agent.latest) : traceShortLabel(agent.latest)) : "";
    const chipLabel = agent.role === "main" ? "久谦研究AI" : agent.label;
    const chipTitle = latestLabel ? `${chipLabel}: ${latestLabel}` : chipLabel;
    return `
      <button
        class="agent-trace-chip ${expanded ? "active" : ""} ${recentlyUpdated ? "recently-updated" : ""}"
        type="button"
        data-agent-key="${escapeHtml(agent.key)}"
        style="--agent-accent: ${escapeHtml(agent.accent)}"
        aria-pressed="${expanded ? "true" : "false"}"
        aria-label="${escapeHtml(`${chipLabel}，${agent.count} 条轨迹事件`)}"
        title="${escapeHtml(chipTitle)}"
      >
        <span class="agent-trace-chip-dot" aria-hidden="true"></span>
        <span class="agent-trace-chip-label">${escapeHtml(chipLabel)}</span>
        <span class="agent-trace-chip-count">${agent.count}</span>
      </button>
    `;
  }).join("");
  agentTraceControlsEl.innerHTML = `
    <div class="agent-trace-chip-strip">
      ${chipMarkup}
    </div>
    <div class="agent-trace-chip-popover" role="tooltip" aria-label="所有轨迹智能体">
      ${chipMarkup}
    </div>
  `;
  requestAnimationFrame(() => {
    const chipStrip = agentTraceControlsEl.querySelector(".agent-trace-chip-strip");
    if (!chipStrip) {
      return;
    }
    agentTraceControlsEl.classList.toggle("is-overflowing", chipStrip.scrollWidth > chipStrip.clientWidth + 1);
  });
  agentTraceControlsEl.querySelectorAll(".agent-trace-chip").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.getAttribute("data-agent-key") || "";
      if (!key) {
        return;
      }
      if (state.expandedTraceAgents.has(key)) {
        state.expandedTraceAgents.delete(key);
      } else {
        state.expandedTraceAgents.add(key);
      }
      renderAgentTraceControls();
      renderMessages();
    });
  });
}

function renderChatMeta() {
  const session = currentSession();
  const uiState = currentUiState();
  chatTitleEl.textContent = "AI 助手";
  chatSubtitleEl.textContent = uiState.active_path
    ? `当前焦点：${uiState.active_path}`
    : `当前模式：${uiState.active_skill || "自动"}`;
  sessionStatusEl.textContent = session ? sessionStatusLabel(session) : "就绪";
  sessionStatusEl.className = `session-status status-${session ? session.status : "idle"}`;
  renderAgentTraceControls();
  renderSessionHistory();
}

function renderPreferredDataSourceSelect() {
  renderPendingMentions();
}

function renderWorkbench() {
  workspaceRootLabelEl.textContent = "";
  renderPanelToggles();
  renderNavTabs();
  renderFileTree();
  renderSkillsList();
  renderCenterView();
  renderChatMeta();
  renderContextChips();
  renderPreferredDataSourceSelect();
  renderComposerToolState();
  renderComposerAction();
  renderMessages();
  renderContextStatus();
}

async function saveCurrentFile() {
  syncMarkdownEditorDraft();
  const session = currentSession();
  const doc = state.currentDocument;
  if (!session || !doc || doc.type !== "file" || !doc.file.editable) {
    return;
  }
  const saved = await fetchJson(userScopedUrl(`/api/workspace/${encodeURIComponent(session.id)}/file`), {
    method: "PUT",
    body: JSON.stringify({
      path: doc.file.path,
      content: state.fileDraft,
    }),
  });
  state.currentDocument = { type: "file", file: saved };
  state.fileDraft = saved.content || "";
  const tab = fileTabByPath(saved.path);
  if (tab) {
    tab.file = saved;
    tab.draft = saved.content || "";
    tab.markdownPreview = state.markdownPreview;
  }
  renderWorkbench();
}

function getChatContext() {
  const uiState = currentUiState();
  return {
    active_path: "",
    active_skill: state.pendingMentions.skill || "",
    center_view: uiState.center_view || "empty",
  };
}

function patchMessage(sessionId, predicate, patcher) {
  const history = ensureHistory(sessionId);
  const nextHistory = history.map((message) => (predicate(message) ? patcher(message) : message));
  state.histories.set(sessionId, nextHistory);
  if (sessionId === state.currentSessionId) {
    renderAgentTraceControls();
    renderMessages();
  }
}

function traceEventIdentity(item) {
  const meta = item && item.meta && typeof item.meta === "object" ? item.meta : {};
  return [
    item?.type || "",
    item?.created_at || "",
    meta.tool_call_id || "",
    meta.subagent_id || "",
    item?.text || "",
  ].join("\x1f");
}

function appendTraceItem(message, item) {
  const trace = Array.isArray(message.trace) ? message.trace : [];
  const nextKey = traceEventIdentity(item);
  if (nextKey && trace.some((existing) => traceEventIdentity(existing) === nextKey)) {
    return message;
  }
  markTraceAgentUpdated(item);
  return {
    ...message,
    trace: [...trace, item],
    updated_at: item.created_at || message.updated_at || new Date().toISOString(),
  };
}

function shouldSplitFinalAssistantMessage(streamedContent, finalContent) {
  const streamed = typeof streamedContent === "string" ? streamedContent.trim() : "";
  const finalText = typeof finalContent === "string" ? finalContent.trim() : "";
  return Boolean(streamed && finalText && streamed !== finalText);
}

function ensureRunningSessionStream(sessionId) {
  const session = sessionById(sessionId);
  if (!session || String(session.status || "").toLowerCase() !== "running") {
    return;
  }
  if (state.activeJobs.has(sessionId)) {
    return;
  }
  const history = ensureHistory(sessionId);
  const runningAssistant = [...history].reverse().find((message) => (
    message
    && message.role === "assistant"
    && message.status === "running"
    && message.job_id
    && message.id
  ));
  if (!runningAssistant) {
    return;
  }
  attachEventStream(sessionId, String(runningAssistant.job_id), String(runningAssistant.id));
}

function closeStream(sessionId) {
  const active = state.activeJobs.get(sessionId);
  if (!active) {
    return;
  }
  active.source.close();
  state.activeJobs.delete(sessionId);
  renderWorkbench();
}

function attachEventStream(sessionId, jobId, placeholderId) {
  const streamUrl = userScopedUrl(`/api/stream/${encodeURIComponent(jobId)}`);
  const source = new EventSource(`${streamUrl}&session_id=${encodeURIComponent(sessionId)}`);
  state.activeJobs.set(sessionId, { jobId, source, placeholderId, stopRequested: false });
  renderWorkbench();

  source.addEventListener("start", () => {
    updateCurrentSessionLocally({ status: "running", updated_at: new Date().toISOString() });
    renderWorkbench();
  });

  for (const eventType of LIVE_TRACE_TYPES) {
    source.addEventListener(eventType, (event) => {
      const payload = JSON.parse(event.data);
      patchMessage(
        sessionId,
        (message) => message.id === placeholderId,
        (message) => appendTraceItem(message, {
          type: eventType,
          text: payload.text,
          created_at: payload.created_at,
          meta: payload.meta && typeof payload.meta === "object" ? payload.meta : null,
        }),
      );
      if (sessionId === state.currentSessionId && shouldRefreshWorkspaceForTrace(eventType, payload)) {
        scheduleWorkspaceTreeRefresh({ delayMs: 180 });
      }
    });
  }

  source.addEventListener("context_status", (event) => {
    const payload = normalizeContextStatusClient(JSON.parse(event.data));
    if (!payload) {
      return;
    }
    const session = sessionById(sessionId);
    if (session) {
      applySessionRecord({ ...session, context_status: payload });
    }
    patchMessage(
      sessionId,
      (message) => message.id === placeholderId,
      (message) => ({ ...message, context_status: payload }),
    );
    renderWorkbench();
  });

  source.addEventListener("assistant_delta", (event) => {
    const payload = JSON.parse(event.data);
    const delta = typeof payload.text === "string" ? payload.text : "";
    if (!delta) {
      return;
    }
    patchMessage(
      sessionId,
      (message) => message.id === placeholderId,
      (message) => ({
        ...message,
        content: `${message.content || ""}${delta}`,
        status: "running",
        updated_at: payload.created_at || new Date().toISOString(),
      }),
    );
  });

  source.addEventListener("assistant_end", () => {
    patchMessage(
      sessionId,
      (message) => message.id === placeholderId,
      (message) => ({ ...message, status: message.status === "running" ? "running" : message.status }),
    );
  });

  source.addEventListener("done", async (event) => {
    const payload = JSON.parse(event.data);
    const history = ensureHistory(sessionId);
    const placeholder = history.find((message) => message.id === placeholderId);
    if (placeholder && shouldSplitFinalAssistantMessage(placeholder.content, payload.text)) {
      const nextHistory = history.map((message) => (
        message.id === placeholderId
          ? {
              ...message,
              status: "complete",
              updated_at: new Date().toISOString(),
            }
          : message
      ));
      nextHistory.push({
        id: `local-final-${randomClientId()}`,
        role: "assistant",
        content: payload.text,
        created_at: payload.created_at || new Date().toISOString(),
        status: payload.status || "complete",
        trace: [],
        meta: payload.meta && typeof payload.meta === "object" ? payload.meta : undefined,
      });
      state.histories.set(sessionId, nextHistory);
      if (sessionId === state.currentSessionId) {
        renderAgentTraceControls();
        renderMessages();
      }
    } else {
      patchMessage(
        sessionId,
        (message) => message.id === placeholderId,
        (message) => ({
          ...message,
          content: payload.text,
          status: payload.status || "complete",
          meta: payload.meta && typeof payload.meta === "object" ? payload.meta : message.meta,
        }),
      );
    }
    closeStream(sessionId);
    await loadSessions();
    if (sessionId === state.currentSessionId) {
      scheduleWorkspaceTreeRefresh({ delayMs: 120 });
    }
  });

  source.addEventListener("error", async (event) => {
    let payload = { message: "流式响应失败。" };
    if (event.data) {
      payload = JSON.parse(event.data);
    }
    patchMessage(
      sessionId,
      (message) => message.id === placeholderId,
      (message) => ({
        ...message,
        content: payload.message,
        status: "error",
        meta: payload.meta && typeof payload.meta === "object" ? payload.meta : message.meta,
      }),
    );
    closeStream(sessionId);
    await loadSessions();
  });
}

async function pollSessionUntilIdle(sessionId, attempts = 20, delayMs = 250) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await loadSessions();
    const session = sessionById(sessionId);
    if (!session || session.status !== "running") {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const session = currentSession();
  if (!session) {
    return;
  }

  const { active, isRunning } = getSessionActivity(session.id);
  if (isRunning) {
    if (active) {
      state.activeJobs.set(session.id, { ...active, stopRequested: true });
      renderWorkbench();
    }
    try {
      const payload = await fetchJson(userScopedUrl(`/api/sessions/${encodeURIComponent(session.id)}/stop`), {
        method: "POST",
      });
      if (!payload.stop_requested) {
        closeStream(session.id);
        await loadSessions();
      } else if (!active) {
        await pollSessionUntilIdle(session.id);
      }
    } catch (error) {
      if (active) {
        state.activeJobs.set(session.id, { ...active, stopRequested: false });
      }
      renderWorkbench();
      throw error;
    }
    return;
  }

  const message = messageInputEl.value.trim();
  if (!message) {
    return;
  }

  const history = ensureHistory(session.id);
  const now = new Date().toISOString();
  const placeholderId = `local-${randomClientId()}`;
  history.push({
    id: `local-user-${randomClientId()}`,
    role: "user",
    content: message,
    created_at: now,
    status: "complete",
  });
  history.push({
    id: placeholderId,
    role: "assistant",
    content: "",
    created_at: now,
    status: "running",
    trace: [],
  });
  state.histories.set(session.id, history);
  updateCurrentSessionLocally({
    status: "running",
    title: session.title === "New Chat" ? message.slice(0, 24) : session.title,
    updated_at: now,
    message_count: Math.max(sessionMessageCount(session), history.length),
  });
  renderWorkbench();
  messageInputEl.value = "";
  resizeMessageInput();

  try {
    const payload = await fetchJson(userScopedUrl("/api/chat"), {
      method: "POST",
      body: JSON.stringify({
        session_id: session.id,
        message,
        user_id: state.userId,
        turn_context: buildTurnContextPayload(),
        context: getChatContext(),
      }),
    });
    clearPendingMentions();
    if (payload.title) {
      updateCurrentSessionLocally({ title: payload.title });
    }
    const assistantMessageId = String(payload.assistant_message_id || placeholderId);
    if (assistantMessageId !== placeholderId) {
      patchMessage(
        session.id,
        (item) => item.id === placeholderId,
        (item) => ({ ...item, id: assistantMessageId, job_id: payload.job_id }),
      );
    }
    attachEventStream(session.id, payload.job_id, assistantMessageId);
  } catch (error) {
    patchMessage(
      session.id,
      (item) => item.id === placeholderId,
      (item) => ({ ...item, content: error.message, status: "error" }),
    );
    closeStream(session.id);
    await loadSessions();
  }
}
