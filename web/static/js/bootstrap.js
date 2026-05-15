filesTabButtonEl.addEventListener("click", () => setActiveTab("files"));
skillsTabButtonEl.addEventListener("click", () => setActiveTab("skills"));
if (sessionHistoryButtonEl) {
  sessionHistoryButtonEl.addEventListener("click", (event) => {
    event.stopPropagation();
    state.sessionHistoryOpen = !state.sessionHistoryOpen;
    renderSessionHistory();
    if (state.sessionHistoryOpen) {
      refreshSessionsListOnly()
        .then(renderWorkbench)
        .catch((error) => {
          sessionHistoryPopoverEl.innerHTML = `<div class="session-history-empty">${escapeHtml(error.message)}</div>`;
        });
    }
  });
}
if (sessionHistoryPopoverEl) {
  sessionHistoryPopoverEl.addEventListener("click", (event) => {
    const deleteButton = event.target.closest(".session-history-delete");
    if (deleteButton) {
      event.preventDefault();
      event.stopPropagation();
      deleteSession(deleteButton.dataset.sessionId).catch((error) => {
        sessionHistoryPopoverEl.innerHTML = `<div class="session-history-empty">${escapeHtml(error.message)}</div>`;
      });
      return;
    }
    const item = event.target.closest(".session-history-item");
    if (!item) {
      return;
    }
    activateSession(item.dataset.sessionId).catch((error) => {
      sessionHistoryPopoverEl.innerHTML = `<div class="session-history-empty">${escapeHtml(error.message)}</div>`;
    });
  });
}
if (toggleNavPanelButtonEl) {
  toggleNavPanelButtonEl.addEventListener("click", () => {
    setPanelHidden("nav", !state.navPanelHidden);
  });
}
if (toggleCenterPanelButtonEl) {
  toggleCenterPanelButtonEl.addEventListener("click", () => {
    setPanelHidden("center", !state.centerPanelHidden);
  });
}
newSessionButtonEl.addEventListener("click", () => {
  createSession().catch((error) => {
    centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
  });
});
if (addFileContextButtonEl) {
  addFileContextButtonEl.addEventListener("click", () => {
    const doc = state.currentDocument;
    if (!doc || doc.type !== "file") {
      return;
    }
    togglePendingFile(doc.file.path);
  });
}
markdownToggleButtonEl.addEventListener("click", () => {
  syncMarkdownEditorDraft();
  state.markdownPreview = !state.markdownPreview;
  const tab = activeFileTab();
  if (tab) {
    tab.draft = state.fileDraft;
    tab.markdownPreview = state.markdownPreview;
  }
  renderWorkbench();
});
saveFileButtonEl.addEventListener("click", () => {
  saveCurrentFile().catch((error) => {
    centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
  });
});
if (fileNewFileButtonEl) {
  fileNewFileButtonEl.addEventListener("click", () => {
    createFileEntry("file").catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
}
if (fileNewFolderButtonEl) {
  fileNewFolderButtonEl.addEventListener("click", () => {
    createFileEntry("directory").catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
}
if (fileUploadButtonEl && fileUploadInputEl) {
  fileUploadButtonEl.addEventListener("click", () => {
    state.uploadTargetPath = pasteTargetPathFromSelection();
    fileUploadInputEl.value = "";
    fileUploadInputEl.click();
  });
  fileUploadInputEl.addEventListener("change", () => {
    const targetPath = state.uploadTargetPath || pasteTargetPathFromSelection();
    state.uploadTargetPath = "";
    uploadFilesToTarget(Array.from(fileUploadInputEl.files || []), targetPath).catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
}
if (fileDownloadButtonEl) {
  fileDownloadButtonEl.addEventListener("click", () => downloadFileEntry());
}
if (filePersistButtonEl) {
  filePersistButtonEl.addEventListener("click", () => {
    persistTempFileEntry().catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
}
if (fileCopyButtonEl) {
  fileCopyButtonEl.addEventListener("click", () => {
    if (!state.selectedFileTreeNode) {
      return;
    }
    copyFileTreeNode(state.selectedFileTreeNode);
    renderWorkbench();
  });
}
if (filePasteButtonEl) {
  filePasteButtonEl.addEventListener("click", () => {
    pasteCopiedFile().catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
}
if (fileRenameButtonEl) {
  fileRenameButtonEl.addEventListener("click", () => {
    if (!state.selectedFileTreeNode) {
      return;
    }
    renameFileEntry(state.selectedFileTreeNode).catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
}
if (fileDeleteButtonEl) {
  fileDeleteButtonEl.addEventListener("click", () => {
    if (!state.selectedFileTreeNode) {
      return;
    }
    deleteFileEntry(state.selectedFileTreeNode).catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
}
if (fileRefreshButtonEl) {
  fileRefreshButtonEl.addEventListener("click", async () => {
    try {
      await refreshWorkspaceTree();
    } catch (error) {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    }
  });
}
if (persistFileButtonEl) {
  persistFileButtonEl.addEventListener("click", () => {
    const doc = state.currentDocument;
    if (!doc || doc.type !== "file") {
      return;
    }
    persistTempFileEntry({ path: doc.file.path, kind: "file" }).catch((error) => {
      centerViewEl.insertAdjacentHTML("afterbegin", `<div class="load-error">${escapeHtml(error.message)}</div>`);
    });
  });
}
showLeftButtonEl.addEventListener("click", () => setMobilePanel("left"));
showChatButtonEl.addEventListener("click", () => setMobilePanel("right"));
showCenterButtonEl.addEventListener("click", () => setMobilePanel("center"));
addSkillButtonEl.addEventListener("click", (event) => {
  event.stopPropagation();
  showSkillPicker();
});
addSourcesButtonEl.addEventListener("click", (event) => {
  event.stopPropagation();
  showSourcePicker();
});
agentEffortButtonEl.addEventListener("click", (event) => {
  event.stopPropagation();
  showEffortPicker();
});
dividerLeftEl.addEventListener("mousedown", (event) => startDividerDrag("left", dividerLeftEl, event));
dividerRightEl.addEventListener("mousedown", (event) => startDividerDrag("right", dividerRightEl, event));

messageInputEl.addEventListener("compositionstart", () => {
  messageInputComposing = true;
});
messageInputEl.addEventListener("compositionend", () => {
  messageInputComposing = false;
});
messageInputEl.addEventListener("keydown", handleMessageInputKeydown);
messageInputEl.addEventListener("input", () => {
  resizeMessageInput();
  updateMentionMenuFromInput();
});
document.addEventListener("keydown", handleDocumentKeydown);
document.addEventListener("click", (event) => {
  if (!event.target.closest(".file-context-menu")) {
    hideFileContextMenu();
  }
  if (!event.target.closest(".file-tab-context-menu")) {
    hideFileTabContextMenu();
  }
  if (!event.target.closest(".mention-menu") && !event.target.closest(".composer-tool-button") && event.target !== messageInputEl) {
    closeMentionMenu();
  }
  if (state.sessionHistoryOpen && !event.target.closest(".session-history-container")) {
    state.sessionHistoryOpen = false;
    renderSessionHistory();
  }
});
document.addEventListener("contextmenu", (event) => {
  if (!event.target.closest(".tree-row")) {
    hideFileContextMenu();
  }
  if (!event.target.closest(".center-file-tab")) {
    hideFileTabContextMenu();
  }
});

composerEl.addEventListener("submit", (event) => {
  sendMessage(event).catch((error) => {
    renderWorkbench();
    messageInputEl.setCustomValidity(error.message);
    messageInputEl.reportValidity();
    messageInputEl.setCustomValidity("");
  });
});

window.addEventListener("beforeunload", (event) => {
  if (!isDocumentDirty()) {
    return;
  }
  event.preventDefault();
  event.returnValue = "";
});

window.addEventListener("resize", () => {
  if (!isMobileLayout()) {
    setMobilePanel("center");
  }
  stopFileSectionResize();
  applyPanelVisibility({ persist: false });
});
window.addEventListener("blur", () => {
  stopDividerDrag();
  stopFileSectionResize();
});

initializeUserId();
resizeMessageInput();
restoreSessionFileSectionRatio();
restorePanelVisibility();

loadSessions().catch((error) => {
  centerTitleEl.textContent = "加载失败";
  centerSubtitleEl.textContent = "工作台无法初始化。";
  centerViewEl.innerHTML = `<div class="load-error">${escapeHtml(error.message)}</div>`;
});
