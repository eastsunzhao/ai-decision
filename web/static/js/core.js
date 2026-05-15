const ACTIVATABLE_SKILLS = new Set(["simple", "deep-research", "industry-overview"]);
const PREFERRED_DATA_SOURCES = new Set(["web", "mid_platform", "forum", "news"]);
const ITERATION_BUDGETS = new Set(["low", "medium", "high", "extra_high"]);

const state = {
  sessions: [],
  histories: new Map(),
  workspaceTree: null,
  skills: [],
  currentSessionId: null,
  activeJobs: new Map(),
  expandedTraceAgents: new Set(),
  recentTraceAgents: new Map(),
  expandedDirs: new Set([""]),
  fileTreeExpansionInitialized: false,
  collapsedFileSections: new Set(),
  currentDocument: null,
  openFileTabs: [],
  activeFilePath: "",
  fileDraft: "",
  selectedFileTreeNode: null,
  fileClipboard: null,
  draggingFileTreeNode: null,
  dragTargetPath: "",
  dragTargetParentPath: "",
  uploadTargetPath: "",
  workspaceRefreshTimer: null,
  workspaceRefreshInFlight: false,
  workspaceRefreshQueued: false,
  fileSectionResizeDrag: null,
  sessionFileSectionRatio: 0.44,
  userId: "10001",
  markdownPreview: false,
  renderedCenterSignature: "",
  sessionHistoryOpen: false,
  sessionCreateInFlight: false,
  navPanelHidden: false,
  centerPanelHidden: false,
  pendingMentions: {
    skill: "",
    sources: [],
    iterationBudget: "medium",
    files: [],
  },
  mentionMenu: {
    open: false,
    kind: "",
    query: "",
    start: 0,
    end: 0,
    selectedIndex: 0,
    items: [],
  },
};

const fileTreeEl = document.getElementById("file-tree");
const skillsListEl = document.getElementById("skills-list");
const filesTabButtonEl = document.getElementById("files-tab-button");
const skillsTabButtonEl = document.getElementById("skills-tab-button");
const filesPanelEl = document.getElementById("files-panel");
const skillsPanelEl = document.getElementById("skills-panel");
const fileNewFileButtonEl = document.getElementById("file-new-file-button");
const fileNewFolderButtonEl = document.getElementById("file-new-folder-button");
const fileUploadButtonEl = document.getElementById("file-upload-button");
const fileDownloadButtonEl = document.getElementById("file-download-button");
const filePersistButtonEl = document.getElementById("file-persist-button");
const fileCopyButtonEl = document.getElementById("file-copy-button");
const filePasteButtonEl = document.getElementById("file-paste-button");
const fileRenameButtonEl = document.getElementById("file-rename-button");
const fileDeleteButtonEl = document.getElementById("file-delete-button");
const fileRefreshButtonEl = document.getElementById("file-refresh-button");
const fileUploadInputEl = document.getElementById("file-upload-input");
const workspaceRootLabelEl = document.getElementById("workspace-root-label");
const centerTabsEl = document.getElementById("center-tabs");
const centerTitleEl = document.getElementById("center-title");
const centerSubtitleEl = document.getElementById("center-subtitle");
const centerViewEl = document.getElementById("center-view");
const addFileContextButtonEl = document.getElementById("add-file-context-button");
const markdownToggleButtonEl = document.getElementById("markdown-toggle-button");
const persistFileButtonEl = document.getElementById("persist-file-button");
const saveFileButtonEl = document.getElementById("save-file-button");
const chatTitleEl = document.getElementById("chat-title");
const chatSubtitleEl = document.getElementById("chat-subtitle");
const agentTraceControlsEl = document.getElementById("agent-trace-controls");
const sessionStatusEl = document.getElementById("session-status");
const toggleNavPanelButtonEl = document.getElementById("toggle-nav-panel-button");
const toggleCenterPanelButtonEl = document.getElementById("toggle-center-panel-button");
const sessionHistoryButtonEl = document.getElementById("session-history-button");
const sessionHistoryPopoverEl = document.getElementById("session-history-popover");
const messagesEl = document.getElementById("messages");
const contextChipsEl = document.getElementById("context-chips");
const pendingMentionsEl = document.getElementById("pending-mentions");
const addSkillButtonEl = document.getElementById("add-skill-button");
const addSourcesButtonEl = document.getElementById("add-sources-button");
const agentEffortButtonEl = document.getElementById("agent-effort-button");
const mentionMenuEl = document.getElementById("mention-menu");
const composerEl = document.getElementById("composer");
const messageInputEl = document.getElementById("message-input");
const sendButtonEl = document.getElementById("send-button");
const sendButtonTextEl = document.getElementById("send-button-text");
const newSessionButtonEl = document.getElementById("new-session-button");
const contextStatusBarEl = document.getElementById("context-status-bar");
const contextStatusFillEl = document.getElementById("context-status-fill");
const contextStatusTextEl = document.getElementById("context-status-text");
const templateEl = document.getElementById("message-template");
const showLeftButtonEl = document.getElementById("show-left-button");
const showChatButtonEl = document.getElementById("show-chat-button");
const showCenterButtonEl = document.getElementById("show-center-button");
const appShellEl = document.getElementById("app-shell");
const dividerLeftEl = document.getElementById("divider-left");
const dividerRightEl = document.getElementById("divider-right");

