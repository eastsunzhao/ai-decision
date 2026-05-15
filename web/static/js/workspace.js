function flattenWorkspaceFiles(node = state.workspaceTree, out = []) {
  if (!node) {
    return out;
  }
  if (node.kind === "file" && node.path) {
    out.push({ path: node.path, name: node.name || node.path });
    return out;
  }
  for (const child of node.children || []) {
    flattenWorkspaceFiles(child, out);
  }
  return out;
}

function countWorkspaceFiles(node) {
  if (!node) {
    return 0;
  }
  if (node.kind === "file") {
    return 1;
  }
  return (node.children || []).reduce((total, child) => total + countWorkspaceFiles(child), 0);
}

function isPermanentPath(path) {
  const normalized = normalizeRelativePath(path);
  return normalized === "permanent" || normalized.startsWith("permanent/");
}

function isTempPath(path) {
  const normalized = normalizeRelativePath(path);
  return normalized.startsWith("temp/");
}

function isCurrentSessionTempPath(path) {
  const session = currentSession();
  if (!session) {
    return false;
  }
  const normalized = normalizeRelativePath(path);
  const sessionRoot = `temp/${session.id}`;
  return normalized === sessionRoot || normalized.startsWith(`${sessionRoot}/`);
}

function isDownloadablePath(path) {
  return isPermanentPath(path) || isCurrentSessionTempPath(path);
}

function currentPendingMentions() {
  return {
    skill: state.pendingMentions.skill || "",
    sources: [...state.pendingMentions.sources],
    iterationBudget: ITERATION_BUDGETS.has(state.pendingMentions.iterationBudget)
      ? state.pendingMentions.iterationBudget
      : "medium",
    files: [...state.pendingMentions.files],
  };
}

function initializeUserId() {
  const params = new URLSearchParams(window.location.search);
  const userId = (params.get("userId") || params.get("user_id") || "").trim();
  state.userId = userId || "10001";
}

function userScopedUrl(path) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("user_id", state.userId || "10001");
  return `${url.pathname}${url.search}`;
}

async function fetchJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`网络请求失败（${url}）：${message}。请确认 web/app.py 正在运行。`);
  }

  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.error || `请求失败：${response.status}`);
  }
  return payload;
}

function normalizeSessionRecordClient(session) {
  return {
    ...session,
    context_status: normalizeContextStatusClient(session?.context_status),
    composer_prefs: normalizeComposerPrefsClient(session.composer_prefs),
    ui_state: normalizeUiStateClient(session.ui_state, session.composer_prefs),
  };
}

function normalizeContextStatusClient(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const promptTokens = Math.max(0, Number.parseInt(payload.prompt_tokens, 10) || 0);
  const contextWindowTokens = Math.max(0, Number.parseInt(payload.context_window_tokens, 10) || 0);
  if (!promptTokens || !contextWindowTokens) {
    return null;
  }
  const remainingTokens = Math.max(0, Number.parseInt(payload.remaining_tokens, 10) || (contextWindowTokens - promptTokens));
  const percentUsed = Math.min(100, Math.max(0, Number(payload.percent_used) || ((promptTokens / contextWindowTokens) * 100)));
  return {
    prompt_tokens: promptTokens,
    context_window_tokens: contextWindowTokens,
    remaining_tokens: remainingTokens,
    percent_used: percentUsed,
    source: String(payload.source || "estimate"),
    updated_at: payload.updated_at || "",
  };
}

function applySessionRecord(session) {
  const normalized = normalizeSessionRecordClient(session);
  const index = state.sessions.findIndex((item) => item.id === normalized.id);
  if (index >= 0) {
    state.sessions[index] = normalized;
  } else {
    state.sessions.unshift(normalized);
  }
  return normalized;
}

