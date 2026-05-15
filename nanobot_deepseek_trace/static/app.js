const fileListEl = document.getElementById("fileList");
const recordsEl = document.getElementById("records");
const activeFileEl = document.getElementById("activeFile");
const summaryEl = document.getElementById("summary");
const refreshFilesBtn = document.getElementById("refreshFiles");
const limitSelect = document.getElementById("limitSelect");
const searchInput = document.getElementById("searchInput");
const expandAllBtn = document.getElementById("expandAll");
const collapseAllBtn = document.getElementById("collapseAll");
const recordTemplate = document.getElementById("recordTemplate");

let files = [];
let currentFile = null;
let currentRecords = [];

function fmtBytes(value) {
  if (!Number.isFinite(value)) return "";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function fmtTime(value) {
  if (!value) return "";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function jsonText(value) {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function textOf(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object" && typeof item.text === "string") return item.text;
      return JSON.stringify(item);
    }).join("\n");
  }
  return JSON.stringify(value, null, 2);
}

function addBlock(parent, kind, title, value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) {
    return;
  }
  const block = document.createElement("section");
  block.className = `block ${kind}`;
  const head = document.createElement("div");
  head.className = "block-head";
  head.textContent = title;
  const body = document.createElement("pre");
  body.className = "block-body";
  body.textContent = textOf(value);
  block.append(head, body);
  parent.appendChild(block);
}

function addPayloadSection(parent, kind, title, builder, startOpen = false) {
  const details = document.createElement("details");
  details.className = `payload-section ${kind}`;
  details.open = startOpen;
  const summary = document.createElement("summary");
  summary.textContent = title;
  const body = document.createElement("div");
  body.className = "payload-section-body";
  builder(body);
  details.append(summary, body);
  parent.appendChild(details);
}

function addKeyValueGrid(parent, values) {
  const entries = Object.entries(values).filter(([, value]) => value !== undefined);
  if (!entries.length) return;
  const grid = document.createElement("div");
  grid.className = "kv-grid";
  entries.forEach(([key, value]) => {
    const label = document.createElement("div");
    label.className = "kv-key";
    label.textContent = key;
    const val = document.createElement("div");
    val.className = "kv-value";
    val.textContent = typeof value === "object" ? jsonText(value) : String(value);
    grid.append(label, val);
  });
  parent.appendChild(grid);
}

function renderFiles() {
  fileListEl.innerHTML = "";
  if (!files.length) {
    fileListEl.innerHTML = '<div class="empty">No JSONL trace files found.</div>';
    return;
  }
  files.forEach((file) => {
    const button = document.createElement("button");
    button.className = `file-item${currentFile && currentFile.path === file.path ? " active" : ""}`;
    button.type = "button";
    button.innerHTML = `
      <div class="file-name"></div>
      <div class="file-meta">
        <span>${file.records} records</span>
        <span>${fmtBytes(file.size)}</span>
        <span>${fmtTime(file.modified)}</span>
      </div>
    `;
    button.querySelector(".file-name").textContent = file.name;
    button.addEventListener("click", () => loadTraces(file));
    fileListEl.appendChild(button);
  });
}

function renderMessage(parent, msg, index) {
  const role = msg.role || "unknown";
  const card = document.createElement("section");
  card.className = `message role-${role}`;
  const head = document.createElement("div");
  head.className = "message-head";
  const extra = [];
  if (msg.name) extra.push(`name: ${msg.name}`);
  if (msg.tool_call_id) extra.push(`tool_call_id: ${msg.tool_call_id}`);
  head.innerHTML = `<span>${index + 1}. ${role}</span><span>${extra.join(" · ")}</span>`;
  const body = document.createElement("pre");
  body.className = "message-body";
  body.textContent = textOf(msg.content);
  card.append(head, body);
  if (msg.tool_calls) addBlock(card, "tools", "Tool calls", msg.tool_calls);
  if (msg.reasoning_content) addBlock(card, "reasoning", "Reasoning content", msg.reasoning_content);
  parent.appendChild(card);
}

function toolName(tool) {
  if (!tool || typeof tool !== "object") return "unknown";
  if (typeof tool.name === "string") return tool.name;
  if (tool.function && typeof tool.function.name === "string") return tool.function.name;
  return "unknown";
}

function renderTool(parent, tool, index) {
  const fn = tool && typeof tool === "object" ? (tool.function || tool) : {};
  const name = toolName(tool);
  const details = document.createElement("details");
  details.className = "tool-card";
  const summary = document.createElement("summary");
  summary.textContent = `${index + 1}. ${name}`;
  const body = document.createElement("div");
  body.className = "tool-body";
  addKeyValueGrid(body, {
    type: tool?.type,
    name,
    description: fn.description,
    tool_choice: tool?.tool_choice,
  });
  addBlock(body, "tools", "Parameters", fn.parameters);
  addBlock(body, "content", "Raw tool definition", tool);
  details.append(summary, body);
  parent.appendChild(details);
}

