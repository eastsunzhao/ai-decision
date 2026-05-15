function renderMarkdown(markdown) {
  const markedApi = window.marked;
  const purifyApi = window.DOMPurify;
  if (markedApi && typeof markedApi.parse === "function") {
    const rendered = markedApi.parse(markdown || "", { breaks: true, gfm: true });
    return purifyApi ? purifyApi.sanitize(rendered) : rendered;
  }
  return escapeHtml(markdown || "");
}

function renderEmptyCenter() {
  destroyMarkdownEditor();
  renderFileTabs();
  const uiState = currentUiState();
  centerTitleEl.textContent = uiState.left_tab === "skills" ? "选择技能" : "选择文件或技能";
  centerSubtitleEl.textContent = uiState.left_tab === "skills"
    ? "技能详情 · 工作流和模式启用"
    : "打开工作区文件或查看技能。";
  addFileContextButtonEl.classList.add("hidden");
  markdownToggleButtonEl.classList.add("hidden");
  persistFileButtonEl.classList.add("hidden");
  saveFileButtonEl.classList.add("hidden");
  centerViewEl.innerHTML = `
    <div class="empty-state">
      <p>点击左侧任意文件，即可在此区域显示或编辑。</p>
    </div>
  `;
}

function renderFileTabs() {
  centerTabsEl.innerHTML = "";
  if (state.openFileTabs.length === 0) {
    centerTabsEl.classList.add("is-empty");
    centerTabsEl.innerHTML = '<span class="center-tab-placeholder">暂无打开的文件</span>';
    return;
  }
  centerTabsEl.classList.remove("is-empty");
  for (const tab of state.openFileTabs) {
    const dirty = isFileTabDirty(tab);
    const isActive = state.currentDocument?.type === "file" && state.activeFilePath === tab.path;
    const tabEl = document.createElement("button");
    tabEl.type = "button";
    tabEl.className = `center-file-tab${isActive ? " active" : ""}${dirty ? " dirty" : ""}`;
    tabEl.setAttribute("role", "tab");
    tabEl.setAttribute("aria-selected", isActive ? "true" : "false");
    tabEl.title = tab.path;
    tabEl.innerHTML = `
      <span class="center-file-tab-icon">${isMarkdownPath(tab.path) ? "M" : "T"}</span>
      <span class="center-file-tab-name">${escapeHtml(basenameFromPath(tab.path))}</span>
      <span class="center-file-tab-dirty" aria-hidden="true">${dirty ? "•" : ""}</span>
      <span class="center-file-tab-close" aria-label="关闭">×</span>
    `;
    tabEl.addEventListener("click", (event) => {
      if (event.target.closest(".center-file-tab-close")) {
        event.stopPropagation();
        closeFileTab(tab.path).catch((error) => {
          centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
        });
        return;
      }
      syncActiveFileTabDraft();
      activateFileTab(tab.path);
      updateCurrentSessionLocally({
        ui_state: {
          ...currentUiState(),
          left_tab: "files",
          active_path: tab.path,
          center_view: "file",
        },
      });
      patchCurrentSession({
        ui_state: {
          ...currentUiState(),
          left_tab: "files",
          active_path: tab.path,
          center_view: "file",
        },
      }).catch(() => {});
      renderWorkbench();
    });
    tabEl.addEventListener("contextmenu", (event) => showFileTabContextMenu(event, tab));
    centerTabsEl.appendChild(tabEl);
  }
}