async function patchCurrentSession(payload) {
  const session = currentSession();
  if (!session) {
    return null;
  }
  const updated = await fetchJson(userScopedUrl(`/api/sessions/${encodeURIComponent(session.id)}`), {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return applySessionRecord(updated);
}

function updateCurrentSessionLocally(patch) {
  const session = currentSession();
  if (!session) {
    return;
  }
  const nextComposerPrefs = patch.composer_prefs
    ? normalizeComposerPrefsClient({
      ...session.composer_prefs,
      ...patch.composer_prefs,
      defaults: {
        ...(session.composer_prefs?.defaults || {}),
        ...(patch.composer_prefs.defaults || {}),
      },
    })
    : session.composer_prefs;
  const nextUiState = patch.ui_state
    ? normalizeUiStateClient({
      ...session.ui_state,
      ...patch.ui_state,
    }, nextComposerPrefs)
    : session.ui_state;
  applySessionRecord({
    ...session,
    ...patch,
    composer_prefs: nextComposerPrefs,
    ui_state: nextUiState,
  });
}

function formatTokenCount(value) {
  const number = Math.max(0, Number(value) || 0);
  if (number >= 1000) {
    return `${(number / 1000).toFixed(number >= 100000 ? 0 : 1)}k`;
  }
  return `${Math.round(number)}`;
}

function sessionShortId(session) {
  const id = typeof session?.id === "string" ? session.id.trim() : "";
  return id ? id.slice(0, 4) : "----";
}

function renderContextStatus() {
  if (!contextStatusBarEl || !contextStatusFillEl || !contextStatusTextEl) {
    return;
  }
  const session = currentSession();
  const sessionLabel = `会话 ${sessionShortId(session)}`;
  const status = normalizeContextStatusClient(session?.context_status);
  const renderStatusText = (contextText) => {
    contextStatusTextEl.innerHTML = `
      <span class="context-status-main">${escapeHtml(contextText)}</span>
      <span class="context-status-session">${escapeHtml(sessionLabel)}</span>
    `;
  };
  if (!status) {
    contextStatusBarEl.classList.add("empty");
    contextStatusFillEl.style.width = "0%";
    renderStatusText("上下文 --/--（已用 --%）");
    return;
  }
  contextStatusBarEl.classList.remove("empty");
  contextStatusFillEl.style.width = `${status.percent_used}%`;
  renderStatusText(`上下文 ${formatTokenCount(status.prompt_tokens)}/${formatTokenCount(status.context_window_tokens)}（已用 ${status.percent_used.toFixed(1)}%）`);
}

async function loadHistory(sessionId) {
  const history = await fetchJson(userScopedUrl(`/api/history/${encodeURIComponent(sessionId)}`));
  state.histories.set(sessionId, history);
  ensureRunningSessionStream(sessionId);
}

function initializeExpandedDirs(tree) {
  state.expandedDirs = new Set([""]);
  state.fileTreeExpansionInitialized = false;
  if (!tree || !Array.isArray(tree.children)) {
    return;
  }
  collectDirectoryPaths(tree).forEach((path) => state.expandedDirs.add(path));
  ensureSessionFileSectionExpanded(tree);
  state.fileTreeExpansionInitialized = true;
}

function ensureSessionFileSectionExpanded(tree = state.workspaceTree) {
  const sessionRoot = (tree?.children || []).find((child) => fileSectionForPath(child.path)?.key === "session");
  if (!sessionRoot) {
    return;
  }
  state.collapsedFileSections.delete("session");
  collectDirectoryPaths(sessionRoot).forEach((path) => state.expandedDirs.add(path));
}

function collectDirectoryPaths(node = state.workspaceTree, out = []) {
  if (!node || !Array.isArray(node.children)) {
    return out;
  }
  collectDirectoryPathsFromNode(node, out);
  return out;
}

function collectDirectoryPathsFromNode(node, out = []) {
  if (!node || node.kind !== "directory") {
    return out;
  }
  if (node.path) {
    out.push(node.path);
  }
  for (const child of node.children || []) {
    collectDirectoryPathsFromNode(child, out);
  }
  return out;
}

async function loadWorkspaceTree(sessionId) {
  const tree = await fetchJson(userScopedUrl(`/api/workspace/${encodeURIComponent(sessionId)}/tree`));
  state.workspaceTree = tree;
  if (!state.fileTreeExpansionInitialized) {
    initializeExpandedDirs(tree);
  }
  ensureSessionFileSectionExpanded(tree);
}

async function refreshWorkspaceTree({ render = true } = {}) {
  const session = currentSession();
  if (!session) {
    return;
  }
  if (state.workspaceRefreshInFlight) {
    state.workspaceRefreshQueued = true;
    return;
  }
  const sessionId = session.id;
  const scrollSnapshot = captureScrollSnapshot(fileTreeEl);
  state.workspaceRefreshInFlight = true;
  try {
    await loadWorkspaceTree(sessionId);
    if (render && sessionId === state.currentSessionId) {
      renderWorkbench();
      restoreScrollSnapshot(fileTreeEl, scrollSnapshot);
    }
  } finally {
    state.workspaceRefreshInFlight = false;
  }
  if (state.workspaceRefreshQueued) {
    state.workspaceRefreshQueued = false;
    scheduleWorkspaceTreeRefresh({ delayMs: 120 });
  }
}

function scheduleWorkspaceTreeRefresh({ delayMs = 250 } = {}) {
  if (state.workspaceRefreshTimer) {
    window.clearTimeout(state.workspaceRefreshTimer);
  }
  state.workspaceRefreshTimer = window.setTimeout(() => {
    state.workspaceRefreshTimer = null;
    refreshWorkspaceTree().catch((error) => {
      if (centerViewEl) {
        centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
      }
    });
  }, Math.max(0, Number(delayMs) || 0));
}

function shouldRefreshWorkspaceForTrace(eventType, payload) {
  if (eventType !== "tool_call_end") {
    return false;
  }
  const meta = payload?.meta && typeof payload.meta === "object" ? payload.meta : {};
  const toolName = String(meta.tool_name || "").trim();
  const status = String(meta.status || "").trim().toLowerCase();
  return status === "ok" && WORKSPACE_REFRESH_TOOL_NAMES.has(toolName);
}

function expandAllFileTreeFolders(rootNode = state.workspaceTree) {
  collectDirectoryPaths(rootNode).forEach((path) => state.expandedDirs.add(path));
  state.fileTreeExpansionInitialized = true;
  const section = fileSectionForPath(rootNode?.path);
  if (section) {
    state.collapsedFileSections.delete(section.key);
  }
  renderFileTree();
}

function collapseAllFileTreeFolders(rootNode = state.workspaceTree) {
  for (const path of collectDirectoryPaths(rootNode)) {
    state.expandedDirs.delete(path);
  }
  if (!rootNode?.path) {
    state.expandedDirs = new Set([""]);
  } else {
    state.expandedDirs.add("");
    state.expandedDirs.add(rootNode.path);
  }
  state.fileTreeExpansionInitialized = true;
  renderFileTree();
}

function refreshWorkspaceTreeFromPayload(tree) {
  state.workspaceTree = tree;
  if (!state.fileTreeExpansionInitialized) {
    initializeExpandedDirs(tree);
  }
  ensureSessionFileSectionExpanded(tree);
}

function parentPathFor(relativePath) {
  const parts = normalizeRelativePath(relativePath).split("/").filter(Boolean);
  if (parts.length <= 1) {
    return "permanent";
  }
  return parts.slice(0, -1).join("/");
}

function selectFileTreeNode(node) {
  if (!node || !node.path) {
    state.selectedFileTreeNode = null;
    return;
  }
  state.selectedFileTreeNode = {
    path: node.path,
    kind: node.kind,
  };
}

function pasteTargetPathFromSelection() {
  const selected = state.selectedFileTreeNode;
  if (selected?.kind === "directory" && isPermanentPath(selected.path)) {
    return selected.path;
  }
  return "permanent";
}

function actionParentPathForNode(node) {
  if (node?.kind === "directory" && isPermanentPath(node.path)) {
    return node.path;
  }
  if (node?.path && isPermanentPath(node.path)) {
    return parentPathFor(node.path);
  }
  return "permanent";
}

function isSymlinkTreeNode(node) {
  return node?.kind === "symlink_directory" || node?.kind === "symlink_file";
}

function promptEntryName(message, defaultName = "") {
  const value = window.prompt(message, defaultName);
  if (value === null) {
    return "";
  }
  return value.trim();
}

async function runFileOperation(payload) {
  const session = currentSession();
  if (!session) {
    throw new Error("没有活动会话");
  }
  return fetchJson(userScopedUrl(`/api/workspace/${encodeURIComponent(session.id)}/fs`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function uploadFilesToTarget(files, targetParent = pasteTargetPathFromSelection()) {
  const session = currentSession();
  if (!session || !files || files.length === 0) {
    return;
  }
  const formData = new FormData();
  formData.append("target_parent", targetParent || "permanent");
  for (const file of files) {
    formData.append("files", file, file.name);
  }
  const payload = await fetchJson(userScopedUrl(`/api/workspace/${encodeURIComponent(session.id)}/upload`), {
    method: "POST",
    body: formData,
  });
  if (payload.tree) {
    refreshWorkspaceTreeFromPayload(payload.tree);
  }
  if (targetParent) {
    state.expandedDirs.add(targetParent);
  }
  const first = Array.isArray(payload.items) ? payload.items[0] : null;
  if (first?.path) {
    expandAncestors(first.path);
    state.selectedFileTreeNode = { path: first.path, kind: "file" };
    await openFile(first.path, { persist: true, skipDirtyCheck: true });
    return;
  }
  renderWorkbench();
}

function downloadFileEntry(node = state.selectedFileTreeNode) {
  if (!node || node.kind !== "file" || !isDownloadablePath(node.path)) {
    return;
  }
  const session = currentSession();
  if (!session) {
    return;
  }
  window.location.href = userScopedUrl(`/api/workspace/${encodeURIComponent(session.id)}/download?path=${encodeURIComponent(node.path)}`);
}

function copyFileTreeNode(node) {
  if (!node || !node.path || !isPermanentPath(node.path)) {
    return;
  }
  state.fileClipboard = {
    path: node.path,
    kind: node.kind,
  };
  renderFileToolbar();
}

function removeOpenTabsUnderPath(relativePath) {
  const normalizedPath = normalizeRelativePath(relativePath);
  if (!normalizedPath) {
    return;
  }
  state.openFileTabs = state.openFileTabs.filter((tab) => (
    tab.path !== normalizedPath && !tab.path.startsWith(`${normalizedPath}/`)
  ));
  if (!state.activeFilePath || fileTabByPath(state.activeFilePath)) {
    return;
  }
  const nextTab = state.openFileTabs[0] || null;
  if (nextTab) {
    activateFileTab(nextTab.path);
  } else {
    state.activeFilePath = "";
    state.currentDocument = null;
    state.fileDraft = "";
    state.markdownPreview = false;
  }
}

async function createFileEntry(kind, parent = pasteTargetPathFromSelection()) {
  const isDirectory = kind === "directory";
  const name = promptEntryName(isDirectory ? "新文件夹名称" : "新文件名称", isDirectory ? "新建文件夹" : "未命名.md");
  if (!name) {
    return;
  }
  const payload = await runFileOperation({
    operation: isDirectory ? "create_dir" : "create_file",
    parent: parent || "permanent",
    name,
  });
  if (payload.tree) {
    refreshWorkspaceTreeFromPayload(payload.tree);
  }
  if (parent) {
    state.expandedDirs.add(parent);
  }
  if (payload.path) {
    expandAncestors(payload.path);
    state.selectedFileTreeNode = { path: payload.path, kind: payload.kind || kind };
    if (payload.kind === "file") {
      await openFile(payload.path, { persist: true, skipDirtyCheck: true });
      return;
    }
  }
  renderWorkbench();
}

async function renameFileEntry(node) {
  if (!node?.path || !isPermanentPath(node.path)) {
    return;
  }
  const name = promptEntryName("重命名为", basenameFromPath(node.path));
  if (!name || name === basenameFromPath(node.path)) {
    return;
  }
  const payload = await runFileOperation({
    operation: "rename",
    path: node.path,
    name,
  });
  removeOpenTabsUnderPath(node.path);
  if (state.fileClipboard?.path === node.path || state.fileClipboard?.path?.startsWith(`${node.path}/`)) {
    state.fileClipboard = null;
  }
  if (payload.tree) {
    refreshWorkspaceTreeFromPayload(payload.tree);
  }
  if (payload.path) {
    expandAncestors(payload.path);
    state.selectedFileTreeNode = { path: payload.path, kind: payload.kind || node.kind };
    if (payload.kind === "file") {
      await openFile(payload.path, { persist: true, skipDirtyCheck: true });
      return;
    }
  }
  renderWorkbench();
}

async function deleteFileEntry(node) {
  if (!node?.path || !isPermanentPath(node.path)) {
    return;
  }
  const ok = window.confirm(`删除${node.kind === "directory" ? "文件夹" : "文件"}“${basenameFromPath(node.path)}”？`);
  if (!ok) {
    return;
  }
  const payload = await runFileOperation({
    operation: "delete",
    path: node.path,
    recursive: node.kind === "directory",
  });
  removeOpenTabsUnderPath(node.path);
  if (state.selectedFileTreeNode?.path === node.path || state.selectedFileTreeNode?.path?.startsWith(`${node.path}/`)) {
    state.selectedFileTreeNode = null;
  }
  if (state.fileClipboard?.path === node.path || state.fileClipboard?.path?.startsWith(`${node.path}/`)) {
    state.fileClipboard = null;
  }
  if (payload.tree) {
    refreshWorkspaceTreeFromPayload(payload.tree);
  }
  renderWorkbench();
}

function canDropFileTreeNode(source, target) {
  const targetParentPath = dropTargetParentPathForTreeNode(target);
  if (!source?.path || !targetParentPath) {
    return false;
  }
  if (!isPermanentPath(source.path) || !isPermanentPath(targetParentPath)) {
    return false;
  }
  if (source.path === targetParentPath || parentPathFor(source.path) === targetParentPath) {
    return false;
  }
  return !targetParentPath.startsWith(`${source.path}/`);
}

function dropTargetParentPathForTreeNode(node) {
  if (!node?.path || !isPermanentPath(node.path)) {
    return "";
  }
  if (node.kind === "directory") {
    return node.path;
  }
  if (node.kind === "file") {
    return parentPathFor(node.path);
  }
  return "";
}

function clearFileTreeDragState() {
  syncFileTreeDragTargetClasses("", "");
  state.draggingFileTreeNode = null;
  state.dragTargetPath = "";
  state.dragTargetParentPath = "";
}

function setFileTreeDragTarget(displayPath, targetParentPath) {
  if (state.dragTargetPath === displayPath && state.dragTargetParentPath === targetParentPath) {
    return;
  }
  syncFileTreeDragTargetClasses(displayPath, targetParentPath);
  state.dragTargetPath = displayPath;
  state.dragTargetParentPath = targetParentPath;
}

function syncFileTreeDragTargetClasses(nextDisplayPath, nextTargetParentPath) {
  if (state.dragTargetPath) {
    for (const element of document.querySelectorAll(`[data-tree-path="${cssEscape(state.dragTargetPath)}"]`)) {
      element.classList.remove("drop-target");
    }
  }
  if (!nextDisplayPath || !nextTargetParentPath) {
    return;
  }
  for (const element of document.querySelectorAll(`[data-tree-path="${cssEscape(nextDisplayPath)}"]`)) {
    element.classList.add("drop-target");
  }
}

function cssEscape(value) {
  if (window.CSS?.escape) {
    return window.CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
}

function attachFileTreeDropTarget(element, node, targetParentPath = dropTargetParentPathForTreeNode(node)) {
  if (!element || !node?.path || !targetParentPath) {
    return;
  }
  element.addEventListener("dragover", (event) => {
    if (!canDropFileTreeNode(state.draggingFileTreeNode, node)) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setFileTreeDragTarget(node.path, targetParentPath);
  });
  element.addEventListener("dragleave", (event) => {
    const relatedTarget = event.relatedTarget;
    if (relatedTarget instanceof Node && element.contains(relatedTarget)) {
      return;
    }
    if (state.dragTargetPath === node.path) {
      setFileTreeDragTarget("", "");
    }
  });
  element.addEventListener("drop", (event) => {
    if (!canDropFileTreeNode(state.draggingFileTreeNode, node)) {
      return;
    }
    event.preventDefault();
    const source = state.draggingFileTreeNode;
    clearFileTreeDragState();
    moveFileTreeNode(source, targetParentPath).catch((error) => {
      centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      renderFileTree();
    });
  });
}

async function moveFileTreeNode(source, targetParentPath) {
  if (!source?.path || !targetParentPath) {
    return;
  }
  const payload = await runFileOperation({
    operation: "move",
    source: source.path,
    target_parent: targetParentPath,
  });
  removeOpenTabsUnderPath(source.path);
  if (state.fileClipboard?.path === source.path || state.fileClipboard?.path?.startsWith(`${source.path}/`)) {
    state.fileClipboard = null;
  }
  if (payload.tree) {
    refreshWorkspaceTreeFromPayload(payload.tree);
  }
  if (targetParentPath) {
    state.expandedDirs.add(targetParentPath);
  }
  if (payload.path) {
    expandAncestors(payload.path);
    state.selectedFileTreeNode = { path: payload.path, kind: payload.kind || source.kind };
    if (payload.kind === "file") {
      await openFile(payload.path, { persist: true, skipDirtyCheck: true });
      return;
    }
  }
  renderWorkbench();
}

async function pasteCopiedFile(targetParent = pasteTargetPathFromSelection()) {
  if (!state.fileClipboard) {
    return;
  }
  const payload = await runFileOperation({
    operation: "copy",
    source: state.fileClipboard.path,
    target_parent: targetParent || "permanent",
  });
  if (payload.tree) {
    refreshWorkspaceTreeFromPayload(payload.tree);
  } else {
    const session = currentSession();
    if (session) {
      await loadWorkspaceTree(session.id);
    }
  }
  if (targetParent) {
    state.expandedDirs.add(targetParent);
  }
  if (payload.path) {
    expandAncestors(payload.path);
    state.selectedFileTreeNode = { path: payload.path, kind: payload.kind || "file" };
    if (payload.kind === "file") {
      await openFile(payload.path, { persist: true, skipDirtyCheck: true });
      return;
    }
  }
  renderWorkbench();
}

async function persistTempFileEntry(node = state.selectedFileTreeNode) {
  if (!node?.path || !isTempPath(node.path) || isSymlinkTreeNode(node)) {
    return;
  }
  const payload = await runFileOperation({
    operation: "persist_temp",
    source: node.path,
  });
  if (payload.tree) {
    refreshWorkspaceTreeFromPayload(payload.tree);
  }
  state.collapsedFileSections.delete("permanent");
  state.expandedDirs.add("permanent");
  if (payload.path) {
    expandAncestors(payload.path);
    state.selectedFileTreeNode = { path: payload.path, kind: payload.kind || node.kind };
    if (payload.kind === "file") {
      await openFile(payload.path, { persist: true, skipDirtyCheck: true });
      return;
    }
  }
  renderWorkbench();
}

async function loadSkills(sessionId) {
  const payload = await fetchJson(userScopedUrl(`/api/workspace/${encodeURIComponent(sessionId)}/skills`));
  state.skills = Array.isArray(payload.items) ? payload.items : [];
}

function isMarkdownPath(path) {
  return String(path || "").toLowerCase().endsWith(".md");
}

function destroyMarkdownEditor() {
  if (markdownEditor && typeof markdownEditor.destroy === "function") {
    markdownEditor.destroy();
  }
  markdownEditor = null;
  markdownEditorReady = false;
  markdownEditorTouched = false;
}

function syncMarkdownEditorDraft() {
  if (markdownEditorTouched && markdownEditor && typeof markdownEditor.getMarkdown === "function") {
    state.fileDraft = markdownEditor.getMarkdown();
  }
}

function displayFileType(path) {
  const normalized = String(path || "").toLowerCase();
  const extension = normalized.includes(".") ? normalized.split(".").pop() : "";
  if (!extension) {
    return "Text";
  }
  return extension === "md" ? "Markdown" : extension.toUpperCase();
}

function isDocumentDirty() {
  const activeTab = activeFileTab();
  if (activeTab) {
    return activeTab.file.editable && activeTab.draft !== activeTab.file.content;
  }
  return Boolean(
    state.currentDocument
    && state.currentDocument.type === "file"
    && state.currentDocument.file
    && state.currentDocument.file.editable
    && state.fileDraft !== state.currentDocument.file.content,
  );
}

function basenameFromPath(relativePath) {
  const parts = normalizeRelativePath(relativePath).split("/").filter(Boolean);
  return parts[parts.length - 1] || relativePath || "未命名";
}

function fileTabByPath(relativePath) {
  const normalizedPath = normalizeRelativePath(relativePath);
  return state.openFileTabs.find((tab) => tab.path === normalizedPath) || null;
}

function activeFileTab() {
  return fileTabByPath(state.activeFilePath);
}

function isFileTabDirty(tab) {
  return Boolean(tab?.file?.editable && tab.draft !== tab.file.content);
}

function syncActiveFileTabDraft() {
  if (state.currentDocument?.type !== "file") {
    return;
  }
  syncMarkdownEditorDraft();
  const tab = activeFileTab();
  if (!tab) {
    return;
  }
  tab.draft = state.fileDraft;
  tab.markdownPreview = state.markdownPreview;
}

function activateFileTab(relativePath) {
  const tab = fileTabByPath(relativePath);
  if (!tab) {
    return false;
  }
  state.activeFilePath = tab.path;
  state.currentDocument = { type: "file", file: tab.file };
  state.fileDraft = tab.draft;
  state.markdownPreview = tab.markdownPreview;
  expandAncestors(tab.path);
  return true;
}

async function confirmDiscardIfDirty() {
  syncActiveFileTabDraft();
  if (!state.openFileTabs.some(isFileTabDirty) && !isDocumentDirty()) {
    return true;
  }
  return window.confirm("一个或多个已打开文件有未保存更改。要放弃更改并继续吗？");
}

function expandAncestors(relativePath) {
  state.expandedDirs.add("");
  const parts = normalizeRelativePath(relativePath).split("/").filter(Boolean);
  if (parts[0] === "permanent") {
    state.collapsedFileSections.delete("permanent");
  } else if (parts[0] === "temp") {
    state.collapsedFileSections.delete("session");
  }
  let currentPath = "";
  for (let index = 0; index < Math.max(0, parts.length - 1); index += 1) {
    currentPath = currentPath ? `${currentPath}/${parts[index]}` : parts[index];
    state.expandedDirs.add(currentPath);
  }
}

async function openFile(relativePath, { persist = true, skipDirtyCheck = false } = {}) {
  const normalizedPath = normalizeRelativePath(relativePath);
  if (!normalizedPath) {
    return;
  }
  const session = currentSession();
  if (!session) {
    return;
  }
  syncActiveFileTabDraft();
  let tab = fileTabByPath(normalizedPath);
  if (!tab) {
    const file = await fetchJson(userScopedUrl(`/api/workspace/${encodeURIComponent(session.id)}/file?path=${encodeURIComponent(normalizedPath)}`));
    tab = {
      path: file.path,
      file,
      draft: file.content || "",
      markdownPreview: isMarkdownPath(file.path),
    };
    state.openFileTabs.push(tab);
  }
  activateFileTab(tab.path);
  expandAncestors(normalizedPath);
  if (persist && !isMobileLayout()) {
    setCenterPanelHidden(false);
  }
  if (persist) {
    updateCurrentSessionLocally({
      ui_state: {
        ...currentUiState(),
        left_tab: "files",
        active_path: normalizedPath,
        center_view: "file",
      },
    });
    try {
      await patchCurrentSession({
        ui_state: {
          ...currentUiState(),
          left_tab: "files",
          active_path: normalizedPath,
          center_view: "file",
        },
      });
    } catch (error) {
      // Keep optimistic UI; next action will retry persistence.
    }
  }
  if (isMobileLayout()) {
    setMobilePanel("center");
  }
  renderWorkbench();
}

async function closeFileTab(relativePath) {
  syncActiveFileTabDraft();
  const normalizedPath = normalizeRelativePath(relativePath);
  const tabIndex = state.openFileTabs.findIndex((tab) => tab.path === normalizedPath);
  if (tabIndex < 0) {
    return;
  }
  const tab = state.openFileTabs[tabIndex];
  if (isFileTabDirty(tab) && !window.confirm("此文件有未保存更改。仍要关闭吗？")) {
    return;
  }
  state.openFileTabs.splice(tabIndex, 1);
  if (state.activeFilePath === normalizedPath) {
    const nextTab = state.openFileTabs[Math.max(0, tabIndex - 1)] || state.openFileTabs[0] || null;
    if (nextTab) {
      activateFileTab(nextTab.path);
      updateCurrentSessionLocally({
        ui_state: {
          ...currentUiState(),
          left_tab: "files",
          active_path: nextTab.path,
          center_view: "file",
        },
      });
      patchCurrentSession({
        ui_state: {
          ...currentUiState(),
          left_tab: "files",
          active_path: nextTab.path,
          center_view: "file",
        },
      }).catch(() => {});
    } else {
      state.activeFilePath = "";
      state.currentDocument = null;
      state.fileDraft = "";
      state.markdownPreview = false;
      updateCurrentSessionLocally({
        ui_state: {
          ...currentUiState(),
          active_path: "",
          center_view: "empty",
        },
      });
      patchCurrentSession({
        ui_state: {
          ...currentUiState(),
          active_path: "",
          center_view: "empty",
        },
      }).catch(() => {});
    }
  }
  renderWorkbench();
}

function confirmCloseDirtyTabs(tabs) {
  const dirtyTabs = tabs.filter(isFileTabDirty);
  if (dirtyTabs.length === 0) {
    return true;
  }
  const message = dirtyTabs.length === 1
    ? "此文件有未保存更改。仍要关闭吗？"
    : `${dirtyTabs.length} 个文件有未保存更改。仍要关闭吗？`;
  return window.confirm(message);
}

function setActiveFileTabAfterBulkClose(nextTab) {
  if (nextTab) {
    activateFileTab(nextTab.path);
    updateCurrentSessionLocally({
      ui_state: {
        ...currentUiState(),
        left_tab: "files",
        active_path: nextTab.path,
        center_view: "file",
      },
    });
    patchCurrentSession({
      ui_state: {
        ...currentUiState(),
        left_tab: "files",
        active_path: nextTab.path,
        center_view: "file",
      },
    }).catch(() => {});
    return;
  }
  state.activeFilePath = "";
  state.currentDocument = null;
  state.fileDraft = "";
  state.markdownPreview = false;
  updateCurrentSessionLocally({
    ui_state: {
      ...currentUiState(),
      active_path: "",
      center_view: "empty",
    },
  });
  patchCurrentSession({
    ui_state: {
      ...currentUiState(),
      active_path: "",
      center_view: "empty",
    },
  }).catch(() => {});
}

async function closeOtherFileTabs(relativePath) {
  syncActiveFileTabDraft();
  const normalizedPath = normalizeRelativePath(relativePath);
  const targetTab = fileTabByPath(normalizedPath);
  if (!targetTab) {
    return;
  }
  const tabsToClose = state.openFileTabs.filter((tab) => tab.path !== normalizedPath);
  if (!confirmCloseDirtyTabs(tabsToClose)) {
    return;
  }
  state.openFileTabs = [targetTab];
  setActiveFileTabAfterBulkClose(targetTab);
  renderWorkbench();
}

async function closeAllFileTabs() {
  syncActiveFileTabDraft();
  const tabsToClose = [...state.openFileTabs];
  if (!confirmCloseDirtyTabs(tabsToClose)) {
    return;
  }
  state.openFileTabs = [];
  setActiveFileTabAfterBulkClose(null);
  renderWorkbench();
}

function hideFileTabContextMenu() {
  const existing = document.querySelector(".file-tab-context-menu");
  if (existing) {
    existing.remove();
  }
}

function showFileTabContextMenu(event, tab) {
  event.preventDefault();
  event.stopPropagation();
  hideFileContextMenu();
  hideFileTabContextMenu();

  const menu = document.createElement("div");
  menu.className = "file-tab-context-menu";
  menu.innerHTML = `
    <button type="button" data-action="close">关闭</button>
    <button type="button" data-action="close-others">关闭其它</button>
    <button type="button" data-action="close-all">关闭所有</button>
  `;
  document.body.appendChild(menu);

  const rect = menu.getBoundingClientRect();
  const x = Math.min(event.clientX, window.innerWidth - rect.width - 8);
  const y = Math.min(event.clientY, window.innerHeight - rect.height - 8);
  menu.style.left = `${Math.max(8, x)}px`;
  menu.style.top = `${Math.max(8, y)}px`;

  const closeOthersButton = menu.querySelector('[data-action="close-others"]');
  if (closeOthersButton) {
    closeOthersButton.disabled = state.openFileTabs.length <= 1;
  }

  menu.addEventListener("click", (clickEvent) => {
    const action = clickEvent.target.closest("button")?.dataset.action;
    if (!action) {
      return;
    }
    hideFileTabContextMenu();
    if (action === "close") {
      closeFileTab(tab.path).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    if (action === "close-others") {
      closeOtherFileTabs(tab.path).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    if (action === "close-all") {
      closeAllFileTabs().catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
    }
  });
}

function skillByName(name) {
  return state.skills.find((skill) => skill.name === name) || null;
}

async function openSkill(name, { persist = true, skipDirtyCheck = false } = {}) {
  const skill = skillByName(name);
  if (!skill) {
    return;
  }
  if (!skipDirtyCheck && !(await confirmDiscardIfDirty())) {
    return;
  }
  const session = currentSession();
  if (!session) {
    return;
  }
  const file = await fetchJson(userScopedUrl(`/api/workspace/${encodeURIComponent(session.id)}/file?path=${encodeURIComponent(skill.path)}`));
  state.currentDocument = {
    type: "skill",
    skill,
    body: file.content || "",
  };
  state.activeFilePath = "";
  state.fileDraft = "";
  state.markdownPreview = true;
  if (persist && !isMobileLayout()) {
    setCenterPanelHidden(false);
  }
  if (persist) {
    updateCurrentSessionLocally({
      ui_state: {
        ...currentUiState(),
        left_tab: "skills",
        active_path: "",
        center_view: "skill",
        selected_skill: skill.name,
      },
    });
    try {
      await patchCurrentSession({
        ui_state: {
          ...currentUiState(),
          left_tab: "skills",
          active_path: "",
          center_view: "skill",
          selected_skill: skill.name,
        },
      });
    } catch {
      // Keep optimistic UI; next action will retry persistence.
    }
  }
  if (isMobileLayout()) {
    setMobilePanel("center");
  }
  renderWorkbench();
}

async function restoreCenterState() {
  const session = currentSession();
  if (!session) {
    state.currentDocument = null;
    state.openFileTabs = [];
    state.activeFilePath = "";
    state.fileDraft = "";
    return;
  }
  const uiState = currentUiState();
  if (uiState.center_view === "file" && uiState.active_path) {
    try {
      await openFile(uiState.active_path, { persist: false, skipDirtyCheck: true });
      return;
    } catch {
      // Fall through to empty state.
    }
  }
  if (uiState.center_view === "skill" && uiState.selected_skill) {
    try {
      await openSkill(uiState.selected_skill, { persist: false, skipDirtyCheck: true });
      return;
    } catch {
      // Fall through to empty state.
    }
  }
  state.currentDocument = null;
  state.activeFilePath = "";
  state.fileDraft = "";
  state.markdownPreview = false;
}

async function loadSessions({ preserveSelection = true } = {}) {
  state.sessions = (await fetchJson(userScopedUrl("/api/sessions"))).map(normalizeSessionRecordClient);
  if (state.sessions.length === 0) {
    const session = await fetchJson(userScopedUrl("/api/sessions"), { method: "POST", body: "{}" });
    state.sessions = [normalizeSessionRecordClient(session)];
  }

  const storedSessionId = localStorage.getItem(currentSessionStorageKey());
  const preferredId = preserveSelection ? (state.currentSessionId || storedSessionId || state.sessions[0].id) : state.sessions[0].id;
  const selected = state.sessions.find((session) => session.id === preferredId) || state.sessions[0];
  state.currentSessionId = selected.id;
  localStorage.setItem(currentSessionStorageKey(), state.currentSessionId);

  state.currentDocument = null;
  state.openFileTabs = [];
  state.activeFilePath = "";
  state.fileDraft = "";
  state.workspaceTree = null;
  state.skills = [];
  resetPendingMentionState();

  await Promise.all([
    loadHistory(state.currentSessionId),
    loadWorkspaceTree(state.currentSessionId),
    loadSkills(state.currentSessionId),
  ]);
  await restoreCenterState();
  renderWorkbench();
}

async function refreshSessionsListOnly() {
  const sessions = (await fetchJson(userScopedUrl("/api/sessions"))).map(normalizeSessionRecordClient);
  state.sessions = sessions;
  if (!state.currentSessionId && sessions.length > 0) {
    state.currentSessionId = sessions[0].id;
    localStorage.setItem(currentSessionStorageKey(), state.currentSessionId);
  }
}

async function activateSession(sessionId) {
  const nextSessionId = String(sessionId || "").trim();
  if (!nextSessionId) {
    return;
  }
  if (nextSessionId === state.currentSessionId) {
    state.sessionHistoryOpen = false;
    renderWorkbench();
    return;
  }
  if (!(await confirmDiscardIfDirty())) {
    return;
  }
  if (!sessionById(nextSessionId)) {
    await refreshSessionsListOnly();
    if (!sessionById(nextSessionId)) {
      return;
    }
  }

  state.currentSessionId = nextSessionId;
  localStorage.setItem(currentSessionStorageKey(), nextSessionId);
  initializeExpandedDirs(null);
  destroyMarkdownEditor();
  state.currentDocument = null;
  state.openFileTabs = [];
  state.activeFilePath = "";
  state.fileDraft = "";
  state.workspaceTree = null;
  state.skills = [];
  state.selectedFileTreeNode = null;
  state.markdownPreview = false;
  state.sessionHistoryOpen = false;
  resetPendingMentionState();

  await Promise.all([
    loadHistory(nextSessionId),
    loadWorkspaceTree(nextSessionId),
    loadSkills(nextSessionId),
  ]);
  await restoreCenterState();
  renderWorkbench();
}

async function createSession() {
  if (state.sessionCreateInFlight) {
    return;
  }
  if (!(await confirmDiscardIfDirty())) {
    return;
  }
  const existingSession = currentSession();
  if (isBlankNewSession(existingSession)) {
    state.histories.set(existingSession.id, []);
    initializeExpandedDirs(null);
    destroyMarkdownEditor();
    state.currentDocument = null;
    state.openFileTabs = [];
    state.activeFilePath = "";
    state.fileDraft = "";
    state.selectedFileTreeNode = null;
    state.markdownPreview = false;
    state.sessionHistoryOpen = false;
    resetPendingMentionState();
    renderWorkbench();
    messageInputEl.focus();
    return;
  }
  state.sessionCreateInFlight = true;
  newSessionButtonEl.disabled = true;
  try {
    const session = await fetchJson(userScopedUrl("/api/sessions"), { method: "POST", body: "{}" });
    applySessionRecord(session);
    state.currentSessionId = session.id;
    state.histories.set(session.id, []);
    localStorage.setItem(currentSessionStorageKey(), session.id);
    resetPendingMentionState();
    initializeExpandedDirs(null);
    await Promise.all([
      loadWorkspaceTree(session.id),
      loadSkills(session.id),
    ]);
    state.currentDocument = null;
    state.openFileTabs = [];
    state.activeFilePath = "";
    state.fileDraft = "";
    state.sessionHistoryOpen = false;
    renderWorkbench();
  } finally {
    state.sessionCreateInFlight = false;
    newSessionButtonEl.disabled = false;
  }
}

async function deleteSession(sessionId) {
  const targetSessionId = String(sessionId || "").trim();
  if (!targetSessionId) {
    return;
  }
  const session = sessionById(targetSessionId);
  if (session && String(session.status || "idle").toLowerCase() === "running") {
    window.alert("此会话仍在运行，暂时无法删除。");
    return;
  }
  if (targetSessionId === state.currentSessionId && !(await confirmDiscardIfDirty())) {
    return;
  }
  const title = session?.title || "新对话";
  if (!window.confirm(`删除“${title}”？此操作无法撤销。`)) {
    return;
  }

  await fetchJson(userScopedUrl(`/api/sessions/${encodeURIComponent(targetSessionId)}`), { method: "DELETE" });
  state.histories.delete(targetSessionId);
  state.sessions = state.sessions.filter((item) => item.id !== targetSessionId);
  if (targetSessionId === state.currentSessionId) {
    localStorage.removeItem(currentSessionStorageKey());
    state.currentSessionId = null;
    await loadSessions({ preserveSelection: false });
    return;
  }
  await refreshSessionsListOnly();
  renderWorkbench();
}

function setActiveTab(tabName) {
  updateCurrentSessionLocally({
    ui_state: {
      ...currentUiState(),
      left_tab: tabName === "skills" ? "skills" : "files",
    },
  });
  renderWorkbench();
  patchCurrentSession({
    ui_state: {
      ...currentUiState(),
      left_tab: tabName === "skills" ? "skills" : "files",
    },
  }).catch(() => {
    // Ignore persistence failure until the next successful action.
  });
}

function renderNavTabs() {
  const uiState = currentUiState();
  const filesActive = uiState.left_tab !== "skills";
  filesTabButtonEl.classList.toggle("active", filesActive);
  filesTabButtonEl.setAttribute("aria-selected", filesActive ? "true" : "false");
  skillsTabButtonEl.classList.toggle("active", !filesActive);
  skillsTabButtonEl.setAttribute("aria-selected", !filesActive ? "true" : "false");
  filesPanelEl.classList.toggle("hidden", !filesActive);
  skillsPanelEl.classList.toggle("hidden", filesActive);
}

function renderFileToolbar() {
  if (!filePasteButtonEl) {
    return;
  }
  const selected = state.selectedFileTreeNode;
  const hasSelection = Boolean(selected?.path && isPermanentPath(selected.path) && !isSymlinkTreeNode(selected));
  const canPersist = Boolean(selectedTempFileTreeNodeForPersist());
  const hasClipboard = Boolean(state.fileClipboard);
  const canDownload = selected?.kind === "file" && isDownloadablePath(selected.path);
  if (fileCopyButtonEl) {
    fileCopyButtonEl.disabled = !hasSelection;
    fileCopyButtonEl.title = hasSelection
      ? `复制 ${basenameFromPath(selected.path)}`
      : "选择要复制的文件或文件夹";
    fileCopyButtonEl.setAttribute("aria-label", fileCopyButtonEl.title);
  }
  filePasteButtonEl.disabled = !hasClipboard;
  filePasteButtonEl.title = hasClipboard
    ? `粘贴 ${basenameFromPath(state.fileClipboard.path)}`
    : "请先复制文件或文件夹";
  filePasteButtonEl.setAttribute("aria-label", filePasteButtonEl.title);
  if (fileDownloadButtonEl) {
    fileDownloadButtonEl.disabled = !canDownload;
    fileDownloadButtonEl.title = canDownload
      ? `下载 ${basenameFromPath(state.selectedFileTreeNode.path)}`
      : "选择要下载的文件";
    fileDownloadButtonEl.setAttribute("aria-label", fileDownloadButtonEl.title);
  }
  if (filePersistButtonEl) {
    filePersistButtonEl.disabled = !canPersist;
    filePersistButtonEl.title = canPersist
      ? `将 ${basenameFromPath(selected.path)} 保存到永久文件`
      : "选择要保存的临时文件或文件夹";
    filePersistButtonEl.setAttribute("aria-label", filePersistButtonEl.title);
  }
  for (const button of document.querySelectorAll("[data-action='persist-session-temp']")) {
    updateSessionPersistButton(button);
  }
  if (fileRenameButtonEl) {
    fileRenameButtonEl.disabled = !hasSelection;
    fileRenameButtonEl.title = hasSelection
      ? `重命名 ${basenameFromPath(selected.path)}`
      : "选择要重命名的文件或文件夹";
    fileRenameButtonEl.setAttribute("aria-label", fileRenameButtonEl.title);
  }
  if (fileDeleteButtonEl) {
    fileDeleteButtonEl.disabled = !hasSelection;
    fileDeleteButtonEl.title = hasSelection
      ? `删除 ${basenameFromPath(selected.path)}`
      : "选择要删除的文件或文件夹";
    fileDeleteButtonEl.setAttribute("aria-label", fileDeleteButtonEl.title);
  }
}

function selectedTempFileTreeNodeForPersist() {
  const selected = state.selectedFileTreeNode;
  if (!selected?.path || !isTempPath(selected.path) || isSymlinkTreeNode(selected)) {
    return null;
  }
  return selected;
}

function hideFileContextMenu() {
  const existing = document.querySelector(".file-context-menu");
  if (existing) {
    existing.remove();
  }
}

function showFileContextMenu(event, node) {
  event.preventDefault();
  event.stopPropagation();
  selectFileTreeNode(node);
  hideFileContextMenu();

  const menu = document.createElement("div");
  menu.className = "file-context-menu";
  const tempActions = isTempPath(node.path) && !isSymlinkTreeNode(node)
    ? '<button type="button" data-action="persist">保存到永久文件</button>'
    : "";
  menu.innerHTML = `
    <button type="button" data-action="new-file">新建文件</button>
    <button type="button" data-action="new-folder">新建文件夹</button>
    <button type="button" data-action="upload">上传</button>
    <button type="button" data-action="download">下载</button>
    ${tempActions}
    <button type="button" data-action="copy">复制</button>
    <button type="button" data-action="paste">粘贴</button>
    <button type="button" data-action="rename">重命名</button>
    <button type="button" data-action="delete">删除</button>
    <button type="button" data-action="refresh">刷新</button>
  `;
  document.body.appendChild(menu);

  const rect = menu.getBoundingClientRect();
  const x = Math.min(event.clientX, window.innerWidth - rect.width - 8);
  const y = Math.min(event.clientY, window.innerHeight - rect.height - 8);
  menu.style.left = `${Math.max(8, x)}px`;
  menu.style.top = `${Math.max(8, y)}px`;

  const pasteButton = menu.querySelector('[data-action="paste"]');
  if (pasteButton) {
    pasteButton.disabled = !state.fileClipboard || !isPermanentPath(actionParentPathForNode(node));
  }
  const downloadButton = menu.querySelector('[data-action="download"]');
  if (downloadButton) {
    downloadButton.disabled = node.kind !== "file" || !isDownloadablePath(node.path);
  }
  for (const action of ["new-file", "new-folder", "upload", "copy", "rename", "delete"]) {
    const button = menu.querySelector(`[data-action="${action}"]`);
    if (button) {
      const needsSelectedPath = action === "copy" || action === "rename" || action === "delete";
      button.disabled = isSymlinkTreeNode(node) || !isPermanentPath(needsSelectedPath ? node.path : actionParentPathForNode(node));
    }
  }

  menu.addEventListener("click", (clickEvent) => {
    const action = clickEvent.target.closest("button")?.dataset.action;
    if (!action) {
      return;
    }
    hideFileContextMenu();
    if (action === "copy") {
      copyFileTreeNode(node);
      renderWorkbench();
      return;
    }
    if (action === "download" && node.kind === "file") {
      downloadFileEntry(node);
      return;
    }
    if (action === "persist") {
      persistTempFileEntry(node).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    if (action === "upload") {
      state.uploadTargetPath = actionParentPathForNode(node);
      if (fileUploadInputEl) {
        fileUploadInputEl.value = "";
        fileUploadInputEl.click();
      }
      return;
    }
    if (action === "rename") {
      renameFileEntry(node).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    if (action === "delete") {
      deleteFileEntry(node).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    if (action === "new-file") {
      createFileEntry("file", actionParentPathForNode(node)).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    if (action === "new-folder") {
      createFileEntry("directory", actionParentPathForNode(node)).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    if (action === "paste") {
      pasteCopiedFile(actionParentPathForNode(node)).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    if (action === "refresh") {
      const session = currentSession();
      if (!session) {
        return;
      }
      loadWorkspaceTree(session.id)
        .then(renderWorkbench)
        .catch((error) => {
          centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
        });
    }
  });

  renderFileTree();
}

function renderTreeNode(node, depth = 0) {
  if (!node) {
    return document.createDocumentFragment();
  }
  const fragment = document.createDocumentFragment();
  if (node.kind === "directory") {
    if (node.path) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "tree-row tree-dir";
      if (state.selectedFileTreeNode?.path === node.path) {
        row.classList.add("selected");
      }
      if (state.dragTargetPath === node.path) {
        row.classList.add("drop-target");
      }
      row.dataset.treePath = node.path;
      row.draggable = isPermanentPath(node.path);
      row.style.setProperty("--depth", String(depth));
      const open = state.expandedDirs.has(node.path);
      row.innerHTML = `
        <span class="tree-caret">${open ? "▾" : "▸"}</span>
        <span class="tree-label">${escapeHtml(node.name)}</span>
      `;
      row.addEventListener("click", () => {
        selectFileTreeNode(node);
        if (state.expandedDirs.has(node.path)) {
          state.expandedDirs.delete(node.path);
        } else {
          state.expandedDirs.add(node.path);
        }
        renderFileTree();
      });
      row.addEventListener("contextmenu", (event) => showFileContextMenu(event, node));
      row.addEventListener("dragstart", (event) => {
        state.draggingFileTreeNode = { path: node.path, kind: node.kind };
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", node.path);
        hideFileContextMenu();
      });
      row.addEventListener("dragend", () => {
        clearFileTreeDragState();
        renderFileTree();
      });
      attachFileTreeDropTarget(row, node);
      fragment.appendChild(row);
    }
    const shouldRenderChildren = !node.path || state.expandedDirs.has(node.path);
    if (shouldRenderChildren) {
      const nextDepth = node.path ? depth + 1 : depth;
      for (const child of node.children || []) {
        fragment.appendChild(renderTreeNode(child, nextDepth));
      }
    }
    return fragment;
  }

  if (isSymlinkTreeNode(node)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tree-row tree-symlink";
    if (state.selectedFileTreeNode?.path === node.path) {
      button.classList.add("selected");
    }
    button.style.setProperty("--depth", String(depth));
    button.title = node.kind === "symlink_directory" ? "链接目录" : "链接文件";
    button.innerHTML = `
      <span class="tree-spacer"></span>
      <span class="tree-label">${escapeHtml(node.name)}</span>
      <span class="tree-node-badge">链接</span>
    `;
    button.addEventListener("click", () => {
      selectFileTreeNode(node);
      renderFileToolbar();
    });
    return button;
  }

  const uiState = currentUiState();
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tree-row tree-file";
  if (uiState.active_path === node.path && uiState.center_view === "file") {
    button.classList.add("active");
  }
  if (state.selectedFileTreeNode?.path === node.path) {
    button.classList.add("selected");
  }
  if (state.dragTargetPath === node.path) {
    button.classList.add("drop-target");
  }
  button.dataset.treePath = node.path;
  button.draggable = isPermanentPath(node.path);
  button.style.setProperty("--depth", String(depth));
  button.innerHTML = `
    <span class="tree-spacer"></span>
    <span class="tree-label">${escapeHtml(node.name)}</span>
  `;
  button.addEventListener("click", () => {
    selectFileTreeNode(node);
    openFile(node.path).catch((error) => {
      centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
    });
  });
  button.addEventListener("contextmenu", (event) => showFileContextMenu(event, node));
  button.addEventListener("dragstart", (event) => {
    state.draggingFileTreeNode = { path: node.path, kind: node.kind };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", node.path);
    hideFileContextMenu();
  });
  button.addEventListener("dragend", () => {
    clearFileTreeDragState();
    renderFileTree();
  });
  attachFileTreeDropTarget(button, node);
  return button;
}

function fileSectionForPath(path) {
  const normalized = normalizeRelativePath(path);
  if (normalized === "permanent") {
    return {
      key: "permanent",
      title: "用户永久文件",
      empty: "暂无永久文件。",
    };
  }
  if (normalized.startsWith("temp/")) {
    return {
      key: "session",
      title: "当前会话临时文件",
      empty: "暂无会话临时文件。",
    };
  }
  return null;
}

function clampFileSectionRatio(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return 0.44;
  }
  return Math.min(FILE_SECTION_RATIO_MAX, Math.max(FILE_SECTION_RATIO_MIN, number));
}

function applySessionFileSectionRatio(value = state.sessionFileSectionRatio, { persist = false } = {}) {
  state.sessionFileSectionRatio = clampFileSectionRatio(value);
  if (fileTreeEl) {
    fileTreeEl.style.setProperty("--session-file-section-ratio", String(state.sessionFileSectionRatio));
    fileTreeEl.style.setProperty("--session-file-section-height", `${Math.round(state.sessionFileSectionRatio * 1000) / 10}%`);
  }
  if (persist) {
    localStorage.setItem(FILE_SECTION_RATIO_STORAGE_KEY, String(state.sessionFileSectionRatio));
  }
}

function restoreSessionFileSectionRatio() {
  const raw = localStorage.getItem(FILE_SECTION_RATIO_STORAGE_KEY);
  applySessionFileSectionRatio(raw ? Number.parseFloat(raw) : state.sessionFileSectionRatio);
}

function stopFileSectionResize() {
  const drag = state.fileSectionResizeDrag;
  if (!drag) {
    return;
  }
  document.body.classList.remove("is-resizing");
  drag.handle.classList.remove("is-dragging");
  applySessionFileSectionRatio(state.sessionFileSectionRatio, { persist: true });
  window.removeEventListener("mousemove", handleFileSectionResize);
  window.removeEventListener("mouseup", stopFileSectionResize);
  state.fileSectionResizeDrag = null;
}

function handleFileSectionResize(event) {
  const drag = state.fileSectionResizeDrag;
  if (!drag) {
    return;
  }
  const deltaY = event.clientY - drag.startY;
  const nextRatio = drag.startRatio - (deltaY / Math.max(1, drag.height));
  applySessionFileSectionRatio(nextRatio);
}

function startFileSectionResize(handle, event) {
  if (isMobileLayout() || !fileTreeEl) {
    return;
  }
  event.preventDefault();
  const rect = fileTreeEl.getBoundingClientRect();
  state.fileSectionResizeDrag = {
    handle,
    startY: event.clientY,
    startRatio: state.sessionFileSectionRatio,
    height: Math.max(1, rect.height),
  };
  document.body.classList.add("is-resizing");
  handle.classList.add("is-dragging");
  window.addEventListener("mousemove", handleFileSectionResize);
  window.addEventListener("mouseup", stopFileSectionResize);
}

function createFileSectionResizeHandle() {
  const handle = document.createElement("div");
  handle.className = "file-section-resize-handle";
  handle.setAttribute("role", "separator");
  handle.setAttribute("aria-orientation", "horizontal");
  handle.setAttribute("aria-label", "调整当前会话文件区域大小");
  handle.title = "拖动以调整当前会话文件区域大小";
  handle.addEventListener("mousedown", (event) => startFileSectionResize(handle, event));
  return handle;
}

function updateSessionPersistButton(button) {
  const target = selectedTempFileTreeNodeForPersist();
  button.disabled = !target;
  button.title = target
    ? `将 ${basenameFromPath(target.path)} 保存到永久文件`
    : "选择要保存的当前会话文件或文件夹";
  button.setAttribute("aria-label", button.title);
}

function createSessionPersistButton() {
  const persistButton = document.createElement("button");
  persistButton.className = "file-tree-section-action";
  persistButton.type = "button";
  persistButton.dataset.action = "persist-session-temp";
  persistButton.innerHTML = `
    <svg class="file-tree-section-action-icon" aria-hidden="true" viewBox="0 0 24 24">
      <path d="M12 3v12"></path>
      <path d="M8 7l4-4 4 4"></path>
      <path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"></path>
    </svg>
  `;
  updateSessionPersistButton(persistButton);
  persistButton.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const target = selectedTempFileTreeNodeForPersist();
    if (!target) {
      return;
    }
    persistTempFileEntry(target).catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
  return persistButton;
}

function createSessionSectionControls(rootNode) {
  const controls = document.createElement("span");
  controls.className = "file-tree-section-actions";

  const expandButton = document.createElement("button");
  expandButton.className = "file-tree-section-action";
  expandButton.type = "button";
  expandButton.title = "展开所有当前会话文件夹";
  expandButton.setAttribute("aria-label", "展开所有当前会话文件夹");
  expandButton.innerHTML = `
    <svg class="file-tree-section-action-icon" aria-hidden="true" viewBox="0 0 24 24">
      <path d="M4 6h16"></path>
      <path d="M8 10l4 4 4-4"></path>
      <path d="M8 16l4 4 4-4"></path>
    </svg>
  `;
  expandButton.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    expandAllFileTreeFolders(rootNode);
  });

  const collapseButton = document.createElement("button");
  collapseButton.className = "file-tree-section-action";
  collapseButton.type = "button";
  collapseButton.title = "折叠所有当前会话文件夹";
  collapseButton.setAttribute("aria-label", "折叠所有当前会话文件夹");
  collapseButton.innerHTML = `
    <svg class="file-tree-section-action-icon" aria-hidden="true" viewBox="0 0 24 24">
      <path d="M4 6h16"></path>
      <path d="M8 14l4-4 4 4"></path>
      <path d="M8 20l4-4 4 4"></path>
    </svg>
  `;
  collapseButton.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    collapseAllFileTreeFolders(rootNode);
  });

  controls.appendChild(createSessionPersistButton());
  controls.appendChild(expandButton);
  controls.appendChild(collapseButton);
  return controls;
}

function renderFileTreeSection(rootNode) {
  const section = fileSectionForPath(rootNode?.path);
  if (!section) {
    return document.createDocumentFragment();
  }

  const wrapper = document.createElement("section");
  const collapsed = state.collapsedFileSections.has(section.key);
  wrapper.className = `file-tree-section file-tree-section-${section.key} ${collapsed ? "collapsed" : "expanded"}`;
  const childCount = Array.isArray(rootNode.children) ? rootNode.children.length : 0;
  const fileCount = countWorkspaceFiles(rootNode);
  wrapper.innerHTML = `
    <button class="file-tree-section-header" type="button" aria-expanded="${collapsed ? "false" : "true"}">
      <span class="file-tree-section-caret">${collapsed ? "▸" : "▾"}</span>
      <span class="file-tree-section-main">
        <span class="file-tree-section-title">${escapeHtml(section.title)}</span>
        ${section.note ? `<span class="file-tree-section-note">${escapeHtml(section.note)}</span>` : ""}
      </span>
      <span class="file-tree-section-count">${fileCount}</span>
    </button>
  `;
  const header = wrapper.querySelector(".file-tree-section-header");
  if (state.dragTargetPath === rootNode?.path) {
    header.classList.add("drop-target");
  }
  if (rootNode?.path) {
    header.dataset.treePath = rootNode.path;
  }
  const sectionMain = header.querySelector(".file-tree-section-main");
  if (section.key === "session" && sectionMain) {
    sectionMain.appendChild(createSessionSectionControls(rootNode));
  }
  header.addEventListener("click", () => {
    if (state.collapsedFileSections.has(section.key)) {
      state.collapsedFileSections.delete(section.key);
    } else {
      state.collapsedFileSections.add(section.key);
    }
    renderFileTree();
  });
  if (isPermanentPath(rootNode?.path)) {
    attachFileTreeDropTarget(header, rootNode, rootNode.path);
  }

  if (!collapsed) {
    const body = document.createElement("div");
    body.className = "file-tree-section-body";
    if (isPermanentPath(rootNode?.path)) {
      attachFileTreeDropTarget(body, rootNode, rootNode.path);
    }
    if (childCount === 0) {
      body.innerHTML = `<div class="file-section-empty">${escapeHtml(section.empty)}</div>`;
    } else {
      for (const child of rootNode.children || []) {
        body.appendChild(renderTreeNode(child, 0));
      }
    }
    wrapper.appendChild(body);
  }
  return wrapper;
}

function renderFileTree() {
  renderFileToolbar();
  applySessionFileSectionRatio();
  fileTreeEl.innerHTML = "";
  if (!state.workspaceTree || !Array.isArray(state.workspaceTree.children) || state.workspaceTree.children.length === 0) {
    fileTreeEl.innerHTML = '<div class="empty-side-state">暂无工作区文件。</div>';
    return;
  }
  const sectionRoots = state.workspaceTree.children.filter((child) => fileSectionForPath(child.path));
  const roots = sectionRoots.length > 0 ? sectionRoots : state.workspaceTree.children;
  roots.forEach((child, index) => {
    const sectionInfo = fileSectionForPath(child.path);
    if (
      sectionInfo?.key === "session"
      && index > 0
      && !state.collapsedFileSections.has("permanent")
      && !state.collapsedFileSections.has("session")
    ) {
      fileTreeEl.appendChild(createFileSectionResizeHandle());
    }
    const section = renderFileTreeSection(child);
    if (section.childNodes.length > 0 || section.nodeType === Node.ELEMENT_NODE) {
      fileTreeEl.appendChild(section);
    } else {
      fileTreeEl.appendChild(renderTreeNode(child, 0));
    }
  });
}

function renderSkillsList() {
  skillsListEl.innerHTML = "";
  if (!Array.isArray(state.skills) || state.skills.length === 0) {
    skillsListEl.innerHTML = '<div class="empty-side-state">暂无可用的工作区技能。</div>';
    return;
  }
  const uiState = currentUiState();
  for (const skill of state.skills) {
    const card = document.createElement("article");
    card.className = "skill-card";
    if (uiState.selected_skill === skill.name && uiState.center_view === "skill") {
      card.classList.add("selected");
    }
    const isPending = state.pendingMentions.skill === skill.name;
    card.innerHTML = `
      <button class="skill-card-main" type="button">
        <span class="skill-card-head">
          <span class="skill-name">${escapeHtml(skill.name)}</span>
          ${isPending ? '<span class="skill-badge active">本轮</span>' : ""}
          ${skill.activatable ? '<span class="skill-badge">模式</span>' : ""}
        </span>
        <span class="skill-summary">${escapeHtml(skill.summary || skill.name)}</span>
      </button>
      ${skill.activatable ? '<button class="skill-activate-button" type="button">加入本轮</button>' : ""}
    `;
    card.querySelector(".skill-card-main").addEventListener("click", () => {
      openSkill(skill.name).catch((error) => {
        centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
      });
    });
    const activateButton = card.querySelector(".skill-activate-button");
    if (activateButton) {
      activateButton.addEventListener("click", () => {
        activateSkill(skill.name).catch((error) => {
          centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
        });
      });
    }
    skillsListEl.appendChild(card);
  }
}