function renderPayload(parent, payload) {
  parent.innerHTML = "";
  const known = new Set([
    "messages",
    "tools",
    "model",
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "reasoning_effort",
    "tool_choice",
    "stream",
    "stream_options",
    "extra_body",
  ]);

  addPayloadSection(parent, "params", "Request params", (body) => {
    addKeyValueGrid(body, {
      model: payload.model,
      temperature: payload.temperature,
      max_tokens: payload.max_tokens,
      max_completion_tokens: payload.max_completion_tokens,
      reasoning_effort: payload.reasoning_effort,
      tool_choice: payload.tool_choice,
      stream: payload.stream,
      stream_options: payload.stream_options,
    });
  }, true);

  addPayloadSection(parent, "messages-section", `Messages (${Array.isArray(payload.messages) ? payload.messages.length : 0})`, (body) => {
    const messages = Array.isArray(payload.messages) ? payload.messages : [];
    if (messages.length) {
      messages.forEach((msg, index) => renderMessage(body, msg, index));
    } else {
      body.innerHTML = '<div class="empty compact">Payload has no messages array.</div>';
    }
  }, true);

  addPayloadSection(parent, "tools-section", `Tool definitions (${Array.isArray(payload.tools) ? payload.tools.length : 0})`, (body) => {
    const tools = Array.isArray(payload.tools) ? payload.tools : [];
    if (tools.length) {
      tools.forEach((tool, index) => renderTool(body, tool, index));
    } else {
      body.innerHTML = '<div class="empty compact">Payload has no tools array.</div>';
    }
  }, Boolean(payload.tools));

  addPayloadSection(parent, "extra-body-section", "Extra body", (body) => {
    if (payload.extra_body && typeof payload.extra_body === "object") {
      addBlock(body, "reasoning", "extra_body", payload.extra_body);
    } else {
      body.innerHTML = '<div class="empty compact">No extra_body.</div>';
    }
  }, Boolean(payload.extra_body));

  const other = {};
  Object.entries(payload).forEach(([key, value]) => {
    if (!known.has(key)) other[key] = value;
  });
  addPayloadSection(parent, "other-section", `Other payload fields (${Object.keys(other).length})`, (body) => {
    if (Object.keys(other).length) {
      addBlock(body, "content", "Other fields", other);
    } else {
      body.innerHTML = '<div class="empty compact">No additional payload fields.</div>';
    }
  }, Object.keys(other).length > 0);
}

function recordMatches(record, needle) {
  if (!needle) return true;
  return JSON.stringify(record).toLowerCase().includes(needle);
}

function renderRecords() {
  const needle = searchInput.value.trim().toLowerCase();
  recordsEl.innerHTML = "";
  const filtered = currentRecords.filter((record) => recordMatches(record, needle));
  if (!filtered.length) {
    recordsEl.innerHTML = '<div class="empty">No matching trace records.</div>';
    return;
  }
  filtered.slice().reverse().forEach((record) => {
    const node = recordTemplate.content.firstElementChild.cloneNode(true);
    const payload = record.payload || {};
    const response = record.response || null;
    const error = record.error || null;
    const result = error || response || {};
    const usage = result.usage || {};
    const totalTokens = usage.total_tokens || "";

    node.querySelector(".mode").textContent = record.mode || "chat";
    node.querySelector(".mode").classList.add(record.mode === "stream" ? "stream" : "chat");
    node.querySelector(".stamp").textContent = fmtTime(record.timestamp);
    node.querySelector(".model").textContent = record.model || payload.model || "";
    node.querySelector(".result").textContent = error ? "error" : (result.finish_reason || "ok");
    node.querySelector(".result").classList.add(error ? "error" : "ok");
    node.querySelector(".tokens").textContent = totalTokens ? `${totalTokens} tokens` : "no usage";

    renderPayload(node.querySelector(".payload-blocks"), payload);

    const responseBlocks = node.querySelector(".response-blocks");
    addBlock(responseBlocks, error ? "error" : "content", error ? "Error content" : "Content", result.content);
    addBlock(responseBlocks, "reasoning", "Reasoning content", result.reasoning_content);
    addBlock(responseBlocks, "tools", "Tool calls", result.tool_calls);
    addBlock(responseBlocks, "content", "Usage", result.usage);
    if (!responseBlocks.children.length) {
      responseBlocks.innerHTML = '<div class="empty">No response body recorded.</div>';
    }

    node.querySelector(".raw-payload").textContent = jsonText(payload);
    node.querySelector(".raw-response").textContent = jsonText(error || response || {});
    node.querySelector(".record-head").addEventListener("click", () => node.classList.toggle("open"));
    recordsEl.appendChild(node);
  });
}

async function loadFiles() {
  const response = await fetch("/api/files");
  const data = await response.json();
  files = data.files || [];
  if (!currentFile && files.length) {
    await loadTraces(files[0]);
  } else {
    renderFiles();
  }
}

async function loadTraces(file) {
  currentFile = file;
  renderFiles();
  activeFileEl.textContent = file.path;
  summaryEl.textContent = "Loading...";
  recordsEl.innerHTML = '<div class="empty">Loading trace records...</div>';
  const params = new URLSearchParams({ file: file.path, limit: limitSelect.value });
  const response = await fetch(`/api/traces?${params.toString()}`);
  const data = await response.json();
  if (!response.ok) {
    recordsEl.innerHTML = `<div class="empty">${data.error || "Failed to load traces."}</div>`;
    summaryEl.textContent = "";
    return;
  }
  currentRecords = data.records || [];
  const badCount = (data.bad_records || []).length;
  summaryEl.textContent = `${currentRecords.length} loaded · ${data.file.records} total · ${badCount} malformed · ${fmtBytes(data.file.size)}`;
  renderRecords();
}

refreshFilesBtn.addEventListener("click", loadFiles);
limitSelect.addEventListener("change", () => {
  if (currentFile) loadTraces(currentFile);
});
searchInput.addEventListener("input", renderRecords);
expandAllBtn.addEventListener("click", () => {
  document.querySelectorAll(".record").forEach((item) => item.classList.add("open"));
});
collapseAllBtn.addEventListener("click", () => {
  document.querySelectorAll(".record").forEach((item) => item.classList.remove("open"));
});

loadFiles().catch((error) => {
  recordsEl.innerHTML = `<div class="empty">${error}</div>`;
});