function renderCenterHeader() {
  renderFileTabs();
  const doc = state.currentDocument;
  if (!doc) {
    renderEmptyCenter();
    return;
  }

  if (doc.type === "file") {
    const file = doc.file;
    const dirty = isDocumentDirty();
    centerTitleEl.textContent = "";
    centerSubtitleEl.innerHTML = file.editable
      ? `
        <span>${escapeHtml(file.path)}</span>
        <span class="center-meta-separator">·</span>
        <span>${escapeHtml(displayFileType(file.path))}</span>
        <span class="center-meta-separator">·</span>
        <span>${escapeHtml(String(file.size))} 字节</span>
        <span class="center-meta-separator">·</span>
        <span class="file-save-state ${dirty ? "unsaved" : "saved"}">${dirty ? "有未保存更改" : "已保存"}</span>
      `
      : `
        <span>${escapeHtml(file.path)}</span>
        <span class="center-meta-separator">·</span>
        <span>${escapeHtml(file.kind === "binary" ? "二进制预览不可用" : "只读预览")}</span>
        <span class="center-meta-separator">·</span>
        <span>${escapeHtml(String(file.size))} 字节</span>
    `;
    const showMarkdownToggle = file.kind === "text" && isMarkdownPath(file.path) && !file.too_large;
    const sourceMode = !state.markdownPreview;
    const fileInContext = state.pendingMentions.files.includes(file.path);
    addFileContextButtonEl.classList.remove("hidden");
    addFileContextButtonEl.classList.toggle("pressed", fileInContext);
    addFileContextButtonEl.innerHTML = `
      <svg class="context-file-icon" aria-hidden="true" viewBox="0 0 24 24">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"></path>
        <path d="M14 2v6h6"></path>
        <path d="M9 15h6"></path>
        <path d="M12 12v6"></path>
      </svg>
    `;
    addFileContextButtonEl.title = fileInContext ? "已加入上下文，点击移除" : "加入上下文";
    addFileContextButtonEl.setAttribute("aria-label", addFileContextButtonEl.title);
    addFileContextButtonEl.setAttribute("aria-pressed", fileInContext ? "true" : "false");
    markdownToggleButtonEl.classList.toggle("hidden", !showMarkdownToggle);
    markdownToggleButtonEl.classList.toggle("pressed", showMarkdownToggle && sourceMode);
    markdownToggleButtonEl.innerHTML = "&lt;/&gt;";
    markdownToggleButtonEl.title = "源码";
    markdownToggleButtonEl.setAttribute("aria-label", "源码");
    markdownToggleButtonEl.setAttribute("aria-pressed", showMarkdownToggle && sourceMode ? "true" : "false");
    persistFileButtonEl.classList.add("hidden");
    persistFileButtonEl.disabled = true;
    persistFileButtonEl.innerHTML = `
      <svg class="save-icon" aria-hidden="true" viewBox="0 0 24 24">
        <path d="M12 3v12"></path>
        <path d="M8 7l4-4 4 4"></path>
        <path d="M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"></path>
      </svg>
    `;
    persistFileButtonEl.title = "保存到永久文件";
    persistFileButtonEl.setAttribute("aria-label", persistFileButtonEl.title);
    saveFileButtonEl.classList.toggle("hidden", !file.editable);
    saveFileButtonEl.disabled = !dirty;
    saveFileButtonEl.classList.toggle("dirty", dirty);
    saveFileButtonEl.classList.toggle("clean", !dirty);
    saveFileButtonEl.innerHTML = `
      <svg class="save-icon" aria-hidden="true" viewBox="0 0 24 24">
        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"></path>
        <path d="M17 21v-8H7v8"></path>
        <path d="M7 3v5h8"></path>
      </svg>
    `;
    saveFileButtonEl.title = dirty ? "保存文件" : "已保存";
    saveFileButtonEl.setAttribute("aria-label", saveFileButtonEl.title);
    return;
  }

  centerTitleEl.textContent = "";
  centerSubtitleEl.innerHTML = `
    <span>${escapeHtml(doc.skill.path)}</span>
    <span class="center-meta-separator">·</span>
    <span>技能</span>
    <span class="center-meta-separator">·</span>
    <span>${escapeHtml(doc.skill.summary || doc.skill.name)}</span>
  `;
  addFileContextButtonEl.classList.add("hidden");
  markdownToggleButtonEl.classList.add("hidden");
  persistFileButtonEl.classList.add("hidden");
  saveFileButtonEl.classList.add("hidden");
}