let messageInputComposing = false;
let desktopLayout = null;
let activeDividerDrag = null;
let markdownEditor = null;
let markdownEditorReady = false;
let markdownEditorTouched = false;

const TOOL_CALL_TRACE_TYPES = new Set(["tool_call_start", "tool_call_output", "tool_call_end"]);
const TRACE_UPDATE_HIGHLIGHT_MS = 1800;
const LIVE_TRACE_TYPES = [
  "tool_hint",
  "progress",
  "tool_result",
  "tool_call_start",
  "tool_call_output",
  "tool_call_end",
  "retry_wait",
  "subagent_start",
  "subagent_progress",
  "subagent_tool_start",
  "subagent_tool_end",
  "subagent_done",
  "subagent_caveat",
  "subagent_error",
  "subagent_barrier",
];
const WORKSPACE_REFRESH_TOOL_NAMES = new Set(["write_file", "edit_file"]);
const LAYOUT_STORAGE_KEY = "nanobot-workbench-layout-v6";
const PANEL_VISIBILITY_STORAGE_KEY = "nanobot-workbench-panel-visibility-v1";
const LEGACY_CENTER_PANEL_HIDDEN_STORAGE_KEY = "nanobot-workbench-center-panel-hidden-v1";
const FILE_SECTION_RATIO_STORAGE_KEY = "nanobot-workbench-session-file-section-ratio-v1";
const NAV_PANEL_MIN_WIDTH = 260;
const FILE_SECTION_RATIO_MIN = 0.2;
const FILE_SECTION_RATIO_MAX = 0.8;

function isNearScrollBottom(element, threshold = 48) {
  if (!element) {
    return true;
  }
  return element.scrollHeight - element.clientHeight - element.scrollTop <= threshold;
}

function restoreScrollPosition(element, snapshot, { stickToBottom = false } = {}) {
  if (!element || !snapshot) {
    return;
  }
  const apply = () => {
    if (stickToBottom && snapshot.atBottom) {
      element.scrollTop = element.scrollHeight;
      return;
    }
    element.scrollTop = Math.min(snapshot.top, Math.max(0, element.scrollHeight - element.clientHeight));
    element.scrollLeft = snapshot.left || 0;
  };
  apply();
  if (typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(apply);
  }
}

function captureScrollPosition(element) {
  if (!element) {
    return null;
  }
  return {
    top: element.scrollTop,
    left: element.scrollLeft,
    atBottom: isNearScrollBottom(element),
  };
}