function renderFileCenter(file) {
  destroyMarkdownEditor();
  if (file.too_large) {
    centerViewEl.innerHTML = `
      <div class="info-card">
        <h3>文件过大，无法内联编辑</h3>
        <p>${escapeHtml(file.path)} 超出了内联编辑器限制。请使用工作区工具，或打开更小的文件。</p>
      </div>
    `;
    return;
  }

  if (file.kind !== "text") {
    centerViewEl.innerHTML = `
      <div class="info-card">
        <h3>二进制预览不可用</h3>
        <p>${escapeHtml(file.path)} 不是 UTF-8 文本文档，因此工作台会以只读方式打开。</p>
      </div>
    `;
    return;
  }

  if (isMarkdownPath(file.path) && file.editable) {
    const Editor = window.toastui?.Editor;
    if (!Editor) {
      centerViewEl.innerHTML = `
        <div class="info-card">
          <h3>Markdown 编辑器不可用</h3>
          <p>所见即所得编辑器加载失败。编辑器库可用时，你仍可以使用源码模式。</p>
        </div>
      `;
      return;
    }
    const mount = document.createElement("div");
    mount.id = "center-markdown-editor";
    mount.className = `document-toast-editor${state.markdownPreview ? "" : " source-only"}`;
    centerViewEl.innerHTML = "";
    centerViewEl.appendChild(mount);
    markdownEditorReady = false;
    markdownEditorTouched = false;
    markdownEditor = new Editor({
      el: mount,
      height: "100%",
      initialEditType: state.markdownPreview ? "wysiwyg" : "markdown",
      initialValue: state.fileDraft || file.content || "",
      previewStyle: "vertical",
      usageStatistics: false,
      hideModeSwitch: true,
    });
    markdownEditor.on("change", () => {
      if (!markdownEditorReady) {
        return;
      }
      markdownEditorTouched = true;
      state.fileDraft = markdownEditor.getMarkdown();
      const tab = activeFileTab();
      if (tab) {
        tab.draft = state.fileDraft;
      }
      renderCenterHeader();
      renderContextChips();
    });
    window.setTimeout(() => {
      markdownEditorReady = true;
    }, 0);
    return;
  }

  if (!file.editable) {
    const pre = document.createElement("pre");
    pre.className = "document-readonly";
    pre.textContent = file.content || "";
    centerViewEl.innerHTML = "";
    centerViewEl.appendChild(pre);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.id = "center-editor";
  textarea.className = "document-editor";
  textarea.spellcheck = false;
  textarea.value = state.fileDraft;
  textarea.addEventListener("input", (event) => {
    state.fileDraft = event.target.value;
    const tab = activeFileTab();
    if (tab) {
      tab.draft = state.fileDraft;
    }
    renderCenterHeader();
    renderContextChips();
  });
  centerViewEl.innerHTML = "";
  centerViewEl.appendChild(textarea);
}

function renderSkillCenter(doc) {
  destroyMarkdownEditor();
  const Editor = window.toastui?.Editor;
  if (!Editor || typeof Editor.factory !== "function") {
    centerViewEl.innerHTML = `
      <div class="skill-detail-card">
        <article class="document-preview markdown-preview">${renderMarkdown(doc.body || "")}</article>
      </div>
    `;
    return;
  }
  centerViewEl.innerHTML = `
    <div class="skill-editor-shell">
      <div class="skill-editor-body">
        <div id="center-skill-markdown-editor" class="document-toast-editor document-toast-viewer"></div>
      </div>
    </div>
  `;
  const mount = document.getElementById("center-skill-markdown-editor");
  markdownEditorReady = false;
  markdownEditorTouched = false;
  markdownEditor = Editor.factory({
    el: mount,
    viewer: true,
    initialValue: doc.body || "",
    usageStatistics: false,
  });
  window.setTimeout(() => {
    markdownEditorReady = true;
  }, 0);
}

function centerViewSignature() {
  const sessionId = state.currentSessionId || "";
  const doc = state.currentDocument;
  if (!doc) {
    return `${sessionId}:empty:${currentUiState().left_tab || "files"}`;
  }
  if (doc.type === "file") {
    const file = doc.file || {};
    return [
      sessionId,
      "file",
      file.path || "",
      file.kind || "",
      file.editable ? "editable" : "readonly",
      file.too_large ? "large" : "inline",
      state.markdownPreview ? "preview" : "source",
    ].join(":");
  }
  return [
    sessionId,
    "skill",
    doc.skill?.name || "",
    doc.skill?.path || "",
    String(doc.body || "").length,
  ].join(":");
}

function renderCenterView() {
  const doc = state.currentDocument;
  const signature = centerViewSignature();
  if (state.renderedCenterSignature === signature && centerViewEl.childNodes.length > 0) {
    renderCenterHeader();
    return;
  }
  const scrollSnapshot = captureScrollSnapshot(centerViewEl);
  if (!doc) {
    renderEmptyCenter();
    state.renderedCenterSignature = signature;
    restoreScrollSnapshot(centerViewEl, scrollSnapshot);
    return;
  }
  renderCenterHeader();
  if (doc.type === "file") {
    renderFileCenter(doc.file);
  } else {
    renderSkillCenter(doc);
  }
  state.renderedCenterSignature = signature;
  restoreScrollSnapshot(centerViewEl, scrollSnapshot);
}