function isScrollableElement(element) {
  if (!element) {
    return false;
  }
  return element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1;
}

function captureScrollSnapshot(root) {
  if (!root) {
    return [];
  }
  const elements = [root, ...Array.from(root.querySelectorAll("*")).filter(isScrollableElement)];
  return elements.map((element) => captureScrollPosition(element));
}

function restoreScrollSnapshot(root, snapshot) {
  if (!root || !Array.isArray(snapshot) || snapshot.length === 0) {
    return;
  }
  const elements = [root, ...Array.from(root.querySelectorAll("*")).filter(isScrollableElement)];
  elements.forEach((element, index) => {
    restoreScrollPosition(element, snapshot[index]);
  });
}

function normalizeComposerPrefsClient(prefs) {
  const defaults = prefs && typeof prefs === "object" && prefs.defaults && typeof prefs.defaults === "object"
    ? prefs.defaults
    : {};
  const activeSkill = typeof defaults.active_skill === "string" && ACTIVATABLE_SKILLS.has(defaults.active_skill)
    ? defaults.active_skill
    : "";
  const preferredDataSource = typeof defaults.preferred_data_source === "string"
    && PREFERRED_DATA_SOURCES.has(defaults.preferred_data_source)
    ? defaults.preferred_data_source
    : "web";
  const iterationBudget = typeof defaults.iteration_budget === "string"
    && ITERATION_BUDGETS.has(defaults.iteration_budget.replaceAll("-", "_"))
    ? defaults.iteration_budget.replaceAll("-", "_")
    : "medium";
  return {
    defaults: {
      active_skill: activeSkill,
      preferred_data_source: preferredDataSource,
      iteration_budget: iterationBudget,
    },
  };
}

function defaultActiveSkillFromPrefs(prefs) {
  const normalized = normalizeComposerPrefsClient(prefs);
  return normalized.defaults.active_skill || "";
}

function normalizeRelativePath(value) {
  const raw = String(value || "").trim().replaceAll("\\", "/");
  if (!raw) {
    return "";
  }
  const parts = raw.split("/").filter(Boolean);
  if (parts.some((part) => part === "." || part === "..")) {
    return "";
  }
  return parts.join("/");
}

function normalizeUiStateClient(uiState, composerPrefs) {
  const input = uiState && typeof uiState === "object" ? uiState : {};
  const defaultSkill = defaultActiveSkillFromPrefs(composerPrefs);
  const leftTab = input.left_tab === "skills" ? "skills" : "files";
  const activePath = normalizeRelativePath(input.active_path || "");
  const activeSkill = ACTIVATABLE_SKILLS.has(input.active_skill)
    ? input.active_skill
    : defaultSkill;
  const selectedSkill = String(input.selected_skill || activeSkill || defaultSkill).trim() || defaultSkill;
  let centerView = String(input.center_view || "empty").trim().toLowerCase();
  if (!["file", "skill", "empty"].includes(centerView)) {
    centerView = "empty";
  }
  if (centerView === "file" && !activePath) {
    centerView = "empty";
  }
  if (centerView === "skill" && !selectedSkill) {
    centerView = "empty";
  }
  return {
    left_tab: leftTab,
    active_path: activePath,
    active_skill: activeSkill,
    selected_skill: selectedSkill,
    center_view: centerView,
  };
}

function currentSessionStorageKey() {
  return `nanobot-workbench-current-session-${state.userId || "10001"}`;
}

function currentSession() {
  return state.sessions.find((session) => session.id === state.currentSessionId) || null;
}

function currentUiState() {
  const session = currentSession();
  return session ? normalizeUiStateClient(session.ui_state, session.composer_prefs) : normalizeUiStateClient({}, {});
}

function isMobileLayout() {
  return window.matchMedia("(max-width: 900px)").matches;
}
