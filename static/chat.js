/* ThreadBear front-end wiring (keeps current HTML/CSS exactly as-is)
   Features:
   - Provider/model loading + model settings panel
   - System prompt dropdown populated from prompts.jsonl
   - Chat history (load/rename/delete) + New Chat
   - Context bar toggle; document upload/list/select/delete; chips in attachments bar
   - Token summary + basic utilization
   - Streaming send + Cancel
   - Message context menu: Summarize / Copy / Delete / Branch
   - Light/Dark theme via System Settings panel
*/

(() => {
  // ===== Utilities =====
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls) => { const n = document.createElement(tag); if (cls) n.className = cls; return n; };
  const text = (t) => document.createTextNode(t);
  const fmt = (n) => (typeof n === 'number' ? n.toLocaleString() : n);
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  const state = {
    config: null,
    providers: [],
    currentProvider: null,
    currentModel: null,
    streaming: false,
    streamSource: null,

    // messages + selection
    messages: [],
    selectedMessageIdx: new Set(),
    selectedSummaryIdx: new Set(),  // Track which summaries are selected (separate from messages)

    // chats
    history: [],
    currentChatFile: null,

    // docs
    docs: [],
    selectedDocIds: new Set(),
    contextBarOpen: false,

    // folders
    folders: [],
    chatFolderMap: {},
    fileFolderMap: {},
    expandedFolders: new Set(),

    // branches (Phase 2)
    branchTree: [],
    expandedBranches: new Set(),

    // agent (Phase 4)
    agentSource: null,
    agentRunning: false,
    currentBranchId: null,

    // prompts
    prompts: [],

    // ui caches
    menus: {
      chat: $('contextMenu'),
      message: $('messageContextMenu')
    },
    ctxMenuTarget: { type: null, index: null, filename: null },

    // settings
    theme: (document.documentElement.getAttribute('data-theme') || 'light')
  };

  // ====== Element map (matching your chat.html exactly) ======
  const E = {
    // Sidebar
    newChatBtn: $('newChatBtn'),
    newFolderBtn: $('newFolderBtn'),
    chatHistory: $('chatHistory'),
    
    // Settings menu
    settingsMenuBtn: $('settingsMenuBtn'),
    settingsDropdown: $('settingsDropdown'),
    openModelSettingsBtn: $('openModelSettingsBtn'),
    openPromptsSettingsBtn: $('openPromptsSettingsBtn'),
    openAppearanceSettingsBtn: $('openAppearanceSettingsBtn'),

    // Header
    providerHeader: $('headerProviderSelect'),
    modelHeader: $('headerModelSelect'),
    systemPromptSelect: $('systemPromptSelect'),
    loadUnloadModelBtn: $('loadUnloadModelBtn'),
    ctxSizeSelect: $('ctxSizeSelect'),

    contextSelectionBtn: $('contextSelectionBtn'),

    chatTitle: $('chatTitle'),

    // Context controls bar (hidden until toggled)
    contextControls: $('contextControls'),
    contextTokenCountTop: $('contextTokenCount'),
    modelMaxTokensLabel: $('modelMaxTokensLabel'),
    selectAllBtn: $('selectAllBtn'),
    selectNoneBtn: $('selectNoneBtn'),
    selectLastBtn: $('selectLastBtn'),
    selectSummariesBtn: $('selectSummariesBtn'),
    summarizeSelectedBtn: $('summarizeSelectedBtn'),
    summarizeAllBtn: $('summarizeAllBtn'),
    addDocumentBtn: $('addDocumentBtn'),
    documentUpload: $('documentUpload'),
    uploadProgressInline: $('uploadProgress'),
    uploadProgressFillInline: $('uploadProgressFill'),
    attachmentListInline: $('attachmentListInline'),
    contextTokenCount2: $('contextTokenCount2'),

    // Upload progress bar (big)
    uploadProgressBar: $('uploadProgressBar'),
    uploadProgressFillLarge: $('uploadProgressFillLarge'),
    uploadProgressPercent: $('uploadProgressPercent'),
    uploadProgressText: $('uploadProgressText'),

    // Attachments chips bar
    documentAttachmentsBar: $('documentAttachmentsBar'),
    attachmentList: $('attachmentList'),

    // Messages
    messages: $('messages'),

    // Input
    userInput: $('userInput'),
    sendBtn: $('sendBtn'),
    cancelBtn: $('cancelButton'),

    // Context overflow warning
    contextOverflowWarning: $('contextOverflowWarning'),
    overflowMessage: $('overflowMessage'),
    overflowSelectContext: $('overflowSelectContext'),
    overflowSummarize: $('overflowSummarize'),
    overflowTrim: $('overflowTrim'),
    overflowSendAnyway: $('overflowSendAnyway'),
    overflowDismiss: $('overflowDismiss'),

    // Branch context menu (Phase 2)
    branchContextMenu: $('branchContextMenu'),
    statusSubMenu: $('statusSubMenu'),
    branchForkMenuItem: $('branchForkMenuItem'),
    branchNewWorkOrderMenuItem: $('branchNewWorkOrderMenuItem'),
    branchStatusMenuItem: $('branchStatusMenuItem'),
    branchMergeMenuItem: $('branchMergeMenuItem'),
    branchRenameMenuItem: $('branchRenameMenuItem'),
    branchArchiveMenuItem: $('branchArchiveMenuItem'),

    // Branch detail panel (Phase 2)
    branchDetailPanel: $('branchDetailPanel'),
    branchDetailTypeIcon: $('branchDetailTypeIcon'),
    branchDetailTypeLabel: $('branchDetailTypeLabel'),
    branchDetailStatus: $('branchDetailStatus'),
    branchDetailStatusBtn: $('branchDetailStatusBtn'),
    branchDetailForkBtn: $('branchDetailForkBtn'),
    branchDetailGoalContainer: $('branchDetailGoalContainer'),
    branchDetailGoal: $('branchDetailGoal'),
    branchDetailEdges: $('branchDetailEdges'),
    branchEdgesList: $('branchEdgesList'),

    // Agent panel (Phase 4)
    agentPanel: $('agentPanel'),
    agentStatusIndicator: $('agentStatusIndicator'),
    agentIteration: $('agentIteration'),
    agentControls: $('agentControls'),
    agentStartBtn: $('agentStartBtn'),
    agentPauseBtn: $('agentPauseBtn'),
    agentResumeBtn: $('agentResumeBtn'),
    agentStopBtn: $('agentStopBtn'),
    agentGoalInput: $('agentGoalInput'),
    agentGoalText: $('agentGoalText'),
    agentConfirmStart: $('agentConfirmStart'),
    agentCancelStart: $('agentCancelStart'),
    agentTodoList: $('agentTodoList'),
    agentPlanList: $('agentPlanList'),
    agentActivityLog: $('agentActivityLog'),

    // Artifact panel (Phase 5)
    artifactPanel: $('artifactPanel'),
    artifactCount: $('artifactCount'),
    artifactProducedList: $('artifactProducedList'),
    artifactIncomingList: $('artifactIncomingList'),
    artifactSendModal: $('artifactSendModal'),
    artifactModalClose: $('artifactModalClose'),
    artifactSendId: $('artifactSendId'),
    artifactTargetBranch: $('artifactTargetBranch'),
    artifactSendCancel: $('artifactSendCancel'),
    artifactSendConfirm: $('artifactSendConfirm'),

    // Model Settings panel (right drawer)
    settingsPanel: $('settingsPanel'),
    closeSettingsBtn: $('closeSettingsBtn'),
    providerSelect: $('providerSelect'),
    modelSettingsSelect: $('modelSettingsSelect'),
    removeModelBtn: $('removeModelBtn'),
    addModelBtn: $('addModelBtn'),
    newModelName: $('newModelName'),
    saveModelBtn: $('saveModelBtn'),
    cancelAddModelBtn: $('cancelAddModelBtn'),
    newModelInputContainer: $('newModelInputContainer'),
    modelSettingsForm: $('modelSettingsForm'),
    contextWindowInput: $('contextWindowInput'),
    nglInput: $('nglInput'),
    nglGroup: $('nglGroup'),
    vramRequiredGroup: $('vramRequiredGroup'),
    vramRequiredInput: $('vramRequiredInput'),
    maxTokensInput: $('maxTokensInput'),
    modelTemperatureRange: $('modelTemperatureRange'),
    modelTemperatureValue: $('modelTemperatureValue'),
    topPRange: $('topPRange'),
    topPValue: $('topPValue'),
    topKRange: $('topKRange'),
    topKValue: $('topKValue'),
    modelSystemPromptTextarea: $('modelSystemPromptTextarea'),
    saveModelSettingsBtn: $('saveModelSettingsBtn'),
    saveModelsBtn: $('saveModelsBtn'),
    resetModelsBtn: $('resetModelsBtn'),
    temperatureRange: $('temperatureRange'),
    temperatureValue: $('temperatureValue'),
    mainCurrentProvider: $('mainCurrentProvider'),
    mainCurrentModel: $('mainCurrentModel'),
    mainModelContextLength: $('mainModelContextLength'),
    mainModelCost: $('mainModelCost'),
    mainContextUtilization: $('mainContextUtilization'),
    mainUtilizationFill: $('mainUtilizationFill'),
    mainUtilizationBreakdown: $('mainUtilizationBreakdown'),
    refreshModelsBtn: $('refreshModelsBtn'),

    // System Settings panel (appearance)
    systemSettingsPanel: $('systemSettingsPanel'),
    closeSystemSettingsBtn: $('closeSystemSettingsBtn'),
    lightThemeBtn: $('lightThemeBtn'),
    darkThemeBtn: $('darkThemeBtn'),
    totalVramInput: $('totalVramInput'),

    // System Prompts panel
    promptsSettingsPanel: $('promptsSettingsPanel'),
    closePromptsSettingsBtn: $('closePromptsSettingsBtn'),
    promptsList: $('promptsList'),
    addPromptBtn: $('addPromptBtn'),
    promptEditor: $('promptEditor'),
    promptId: $('promptId'),
    promptTitle: $('promptTitle'),
    promptBody: $('promptBody'),
    savePromptBtn: $('savePromptBtn'),
    cancelPromptBtn: $('cancelPromptBtn'),
    deletePromptBtn: $('deletePromptBtn'),

    // Browse Models panel
    openBrowseModelsBtn: $('openBrowseModelsBtn'),
    openrouterBrowsePanel: $('openrouterBrowsePanel'),
    closeBrowseModelsBtn: $('closeBrowseModelsBtn'),
    settingsOverlay: $('settingsOverlay'),
    browseProviderSelect: $('browseProviderSelect'),
    browseModelSearch: $('browseModelSearch'),
    browseFreeOnly: $('browseFreeOnly'),
    browseFreeOnlyLabel: $('browseFreeOnlyLabel'),
    browseSortSelect: $('browseSortSelect'),
    modelBrowseList: $('modelBrowseList'),
    browseSelectedCount: $('browseSelectedCount'),
    refreshCatalogBtn: $('refreshCatalogBtn'),

    // Context menus
    chatContextMenu: $('contextMenu'),
    menuLoadChat: $('loadChatMenuItem'),
    menuRenameChat: $('renameChatMenuItem'),
    menuDuplicateChat: $('duplicateChatMenuItem'),
    menuMoveToFolder: $('moveToFolderMenuItem'),
    menuRemoveFromFolder: $('removeFromFolderMenuItem'),
    menuDeleteChat: $('deleteChatMenuItem'),

    folderContextMenu: $('folderContextMenu'),
    menuRenameFolder: $('renameFolderMenuItem'),
    menuAddSubfolder: $('addSubfolderMenuItem'),
    menuFolderContextSettings: $('folderContextSettingsMenuItem'),
    menuDeleteFolder: $('deleteFolderMenuItem'),
    moveToFolderMenu: $('moveToFolderMenu'),

    msgContextMenu: $('messageContextMenu'),
    menuBranchFull: $('branchFullMenuItem'),
    menuBranchSelected: $('branchSelectedMenuItem'),
    menuSummarize: $('summarizeResponseMenuItem'),
    menuCopySelected: $('copySelectedMenuItem'),
    menuCopy: $('copyResponseMenuItem'),
    menuAddToFolderMemory: $('addToFolderMemoryMenuItem'),
    menuSaveAsFolderPrompt: $('saveAsFolderPromptMenuItem'),
    menuDelete: $('deleteResponseMenuItem'),

    // Folder badge
    folderBadge: $('folderBadge'),

    // Folder context settings panel
    folderContextPanel: $('folderContextPanel'),
    folderContextTitle: $('folderContextTitle'),
    closeFolderContextBtn: $('closeFolderContextBtn'),
    savedPromptsList: $('savedPromptsList'),
    memoryNotesList: $('memoryNotesList'),
    memoryNotesCount: $('memoryNotesCount'),
    clearMemoryBtn: $('clearMemoryBtn'),

    inputContextMenu: $('inputContextMenu'),
    inputCut: $('inputCutMenuItem'),
    inputCopy: $('inputCopyMenuItem'),
    inputPaste: $('inputPasteMenuItem'),
    inputSelectAll: $('inputSelectAllMenuItem'),
  };

  // ===== Helpers =====

  function show(elm) { elm.style.display = ''; }
  function hide(elm) { elm.style.display = 'none'; }
  function setProgress(pct) {
    E.uploadProgressFillLarge.style.width = Math.max(0, Math.min(100, pct)) + '%';
    E.uploadProgressPercent.textContent = Math.round(Math.max(0, Math.min(100, pct))) + '%';
  }
  function clearNode(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  // === Token estimate (mirror of backend heuristic: ~4 chars per token) ===
  function estimateTokensJS(text) {
    const s = (text || "");
    return Math.ceil(s.length / 4);
  }

  function renderChatTitle() {
    const chat = state.history.find(c => c.filename === state.currentChatFile);
    if (chat && chat.title) {
      E.chatTitle.textContent = chat.title;
    } else {
      E.chatTitle.textContent = state.currentChatFile ? state.currentChatFile.replace(/\.json$/,'') : 'New Chat';
    }
    // Update folder badge
    const folderId = state.chatFolderMap[state.currentChatFile];
    if (folderId) {
      const folder = findFolderById(folderId);
      if (folder) {
        E.folderBadge.textContent = folder.name;
        E.folderBadge.style.display = '';
      } else {
        E.folderBadge.style.display = 'none';
      }
    } else {
      E.folderBadge.style.display = 'none';
    }
  }

  function findFolderById(id) {
    for (const f of state.folders) {
      if (f.id === id) return f;
      for (const c of (f.children || [])) {
        if (c.id === id) return c;
      }
    }
    return null;
  }

  function modelMatchesLoaded(selectedModel, loadedModel) {
    if (!selectedModel || !loadedModel) return false;
    const byName = loadedModel.name || '';
    const byPath = loadedModel.path || '';
    const byConfigured = loadedModel.configured_name || '';
    const byBasename = byPath.split('/').pop().split('\\').pop();

    // Exact match first (includes configured_name — the name the user selected)
    if (selectedModel === byName || selectedModel === byPath || selectedModel === byBasename || selectedModel === byConfigured) return true;

    // Normalize: lowercase, strip .gguf suffix, collapse separators
    const norm = (s) => s.toLowerCase().replace(/\.gguf$/i, '').replace(/[-_]/g, '');
    const sel = norm(selectedModel);
    const candidates = [byName, byPath, byBasename, byConfigured].map(norm);

    // Normalized exact match
    if (candidates.some(c => c === sel)) return true;

    // Substring: selected model name appears in loaded path/name or vice versa
    if (candidates.some(c => c && (c.includes(sel) || sel.includes(c)))) return true;

    return false;
  }

  function formatVramLabel(vram) {
    if (typeof vram !== 'number' || !isFinite(vram) || vram <= 0) return '';
    const pretty = Number.isInteger(vram) ? String(vram) : vram.toFixed(1).replace(/\.0$/, '');
    return ` (${pretty}GB)`;
  }

  function messageNode(msg, index) {
  const row = el('div', `message ${msg.role}`);
  row.dataset.index = index;

  // ===== Model label for assistant messages (keep as you had) =====
  if (msg.role === 'assistant') {
    const label = el('div', 'model-label');
    let suffix = '';
    if (msg.isSummary) suffix = ' (Summary)';
    else if (msg.summary) suffix = ' (Summary)';   // was "(Summary attached)"
    label.textContent = msg.model ? `${msg.model}${suffix}` : 'Assistant';
    row.appendChild(label);
  }

  // ===== Main content container (flex so checkbox sits left of bubble) =====
  const content = el('div', 'message-content');
  content.style.display = 'flex';
  content.style.alignItems = 'flex-start';

  // ===== Checkbox (only when context bar is open) =====
  if (state.contextBarOpen) {
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'message-checkbox';
    checkbox.checked = state.selectedMessageIdx.has(index);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) state.selectedMessageIdx.add(index);
      else state.selectedMessageIdx.delete(index);
      updateContextTokenSummary();
    });
    content.appendChild(checkbox);
  }

  // ===== Bubble (markdown) =====
  const bubble = el('div', 'message-bubble');
  try {
    bubble.innerHTML = marked.parse(msg.content || '');
  } catch {
    bubble.textContent = msg.content || '';
  }
  content.appendChild(bubble);

  // === Inline preview toggle when a summary is attached ===
  // Removed: summary is always shown inline below the bubble.

  // Append summary under the bubble if present
  if (msg.summary) {
    // Blue chip for the summary model (same style as normal responses)
    const summaryLabel = el('div', 'model-label summary-label');
    const summaryModel = msg.summary_model || state.currentModel || msg.model || '';
    summaryLabel.textContent = summaryModel ? `${summaryModel} (Summary)` : '(Summary)';
    content.appendChild(summaryLabel);

    // If context bar is open, wrap summary in a container with checkbox
    if (state.contextBarOpen) {
      const summaryContainer = el('div', 'summary-checkbox-container');

      // Add checkbox for summary
      const summaryCheckbox = document.createElement('input');
      summaryCheckbox.type = 'checkbox';
      summaryCheckbox.className = 'summary-checkbox';
      summaryCheckbox.checked = state.selectedSummaryIdx.has(index);
      summaryCheckbox.addEventListener('change', (e) => {
        e.stopPropagation(); // Prevent row click from toggling
        if (summaryCheckbox.checked) state.selectedSummaryIdx.add(index);
        else state.selectedSummaryIdx.delete(index);
        updateContextTokenSummary();
      });
      summaryContainer.appendChild(summaryCheckbox);

      const s = el('div', 'message-summary');
      try { s.innerHTML = marked.parse(msg.summary); }
      catch { s.textContent = msg.summary; }
      summaryContainer.appendChild(s);
      content.appendChild(summaryContainer);
    } else {
      const s = el('div', 'message-summary');
      try { s.innerHTML = marked.parse(msg.summary); }
      catch { s.textContent = msg.summary; }
      content.appendChild(s);
    }
  }

  // ===== Time =====
  const time = el('div', 'message-time');
  time.textContent = msg.timestamp || '';
  content.appendChild(time);

  // Attach content to row
  row.appendChild(content);

  // ===== Visual selection outline (removed, we now rely on checkboxes + token counter) =====
  // No extra styling for selected messages.

  // ===== Left-click toggles selection when context bar is open (no text selected) =====
  row.addEventListener('click', (e) => {
    // Ignore clicks on the checkbox itself
    if (e.target && e.target.classList && e.target.classList.contains('message-checkbox')) return;
    if (!state.contextBarOpen) return;
    const sel = window.getSelection && window.getSelection().toString();
    if (sel && sel.trim()) return;
    if (state.selectedMessageIdx.has(index)) state.selectedMessageIdx.delete(index);
    else state.selectedMessageIdx.add(index);
    renderMessages();
    updateContextTokenSummary();
  });

  // ===== Right-click message context menu =====
  row.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    state.ctxMenuTarget = { type: 'message', index };
    openMenuAt(E.msgContextMenu, e.pageX, e.pageY, () => {
      const sel = window.getSelection ? window.getSelection().toString().trim() : '';
      E.menuBranchSelected.style.display = sel ? '' : 'none';
      E.menuCopySelected.style.display = sel ? '' : 'none';
      // Show folder actions only when chat is in a folder
      const inFolder = state.chatFolderMap[state.currentChatFile];
      const msgRole = state.messages[index] && state.messages[index].role;
      E.menuAddToFolderMemory.style.display = inFolder ? '' : 'none';
      // Show "Save as Folder Prompt" for assistant messages when chat is in a folder
      E.menuSaveAsFolderPrompt.style.display = (inFolder && msgRole === 'assistant') ? '' : 'none';
    });
  });

  return row;
}


  function renderMessages() {
  clearNode(E.messages);

  if (!state.messages.length) {
    const empty = el('div', 'empty-state');
    empty.innerHTML = `
      <h2>Welcome to ThreadBear</h2>
      <p>Start a conversation by typing a message below.</p>
      <p>Press Enter to send, Shift+Enter for new line.</p>
    `;
    E.messages.appendChild(empty);
    return;
  }

  state.messages.forEach((m, i) => {
    const row = messageNode(m, i);
    E.messages.appendChild(row);
  });

  E.messages.scrollTop = E.messages.scrollHeight;
}

  // ===== Tool Chip Rendering (Phase 3) =====

  function appendToolChip(msgIndex, name, args, status) {
    // Get or create the message bubble
    const bubble = E.messages.querySelector(`.message.assistant[data-index="${msgIndex}"] .message-bubble`);
    if (!bubble) return;

    const chip = el('div', 'tool-chip ' + status);
    chip.dataset.toolName = name;
    chip.dataset.toolIndex = msgIndex;
    
    const argsStr = truncateArgs(args);
    chip.innerHTML = `
      <span class="tool-icon">🔧</span>
      <span class="tool-name">${name}</span>
      <span class="tool-args">${argsStr}</span>
      <span class="tool-status-icon">⏳</span>
    `;
    
    chip.onclick = () => toggleToolDetail(chip);
    bubble.appendChild(chip);
    bubble.scrollTop = bubble.scrollHeight;
  }

  function updateToolChip(msgIndex, name, result) {
    const chip = E.messages.querySelector(`.message.assistant[data-index="${msgIndex}"] .tool-chip[data-tool-name="${name}"].running`);
    if (!chip) return;

    const success = result && result.success;
    chip.className = 'tool-chip ' + (success ? 'success' : 'error');
    chip.querySelector('.tool-status-icon').textContent = success ? '✅' : '❌';

    // Add expandable detail panel
    const detail = el('div', 'tool-detail hidden');
    detail.textContent = JSON.stringify(result, null, 2);
    chip.appendChild(detail);
  }

  function toggleToolDetail(chip) {
    const detail = chip.querySelector('.tool-detail');
    if (detail) detail.classList.toggle('hidden');
  }

  function truncateArgs(args, maxLen = 40) {
    try {
      const str = typeof args === 'string' ? args : JSON.stringify(args);
      if (str.length > maxLen) return str.substring(0, maxLen) + '...';
      return str;
    } catch {
      return '...';
    }
  }

  // ===== Folder API & UI =====

  async function loadFolders() {
    try {
      const res = await fetch('/api/folders');
      const data = await res.json();
      state.folders = data.folders || [];
      state.chatFolderMap = data.chat_folder_map || {};
      state.fileFolderMap = data.file_folder_map || {};
    } catch (e) {
      console.warn('Failed to load folders:', e);
    }
  }

  // ===== Branch Tree API & UI (Phase 2) =====

  async function loadBranchTree() {
    try {
      const res = await fetch('/api/branches');
      const data = await res.json();
      if (data.success) {
        // Build tree structure from flat list
        state.branchTree = buildBranchTreeFromFlatList(data.branches || []);
      }
    } catch (e) {
      console.warn('Failed to load branch tree:', e);
    }
  }

  function buildBranchTreeFromFlatList(branches) {
    // Build parent->children map
    const childrenMap = {};
    const roots = [];

    branches.forEach(b => {
      const parentId = b.parent_id;
      if (parentId) {
        if (!childrenMap[parentId]) childrenMap[parentId] = [];
        childrenMap[parentId].push(b);
      } else {
        roots.push(b);
      }
    });

    function buildNode(branch) {
      const children = childrenMap[branch.id] || [];
      return {
        ...branch,
        children: children.map(buildNode)
      };
    }

    return roots.map(buildNode);
  }

  async function createBranch(type, name, parentId, goal = '') {
    try {
      const res = await fetch('/api/branches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, name, parent_id: parentId, goal })
      });
      const data = await res.json();
      if (data.success) {
        await loadBranchTree();
        renderHistory();
      }
      return data;
    } catch (e) {
      console.error('Create branch failed:', e);
      return { success: false, error: String(e) };
    }
  }

  async function forkBranch(sourceId, messageIndex, name) {
    try {
      const res = await fetch(`/api/branches/${sourceId}/fork`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_index: messageIndex, name })
      });
      const data = await res.json();
      if (data.success) {
        await loadBranchTree();
        renderHistory();
      }
      return data;
    } catch (e) {
      console.error('Fork branch failed:', e);
      return { success: false, error: String(e) };
    }
  }

  async function transitionBranchStatus(branchId, newStatus) {
    try {
      const res = await fetch(`/api/branches/${branchId}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus })
      });
      const data = await res.json();
      if (data.success) {
        await loadBranchTree();
        renderHistory();
      }
      return data;
    } catch (e) {
      console.error('Transition status failed:', e);
      return { success: false, error: String(e) };
    }
  }

  async function deleteBranch(branchId) {
    try {
      const res = await fetch(`/api/branches/${branchId}`, {
        method: 'DELETE'
      });
      const data = await res.json();
      if (data.success) {
        await loadBranchTree();
        renderHistory();
      }
      return data;
    } catch (e) {
      console.error('Delete branch failed:', e);
      return { success: false, error: String(e) };
    }
  }

  // ===== Agent Functions (Phase 4) =====

  function checkAgentApplicable() {
    // Show agent panel only for work_order branches
    const branch = state.branchTree.find(b => b.filename === state.currentChatFile);
    if (branch && branch.type === 'work_order') {
      E.agentPanel.style.display = 'flex';
      state.currentBranchId = branch.id;
      loadAgentStatus(branch.id);
      loadAgentTodos(branch.id);
      loadAgentPlan(branch.id);
    } else {
      E.agentPanel.style.display = 'none';
      state.currentBranchId = null;
      disconnectAgentStream();
    }
  }

  async function loadAgentStatus(branchId) {
    try {
      const res = await fetch(`/api/branches/${branchId}/agent/status`);
      const data = await res.json();
      if (data.success) {
        updateAgentStatusDisplay(data);
        if (data.running && !state.agentRunning) {
          connectAgentStream(branchId);
        } else if (!data.running && state.agentRunning) {
          disconnectAgentStream();
        }
      }
    } catch (e) {
      console.warn('Failed to load agent status:', e);
    }
  }

  function updateAgentStatusDisplay(status) {
    const dot = E.agentStatusIndicator.querySelector('.status-dot');
    const text = E.agentStatusIndicator.querySelector('.status-text');
    
    dot.className = 'status-dot ' + (status.paused ? 'paused' : status.running ? 'running' : 'stopped');
    text.textContent = status.paused ? 'Paused' : status.running ? 'Running' : 'Stopped';
    
    E.agentIteration.textContent = `Iteration: ${status.iteration || 0} / ${status.max_iterations || 100}`;
    
    // Update button visibility
    if (status.running && !status.paused) {
      E.agentStartBtn.style.display = 'none';
      E.agentPauseBtn.style.display = 'flex';
      E.agentResumeBtn.style.display = 'none';
      E.agentStopBtn.style.display = 'flex';
    } else if (status.running && status.paused) {
      E.agentStartBtn.style.display = 'none';
      E.agentPauseBtn.style.display = 'none';
      E.agentResumeBtn.style.display = 'flex';
      E.agentStopBtn.style.display = 'flex';
    } else {
      E.agentStartBtn.style.display = 'flex';
      E.agentPauseBtn.style.display = 'none';
      E.agentResumeBtn.style.display = 'none';
      E.agentStopBtn.style.display = 'none';
    }
  }

  async function loadAgentTodos(branchId) {
    try {
      const res = await fetch(`/api/branches/${branchId}/todos`);
      const data = await res.json();
      if (data.success) {
        renderAgentTodos(data.todos || []);
      }
    } catch (e) {
      console.warn('Failed to load agent todos:', e);
    }
  }

  function renderAgentTodos(todos) {
    if (!todos || todos.length === 0) {
      E.agentTodoList.innerHTML = '<div class="agent-todo-item"><span class="agent-todo-status"></span>No todos yet</div>';
      return;
    }
    
    E.agentTodoList.innerHTML = todos.map(t => {
      const icon = {
        'pending': '[  ]',
        'in_progress': '[>>]',
        'completed': '[OK]',
        'blocked': '[!!]'
      }[t.status] || '[??]';
      return `<div class="agent-todo-item">
        <span class="agent-todo-status">${icon}</span>
        <span>${t.description}</span>
      </div>`;
    }).join('');
  }

  async function loadAgentPlan(branchId) {
    try {
      const res = await fetch(`/api/branches/${branchId}/plan`);
      const data = await res.json();
      if (data.success && data.plan) {
        renderAgentPlan(data.plan, data.next_step);
      } else {
        E.agentPlanList.innerHTML = '<div class="agent-plan-step">No plan created yet</div>';
      }
    } catch (e) {
      console.warn('Failed to load agent plan:', e);
    }
  }

  function renderAgentPlan(plan, nextStep) {
    if (!plan || !plan.steps) {
      E.agentPlanList.innerHTML = '<div class="agent-plan-step">No plan</div>';
      return;
    }
    
    E.agentPlanList.innerHTML = plan.steps.map(s => {
      const icon = {
        'pending': '[ ]',
        'in_progress': '[>>]',
        'completed': '[OK]',
        'skipped': '[--]'
      }[s.status] || '[??]';
      const marker = nextStep && s.id === nextStep.id ? ' ← NEXT' : '';
      return `<div class="agent-plan-step">
        <span class="agent-plan-status">${icon}</span>
        <span>${s.description}${marker}</span>
      </div>`;
    }).join('');
  }

  function appendAgentActivity(type, data) {
    const entry = document.createElement('div');
    entry.className = 'agent-activity-entry';
    
    if (type === 'tool_start') {
      entry.innerHTML = `<span class="agent-activity-tool">🔧 ${data.name}(${JSON.stringify(data.args).substring(0, 50)}...)</span>`;
    } else if (type === 'tool_end') {
      const success = data.result && data.result.success;
      entry.innerHTML = `<span class="agent-activity-${success ? 'success' : 'error'}">${success ? '✅' : '❌'} ${data.name}</span>`;
    } else if (type === 'content') {
      const text = typeof data === 'string' ? data.substring(0, 100) : JSON.stringify(data).substring(0, 100);
      entry.innerHTML = `<span class="agent-activity-content">"${text}..."</span>`;
    } else if (type === 'iteration') {
      entry.textContent = `Iteration ${data}`;
    } else if (type === 'complete') {
      entry.innerHTML = `<span class="agent-activity-success">✅ Task Complete (${data.reason}, ${data.iterations} iterations)</span>`;
    } else if (type === 'loop_detected') {
      entry.innerHTML = `<span class="agent-activity-error">⚠️ Loop Detected: ${data}</span>`;
    } else if (type === 'error') {
      entry.innerHTML = `<span class="agent-activity-error">❌ Error: ${data}</span>`;
    } else {
      entry.textContent = JSON.stringify(data);
    }
    
    E.agentActivityLog.appendChild(entry);
    E.agentActivityLog.scrollTop = E.agentActivityLog.scrollHeight;
  }

  function connectAgentStream(branchId) {
    if (state.agentSource) {
      state.agentSource.close();
    }
    
    state.agentSource = new EventSource(`/api/branches/${branchId}/agent/stream`);
    state.agentRunning = true;
    
    state.agentSource.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === 'status') {
          if (data.data === 'stopped') {
            state.agentRunning = false;
            loadAgentStatus(branchId);
          }
        } else if (data.type === 'iteration') {
          appendAgentActivity('iteration', data.data);
        } else if (data.type === 'tool_start') {
          appendAgentActivity('tool_start', data.data);
        } else if (data.type === 'tool_end') {
          appendAgentActivity('tool_end', data.data);
          loadAgentTodos(branchId);
          loadAgentPlan(branchId);
        } else if (data.type === 'content') {
          appendAgentActivity('content', data.data);
        } else if (data.type === 'complete') {
          appendAgentActivity('complete', data.data);
          state.agentRunning = false;
          loadAgentStatus(branchId);
        } else if (data.type === 'loop_detected') {
          appendAgentActivity('loop_detected', data.data);
          loadAgentStatus(branchId);
        } else if (data.type === 'error') {
          appendAgentActivity('error', data.data);
        }
      } catch (e) {
        console.warn('Agent stream parse error:', e);
      }
    };
    
    state.agentSource.onerror = () => {
      state.agentRunning = false;
      loadAgentStatus(branchId);
    };
  }

  function disconnectAgentStream() {
    if (state.agentSource) {
      state.agentSource.close();
      state.agentSource = null;
    }
    state.agentRunning = false;
  }

  async function startAgent(branchId, goal) {
    try {
      const res = await fetch(`/api/branches/${branchId}/agent/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal })
      });
      const data = await res.json();
      if (data.success) {
        E.agentGoalInput.style.display = 'none';
        E.agentGoalText.value = '';
        E.agentActivityLog.innerHTML = '';
        connectAgentStream(branchId);
        loadAgentStatus(branchId);
      } else {
        alert('Failed to start agent: ' + (data.error || 'Unknown error'));
      }
    } catch (e) {
      console.error('Start agent failed:', e);
      alert('Failed to start agent: ' + e);
    }
  }

  async function stopAgent(branchId) {
    try {
      const res = await fetch(`/api/branches/${branchId}/agent/stop`, {
        method: 'POST'
      });
      const data = await res.json();
      if (data.success) {
        disconnectAgentStream();
        loadAgentStatus(branchId);
      }
    } catch (e) {
      console.error('Stop agent failed:', e);
    }
  }

  async function pauseAgent(branchId) {
    try {
      const res = await fetch(`/api/branches/${branchId}/agent/pause`, {
        method: 'POST'
      });
      const data = await res.json();
      if (data.success) {
        loadAgentStatus(branchId);
      }
    } catch (e) {
      console.error('Pause agent failed:', e);
    }
  }

  async function resumeAgent(branchId) {
    try {
      const res = await fetch(`/api/branches/${branchId}/agent/resume`, {
        method: 'POST'
      });
      const data = await res.json();
      if (data.success) {
        loadAgentStatus(branchId);
      }
    } catch (e) {
      console.error('Resume agent failed:', e);
    }
  }

  // ===== Artifact Functions (Phase 5) =====

  function loadArtifacts(branchId) {
    fetch(`/api/branches/${branchId}/artifacts`)
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          renderArtifacts(data.produced || [], data.incoming || []);
        }
      })
      .catch(e => console.warn('Failed to load artifacts:', e));
  }

  function renderArtifacts(produced, incoming) {
    const typeIcons = {
      'document': '[doc]',
      'code': '[code]',
      'image': '[img]',
      'data': '[data]',
      'summary': '[sum]'
    };

    // Render produced
    if (!produced || produced.length === 0) {
      E.artifactProducedList.innerHTML = '<div class="artifact-item"><span class="artifact-name">No artifacts produced yet</span></div>';
    } else {
      E.artifactProducedList.innerHTML = produced.map(a => {
        const icon = typeIcons[a.type] || '[?]';
        return `<div class="artifact-item" onclick="viewArtifact('${a.id}')">
          <span class="artifact-type-icon ${a.type}">${icon}</span>
          <span class="artifact-name">${a.name || a.id}</span>
          <button class="artifact-send-btn" onclick="event.stopPropagation(); openSendArtifact('${a.id}')">Send</button>
        </div>`;
      }).join('');
    }

    // Render incoming
    if (!incoming || incoming.length === 0) {
      E.artifactIncomingList.innerHTML = '<div class="artifact-item"><span class="artifact-name">No incoming artifacts</span></div>';
    } else {
      E.artifactIncomingList.innerHTML = incoming.map(a => {
        const icon = typeIcons[a.type] || '[?]';
        const fromBranch = a.producer_branch_id ? a.producer_branch_id.substring(0, 8) : 'unknown';
        return `<div class="artifact-item" onclick="viewArtifact('${a.id}')">
          <span class="artifact-type-icon ${a.type}">${icon}</span>
          <span class="artifact-name">${a.name || a.id}</span>
          <span class="artifact-from">(from ${fromBranch})</span>
        </div>`;
      }).join('');
    }

    // Update count
    const total = (produced ? produced.length : 0) + (incoming ? incoming.length : 0);
    E.artifactCount.textContent = `${total} artifact${total !== 1 ? 's' : ''}`;
  }

  function openSendArtifact(artifactId) {
    E.artifactSendId.value = artifactId;
    
    // Load branches for dropdown
    fetch('/api/branches')
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          const branches = data.branches || [];
          E.artifactTargetBranch.innerHTML = branches
            .filter(b => b.id !== state.currentBranchId)
            .map(b => `<option value="${b.id}">${b.title || b.name || b.id.substring(0, 8)}</option>`)
            .join('');
        }
      });
    
    E.artifactSendModal.style.display = 'flex';
  }

  function closeSendArtifact() {
    E.artifactSendModal.style.display = 'none';
    E.artifactSendId.value = '';
  }

  function sendArtifact() {
    const artifactId = E.artifactSendId.value;
    const toBranchId = E.artifactTargetBranch.value;
    
    if (!artifactId || !toBranchId || !state.currentBranchId) {
      alert('Missing required information');
      return;
    }
    
    fetch(`/api/artifacts/${artifactId}/flow`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from_branch_id: state.currentBranchId,
        to_branch_id: toBranchId
      })
    })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        closeSendArtifact();
        loadArtifacts(state.currentBranchId);
        alert('Artifact sent successfully');
      } else {
        alert('Failed to send artifact: ' + (data.error || 'Unknown error'));
      }
    })
    .catch(e => alert('Failed to send artifact: ' + e));
  }

  function viewArtifact(artifactId) {
    fetch(`/api/artifacts/${artifactId}`)
      .then(r => r.json())
      .then(data => {
        if (data.success && data.artifact) {
          const a = data.artifact;
          const content = a.content || '[No content]';
          const info = `Type: ${a.type}\nName: ${a.name || a.id}\nCreated: ${a.created_at || 'unknown'}\nProducer: ${a.producer_branch_id || 'unknown'}\n\n--- Content ---\n\n${content}`;
          alert(info);
        } else {
          alert('Failed to load artifact');
        }
      })
      .catch(e => alert('Failed to load artifact: ' + e));
  }

  function checkArtifactApplicable() {
    // Show artifact panel for all branch types
    if (state.currentBranchId) {
      E.artifactPanel.style.display = 'flex';
      loadArtifacts(state.currentBranchId);
    } else {
      E.artifactPanel.style.display = 'none';
    }
  }


  async function createFolder(name, parentId) {
    try {
      const res = await fetch('/api/folders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, parent_id: parentId || null })
      });
      const data = await res.json();
      if (data.success) {
        await loadFolders();
        renderHistory();
        // Start rename inline for the new folder
        startFolderRename(data.folder.id);
      }
      return data;
    } catch (e) {
      console.error('Create folder failed:', e);
    }
  }

  async function renameFolder(folderId, newName) {
    try {
      const res = await fetch(`/api/folders/${folderId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName })
      });
      const data = await res.json();
      if (data.success) {
        await loadFolders();
        renderHistory();
      } else if (data.error) {
        alert(data.error);
      }
    } catch (e) {
      console.error('Rename folder failed:', e);
    }
  }

  async function deleteFolder(folderId, deleteContents) {
    try {
      const res = await fetch(`/api/folders/${folderId}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delete_contents: deleteContents, move_to_parent: !deleteContents })
      });
      const data = await res.json();
      if (data.success) {
        state.expandedFolders.delete(folderId);
        await loadFolders();
        renderHistory();
      }
    } catch (e) {
      console.error('Delete folder failed:', e);
    }
  }

  async function moveChatToFolder(filename, folderId) {
    // Remove from any current folder first
    const currentFolder = state.chatFolderMap[filename];
    if (currentFolder) {
      await fetch(`/api/folders/${currentFolder}/chats/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    }
    if (folderId) {
      await fetch(`/api/folders/${folderId}/chats`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename })
      });
    }
    await loadFolders();
    renderHistory();
  }

  function startFolderRename(folderId) {
    const nameEl = document.querySelector(`[data-folder-id="${folderId}"] .folder-name`);
    if (!nameEl) return;
    const currentName = nameEl.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'folder-rename-input';
    input.value = currentName;
    nameEl.textContent = '';
    nameEl.appendChild(input);
    input.focus();
    input.select();

    const finish = async () => {
      const newName = input.value.trim();
      if (newName && newName !== currentName) {
        await renameFolder(folderId, newName);
      } else {
        nameEl.textContent = currentName;
      }
    };

    input.addEventListener('blur', finish);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { input.value = currentName; input.blur(); }
    });
  }

  function toggleFolderExpand(folderId) {
    if (state.expandedFolders.has(folderId)) {
      state.expandedFolders.delete(folderId);
    } else {
      state.expandedFolders.add(folderId);
    }
    renderHistory();
  }

  async function openFolderContextSettings(folderId) {
    const folder = findFolderById(folderId);
    if (!folder) return;

    E.folderContextPanel.dataset.folderId = folderId;
    E.folderContextTitle.textContent = `Context: ${folder.name}`;

    try {
      const res = await fetch(`/api/folders/${folderId}/context`);
      const data = await res.json();
      renderSavedPrompts(data.saved_prompts || [], data.active_prompt_id);
      renderMemoryNotes(data.memory_notes || []);
    } catch (e) {
      console.error('Failed to load folder context:', e);
      renderSavedPrompts([], null);
      renderMemoryNotes([]);
    }

    openSettingsPanel(E.folderContextPanel);
  }

  function renderSavedPrompts(prompts, activeId) {
    clearNode(E.savedPromptsList);

    if (prompts.length === 0) {
      const empty = el('div');
      empty.style.cssText = 'color: var(--text-secondary); font-size: 13px; padding: 8px;';
      empty.textContent = 'No saved prompts. Use the prompt branch to develop a context prompt, then right-click a message \u2192 "Save as Folder Prompt".';
      E.savedPromptsList.appendChild(empty);
      return;
    }

    prompts.forEach(prompt => {
      const isActive = prompt.id === activeId;
      const item = el('div', 'saved-prompt-item' + (isActive ? ' active' : ''));

      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'activePrompt';
      radio.checked = isActive;
      radio.addEventListener('click', async (e) => {
        e.stopPropagation();
        const fid = E.folderContextPanel.dataset.folderId;
        // Toggle: if already active, deactivate; else activate
        const newId = isActive ? null : prompt.id;
        await fetch(`/api/folders/${fid}/active-prompt`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt_id: newId }),
        });
        openFolderContextSettings(fid);
      });
      item.appendChild(radio);

      const nameSpan = el('span', 'prompt-name');
      nameSpan.textContent = prompt.name;
      nameSpan.title = 'Click to expand/collapse';
      item.appendChild(nameSpan);

      const tokensSpan = el('span', 'prompt-tokens');
      tokensSpan.textContent = `~${prompt.tokens} tok`;
      item.appendChild(tokensSpan);

      const delBtn = el('button', 'prompt-delete');
      delBtn.textContent = '\u2715';
      delBtn.title = 'Delete prompt';
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete saved prompt "${prompt.name}"?`)) return;
        const fid = E.folderContextPanel.dataset.folderId;
        await fetch(`/api/folders/${fid}/prompts/${prompt.id}`, { method: 'DELETE' });
        openFolderContextSettings(fid);
      });
      item.appendChild(delBtn);

      // Content preview (toggle on name click)
      const contentDiv = el('div', 'saved-prompt-content');
      contentDiv.textContent = prompt.content;

      nameSpan.addEventListener('click', (e) => {
        e.stopPropagation();
        contentDiv.classList.toggle('expanded');
      });

      E.savedPromptsList.appendChild(item);
      E.savedPromptsList.appendChild(contentDiv);
    });
  }

  function renderMemoryNotes(notes) {
    clearNode(E.memoryNotesList);
    E.memoryNotesCount.textContent = notes.length + ' note' + (notes.length !== 1 ? 's' : '');

    if (notes.length === 0) {
      const empty = el('div');
      empty.style.cssText = 'color: var(--text-secondary); font-size: 13px; padding: 8px;';
      empty.textContent = 'No memory notes yet.';
      E.memoryNotesList.appendChild(empty);
    }

    notes.forEach((note, idx) => {
      const item = el('div', 'memory-note-item');

      const textDiv = el('div');
      textDiv.style.cssText = 'flex: 1; min-width: 0;';

      // Display text (click to edit)
      const noteText = el('div', 'note-text');
      noteText.textContent = note.text;
      noteText.title = 'Click to edit';
      noteText.style.cursor = 'pointer';
      textDiv.appendChild(noteText);

      // Hidden edit textarea
      const editArea = document.createElement('textarea');
      editArea.className = 'note-edit-area';
      editArea.style.cssText = 'display:none; width:100%; min-height:60px; resize:vertical; font-size:13px; padding:4px 6px; border:1px solid var(--border-color); border-radius:4px; background:var(--bg-primary); color:var(--text-primary); font-family:inherit;';
      editArea.value = note.text;
      textDiv.appendChild(editArea);

      // Click text to enter edit mode
      noteText.addEventListener('click', (e) => {
        e.stopPropagation();
        noteText.style.display = 'none';
        editArea.style.display = '';
        editArea.value = noteText.textContent;
        editArea.focus();
      });

      // Save on blur
      editArea.addEventListener('blur', async () => {
        const newText = editArea.value.trim();
        if (newText && newText !== noteText.textContent) {
          const fid = E.folderContextPanel.dataset.folderId;
          await fetch(`/api/folders/${fid}/memory/note/${idx}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: newText }),
          });
          noteText.textContent = newText;
        }
        editArea.style.display = 'none';
        noteText.style.display = '';
      });

      // Save on Enter (Shift+Enter for newline)
      editArea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          editArea.blur();
        }
        if (e.key === 'Escape') {
          editArea.value = noteText.textContent;
          editArea.blur();
        }
      });

      if (note.source) {
        const src = el('div', 'note-source');
        src.textContent = note.source;
        textDiv.appendChild(src);
      }
      item.appendChild(textDiv);

      const btnGroup = el('div');
      btnGroup.style.cssText = 'display: flex; gap: 4px; flex-shrink: 0;';

      const compactBtn = el('button', 'note-compact');
      compactBtn.textContent = '\u2702';
      compactBtn.title = 'Compact (tighten wording, keep all facts)';
      compactBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const fid = E.folderContextPanel.dataset.folderId;
        if (!fid) return;
        compactBtn.textContent = '\u2026';
        compactBtn.disabled = true;
        try {
          const res = await fetch(`/api/folders/${fid}/memory/note/${idx}/compact`, { method: 'POST' });
          const data = await res.json();
          if (data.success) {
            noteText.textContent = data.text;
          } else {
            alert('Compact failed: ' + (data.error || 'Unknown'));
          }
        } catch (err) {
          console.error('Compact note failed:', err);
        }
        compactBtn.textContent = '\u2702';
        compactBtn.disabled = false;
      });
      btnGroup.appendChild(compactBtn);

      const delBtn = el('button', 'note-delete');
      delBtn.textContent = '\u2715';
      delBtn.title = 'Delete note';
      delBtn.addEventListener('click', async () => {
        const fid = E.folderContextPanel.dataset.folderId;
        if (!fid) return;
        await fetch(`/api/folders/${fid}/memory/note/${idx}`, { method: 'DELETE' });
        openFolderContextSettings(fid);
      });
      btnGroup.appendChild(delBtn);

      item.appendChild(btnGroup);

      E.memoryNotesList.appendChild(item);
    });

    // "Add Note" button
    const addBtn = el('button', 'control-btn');
    addBtn.style.cssText = 'width: 100%; margin-top: 8px;';
    addBtn.textContent = '+ Add Note';
    addBtn.addEventListener('click', () => {
      // Replace button with textarea for input
      const wrapper = el('div');
      wrapper.style.cssText = 'margin-top: 8px;';
      const ta = document.createElement('textarea');
      ta.style.cssText = 'width:100%; min-height:60px; resize:vertical; font-size:13px; padding:6px 8px; border:1px solid var(--border-color); border-radius:4px; background:var(--bg-primary); color:var(--text-primary); font-family:inherit;';
      ta.placeholder = 'Type or paste a memory note...';
      wrapper.appendChild(ta);

      const btnRow = el('div');
      btnRow.style.cssText = 'display:flex; gap:6px; margin-top:6px;';
      const saveBtn = el('button', 'control-btn');
      saveBtn.textContent = 'Save';
      saveBtn.style.flex = '1';
      const cancelBtn = el('button', 'control-btn');
      cancelBtn.textContent = 'Cancel';
      cancelBtn.style.cssText = 'flex:1; background:transparent;';
      btnRow.appendChild(saveBtn);
      btnRow.appendChild(cancelBtn);
      wrapper.appendChild(btnRow);

      addBtn.replaceWith(wrapper);
      ta.focus();

      saveBtn.addEventListener('click', async () => {
        const text = ta.value.trim();
        if (!text) return;
        const fid = E.folderContextPanel.dataset.folderId;
        await fetch(`/api/folders/${fid}/memory/add`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, source: 'manual' }),
        });
        openFolderContextSettings(fid);
      });

      cancelBtn.addEventListener('click', () => {
        wrapper.replaceWith(addBtn);
      });

      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          saveBtn.click();
        }
        if (e.key === 'Escape') cancelBtn.click();
      });
    });
    E.memoryNotesList.parentNode.insertBefore(addBtn, E.memoryNotesList.nextSibling);
  }

  function showMoveToFolderMenu(x, y, filename) {
    const menu = E.moveToFolderMenu;
    menu.innerHTML = '';

    // "Remove from folder" option if already in a folder
    if (state.chatFolderMap[filename]) {
      const removeItem = el('div', 'context-menu-item');
      removeItem.innerHTML = '<span>📤</span><span>Remove from Folder</span>';
      removeItem.addEventListener('click', () => {
        moveChatToFolder(filename, null);
        menu.style.display = 'none';
      });
      menu.appendChild(removeItem);
      const sep = el('div', 'context-menu-separator');
      menu.appendChild(sep);
    }

    // Build flat list of all folders
    const allFolders = [];
    state.folders.forEach(f => {
      allFolders.push({ id: f.id, name: f.name, depth: 0 });
      (f.children || []).forEach(c => {
        allFolders.push({ id: c.id, name: c.name, depth: 1, parentName: f.name });
      });
    });

    if (allFolders.length === 0) {
      const empty = el('div', 'context-menu-item');
      empty.innerHTML = '<span>📁</span><span style="color: var(--text-secondary)">No folders yet</span>';
      menu.appendChild(empty);
    }

    allFolders.forEach(f => {
      const item = el('div', 'context-menu-item');
      const label = f.depth > 0 ? `  ${f.parentName} / ${f.name}` : f.name;
      item.innerHTML = `<span>📁</span><span>${label}</span>`;
      item.addEventListener('click', () => {
        moveChatToFolder(filename, f.id);
        menu.style.display = 'none';
      });
      menu.appendChild(item);
    });

    openMenuAt(menu, x, y);
  }

  function renderFolderNode(folder, container, depth) {
    const isExpanded = state.expandedFolders.has(folder.id);

    // Count contents
    const chatCount = Object.values(state.chatFolderMap).filter(fid => fid === folder.id).length;
    const fileCount = Object.values(state.fileFolderMap).filter(fid => fid === folder.id).length;
    const childCount = (folder.children || []).length;
    const totalCount = chatCount + fileCount + childCount;

    // Folder header row
    const div = el('div', 'folder-item' + (depth > 0 ? ' subfolder' : ''));
    div.setAttribute('data-folder-id', folder.id);

    const chevron = el('span', 'folder-chevron' + (isExpanded ? ' expanded' : ''));
    chevron.textContent = '\u25B6'; // right-pointing triangle
    div.appendChild(chevron);

    const icon = el('span', 'folder-icon');
    icon.textContent = isExpanded ? '\uD83D\uDCC2' : '\uD83D\uDCC1'; // open/closed folder
    div.appendChild(icon);

    const nameSpan = el('span', 'folder-name');
    nameSpan.textContent = folder.name;
    div.appendChild(nameSpan);

    if (totalCount > 0) {
      const countSpan = el('span', 'folder-count');
      countSpan.textContent = totalCount;
      div.appendChild(countSpan);
    }

    // Click to expand/collapse
    div.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleFolderExpand(folder.id);
    });

    // Right-click for folder context menu
    div.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      state.ctxMenuTarget = { type: 'folder', folderId: folder.id, isRoot: depth === 0 };
      // Hide "Add Subfolder" for subfolders (max depth = 2)
      E.menuAddSubfolder.style.display = depth === 0 ? '' : 'none';
      openMenuAt(E.folderContextMenu, e.pageX, e.pageY);
    });

    container.appendChild(div);

    // Contents container
    if (isExpanded) {
      const contents = el('div', 'folder-contents expanded');

      // Render prompt branch first (if any)
      const promptFn = folder.prompt_branch_filename;
      const folderChats = state.history.filter(c => state.chatFolderMap[c.filename] === folder.id);
      if (promptFn) {
        const promptChat = folderChats.find(c => c.filename === promptFn);
        if (promptChat) {
          renderChatNode(promptChat, contents, 0, true, true);
        }
      }

      // Render other chats
      folderChats.filter(c => c.filename !== promptFn).forEach(chat => {
        renderChatNode(chat, contents, 0, true);
      });

      // Render files in this folder
      const folderFiles = Object.keys(state.fileFolderMap).filter(fn => state.fileFolderMap[fn] === folder.id);
      folderFiles.forEach(fn => {
        const fileDiv = el('div', 'folder-file-item');
        fileDiv.innerHTML = `<span>\uD83D\uDCC4</span><span>${fn}</span>`;
        contents.appendChild(fileDiv);
      });

      // Render subfolders
      (folder.children || []).forEach(child => {
        renderFolderNode(child, contents, depth + 1);
      });

      container.appendChild(contents);
    }
  }

  // ===== Branch Tree Rendering (Phase 2) =====

  function renderBranchNode(node, container, depth) {
    const div = el('div', 'branch-item');
    
    // Set depth for indentation
    div.style.paddingLeft = (8 + (depth * 16)) + 'px';
    
    // Status badge
    const status = node.status || 'active';
    const statusClass = `status-${status}`;
    
    // Icon based on type
    let icon = '💬';
    if (node.type === 'domain') icon = '📁';
    else if (node.type === 'work_order') icon = '📋';
    
    // Build content row
    const contentRow = el('div', 'branch-content-row');
    
    // Expand/collapse toggle for nodes with children
    const hasChildren = node.children && node.children.length > 0;
    if (hasChildren) {
      const toggle = el('span', 'branch-toggle');
      toggle.textContent = state.expandedBranches.has(node.id) ? '▼' : '▶';
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state.expandedBranches.has(node.id)) {
          state.expandedBranches.delete(node.id);
        } else {
          state.expandedBranches.add(node.id);
        }
        renderHistory();
      });
      contentRow.appendChild(toggle);
    } else {
      const spacer = el('span', 'branch-spacer');
      spacer.style.width = '16px';
      spacer.style.display = 'inline-block';
      contentRow.appendChild(spacer);
    }
    
    // Icon
    const iconSpan = el('span', 'branch-icon');
    iconSpan.textContent = icon;
    contentRow.appendChild(iconSpan);
    
    // Name
    const nameSpan = el('span', 'branch-name');
    nameSpan.textContent = node.name || node.title || 'Untitled';
    nameSpan.addEventListener('click', () => {
      // Load the branch's chat if it has one
      if (node.filename) {
        loadChat(node.filename);
      }
    });
    contentRow.appendChild(nameSpan);
    
    // Status badge
    const statusBadge = el('span', `branch-status status-${status}`);
    statusBadge.textContent = status;
    statusBadge.title = `Status: ${status}`;
    contentRow.appendChild(statusBadge);
    
    div.appendChild(contentRow);
    
    // Context menu
    div.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      state.ctxMenuTarget = { type: 'branch', branch: node };
      openBranchContextMenu(e, node);
    });
    
    container.appendChild(div);
    
    // Render children if expanded
    if (hasChildren && state.expandedBranches.has(node.id)) {
      node.children.forEach(child => {
        renderBranchNode(child, container, depth + 1);
      });
    }
  }

  function openBranchContextMenu(e, node) {
    // Store target branch
    state.ctxMenuTarget = { type: 'branch', branch: node };
    
    // Hide status submenu initially
    E.statusSubMenu.style.display = 'none';
    
    // Show/hide menu items based on branch type
    const isDomain = node.type === 'domain';
    const isWorkOrder = node.type === 'work_order';
    const isChat = node.type === 'chat';
    
    // Work Order option only for domains
    E.branchNewWorkOrderMenuItem.style.display = isDomain ? '' : 'none';
    
    // Show menu
    openMenuAt(E.branchContextMenu, e.pageX, e.pageY);
  }

  function renderChatNode(chat, container, depth, inFolder, isPromptBranch) {
    const div = el('div', 'chat-item');

    if (isPromptBranch) {
      div.classList.add('prompt-branch');
    }

    if (depth > 0) {
      div.classList.add('side-chat');
      div.setAttribute('data-depth', depth.toString());
    }

    if (chat.filename === state.currentChatFile) {
      div.classList.add('active');
    }

    let title = chat.title || chat.first_message?.substring(0, 60) || chat.filename.replace(/\.json$/, '');

    if (!inFolder) {
      div.style.paddingLeft = (12 + (depth * 20)) + 'px';
    }

    const titleDiv = el('div', 'chat-item-title');
    titleDiv.textContent = title;
    div.appendChild(titleDiv);

    div.addEventListener('click', () => loadChat(chat.filename));

    div.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      state.ctxMenuTarget = { type: 'chat', filename: chat.filename };
      // Show/hide "Remove from Folder" based on whether chat is in a folder
      E.menuRemoveFromFolder.style.display = state.chatFolderMap[chat.filename] ? '' : 'none';
      openMenuAt(E.chatContextMenu, e.pageX, e.pageY);
    });

    container.appendChild(div);

    // Render children (branches)
    const chatId = chat.chat_id || chat.filename;
    const children = state.history.filter(c => c.parent_chat_id === chatId);
    children.forEach(child => {
      renderChatNode(child, container, depth + 1, inFolder);
    });
  }

  function renderHistory() {
    clearNode(E.chatHistory);

    // === Render Branch Tree (Phase 2) ===
    if (state.branchTree && state.branchTree.length > 0) {
      const branchSection = el('div', 'branch-section');
      
      // Section header with "New Domain" button
      const headerRow = el('div', 'branch-section-header');
      const sectionTitle = el('span', 'branch-section-title');
      sectionTitle.textContent = 'Branches';
      const newDomainBtn = el('button', 'control-btn branch-new-btn');
      newDomainBtn.textContent = '+ Domain';
      newDomainBtn.title = 'Create new domain branch';
      newDomainBtn.addEventListener('click', () => {
        const name = prompt('Domain name:');
        if (name) createBranch('domain', name, null, '');
      });
      headerRow.appendChild(sectionTitle);
      headerRow.appendChild(newDomainBtn);
      branchSection.appendChild(headerRow);

      // Render tree
      state.branchTree.forEach(node => {
        renderBranchNode(node, branchSection, 0);
      });

      E.chatHistory.appendChild(branchSection);

      // Divider before folders
      const divider = el('div', 'sidebar-divider');
      E.chatHistory.appendChild(divider);
    }

    // Build set of chats that are in folders
    const chatsInFolders = new Set(Object.keys(state.chatFolderMap));

    // Build set of child chats (have parent_chat_id)
    const childChats = new Set();
    state.history.forEach(chat => {
      if (chat.parent_chat_id) childChats.add(chat.filename);
    });

    // Render folders section
    if (state.folders.length > 0) {
      state.folders.forEach(folder => {
        renderFolderNode(folder, E.chatHistory, 0);
      });

      // Divider between folders and unfiled chats
      const unfiledChats = state.history.filter(c => !chatsInFolders.has(c.filename) && !c.parent_chat_id);
      if (unfiledChats.length > 0) {
        const label = el('div', 'folder-section-label');
        label.textContent = 'Folders';
        E.chatHistory.appendChild(label);
      }
    }

    // Render unfiled root chats (not in any folder, no parent)
    state.history.forEach(chat => {
      if (!chatsInFolders.has(chat.filename) && !chat.parent_chat_id) {
        renderChatNode(chat, E.chatHistory, 0, false);
      }
    });
  }

  function chipForDoc(d) {
    const item = el('div', 'attachment-item' + (d.selected ? ' selected' : ''));
    const icon = el('span', 'attachment-icon'); icon.textContent = '📎';
    const name = el('span', 'attachment-name'); name.textContent = d.name;
    const tok = el('span', 'attachment-tokens'); tok.textContent = (d.token_estimate_total || 0) + 't';
    const view = el('span', 'attachment-view'); view.textContent = '👁';
    view.title = 'View document';
    view.style.cursor = 'pointer';
    const remove = el('span', 'attachment-remove'); remove.textContent = '×';
    remove.title = 'Delete document';

    item.appendChild(icon);
    item.appendChild(name);
    item.appendChild(tok);
    item.appendChild(view);
    item.appendChild(remove);

    // View document
    view.addEventListener('click', (e) => {
      e.stopPropagation();
      if (window.openDocViewer) window.openDocViewer(d.doc_id);
    });

    // Toggle selection
    item.addEventListener('click', async (e) => {
      if (e.target === remove || e.target === view) return; // other handlers
      await fetch('/api/context/docs/select', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc_id: d.doc_id, selected: !d.selected })
      });
      await loadDocs();
      await updateContextTokenSummary();
    });

    // Delete
    remove.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Delete this document?')) return;
      const r = await fetch(`/api/context/docs/${encodeURIComponent(d.doc_id)}`, { method: 'DELETE' });
      const js = await r.json().catch(() => ({}));
      if (js && js.success === false) alert(js.error || 'Failed to delete');
      await loadDocs();
      await updateContextTokenSummary();
    });

    return item;
  }

  function renderDocChips() {
    // inline area inside context controls (if you want to show) and the global bar
    const selected = state.docs.filter(d => d.selected);
    clearNode(E.attachmentList);
    clearNode(E.attachmentListInline);
    selected.forEach(d => {
      const chip = chipForDoc(d);
      E.attachmentList.appendChild(chip.cloneNode(true)); // visible persistent bar
      E.attachmentListInline.appendChild(chip);           // inline mirror
    });
    // show/hide the persistent bar
    if (selected.length) show(E.documentAttachmentsBar); else hide(E.documentAttachmentsBar);
  }

  function openMenuAt(menu, x, y, onOpen) {
    // Show off-screen first so we can measure its size
    menu.style.left = '-9999px';
    menu.style.top = '-9999px';
    menu.style.display = 'block';
    if (onOpen) onOpen();

    const mw = menu.offsetWidth;
    const mh = menu.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Flip left if it would overflow the right edge
    if (x + mw > vw) x = Math.max(0, x - mw);
    // Flip up if it would overflow the bottom edge
    if (y + mh > vh) y = Math.max(0, y - mh);

    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    const closer = (ev) => {
      if (!menu.contains(ev.target)) { menu.style.display = 'none'; window.removeEventListener('mousedown', closer, true); }
    };
    setTimeout(() => window.addEventListener('mousedown', closer, true));
  }

  // ====== API ======
  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return await r.json();
  }

  async function postJSON(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const js = await r.json().catch(() => ({}));
    if (!r.ok || js.success === false) throw new Error(js.error || `${r.status} ${r.statusText}`);
    return js;
  }

  // ====== Loads / init ======
  async function loadConfigAndModels() {
    const cfg = await getJSON('/api/config');
    state.config = cfg;
    state.providers = cfg.providers || ['groq','google','mistral','openrouter'];
    state.currentProvider = cfg.current_provider || 'groq';

    // ----- Provider dropdowns -----
    E.providerHeader.innerHTML = '';
    state.providers.forEach(p => {
      const o = el('option'); 
      o.value = p; 
      o.textContent = p[0].toUpperCase() + p.slice(1);
      if (p === state.currentProvider) o.selected = true;
      E.providerHeader.appendChild(o);
    });
    // Also rebuild settings panel provider dropdown dynamically
    if (E.providerSelect) {
      E.providerSelect.innerHTML = '';
      state.providers.forEach(p => {
        const o = el('option');
        o.value = p;
        o.textContent = p[0].toUpperCase() + p.slice(1);
        if (p === state.currentProvider) o.selected = true;
        E.providerSelect.appendChild(o);
      });
    }

    // ----- Models -----
    const models = cfg.models || [];
    let chosenModel = (cfg.current_model && models.includes(cfg.current_model)) ? cfg.current_model : '';

    if (!chosenModel && models.length > 0) {
      chosenModel = models[0];
      // Persist fallback model in background (non-blocking)
      postJSON('/api/config/update', {
        provider: state.currentProvider,
        model: chosenModel
      }).catch(e => console.warn('Failed to persist model selection:', e));
    }

    state.currentModel = chosenModel;
    applyModelsToSelect(E.modelHeader, models, state.currentModel);

    // Use cfg.models directly for BOTH header and settings (no extra fetch)
    function applyModelsToSelect(selectEl, models, currentModel) {
      selectEl.innerHTML = '';
      (models || []).forEach(m => {
        const o = el('option'); o.value = m; o.textContent = m;
        if ((currentModel || cfg.current_model) === m) o.selected = true;
        selectEl.appendChild(o);
      });
    }

    // Temperature
    // Removed: legacy global temperature slider

    renderChatTitle();
  }

  async function refreshModelsHeader(provider, current) {
    const data = await getJSON(`/api/models/${encodeURIComponent(provider)}`);
    
    E.modelHeader.innerHTML = '';
    (data.models || []).forEach(m => {
      const o = el('option'); 
      o.value = m;
      o.textContent = m;   // removed (undefined) / (tokens)
      if ((current || data.current_model) === m) o.selected = true;
      E.modelHeader.appendChild(o);
    });
    state.currentModel = E.modelHeader.value || data.current_model || '';
  }

  /**
   * Check if a model can be removed.
   * All models can now be removed - no restrictions.
   */
  async function isRemovableModel(provider, modelName) {
    // Any model can be removed
    return modelName ? true : false;
  }

  async function refreshModelsSettings(provider) {
  try {
    const res = await fetch(`/api/models/${provider}`);
    const data = await res.json();
    
    if (data.models) {
      // Clear existing options
      E.modelSettingsSelect.innerHTML = '';
      
      // Add models to dropdown
      data.models.forEach(m => {
        const o = el('option'); 
        o.value = m;
        o.textContent = m;
        E.modelSettingsSelect.appendChild(o);
      });
      
      // If we have models, load settings for the first one
      if (data.models.length > 0) {
        // Show the settings form
        E.modelSettingsForm.style.display = '';
        
        // Load settings for the first model (AWAIT it!)
        await loadModelSettings(provider, data.models[0]);
      } else {
        // Hide the settings form if no models
        E.modelSettingsForm.style.display = 'none';
      }
    }
  } catch (err) {
    console.error('Failed to refresh model list for', provider, err);
  }
}

  async function loadModelSettings(provider, model) {
    // Show/hide nglGroup based on provider
    if (E.nglGroup) {
      E.nglGroup.style.display = (provider === 'llamacpp') ? '' : 'none';
    }
    if (E.vramRequiredGroup) {
      E.vramRequiredGroup.style.display = (provider === 'llamacpp') ? '' : 'none';
    }
    try {
      const res = await fetch(`/api/models/${provider}/settings/${encodeURIComponent(model)}`);
      const data = await res.json();
      if (data.success && data.settings) {
        const settings = data.settings;

        // Update form fields with model settings
        if (settings.context_window !== undefined) {
          E.contextWindowInput.value = settings.context_window;
        } else {
          E.contextWindowInput.value = '';
        }
        E.nglInput.value = (settings.n_gpu_layers !== undefined) ? settings.n_gpu_layers : 99;
        if (E.vramRequiredInput) {
          if (settings.vram_required_gb !== undefined) {
            E.vramRequiredInput.value = settings.vram_required_gb;
          } else {
            E.vramRequiredInput.value = '';
          }
        }
        if (settings.max_tokens !== undefined) {
          E.maxTokensInput.value = settings.max_tokens;
        }
        if (settings.temperature !== undefined) {
          E.modelTemperatureRange.value = settings.temperature;
          E.modelTemperatureValue.textContent = settings.temperature.toFixed(1);
        }
        if (settings.top_p !== undefined) {
          E.topPRange.value = settings.top_p;
          E.topPValue.textContent = settings.top_p.toFixed(2);
        }
        if (settings.top_k !== undefined) {
          E.topKRange.value = settings.top_k;
          E.topKValue.textContent = settings.top_k;
        }
        if (settings.system_prompt !== undefined) {
          E.modelSystemPromptTextarea.value = settings.system_prompt || '';
        }
      }
    } catch (err) {
      console.error('Failed to load model settings for', model, err);
    }
  }

async function loadPrompts() {
  try {
    // Use API endpoint instead of static file to avoid caching issues
    const r = await fetch('/api/prompts');
    if (!r.ok) throw new Error('Failed to load prompts');
    const data = await r.json();
    state.prompts = data.prompts || [];
  } catch (err) {
    console.error('Failed to load prompts:', err);
    state.prompts = [];
  }

  // populate header dropdown
  E.systemPromptSelect.innerHTML = '';

  // Add "None" option as the first option
  const noneOption = el('option');
  noneOption.value = '';
  noneOption.textContent = 'None';
  E.systemPromptSelect.appendChild(noneOption);

  if (!state.prompts.length) {
    const o = el('option');
    o.disabled = true;
    o.textContent = '(no prompts found)';
    E.systemPromptSelect.appendChild(o);
  } else {
    state.prompts.forEach(p => {
      const o = el('option');
      o.value = p.id || p.name || p.title;
      o.textContent = p.title || p.name || p.id;
      E.systemPromptSelect.appendChild(o);
    });
  }
  
  // Set default system prompt to "general_chat" if it exists, otherwise keep "None" selected
  if (E.systemPromptSelect) {
    const defaultId = "general_chat";
    if ([...E.systemPromptSelect.options].some(o => o.value === defaultId)) {
      // Default selection is "general_chat"
      E.systemPromptSelect.value = defaultId;
    } else {
      // If not found, leave "None" selected
      E.systemPromptSelect.value = "";
    }
  }
}

  // ====== System Prompts Management ======
  
  async function loadPromptsList() {
    try {
      const response = await fetch('/api/prompts');
      const data = await response.json();
      
      if (data.success && data.prompts) {
        state.prompts = data.prompts;
        
        // Populate the prompts list
        E.promptsList.innerHTML = '';
        data.prompts.forEach(prompt => {
          const option = el('option');
          option.value = prompt.id;
          option.textContent = prompt.title || prompt.id;
          option.dataset.prompt = JSON.stringify(prompt);
          E.promptsList.appendChild(option);
        });
        
        // Hide editor by default
        E.promptEditor.style.display = 'none';
      }
    } catch (err) {
      console.error('Failed to load prompts:', err);
      alert('Failed to load prompts: ' + err.message);
    }
  }

  function showPromptEditor(prompt = null) {
    E.promptEditor.style.display = 'block';
    
    if (prompt) {
      // Editing existing prompt
      E.promptId.value = prompt.id;
      E.promptId.disabled = true; // Can't change ID of existing prompt
      E.promptTitle.value = prompt.title || '';
      E.promptBody.value = prompt.body || '';
      E.deletePromptBtn.style.display = '';
    } else {
      // New prompt
      E.promptId.value = '';
      E.promptId.disabled = false;
      E.promptTitle.value = '';
      E.promptBody.value = '';
      E.deletePromptBtn.style.display = 'none';
    }
  }

  async function savePrompt() {
    const id = E.promptId.value.trim();
    const title = E.promptTitle.value.trim();
    const body = E.promptBody.value.trim();

    if (!id) {
      alert('Please enter a prompt ID');
      return;
    }

    if (!/^[a-z0-9_]+$/.test(id)) {
      alert('Prompt ID must be lowercase letters, numbers, and underscores only');
      return;
    }

    if (!title) {
      alert('Please enter a title');
      return;
    }

    if (!body) {
      alert('Please enter the prompt text');
      return;
    }

    try {
      // Check if editing existing or creating new
      const isEdit = state.prompts.some(p => p.id === id);
      const method = isEdit ? 'PUT' : 'POST';
      const url = isEdit ? `/api/prompts/${id}` : '/api/prompts';

      const response = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, title, body })
      });

      const data = await response.json();

      if (data.success) {
        alert(isEdit ? 'Prompt updated successfully!' : 'Prompt created successfully!');
        await loadPromptsList();
        await loadPrompts(); // Refresh the header dropdown too
        E.promptEditor.style.display = 'none';
      } else {
        alert('Failed to save prompt: ' + (data.error || 'Unknown error'));
      }
    } catch (err) {
      console.error('Failed to save prompt:', err);
      alert('Failed to save prompt: ' + err.message);
    }
  }

  async function deletePrompt() {
    const id = E.promptId.value.trim();

    if (!id) return;

    if (!confirm(`Are you sure you want to delete the prompt "${E.promptTitle.value}"?`)) {
      return;
    }

    try {
      const response = await fetch(`/api/prompts/${id}`, {
        method: 'DELETE'
      });

      const data = await response.json();

      if (data.success) {
        alert('Prompt deleted successfully!');
        await loadPromptsList();
        await loadPrompts(); // Refresh the header dropdown too
        E.promptEditor.style.display = 'none';
      } else {
        alert('Failed to delete prompt: ' + (data.error || 'Unknown error'));
      }
    } catch (err) {
      console.error('Failed to delete prompt:', err);
      alert('Failed to delete prompt: ' + err.message);
    }
  }

  async function loadHistory() {
    const js = await getJSON('/api/chat/history');
    state.history = js.chats || [];
    state.currentChatFile = js.current_chat || state.currentChatFile;
    renderHistory();
  }

  async function loadMessages() {
    // If you have an endpoint, use it; otherwise messages are updated by send/stream.
    try {
      const js = await getJSON('/api/chat/messages');
      state.messages = js.messages || [];
    } catch { /* optional */ }
    renderMessages();
  }

  async function loadDocs() {
    // Using the endpoint your logs showed
    const js = await getJSON('/api/context/docs');
    state.docs = js.documents || js.docs || [];
    // set selection set
    state.selectedDocIds = new Set(state.docs.filter(d => d.selected).map(d => d.doc_id));
    renderDocChips();
  }

  async function updateModelMaxTokensLabel(provider, model) {
    try {
      // Fetch model settings to get max_tokens for the current model
      const settingsRes = await fetch(`/api/models/${encodeURIComponent(provider)}/settings`);
      const settingsData = await settingsRes.json();
      
      if (settingsData.success && settingsData.settings && settingsData.settings[model]) {
        const modelSettings = settingsData.settings[model];
        const maxTokens = modelSettings.max_tokens;
        
        if (maxTokens) {
          // Format as 8k, 32k, 128k, etc.
          let maxTokensLabel = '';
          if (maxTokens >= 1000) {
            maxTokensLabel = `Model max: ${Math.round(maxTokens / 1000)}k tokens`;
          } else {
            maxTokensLabel = `Model max: ${maxTokens} tokens`;
          }
          
          if (E.modelMaxTokensLabel) {
            E.modelMaxTokensLabel.textContent = maxTokensLabel;
          }
        } else {
          // Fallback to provider default
          const providerDefault = state.config ? state.config[`${provider}_max_tokens`] : 4096;
          let maxTokensLabel = '';
          if (providerDefault >= 1000) {
            maxTokensLabel = `Model max: ${Math.round(providerDefault / 1000)}k tokens`;
          } else {
            maxTokensLabel = `Model max: ${providerDefault} tokens`;
          }
          
          if (E.modelMaxTokensLabel) {
            E.modelMaxTokensLabel.textContent = maxTokensLabel;
          }
        }
      } else {
        // Fallback to provider default
        const providerDefault = state.config ? state.config[`${provider}_max_tokens`] : 4096;
        let maxTokensLabel = '';
        if (providerDefault >= 1000) {
          maxTokensLabel = `Model max: ${Math.round(providerDefault / 1000)}k tokens`;
        } else {
          maxTokensLabel = `Model max: ${providerDefault} tokens`;
        }
        
        if (E.modelMaxTokensLabel) {
          E.modelMaxTokensLabel.textContent = maxTokensLabel;
        }
      }
    } catch (err) {
      console.error('Failed to update model max tokens label:', err);
      if (E.modelMaxTokensLabel) {
        E.modelMaxTokensLabel.textContent = '';
      }
    }
  }

  // ====== Actions ======
  async function newChat() {
    try {
      const js = await postJSON('/api/chat/new', {});
      // If backend ever returns success:false, postJSON throws, caught below
      state.currentChatFile = js.filename || js.chat_file || state.currentChatFile;
      state.messages = [];
      renderChatTitle();
      renderMessages();
      await loadHistory();

      // Simple sanity check: make sure the new filename appears in history
      if (!state.history.some(c => c.filename === state.currentChatFile)) {
        console.warn('New chat created but not found in history:', state.currentChatFile);
        alert('New chat created, but it did not appear in the history. Try refreshing the page.');
      }
    } catch (e) {
      alert('Failed to create new chat: ' + String(e));
    }
  }

  async function loadChat(filename) {
    const js = await getJSON(`/api/chat/load/${encodeURIComponent(filename)}`);
    if (js.success === false) return alert(js.error || 'Failed to load chat');
    state.currentChatFile = filename;
    state.messages = js.messages || [];
    renderChatTitle();
    renderHistory();
    renderMessages();

    // Update branch detail panel if this chat has a branch record
    await updateBranchDetailPanel(filename);
    
    // Check if agent panel should be shown (work_order branches only)
    checkAgentApplicable();
    
    // Check if artifact panel should be shown (all branches)
    checkArtifactApplicable();
  }

  async function updateBranchDetailPanel(filename) {
    // Try to find branch record for this chat
    const res = await fetch('/api/branches');
    const data = await res.json();
    if (!data.success) return;
    
    const branches = data.branches || [];
    const branch = branches.find(b => b.filename === filename);
    
    if (!branch) {
      E.branchDetailPanel.style.display = 'none';
      return;
    }
    
    // Show panel
    E.branchDetailPanel.style.display = 'flex';
    
    // Update type icon and label
    const typeIcons = { 'domain': '📁', 'work_order': '📋', 'chat': '💬' };
    E.branchDetailTypeIcon.textContent = typeIcons[branch.type] || '💬';
    E.branchDetailTypeLabel.textContent = branch.type.replace('_', ' ');
    
    // Update status
    E.branchDetailStatus.textContent = branch.status || 'active';
    E.branchDetailStatus.className = 'branch-status-badge status-' + (branch.status || 'active');
    
    // Update goal (for work orders)
    const meta = branch.metadata ? (typeof branch.metadata === 'string' ? JSON.parse(branch.metadata) : branch.metadata) : {};
    if (meta.goal && branch.type === 'work_order') {
      E.branchDetailGoal.textContent = meta.goal;
      E.branchDetailGoalContainer.style.display = 'flex';
    } else {
      E.branchDetailGoalContainer.style.display = 'none';
    }
    
    // Update edges
    if (branch.edges && branch.edges.length > 0) {
      E.branchEdgesList.innerHTML = branch.edges.map(e => 
        `<span class="branch-edge-chip">${e.type}: ${e.to_branch || e.from_branch}</span>`
      ).join('');
      E.branchDetailEdges.style.display = 'flex';
    } else {
      E.branchDetailEdges.style.display = 'none';
    }
  }

  async function renameChat(filename) {
    const newTitle = prompt('Rename chat to:', filename.replace(/\.json$/,''));
    if (!newTitle) return;

    // Immediately update the UI optimistically
    const oldTitle = state.history.find(chat => chat.filename === filename)?.title;
    const chatIndex = state.history.findIndex(chat => chat.filename === filename);
    if (chatIndex !== -1) {
      state.history[chatIndex].title = newTitle;
      renderHistory();
      renderChatTitle();
    }

    // Send the rename request in the background
    const js = await postJSON('/api/chat/rename', { filename, new_title: newTitle });
    if (js.success === false) {
      // Revert the optimistic update on failure
      if (chatIndex !== -1 && oldTitle) {
        state.history[chatIndex].title = oldTitle;
        renderHistory();
        renderChatTitle();
      }
      return alert(js.error || 'Rename failed');
    }

    // Update with the actual filename from backend if different
    if (js.filename && js.filename !== filename) {
      state.currentChatFile = js.filename;
      if (chatIndex !== -1) {
        state.history[chatIndex].filename = js.filename;
        renderHistory();
        renderChatTitle();
      }
    }
  }

  async function deleteChat(filename) {
    if (!confirm('Delete this chat?')) return;
    const r = await fetch(`/api/chat/delete/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    const js = await r.json().catch(() => ({}));
    if (js && js.success === false) return alert(js.error || 'Delete failed');
    if (state.currentChatFile === filename) {
      state.currentChatFile = null;
      state.messages = [];
      renderChatTitle();
      renderMessages();
    }
    await loadHistory();
  }

  async function duplicateChat(filename) {
    try {
      const r = await fetch(`/api/chat/duplicate/${encodeURIComponent(filename)}`, { method: 'POST' });
      const js = await r.json().catch(() => ({}));
      if (js && js.success === false) return alert(js.error || 'Duplicate failed');
      await loadHistory();
      if (js.filename) {
        await loadChat(js.filename);
      }
    } catch (e) {
      alert('Duplicate failed: ' + String(e));
    }
  }

  // ====== Context Overflow Warning ======
  function showOverflowWarning(check) {
    const msg = `Input tokens (~${fmt(check.total_tokens)}) exceed available context ` +
      `(${fmt(check.available_input)} of ${fmt(check.context_window)} after reserving ` +
      `${fmt(check.max_output_tokens)} for output). ` +
      `Over by ~${fmt(check.overflow_amount)} tokens. ` +
      `Breakdown: system ${fmt(check.breakdown.system_prompt)}, ` +
      `conversation ${fmt(check.breakdown.conversation)}, ` +
      `docs ${fmt(check.breakdown.documents)}, ` +
      `new message ${fmt(check.breakdown.new_message)}.`;
    E.overflowMessage.textContent = msg;
    show(E.contextOverflowWarning);
  }

  function hideOverflowWarning() {
    hide(E.contextOverflowWarning);
    state._pendingSendText = null;
    state._pendingSendPayload = null;
    state._overflowCheck = null;
  }

  async function autoTrimToFit() {
    // Open context bar if not open
    if (!state.contextBarOpen) {
      state.contextBarOpen = true;
      E.contextControls.style.display = 'flex';
    }
    // Select all messages initially
    state.selectedMessageIdx = new Set(state.messages.map((_, i) => i));
    state.selectedSummaryIdx = new Set(
      state.messages.map((m, i) => m.summary ? i : -1).filter(i => i >= 0)
    );

    // Deselect from oldest until we fit
    const check = state._overflowCheck;
    if (!check) return;
    let tokensToFree = check.overflow_amount;

    for (let i = 0; i < state.messages.length && tokensToFree > 0; i++) {
      const m = state.messages[i];
      if (!m) continue;
      // Estimate tokens for what this message contributes
      let msgTokens;
      if (m.summary && state.selectedSummaryIdx.has(i)) {
        msgTokens = estimateTokensJS(m.summary);
        state.selectedSummaryIdx.delete(i);
      } else {
        msgTokens = estimateTokensJS(m.content || '');
      }
      state.selectedMessageIdx.delete(i);
      tokensToFree -= msgTokens;
    }

    renderMessages();
    await updateContextTokenSummary();
  }

  async function autoSummarizeToFit() {
    const check = state._overflowCheck;
    if (!check) return;

    // Open context bar
    if (!state.contextBarOpen) {
      state.contextBarOpen = true;
      E.contextControls.style.display = 'flex';
      state.selectedMessageIdx = new Set(state.messages.map((_, i) => i));
      state.selectedSummaryIdx = new Set(
        state.messages.map((m, i) => m.summary ? i : -1).filter(i => i >= 0)
      );
    }

    let tokensFreed = 0;
    const target = check.overflow_amount;

    for (let i = 0; i < state.messages.length && tokensFreed < target; i++) {
      const m = state.messages[i];
      if (!m || m.role === 'system') continue;
      if (m.summary) {
        // Already summarized - just select the summary
        state.selectedSummaryIdx.add(i);
        const saved = estimateTokensJS(m.content || '') - estimateTokensJS(m.summary);
        if (saved > 0) tokensFreed += saved;
        continue;
      }
      // Summarize this message
      try {
        const res = await postJSON('/api/chat/summarize', { content: m.content });
        if (res.success && res.summary) {
          await postJSON('/api/chat/add_summary', {
            message_index: i,
            summary: res.summary
          });
          m.summary = res.summary;
          state.selectedSummaryIdx.add(i);
          const saved = estimateTokensJS(m.content || '') - estimateTokensJS(res.summary);
          if (saved > 0) tokensFreed += saved;
        }
      } catch (e) {
        console.warn('Failed to summarize message', i, e);
      }
    }

    renderMessages();
    await updateContextTokenSummary();
  }

  async function sendMessage() {
    // Ensure a chat exists before sending
    if (!state.currentChatFile) {
      await newChat();
      if (!state.currentChatFile) {
        alert("Failed to create a new chat. Please try again.");
        return;
      }
    }

    if (state.streaming) return;
    const text = E.userInput.value.trim();
    if (!text) return;

    // optimistic user message
    const now = new Date().toLocaleTimeString();
    state.messages.push({ role: 'user', content: text, timestamp: now });
    renderMessages();
    E.userInput.value = '';

    // build payload
    const selectedPromptId = E.systemPromptSelect.value;
    let systemPrompt = '';
    
    // If dropdown value is non-empty, include that template's content as the system prompt
    // If dropdown value is empty ("None"), omit a global prompt; backend will pull system_prompt from model_settings
    if (selectedPromptId) {
      const selectedPrompt = state.prompts.find(p => p.id === selectedPromptId);
      if (selectedPrompt) {
        systemPrompt = selectedPrompt.body;
      }
    }
    // If selectedPromptId is empty (None), systemPrompt remains empty and backend will use model-specific prompt

    const payload = {
      message: text,
      provider: E.providerHeader.value,
      model: E.modelHeader.value,
      system_prompt: systemPrompt
    };

    // Only send selection when the context bar is open.
    // If closed, omit this field so backend uses full conversation.
    if (state.contextBarOpen) {
      payload.selected_context = Array.from(state.selectedMessageIdx);
      payload.selected_summaries = Array.from(state.selectedSummaryIdx);
    }

    // --- Context overflow check (pre-send) ---
    if (!state._skipOverflowCheck) {
      try {
        const checkPayload = {
          message: text,
          context_bar_open: state.contextBarOpen,
        };
        if (state.contextBarOpen) {
          checkPayload.selected_context = Array.from(state.selectedMessageIdx);
          checkPayload.selected_summaries = Array.from(state.selectedSummaryIdx);
        }
        const check = await postJSON('/api/chat/context-check', checkPayload);
        if (check.overflow) {
          // Show warning and wait for user choice
          state._pendingSendText = text;
          state._pendingSendPayload = payload;
          state._overflowCheck = check;
          showOverflowWarning(check);
          // Remove the optimistic user message we added
          state.messages.pop();
          renderMessages();
          E.userInput.value = text; // put text back
          return;
        }
      } catch (e) {
        console.warn('Context check failed, sending anyway:', e);
      }
    }
    state._skipOverflowCheck = false;

    const js = await postJSON('/api/chat/send', payload).catch(err => ({ success:false, error: String(err) }));
    if (js.success === false) {
      alert(js.error || 'Send failed');
      return;
    }
    state.currentChatFile = js.current_chat_file || state.currentChatFile;
    state.streamingMessageId = js.message_id;  // Store for cancel

    // Check if the response includes a new filename (auto-renamed chat)
    if (js.filename) {
      state.currentChatFile = js.filename;
      await loadHistory();   // refresh sidebar from backend
      renderChatTitle();     // update header title
    }

    // prepare assistant streaming node
    const ts = new Date().toLocaleTimeString();
    state.messages.push({ role: 'assistant', content: '', timestamp: ts, model: state.currentModel });
    renderMessages();
    const idx = state.messages.length - 1;

    // Get the bubble reference immediately after rendering
    const getBubble = () => E.messages.querySelector(`.message.assistant[data-index="${idx}"] .message-bubble`);
    state.currentStreamingBubble = getBubble();

    if (!state.currentStreamingBubble) {
      console.warn('Could not find bubble for streaming index:', idx);
    }

    // Cancel button visible while streaming
    show(E.cancelBtn);
    hide(E.sendBtn);
    state.streaming = true;

    // SSE
    const src = new EventSource(`/api/chat/stream/${js.message_id}`);
    state.streamSource = src;

    src.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === 'model') {
          state.messages[idx].model = data.content || '';
          renderMessages();
          state.currentStreamingBubble = E.messages.querySelector(`.message.assistant[data-index="${idx}"] .message-bubble`);
        } else if (data.type === 'content') {
          state.messages[idx].content += data.content || '';
          if (state.currentStreamingBubble) {
            try {
              state.currentStreamingBubble.innerHTML = marked.parse(state.messages[idx].content);
            } catch {
              state.currentStreamingBubble.textContent = state.messages[idx].content;
            }
            E.messages.scrollTop = E.messages.scrollHeight;
          }
        } else if (data.type === 'tool_start') {
          // Tool execution starting - append tool chip
          appendToolChip(idx, data.name, data.args, 'running');
        } else if (data.type === 'tool_end') {
          // Tool execution complete - update chip
          updateToolChip(idx, data.name, data.result);
        } else if (data.type === 'title') {
          const ti = state.history.findIndex(c => c.filename === (data.filename || state.currentChatFile));
          if (ti !== -1) state.history[ti].title = data.title;
          renderChatTitle();
          renderHistory();
        } else if (data.type === 'complete') {
          src.close();
          state.streaming = false;
          hide(E.cancelBtn);
          show(E.sendBtn);
          state.currentStreamingBubble = null;
          renderMessages();
          afterMessageSettled();
        } else if (data.type === 'error') {
          src.close();
          state.streaming = false;
          hide(E.cancelBtn);
          show(E.sendBtn);
          state.currentStreamingBubble = null;
          state.messages[idx].content += `\n\n${data.content || 'Error.'}`;
          renderMessages();
        }
      } catch { /* ignore bad chunks */ }
    };

    src.onerror = () => {
      try { src.close(); } catch {}
      state.streaming = false;
      hide(E.cancelBtn);
      show(E.sendBtn);
    };
  }

  async function cancelGeneration() {
    if (!state.streaming || !state.streamingMessageId) return;
    await fetch('/api/chat/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message_id: state.streamingMessageId })
    });
    // Force-close the EventSource and reset UI in case the server
    // never sends a complete/error event back
    if (state.streamSource) {
      try { state.streamSource.close(); } catch {}
      state.streamSource = null;
    }
    state.streaming = false;
    state.currentStreamingBubble = null;
    hide(E.cancelBtn);
    show(E.sendBtn);
    renderMessages();
  }

  async function summarizeMessage(index) {
  const msg = state.messages[index];
  if (!msg || !msg.content) return;

  try {
    const r = await fetch("/api/chat/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: msg.content,
        provider: state.currentProvider,          // <--
        model: state.currentModel                 // <--
      })
    });
    const data = await r.json();
    if (!r.ok || data.success === false) throw new Error(data.error || 'Summarize failed');

    // Persist summary in the chat file so it survives reloads
    await fetch('/api/chat/add_summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message_index: index,
        summary: data.summary || '',
        summary_model: state.currentModel            // <— add this
      })
    });

    // Reflect in UI immediately
    state.messages[index].summary = data.summary || '';
    state.messages[index].summary_model = state.currentModel;  // <— add this
    renderMessages();
  } catch (e) {
    alert('Summarize failed: ' + String(e));
  }
}


  async function deleteMessage(index) {
    if (state.currentChatFile == null) return alert('No chat opened.');
    const js = await fetch('/api/chat/delete_message', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: state.currentChatFile, message_index: index })
    });
    const res = await js.json().catch(() => ({}));
    if (res && res.success === false) return alert(res.error || 'Delete failed');

    // Reload messages
    await loadMessages();
  }

  async function branchFromMessage(index) {
    // Add debug logging at the very beginning as requested
    console.log('DEBUG: branchFromMessage called with index:', index);
    console.log('DEBUG: state.currentChatFile:', state.currentChatFile);
    console.log('DEBUG: state.messages length:', state.messages.length);
    if (state.messages[index]) {
      console.log('DEBUG: message at index exists, role:', state.messages[index].role);
    }
    
    if (state.currentChatFile == null) return alert('No chat opened.');
    const sel = window.getSelection ? window.getSelection().toString().trim() : '';
    const body = { parent_chat_id: state.currentChatFile, parent_message_index: index };
    if (sel) body.selected_text = sel;

    const js = await postJSON('/api/chat/create_side_chat', body).catch(e => ({ success:false, error:String(e) }));
    if (js.success === false) return alert(js.error || 'Failed to create side chat');

    const fname = js.filename || `${js.side_chat_id}.json`;
    await loadChat(fname);
    await loadHistory();
  }
// ===== Streaming helpers =====
function appendStreamingMessage(role, initial = "", atIndex = null, isSummary = false) {
  const ts = new Date().toLocaleTimeString();
  const msg = { role, content: initial, timestamp: ts, model: state.currentModel, isSummary };
  if (atIndex == null) {
    state.messages.push(msg);
    atIndex = state.messages.length - 1;
  } else {
    state.messages.splice(atIndex, 0, msg);
  }
  renderMessages();
  const row = E.messages.querySelector(`.message[data-index="${atIndex}"]`);
  const bubble = row?.querySelector(".message-bubble");
  return { box: row, bubble };
}

function streamAssistant(messageId, bubbleEl, index, isSummary = false) {
  state.streaming = true;
  const src = new EventSource(`/api/chat/stream/${messageId}`);
  state.streamSource = src;

  src.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === "model") {
        state.messages[index].model = data.content || "";
      } else if (data.type === "content") {
        state.messages[index].content += data.content || "";
        bubbleEl.textContent += data.content || "";
        E.messages.scrollTop = E.messages.scrollHeight;
      } else if (data.type === "complete") {
        src.close();
        state.streaming = false;
      } else if (data.type === "error") {
        src.close();
        state.streaming = false;
        state.messages[index].content += "\n\n" + (data.content || "Error");
        bubbleEl.textContent = state.messages[index].content;
      }
    } catch {}
  };

  src.onerror = () => {
    src.close();
    state.streaming = false;
  };
}



  // ====== Context actions ======
  async function uploadDocument(file) {
    const fd = new FormData();
    fd.append('file', file);

    show(E.uploadProgressBar);
    setProgress(0);
    E.uploadProgressText.textContent = `Uploading ${file.name}...`;
    // Fake smooth progress (fetch doesn't give progress)
    let p = 5;
    const timer = setInterval(() => { p = Math.min(95, p + 5); setProgress(p); }, 120);

    try {
      const r = await fetch('/api/context/docs/upload', { method: 'POST', body: fd });
      const js = await r.json().catch(() => ({}));
      setProgress(100);
      if (js && js.success === false) alert(js.error || 'Upload failed');
      await loadDocs();
      await updateContextTokenSummary();
    } finally {
      clearInterval(timer);
      await sleep(400);
      hide(E.uploadProgressBar);
      setProgress(0);
      E.uploadProgressText.textContent = 'Uploading...';
      E.documentUpload.value = '';
    }
  }

  async function selectAllDocs(flag) {
    for (const d of state.docs) {
      if (!!d.selected !== flag) {
        await fetch('/api/context/docs/select', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ doc_id: d.doc_id, selected: flag })
        });
      }
    }
    await loadDocs();
    await updateContextTokenSummary();
  }

  // ====== Menus handlers ======
  function bindMenus() {
    // Chat menu
    E.menuLoadChat.addEventListener('click', () => {
      const f = state.ctxMenuTarget.filename;
      E.chatContextMenu.style.display = 'none';
      if (f) loadChat(f);
    });
    E.menuRenameChat.addEventListener('click', () => {
      const f = state.ctxMenuTarget.filename;
      E.chatContextMenu.style.display = 'none';
      if (f) renameChat(f);
    });
    E.menuDuplicateChat.addEventListener('click', () => {
      const f = state.ctxMenuTarget.filename;
      E.chatContextMenu.style.display = 'none';
      if (f) duplicateChat(f);
    });
    E.menuDeleteChat.addEventListener('click', () => {
      const f = state.ctxMenuTarget.filename;
      E.chatContextMenu.style.display = 'none';
      if (f) deleteChat(f);
    });

    // Message menu
    E.menuSummarize.addEventListener('click', () => {
      const i = state.ctxMenuTarget.index;
      E.msgContextMenu.style.display = 'none';
      if (typeof i === 'number') summarizeMessage(i);
    });
    E.menuCopySelected.addEventListener('click', async () => {
      E.msgContextMenu.style.display = 'none';
      const sel = window.getSelection ? window.getSelection().toString().trim() : '';
      if (sel) {
        try { await navigator.clipboard.writeText(sel); } catch {}
      }
    });
    E.menuCopy.addEventListener('click', async () => {
      const i = state.ctxMenuTarget.index;
      E.msgContextMenu.style.display = 'none';
      if (typeof i === 'number') {
        const txt = state.messages[i]?.content || '';
        try { await navigator.clipboard.writeText(txt); } catch {}
      }
    });
    E.menuDelete.addEventListener('click', () => {
      const i = state.ctxMenuTarget.index;
      E.msgContextMenu.style.display = 'none';
      if (typeof i === 'number') deleteMessage(i);
    });
    E.menuBranchFull.addEventListener('click', () => {
      const i = state.ctxMenuTarget.index;
      E.msgContextMenu.style.display = 'none';
      if (typeof i === 'number') branchFromMessage(i);
    });
    E.menuBranchSelected.addEventListener('click', () => {
      const i = state.ctxMenuTarget.index;
      E.msgContextMenu.style.display = 'none';
      if (typeof i === 'number') branchFromMessage(i);
    });

    // Branch context menu (Phase 2)
    E.branchForkMenuItem.addEventListener('click', async () => {
      const branch = state.ctxMenuTarget.branch;
      E.branchContextMenu.style.display = 'none';
      if (branch) {
        const name = prompt('Fork branch name:');
        if (name) {
          await forkBranch(branch.id, null, name);
        }
      }
    });

    E.branchNewWorkOrderMenuItem.addEventListener('click', async () => {
      const branch = state.ctxMenuTarget.branch;
      E.branchContextMenu.style.display = 'none';
      if (branch && branch.type === 'domain') {
        const name = prompt('Work Order name:');
        if (name) {
          const goal = prompt('Work Order goal:') || '';
          await createBranch('work_order', name, branch.id, goal);
        }
      }
    });

    E.branchStatusMenuItem.addEventListener('click', (e) => {
      const branch = state.ctxMenuTarget.branch;
      // Position status submenu next to the status menu item
      const rect = E.branchStatusMenuItem.getBoundingClientRect();
      E.statusSubMenu.style.display = 'block';
      E.statusSubMenu.style.left = (rect.right + 4) + 'px';
      E.statusSubMenu.style.top = rect.top + 'px';
      
      // Handle status selection
      const statusItems = E.statusSubMenu.querySelectorAll('.context-menu-item');
      statusItems.forEach(item => {
        item.onclick = async (ev) => {
          ev.stopPropagation();
          const newStatus = item.getAttribute('data-status');
          if (branch && newStatus) {
            await transitionBranchStatus(branch.id, newStatus);
          }
          E.statusSubMenu.style.display = 'none';
          E.branchContextMenu.style.display = 'none';
        };
      });
    });

    E.branchMergeMenuItem.addEventListener('click', async () => {
      const branch = state.ctxMenuTarget.branch;
      E.branchContextMenu.style.display = 'none';
      if (branch) {
        // Get list of branches to merge into
        const res = await fetch('/api/branches');
        const data = await res.json();
        if (data.success) {
          const branches = data.branches || [];
          const options = branches
            .filter(b => b.id !== branch.id)
            .map(b => `${b.id}|${b.title || b.name || 'Untitled'}`)
            .join('\n');
          const selected = prompt(`Merge "${branch.name || branch.title}" into:\n(Enter branch ID|name from list below)\n\n${options}`);
          if (selected) {
            const [targetId, targetName] = selected.split('|');
            if (targetId) {
              const notes = prompt('Approval notes (optional):') || '';
              const res = await fetch(`/api/branches/${branch.id}/merge`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target_id: targetId, notes })
              });
              const result = await res.json();
              if (!result.success) {
                alert('Merge failed: ' + (result.error || 'Unknown error'));
              }
            }
          }
        }
      }
    });

    E.branchRenameMenuItem.addEventListener('click', () => {
      const branch = state.ctxMenuTarget.branch;
      E.branchContextMenu.style.display = 'none';
      if (branch) {
        const newName = prompt('Rename branch:', branch.name || branch.title);
        if (newName) {
          fetch(`/api/branches/${branch.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: newName })
          }).then(r => r.json()).then(data => {
            if (data.success) {
              loadBranchTree();
              renderHistory();
            }
          });
        }
      }
    });

    E.branchArchiveMenuItem.addEventListener('click', async () => {
      const branch = state.ctxMenuTarget.branch;
      E.branchContextMenu.style.display = 'none';
      if (branch) {
        if (confirm(`Archive branch "${branch.name || branch.title}"?`)) {
          await deleteBranch(branch.id);
        }
      }
    });

    // Branch detail panel buttons
    E.branchDetailStatusBtn.addEventListener('click', async () => {
      // Get current branch from panel
      const res = await fetch('/api/branches');
      const data = await res.json();
      if (!data.success) return;
      
      const branches = data.branches || [];
      const branch = branches.find(b => b.filename === state.currentChatFile);
      if (!branch) return;
      
      // Show status options
      const statuses = ['active', 'review', 'merged', 'archived'];
      const current = branch.status || 'active';
      const options = statuses.map(s => `${s}${s === current ? ' (current)' : ''}`).join('\n');
      const newStatus = prompt(`Set status:\n${options}`, current);
      if (newStatus && statuses.includes(newStatus)) {
        await transitionBranchStatus(branch.id, newStatus);
        await updateBranchDetailPanel(state.currentChatFile);
      }
    });

    E.branchDetailForkBtn.addEventListener('click', async () => {
      // Get current branch from panel
      const res = await fetch('/api/branches');
      const data = await res.json();
      if (!data.success) return;
      
      const branches = data.branches || [];
      const branch = branches.find(b => b.filename === state.currentChatFile);
      if (!branch) return;
      
      const name = prompt('Fork branch name:');
      if (name) {
        await forkBranch(branch.id, null, name);
        await updateBranchDetailPanel(state.currentChatFile);
      }
    });

    // Agent panel buttons (Phase 4)
    E.agentStartBtn.addEventListener('click', () => {
      E.agentGoalInput.style.display = 'flex';
      E.agentGoalText.focus();
    });

    E.agentConfirmStart.addEventListener('click', () => {
      const goal = E.agentGoalText.value.trim();
      if (goal && state.currentBranchId) {
        startAgent(state.currentBranchId, goal);
      }
    });

    E.agentCancelStart.addEventListener('click', () => {
      E.agentGoalInput.style.display = 'none';
      E.agentGoalText.value = '';
    });

    E.agentPauseBtn.addEventListener('click', () => {
      if (state.currentBranchId) {
        pauseAgent(state.currentBranchId);
      }
    });

    E.agentResumeBtn.addEventListener('click', () => {
      if (state.currentBranchId) {
        resumeAgent(state.currentBranchId);
      }
    });

    E.agentStopBtn.addEventListener('click', () => {
      if (state.currentBranchId && confirm('Stop the running agent?')) {
        stopAgent(state.currentBranchId);
      }
    });

    // Artifact panel bindings (Phase 5)
    E.artifactModalClose.addEventListener('click', closeSendArtifact);
    E.artifactSendCancel.addEventListener('click', closeSendArtifact);
    E.artifactSendConfirm.addEventListener('click', sendArtifact);
    
    // Close modal when clicking outside
    E.artifactSendModal.addEventListener('click', (e) => {
      if (e.target === E.artifactSendModal) {
        closeSendArtifact();
      }
    });
  }

  // ====== Settings bindings ======
  // --- Settings panel overlay helpers ---
  const ALL_SETTINGS_PANELS = () => [
    E.settingsPanel, E.promptsSettingsPanel,
    E.systemSettingsPanel, E.openrouterBrowsePanel,
    E.folderContextPanel,
  ];

  function openSettingsPanel(panel) {
    // Close any other open panels first
    ALL_SETTINGS_PANELS().forEach(p => p.classList.remove('open'));
    panel.classList.add('open');
    E.settingsOverlay.classList.add('open');
  }

  function closeAllSettingsPanels() {
    ALL_SETTINGS_PANELS().forEach(p => p.classList.remove('open'));
    E.settingsOverlay.classList.remove('open');
  }

  function bindSettingsPanels() {
    // Click overlay to close all settings panels
    E.settingsOverlay.addEventListener('click', () => closeAllSettingsPanels());

    // Settings menu toggle
    E.settingsMenuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      E.settingsDropdown.classList.toggle('open');
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
      if (!E.settingsMenuBtn.contains(e.target) && !E.settingsDropdown.contains(e.target)) {
        E.settingsDropdown.classList.remove('open');
      }
    });

    // Open Model Settings
    E.openModelSettingsBtn.addEventListener('click', async () => {
      E.settingsDropdown.classList.remove('open');
      openSettingsPanel(E.settingsPanel);

      // When opening, refresh the model list and load settings for current provider
      const provider = E.providerSelect.value;
      await refreshModelsSettings(provider);
      
      // After refreshing models, load settings for the first/selected model
      if (E.modelSettingsSelect.options.length > 0) {
        const model = E.modelSettingsSelect.value;
        if (model) {
          await loadModelSettings(provider, model);
          
          // Show/hide remove button - any model can be removed
          const canRemove = await isRemovableModel(provider, model);
          E.removeModelBtn.style.display = canRemove ? 'block' : 'none';
        } else {
          // No models available
          E.removeModelBtn.style.display = 'none';
        }
      } else {
        E.removeModelBtn.style.display = 'none';
      }
    });
    
    E.closeSettingsBtn.addEventListener('click', () => closeAllSettingsPanels());

    // Open System Prompts
    E.openPromptsSettingsBtn.addEventListener('click', async () => {
      E.settingsDropdown.classList.remove('open');
      openSettingsPanel(E.promptsSettingsPanel);
      await loadPromptsList();
    });

    E.closePromptsSettingsBtn.addEventListener('click', () => {
      closeAllSettingsPanels();
      E.promptEditor.style.display = 'none';
    });

    // Open Appearance Settings  
    E.openAppearanceSettingsBtn.addEventListener('click', () => {
      E.settingsDropdown.classList.remove('open');
      openSettingsPanel(E.systemSettingsPanel);
    });

    E.closeSystemSettingsBtn.addEventListener('click', () => closeAllSettingsPanels());

    // ===== Browse Models Panel (multi-provider) =====
    let _browseDebounce = null;
    const BROWSE_PROVIDERS = ['openrouter', 'groq', 'google', 'mistral', 'llamacpp'];
    state.browseCatalog = [];  // catalog for currently-selected browse provider

    function getBrowseProvider() {
      return E.browseProviderSelect.value || 'openrouter';
    }

    function browseProviderHasPricing(provider) {
      return provider === 'openrouter';
    }

    function formatContextLength(ctx) {
      if (!ctx) return '?';
      if (ctx >= 1000000) return (ctx / 1000000).toFixed(1) + 'M';
      if (ctx >= 1000) return Math.round(ctx / 1000) + 'k';
      return String(ctx);
    }

    function formatPrice(priceStr) {
      const v = parseFloat(priceStr);
      if (!v || v === 0) return null;
      const perM = v * 1000000;
      if (perM < 0.01) return '<$0.01/M';
      return '$' + perM.toFixed(2) + '/M';
    }

    async function loadBrowseCatalog() {
      const provider = getBrowseProvider();
      const hasPricing = browseProviderHasPricing(provider);
      // Show/hide free-only checkbox and price sort based on provider
      E.browseFreeOnlyLabel.style.display = hasPricing ? '' : 'none';
      E.browseFreeOnly.checked = false;
      // Show/hide price sort option
      const priceOpt = E.browseSortSelect.querySelector('option[value="price"]');
      if (priceOpt) priceOpt.style.display = hasPricing ? '' : 'none';
      if (!hasPricing && E.browseSortSelect.value === 'price') E.browseSortSelect.value = 'name';

      let catalog = await getJSON(`/api/browse/${provider}/catalog`);
      if (!catalog || !Array.isArray(catalog) || catalog.length === 0) {
        E.refreshCatalogBtn.textContent = 'Refreshing...';
        E.refreshCatalogBtn.disabled = true;
        try {
          const res = await postJSON(`/api/browse/${provider}/refresh`, {});
          if (res.success) {
            catalog = await getJSON(`/api/browse/${provider}/catalog`);
          }
        } finally {
          E.refreshCatalogBtn.textContent = 'Refresh Catalog';
          E.refreshCatalogBtn.disabled = false;
        }
      }
      state.browseCatalog = Array.isArray(catalog) ? catalog : [];
      renderBrowseList();
    }

    async function getSelectedBrowseModels(provider) {
      const data = await getJSON(`/api/models/${provider}`);
      return new Set(data.models || []);
    }

    async function renderBrowseList() {
      const provider = getBrowseProvider();
      const hasPricing = browseProviderHasPricing(provider);
      const search = (E.browseModelSearch.value || '').toLowerCase();
      const freeOnly = hasPricing && E.browseFreeOnly.checked;
      const sort = E.browseSortSelect.value;
      const selected = await getSelectedBrowseModels(provider);

      let list = state.browseCatalog.filter(m => {
        if (freeOnly && !m.is_free) return false;
        if (search) {
          return m.id.toLowerCase().includes(search) || (m.name || '').toLowerCase().includes(search);
        }
        return true;
      });

      if (sort === 'name') list.sort((a, b) => a.id.localeCompare(b.id));
      else if (sort === 'context') {
        if (provider === 'llamacpp') list.sort((a, b) => (b.size_gb || 0) - (a.size_gb || 0));
        else list.sort((a, b) => (b.context_length || 0) - (a.context_length || 0));
      }
      else if (sort === 'price') list.sort((a, b) => parseFloat(a.prompt_price || 0) - parseFloat(b.prompt_price || 0));

      E.modelBrowseList.innerHTML = '';
      list.forEach(m => {
        const row = el('div');
        row.className = 'model-browse-item';

        const cb = el('input');
        cb.type = 'checkbox';
        cb.checked = selected.has(m.id);
        cb.addEventListener('change', async () => {
          await postJSON(`/api/browse/${provider}/toggle`, { model: m.id, checked: cb.checked });
          if (state.currentProvider === provider) {
            await refreshModelsHeader(provider, state.currentModel);
          }
          updateBrowseSelectedCount();
        });

        const info = el('div');
        info.className = 'model-browse-info';
        const idLine = el('div');
        idLine.className = 'model-browse-id';
        idLine.textContent = m.id;
        idLine.title = m.id;
        info.appendChild(idLine);
        if (m.name && m.name !== m.id) {
          const nameLine = el('div');
          nameLine.className = 'model-browse-name';
          nameLine.textContent = m.name;
          info.appendChild(nameLine);
        }

        const ctxBadge = el('span');
        ctxBadge.className = 'model-badge';
        if (provider === 'llamacpp' && m.size_gb) {
          ctxBadge.textContent = m.size_gb + ' GB';
        } else {
          ctxBadge.textContent = formatContextLength(m.context_length);
        }

        row.appendChild(cb);
        row.appendChild(info);
        row.appendChild(ctxBadge);

        if (hasPricing) {
          const priceBadge = el('span');
          if (m.is_free) {
            priceBadge.className = 'model-badge free';
            priceBadge.textContent = 'Free';
          } else {
            priceBadge.className = 'model-badge';
            priceBadge.textContent = formatPrice(m.prompt_price) || '?';
          }
          row.appendChild(priceBadge);
        }

        E.modelBrowseList.appendChild(row);
      });

      updateBrowseSelectedCount();
    }

    async function updateBrowseSelectedCount() {
      const provider = getBrowseProvider();
      const selected = await getSelectedBrowseModels(provider);
      E.browseSelectedCount.textContent = selected.size + ' model' + (selected.size !== 1 ? 's' : '') + ' selected';
    }

    E.openBrowseModelsBtn.addEventListener('click', async () => {
      E.settingsDropdown.classList.remove('open');
      // Auto-select current provider if browseable
      if (BROWSE_PROVIDERS.includes(state.currentProvider)) {
        E.browseProviderSelect.value = state.currentProvider;
      }
      openSettingsPanel(E.openrouterBrowsePanel);
      await loadBrowseCatalog();
    });

    E.closeBrowseModelsBtn.addEventListener('click', () => {
      closeAllSettingsPanels();
    });

    E.browseProviderSelect.addEventListener('change', async () => {
      E.browseModelSearch.value = '';
      await loadBrowseCatalog();
    });

    E.browseModelSearch.addEventListener('input', () => {
      clearTimeout(_browseDebounce);
      _browseDebounce = setTimeout(() => renderBrowseList(), 300);
    });

    E.browseFreeOnly.addEventListener('change', () => renderBrowseList());
    E.browseSortSelect.addEventListener('change', () => renderBrowseList());

    E.refreshCatalogBtn.addEventListener('click', async () => {
      const provider = getBrowseProvider();
      E.refreshCatalogBtn.textContent = 'Refreshing...';
      E.refreshCatalogBtn.disabled = true;
      try {
        const res = await postJSON(`/api/browse/${provider}/refresh`, {});
        if (res.success) {
          state.browseCatalog = await getJSON(`/api/browse/${provider}/catalog`);
          if (!Array.isArray(state.browseCatalog)) state.browseCatalog = [];
          renderBrowseList();
        }
      } finally {
        E.refreshCatalogBtn.textContent = 'Refresh Catalog';
        E.refreshCatalogBtn.disabled = false;
      }
    });

    // Provider change in settings panel (INDEPENDENT from header)
    E.providerSelect.addEventListener('change', async () => {
      const p = E.providerSelect.value;

      // DO NOT mirror to header - settings panel is independent!

      // Rebuild settings model list for this provider
      await refreshModelsSettings(p);

      // Load settings for the first model in the list
      if (E.modelSettingsSelect.options.length > 0) {
        const firstModel = E.modelSettingsSelect.value;
        if (firstModel) {
          await loadModelSettings(p, firstModel);
        }
      }
    });

    // Model selection change in settings panel
    E.modelSettingsSelect.addEventListener('change', async () => {
      const provider = E.providerSelect.value;
      const model = E.modelSettingsSelect.value;
      if (model) {
        await loadModelSettings(provider, model);
        
        // Show/hide remove button - any model can be removed
        const canRemove = await isRemovableModel(provider, model);
        E.removeModelBtn.style.display = canRemove ? 'block' : 'none';
      }
    });

    // Add Model button
    E.addModelBtn.addEventListener('click', () => {
      E.newModelInputContainer.style.display = '';
      E.newModelName.value = '';
      E.newModelName.focus();
    });

    // Cancel Add Model
    E.cancelAddModelBtn.addEventListener('click', () => {
      E.newModelInputContainer.style.display = 'none';
    });

    // Save Model button
    E.saveModelBtn.addEventListener('click', async () => {
      const provider = E.providerSelect.value;
      const modelName = E.newModelName.value.trim();
      
      if (!modelName) {
        alert('Please enter a model name');
        return;
      }
      
      try {
        const res = await fetch(`/api/models/${provider}/add`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: modelName })
        });
        const data = await res.json();
        if (data.success) {
          // Refresh the model list
          await refreshModelsSettings(provider);
          // Hide the input container
          E.newModelInputContainer.style.display = 'none';
        } else {
          alert('Failed to add model: ' + (data.error || 'Unknown error'));
        }
      } catch (err) {
        alert('Failed to add model: ' + err.message);
      }
    });

    // Save Model Settings button
    E.saveModelSettingsBtn.addEventListener('click', async () => {
      const provider = E.providerSelect.value;
      const model = E.modelSettingsSelect.value;
      
      if (!model) {
        alert('Please select a model first');
        return;
      }
      
      try {
        const settings = {
          max_tokens: parseInt(E.maxTokensInput.value) || 4096,
          temperature: parseFloat(E.modelTemperatureRange.value) || 0.7,
          system_prompt: E.modelSystemPromptTextarea.value || ''
        };

        // Context window (0 or empty = use provider default)
        const cw = parseInt(E.contextWindowInput.value);
        if (cw > 0) {
          settings.context_window = cw;
        }

        // GPU layers (llamacpp only, default 99 = all on GPU)
        const nglVal = E.nglInput.value.trim();
        const ngl = (nglVal !== '') ? parseInt(nglVal) : 99;
        if (ngl >= -1) {
          settings.n_gpu_layers = ngl;
        }

        if (E.vramRequiredInput) {
          const vramVal = E.vramRequiredInput.value.trim();
          if (vramVal !== '') {
            const vram = parseFloat(vramVal);
            if (!Number.isNaN(vram) && vram >= 0) {
              settings.vram_required_gb = vram;
            }
          }
        }
        
        // Add optional parameters if they have non-default values
        const topP = parseFloat(E.topPRange.value);
        if (topP !== 1.0) {
          settings.top_p = topP;
        }
        
        const topK = parseInt(E.topKRange.value);
        if (topK !== 40) {
          settings.top_k = topK;
        }
        
        const res = await fetch(`/api/models/${provider}/settings/${encodeURIComponent(model)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(settings)
        });
        const data = await res.json();
        if (data.success) {
          alert('Settings saved successfully');
        } else {
          alert('Failed to save settings: ' + (data.error || 'Unknown error'));
        }
      } catch (err) {
        alert('Failed to save settings: ' + err.message);
      }
    });

    // Remove Model button
    E.removeModelBtn.addEventListener('click', async () => {
      const provider = E.providerSelect.value;
      const model = E.modelSettingsSelect.value;
      
      if (!model) {
        alert('No model selected');
        return;
      }
      
      // Confirm deletion
      if (!confirm(`Are you sure you want to remove "${model}" from your custom models?\n\nThis will delete the model from your list and all its saved settings.`)) {
        return;
      }
      
      try {
        const res = await fetch(`/api/models/${provider}/delete/${encodeURIComponent(model)}`, {
          method: 'DELETE'
        });
        const data = await res.json();
        
        if (data.success) {
          alert('Model removed successfully');
          
          // Refresh the model list
          await refreshModelsSettings(provider);
          
          // Hide the remove button
          E.removeModelBtn.style.display = 'none';
          
          // If there are models left, select and load the first one
          if (E.modelSettingsSelect.options.length > 0) {
            E.modelSettingsSelect.selectedIndex = 0;
            const newModel = E.modelSettingsSelect.value;
            await loadModelSettings(provider, newModel);
            
            // Check if the new selection can be removed
            const canRemove = await isRemovableModel(provider, newModel);
            E.removeModelBtn.style.display = canRemove ? 'block' : 'none';
          } else {
            // No models left, hide the settings form
            E.modelSettingsForm.style.display = 'none';
          }
        } else {
          alert('Failed to remove model: ' + (data.error || 'Unknown error'));
        }
      } catch (err) {
        alert('Failed to remove model: ' + err.message);
      }
    });

    // Update temperature label for model settings
    E.modelTemperatureRange.addEventListener('input', (e) => {
      E.modelTemperatureValue.textContent = (+e.target.value).toFixed(1);
    });

    // Update top-p label
    E.topPRange.addEventListener('input', (e) => {
      E.topPValue.textContent = (+e.target.value).toFixed(2);
    });

    // Update top-k label
    E.topKRange.addEventListener('input', (e) => {
      E.topKValue.textContent = (+e.target.value);
    });

    // Header provider/model
    E.providerHeader.addEventListener('change', async () => {
      const p = E.providerHeader.value;
      state.currentProvider = p;  // Keep state in sync

      // Mirror to settings immediately
      E.providerSelect.value = p;

      // Persist provider
      await postJSON('/api/config/update', { provider: p });

      // Rebuild header + settings lists from backend
      await refreshModelsHeader(p, null);
      await refreshModelsSettings(p);

      // Update Load/Unload button if relevant
      await updateLoadUnloadButtonText();
    });
    E.modelHeader.addEventListener('change', async () => {
      const m = E.modelHeader.value;
      const provider = E.providerHeader.value;
      state.currentModel = m;
      await postJSON('/api/config/update', { provider, model: m });

      // For llamacpp, just update the button text — don't auto-load on model switch.
      // Model will be loaded when user clicks Load or sends a message.
      
      // Update max tokens label for the selected model
      await updateModelMaxTokensLabel(provider, m);
      
      // Update the load/unload button text when model changes
      await updateLoadUnloadButtonText();
    });

    // System prompt
    E.systemPromptSelect.addEventListener('change', async () => {
      const selectedPromptId = E.systemPromptSelect.value;
      const selectedPrompt = state.prompts.find(p => p.id === selectedPromptId);
      await postJSON('/api/config/update', { system_prompt: selectedPrompt ? selectedPrompt.body : '' });
    });

    // Refresh models button
    E.refreshModelsBtn?.addEventListener('click', async () => {
      const provider = E.providerHeader.value;
      try {
        // For llamacpp, scan the models directory via SSH first
        if (provider === 'llamacpp') {
          const res = await fetch('/api/llamacpp/refresh', { method: 'POST' });
          const data = await res.json();
          if (!data.success) {
            console.warn('llamacpp refresh warning:', data.error || data.note);
          }
        }
        await refreshModelsHeader(provider, E.modelHeader.value);
        await refreshModelsSettings(provider);
        alert('Models refreshed.');
      } catch (e) {
        alert('Failed to refresh models: ' + String(e));
      }
    });

    // Save edited model list
    if (E.saveModelsBtn) {
      E.saveModelsBtn.addEventListener('click', async () => {
        console.debug('Legacy saveModelsBtn clicked — old model list editor is deprecated.');
        alert('Model list editor has moved. Use "Add Model" and per-model settings below.');
      });
    } else {
      console.debug('saveModelsBtn not in DOM (using new model settings panel).');
    }

    // Reset to defaults
    if (E.resetModelsBtn) {
      E.resetModelsBtn.addEventListener('click', async () => {
        console.debug('Legacy resetModelsBtn clicked — ignored.');
        alert('Use the provider refresh or edit models individually.');
      });
    } else {
      console.debug('resetModelsBtn not in DOM (using new model settings panel).');
    }

    // Theme (system settings)
    function setTheme(t) {
      document.documentElement.setAttribute('data-theme', t);
      state.theme = t;
      // optional: persist locally
      try { localStorage.setItem('tb.theme', t); } catch {}
      // UI highlight
      E.lightThemeBtn.classList.toggle('active', t === 'light');
      E.darkThemeBtn.classList.toggle('active', t === 'dark');
    }
    
    // ====== System Prompts panel event handlers ======
    E.promptsList.addEventListener('dblclick', () => {
      const selectedOption = E.promptsList.options[E.promptsList.selectedIndex];
      if (selectedOption && selectedOption.dataset.prompt) {
        const prompt = JSON.parse(selectedOption.dataset.prompt);
        showPromptEditor(prompt);
      }
    });

    E.addPromptBtn.addEventListener('click', () => {
      showPromptEditor(null);
    });

    E.savePromptBtn.addEventListener('click', async () => {
      await savePrompt();
    });

    E.cancelPromptBtn.addEventListener('click', () => {
      E.promptEditor.style.display = 'none';
    });

    E.deletePromptBtn.addEventListener('click', async () => {
      await deletePrompt();
    });
    
    E.lightThemeBtn.addEventListener('click', () => setTheme('light'));
    E.darkThemeBtn.addEventListener('click', () => setTheme('dark'));
    // Restore theme
    try {
      const t = localStorage.getItem('tb.theme');
      if (t) setTheme(t);
    } catch {}

    // --- SSH settings ---
    const sshEnabled = $('sshEnabledCheck');
    const sshFields = $('sshFieldsGroup');
    const sshSaveBtn = $('sshSaveBtn');
    const sshSaveStatus = $('sshSaveStatus');

    if (sshEnabled && sshFields) {
      sshEnabled.addEventListener('change', () => {
        sshFields.style.display = sshEnabled.checked ? '' : 'none';
      });

      // Load SSH config when Appearance/System settings panel opens
      E.openAppearanceSettingsBtn.addEventListener('click', async () => {
        try {
          const resp = await fetch('/api/llamacpp/ssh-config');
          const data = await resp.json();
          if (data.success) {
            sshEnabled.checked = !!data.llamacpp_ssh_enabled;
            sshFields.style.display = sshEnabled.checked ? '' : 'none';
            $('sshHostInput').value = data.llamacpp_ssh_host || '';
            $('sshPortInput').value = data.llamacpp_ssh_port || 22;
            $('sshUserInput').value = data.llamacpp_ssh_user || '';
            $('sshBinaryInput').value = data.llamacpp_server_binary || '';
            $('sshArgsInput').value = data.llamacpp_server_args || '';
            if (E.totalVramInput) E.totalVramInput.value = data.llamacpp_total_vram_gb || '';
            const ztSshHost = $('ztSshHostInput');
            const ztUrl = $('ztUrlInput');
            if (ztSshHost) ztSshHost.value = data.llamacpp_zerotier_ssh_host || '';
            if (ztUrl) ztUrl.value = data.llamacpp_zerotier_url || '';
          }
        } catch (e) {
          console.warn('Failed to load SSH config:', e);
        }
      });

      if (sshSaveBtn) {
        sshSaveBtn.addEventListener('click', async () => {
          try {
            const payload = {
              llamacpp_ssh_enabled: sshEnabled.checked,
              llamacpp_ssh_host: $('sshHostInput').value.trim(),
              llamacpp_ssh_port: parseInt($('sshPortInput').value) || 22,
              llamacpp_ssh_user: $('sshUserInput').value.trim(),
              llamacpp_server_binary: $('sshBinaryInput').value.trim(),
              llamacpp_server_args: $('sshArgsInput').value.trim(),
              llamacpp_total_vram_gb: parseFloat(E.totalVramInput?.value || '0') || 0,
              llamacpp_zerotier_ssh_host: ($('ztSshHostInput')?.value || '').trim(),
              llamacpp_zerotier_url: ($('ztUrlInput')?.value || '').trim(),
            };
            const resp = await fetch('/api/llamacpp/ssh-config', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            });
            const json = await resp.json();
            if (sshSaveStatus) {
              sshSaveStatus.textContent = json.success ? 'Saved' : 'Error';
              setTimeout(() => { sshSaveStatus.textContent = ''; }, 2000);
            }
            // Refresh button state since ssh_enabled may have changed
            await updateLoadUnloadButtonText();
          } catch (e) {
            console.error('Failed to save SSH config:', e);
            if (sshSaveStatus) sshSaveStatus.textContent = 'Error';
          }
        });
      }
    }
  }

  // ====== Context bar bindings ======
  function bindContextBar() {
    // Toggle bar
    E.contextSelectionBtn.addEventListener('click', () => {
      state.contextBarOpen = !state.contextBarOpen;
      E.contextControls.style.display = state.contextBarOpen ? 'flex' : 'none';

      if (state.contextBarOpen) {
        // Default: select ALL messages when opening the bar
        state.selectedMessageIdx = new Set(state.messages.map((_, i) => i));
        // Default: select ALL summaries for messages that have them
        state.selectedSummaryIdx = new Set(
          state.messages
            .map((m, i) => m.summary ? i : -1)
            .filter(i => i >= 0)
        );
      } else {
        // Optional: keep selection when closing; do not clear
        // If you want to clear on close, uncomment the next lines:
        // state.selectedMessageIdx.clear();
        // state.selectedSummaryIdx.clear();
      }

      renderMessages();
      // Keep docs up to date so doc tokens are available to counter
      loadDocs().then(updateContextTokenSummary);
    });

    // Upload doc
    E.addDocumentBtn.addEventListener('click', () => E.documentUpload.click());
    E.documentUpload.addEventListener('change', async () => {
      if (!E.documentUpload.files || !E.documentUpload.files.length) return;
      await uploadDocument(E.documentUpload.files[0]);
    });

    // URL Ingestion (Phase 7)
    E.urlIngestBtn.addEventListener('click', async () => {
      const url = E.urlIngestInput.value.trim();
      if (!url) {
        alert('Please enter a URL');
        return;
      }
      
      E.urlIngestBtn.disabled = true;
      E.urlIngestBtn.textContent = 'Ingesting...';
      
      try {
        const res = await fetch('/api/context/url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url })
        });
        const data = await res.json();
        
        if (data.success) {
          alert(`Successfully ingested: ${data.document.name}`);
          E.urlIngestInput.value = '';
          await loadDocs();
          await updateContextTokenSummary();
        } else {
          alert('Failed to ingest URL: ' + (data.error || 'Unknown error'));
        }
      } catch (e) {
        alert('Failed to ingest URL: ' + e);
      } finally {
        E.urlIngestBtn.disabled = false;
        E.urlIngestBtn.textContent = '🌐 Ingest URL';
      }
    });

    // Selection helpers (operate on MESSAGES, not documents)
    E.selectAllBtn.addEventListener('click', () => {
      state.selectedMessageIdx = new Set(state.messages.map((_, i) => i));
      // Also select all summaries
      state.selectedSummaryIdx = new Set(
        state.messages
          .map((m, i) => m.summary ? i : -1)
          .filter(i => i >= 0)
      );
      renderMessages();
      updateContextTokenSummary();
    });

    E.selectNoneBtn.addEventListener('click', () => {
      // Clear messages
      state.selectedMessageIdx.clear();
      // Clear summaries
      state.selectedSummaryIdx.clear();
      // Clear documents
      state.docs.forEach(d => { d.selected = false; });
      renderMessages();
      updateContextTokenSummary();
    });

    E.selectLastBtn.addEventListener('click', () => {
      const N = 5;
      const total = state.messages.length;
      const start = Math.max(0, total - N);
      state.selectedMessageIdx = new Set(Array.from({ length: total }, (_, i) => i).slice(start));
      // Select summaries for the last N messages that have them
      state.selectedSummaryIdx = new Set(
        state.messages
          .map((m, i) => (i >= start && m.summary) ? i : -1)
          .filter(i => i >= 0)
      );
      renderMessages();
      updateContextTokenSummary();
    });

    E.selectSummariesBtn.addEventListener('click', () => {
      // Clear message selections but select all summaries
      // This allows sending ONLY summaries without the full messages
      state.selectedMessageIdx.clear();
      state.selectedSummaryIdx = new Set(
        state.messages
          .map((m, i) => m.summary ? i : -1)
          .filter(i => i >= 0)
      );
      renderMessages();
      updateContextTokenSummary();
    });

    E.summarizeSelectedBtn.addEventListener('click', async () => {
      if (!state.selectedMessageIdx.size) return;
      // Summarize all selected messages and insert as a new inline summary
      const combined = Array.from(state.selectedMessageIdx)
          .map(i => state.messages[i]?.content || '')
          .filter(Boolean)
          .join('\n\n');
      if (!combined) return;

      try {
        const r = await fetch("/api/chat/summarize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            content: combined,
            provider: state.currentProvider,          // <--
            model: state.currentModel                 // <--
          })
        });
        const data = await r.json();
        if (!r.ok || data.success === false) throw new Error(data.error || 'Summarize failed');

        // Insert assistant-only summary message (no green user bubble)
        state.messages.push({
          role: 'assistant',
          content: data.summary || '(no summary)',
          model: state.currentModel,
          timestamp: new Date().toLocaleTimeString(),
          isSummary: true
        });
        renderMessages();
      } catch (e) {
        alert('Summarize failed: ' + String(e));
      }
    });

    E.summarizeAllBtn.addEventListener('click', async () => {
    const all = state.messages.map(m => `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.content}`).join('\n');
    if (!all) return;

    try {
      const r = await fetch("/api/chat/summarize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: all,
          provider: state.currentProvider,          // <--
          model: state.currentModel                 // <--
        })
      });
      const data = await r.json();
      if (!r.ok || data.success === false) throw new Error(data.error || 'Summarize failed');

      state.messages.push({
        role: 'assistant',
        content: data.summary || '(no summary)',
        model: state.currentModel,
        timestamp: new Date().toLocaleTimeString(),
        isSummary: true
      });
      renderMessages();
    } catch (e) {
      alert('Summarize failed: ' + String(e));
    }
});

  }  // This closes the bindContextBar function

  // ====== Input bindings ======
  function bindInput() {
    E.sendBtn.addEventListener('click', sendMessage);
    E.userInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    E.cancelBtn.addEventListener('click', cancelGeneration);

    // Overflow warning buttons
    if (E.overflowDismiss) E.overflowDismiss.addEventListener('click', hideOverflowWarning);

    if (E.overflowSelectContext) E.overflowSelectContext.addEventListener('click', () => {
      hideOverflowWarning();
      // Open context bar for manual selection
      if (!state.contextBarOpen) {
        E.contextSelectionBtn.click();
      }
    });

    if (E.overflowSummarize) E.overflowSummarize.addEventListener('click', async () => {
      hide(E.contextOverflowWarning);
      await autoSummarizeToFit();
      // Re-check after summarizing
      const savedText = state._pendingSendText;
      if (savedText) {
        E.userInput.value = savedText;
        state._skipOverflowCheck = false;
        state._pendingSendText = null;
        state._pendingSendPayload = null;
        state._overflowCheck = null;
        await sendMessage();
      }
    });

    if (E.overflowTrim) E.overflowTrim.addEventListener('click', async () => {
      hide(E.contextOverflowWarning);
      await autoTrimToFit();
      // Re-check after trimming
      const savedText = state._pendingSendText;
      if (savedText) {
        E.userInput.value = savedText;
        state._skipOverflowCheck = false;
        state._pendingSendText = null;
        state._pendingSendPayload = null;
        state._overflowCheck = null;
        await sendMessage();
      }
    });

    if (E.overflowSendAnyway) E.overflowSendAnyway.addEventListener('click', async () => {
      hideOverflowWarning();
      const savedText = state._pendingSendText;
      if (savedText) {
        E.userInput.value = savedText;
        state._skipOverflowCheck = true;
        await sendMessage();
      }
    });

    // --- Input box right-click context menu (pywebview has no default) ---
    E.userInput.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const hasSelection = E.userInput.selectionStart !== E.userInput.selectionEnd;
      E.inputCut.style.display = hasSelection ? '' : 'none';
      E.inputCopy.style.display = hasSelection ? '' : 'none';
      openMenuAt(E.inputContextMenu, e.pageX, e.pageY);
    });

    E.inputPaste.addEventListener('click', async () => {
      E.inputContextMenu.style.display = 'none';
      try {
        const text = await navigator.clipboard.readText();
        const start = E.userInput.selectionStart;
        const end = E.userInput.selectionEnd;
        const val = E.userInput.value;
        E.userInput.value = val.slice(0, start) + text + val.slice(end);
        const pos = start + text.length;
        E.userInput.setSelectionRange(pos, pos);
        E.userInput.focus();
      } catch { /* clipboard permission denied */ }
    });

    E.inputCut.addEventListener('click', async () => {
      E.inputContextMenu.style.display = 'none';
      const start = E.userInput.selectionStart;
      const end = E.userInput.selectionEnd;
      const selected = E.userInput.value.slice(start, end);
      try { await navigator.clipboard.writeText(selected); } catch {}
      const val = E.userInput.value;
      E.userInput.value = val.slice(0, start) + val.slice(end);
      E.userInput.setSelectionRange(start, start);
      E.userInput.focus();
    });

    E.inputCopy.addEventListener('click', async () => {
      E.inputContextMenu.style.display = 'none';
      const selected = E.userInput.value.slice(E.userInput.selectionStart, E.userInput.selectionEnd);
      try { await navigator.clipboard.writeText(selected); } catch {}
      E.userInput.focus();
    });

    E.inputSelectAll.addEventListener('click', () => {
      E.inputContextMenu.style.display = 'none';
      E.userInput.select();
      E.userInput.focus();
    });
  }

  // ====== Missing functions ======
  function afterMessageSettled() {
    loadHistory();
    updateContextTokenSummary();
    renderChatTitle();
  }

  async function updateContextTokenSummary() {
    // Message tokens: calculate based on what will actually be sent
    // Logic:
    // - If message is selected AND summary is selected: use summary tokens
    // - If message is selected but summary is NOT selected: use message content tokens
    // - If summary is selected but message is NOT selected: use summary tokens (summary-only mode)
    let msgTokens = 0;
    let itemCount = 0;

    // First, count tokens for selected messages
    state.selectedMessageIdx.forEach((i) => {
      const m = state.messages[i];
      if (!m) return;
      // If this message has a summary and the summary is selected, use summary
      // Otherwise use the full content
      if (m.summary && state.selectedSummaryIdx.has(i)) {
        msgTokens += estimateTokensJS(m.summary);
      } else {
        msgTokens += estimateTokensJS(m.content || '');
      }
      itemCount++;
    });

    // Then, count tokens for summaries that are selected but their messages are NOT
    // (This is the key feature: sending just the summary without the full message)
    state.selectedSummaryIdx.forEach((i) => {
      const m = state.messages[i];
      if (!m || !m.summary) return;
      // Only count if the message itself is NOT selected
      if (!state.selectedMessageIdx.has(i)) {
        msgTokens += estimateTokensJS(m.summary);
        itemCount++;
      }
    });

    // Doc tokens: selected documents only (use already-loaded state.docs)
    const selectedDocs = state.docs.filter(d => d.selected);
    const docTokens = selectedDocs.reduce((sum, d) => sum + (d.token_estimate_total || 0), 0);

    const total = msgTokens + docTokens;
    const docCount = selectedDocs.length;

    // Show summary count separately if there are summary-only selections
    const summaryOnlyCount = Array.from(state.selectedSummaryIdx)
      .filter(i => !state.selectedMessageIdx.has(i) && state.messages[i]?.summary).length;

    let contextText;
    if (summaryOnlyCount > 0) {
      contextText = `Context: ${fmt(total)} tokens (${state.selectedMessageIdx.size} msgs + ${summaryOnlyCount} summaries, ${docCount} docs)`;
    } else {
      contextText = `Context: ${fmt(total)} tokens (${state.selectedMessageIdx.size} messages, ${docCount} docs)`;
    }

    if (E.contextTokenCountTop) E.contextTokenCountTop.textContent = contextText;
    if (E.contextTokenCount2)  E.contextTokenCount2.textContent  = `${fmt(total)} tokens`;
  }

  // ====== Document Viewer ======
  const docViewer = {
    currentDocId: null,
    currentText: '',
    selectedSections: new Set(),
    selectedHighlights: new Set(),

    async open(docId) {
      this.currentDocId = docId;
      const panel = document.getElementById('docViewerPanel');
      if (!panel) return;

      // Fetch full document data
      try {
        const res = await fetch(`/api/context/docs/${docId}/full`);
        const data = await res.json();
        if (!data.success) {
          alert('Failed to load document');
          return;
        }

        this.currentText = data.text || '';
        this.populate(data.document, data.text);
        panel.classList.add('open');
      } catch (err) {
        console.error('Error loading document:', err);
        alert('Error loading document');
      }
    },

    close() {
      const panel = document.getElementById('docViewerPanel');
      if (panel) panel.classList.remove('open');
      this.currentDocId = null;
    },

    populate(doc, text) {
      // Title
      const titleEl = document.getElementById('docViewerTitle');
      if (titleEl) titleEl.textContent = doc.name || 'Document';

      // Meta
      const tokensEl = document.getElementById('docViewerTokens');
      if (tokensEl) tokensEl.textContent = `${doc.total_tokens || 0} tokens`;

      const analysisEl = document.getElementById('docViewerAnalysis');
      if (analysisEl) {
        analysisEl.textContent = doc.analysis_level === 'deep' ? 'Deep analysis' : 'Quick parse';
      }

      // Sections
      this.renderSections(doc.sections || []);

      // Highlights
      this.renderHighlights(doc.highlights || [], text);

      // Text
      const textView = document.getElementById('docTextView');
      if (textView) textView.textContent = text;

      // Tags
      this.renderTags(doc.tags || []);

      // Update selected count
      this.updateSelectedTokens();
    },

    renderSections(sections) {
      const container = document.getElementById('sectionsList');
      if (!container) return;

      if (!sections.length) {
        container.innerHTML = '<div style="color: #6b7280; font-size: 12px;">No sections detected</div>';
        return;
      }

      container.innerHTML = sections.map((s, i) => `
        <div class="doc-section-item">
          <input type="checkbox" id="section-${i}" data-section-id="${s.id}" data-tokens="${s.tokens || 0}" checked>
          <div class="doc-section-item-content">
            <div class="doc-section-item-title">${s.title || `Section ${i + 1}`}</div>
            <div class="doc-section-item-meta">${s.tokens || 0} tokens</div>
          </div>
        </div>
      `).join('');

      // Add listeners
      container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', () => this.updateSelectedTokens());
      });
    },

    renderHighlights(highlights, text) {
      const container = document.getElementById('highlightsList');
      if (!container) return;

      if (!highlights.length) {
        container.innerHTML = '<div style="color: #6b7280; font-size: 12px;">No selections yet. Select text below to add.</div>';
        return;
      }

      container.innerHTML = highlights.map(h => {
        const snippet = text.substring(h.start_pos || h.start, h.end_pos || h.end).substring(0, 100);
        return `
          <div class="doc-highlight-item">
            <input type="checkbox" data-highlight-id="${h.id}" data-tokens="${h.tokens || 0}" checked>
            <div class="doc-highlight-item-content">
              <div class="doc-highlight-item-text">"${snippet}${snippet.length >= 100 ? '...' : ''}"</div>
              <div class="doc-highlight-item-meta">${h.tokens || 0} tokens</div>
            </div>
            <button class="delete-btn" data-highlight-id="${h.id}">&times;</button>
          </div>
        `;
      }).join('');

      // Add listeners
      container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', () => this.updateSelectedTokens());
      });

      container.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const hid = e.target.dataset.highlightId;
          await this.deleteHighlight(hid);
        });
      });
    },

    renderTags(tags) {
      const container = document.getElementById('docTags');
      if (!container) return;

      if (!tags.length) {
        container.innerHTML = '<span style="color: #6b7280; font-size: 12px;">No tags</span>';
        return;
      }

      container.innerHTML = tags.map(t =>
        `<span class="doc-tag">#${t.tag || t}</span>`
      ).join('');
    },

    updateSelectedTokens() {
      // Calculate tokens from selected sections and highlights
      let total = 0;
      document.querySelectorAll('#sectionsList input:checked').forEach(cb => {
        total += parseInt(cb.dataset.tokens || 0);
      });
      document.querySelectorAll('#highlightsList input:checked').forEach(cb => {
        total += parseInt(cb.dataset.tokens || 0);
      });

      const el = document.getElementById('docViewerSelectedTokens');
      if (el) el.textContent = `Selected: ${total} tokens`;
    },

    getSelectedContext() {
      // Get selected section IDs and highlight IDs
      const sections = [];
      const highlights = [];

      document.querySelectorAll('#sectionsList input:checked').forEach(cb => {
        sections.push(cb.dataset.sectionId);
      });
      document.querySelectorAll('#highlightsList input:checked').forEach(cb => {
        highlights.push(cb.dataset.highlightId);
      });

      return {
        doc_id: this.currentDocId,
        sections,
        highlights
      };
    },

    async addHighlight(start, end, label) {
      if (!this.currentDocId) return;

      try {
        const res = await fetch(`/api/context/docs/${this.currentDocId}/highlights`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start, end, label })
        });
        const data = await res.json();
        if (data.success) {
          // Refresh the document
          await this.open(this.currentDocId);
        }
      } catch (err) {
        console.error('Error adding highlight:', err);
      }
    },

    async deleteHighlight(highlightId) {
      if (!this.currentDocId) return;

      try {
        await fetch(`/api/context/docs/${this.currentDocId}/highlights/${highlightId}`, {
          method: 'DELETE'
        });
        // Refresh
        await this.open(this.currentDocId);
      } catch (err) {
        console.error('Error deleting highlight:', err);
      }
    }
  };

  // Document viewer event bindings
  function bindDocViewer() {
    // Close button
    const closeBtn = document.getElementById('closeDocViewerBtn');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => docViewer.close());
    }

    // Add to Context button
    const addToContextBtn = document.getElementById('addDocToContextBtn');
    if (addToContextBtn) {
      addToContextBtn.addEventListener('click', async () => {
        const selection = docViewer.getSelectedContext();
        if (!selection.doc_id) return;

        // Save selection to backend
        try {
          await fetch('/api/context/docs/save-selection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(selection)
          });

          // Close viewer and refresh docs
          docViewer.close();
          await loadDocs();
          await updateContextTokenSummary();
        } catch (err) {
          console.error('Error saving selection:', err);
        }
      });
    }

    // Section headers (collapsible)
    document.querySelectorAll('.doc-viewer-section-header').forEach(header => {
      header.addEventListener('click', () => {
        header.classList.toggle('collapsed');
        const content = header.nextElementSibling;
        if (content) content.classList.toggle('collapsed');
      });
    });

    // Text selection for highlights
    const textView = document.getElementById('docTextView');
    const addSelectionBtn = document.getElementById('addSelectionBtn');

    if (textView && addSelectionBtn) {
      textView.addEventListener('mouseup', (e) => {
        const selection = window.getSelection();
        const text = selection.toString().trim();

        if (text && text.length > 0) {
          // Position the button near the selection
          const range = selection.getRangeAt(0);
          const rect = range.getBoundingClientRect();

          addSelectionBtn.style.display = 'block';
          addSelectionBtn.style.left = `${rect.left + rect.width / 2 - 50}px`;
          addSelectionBtn.style.top = `${rect.bottom + 5}px`;

          // Store selection info
          addSelectionBtn.dataset.start = getTextOffset(textView, range.startContainer, range.startOffset);
          addSelectionBtn.dataset.end = getTextOffset(textView, range.endContainer, range.endOffset);
        } else {
          addSelectionBtn.style.display = 'none';
        }
      });

      addSelectionBtn.addEventListener('click', async () => {
        const start = parseInt(addSelectionBtn.dataset.start);
        const end = parseInt(addSelectionBtn.dataset.end);
        const text = docViewer.currentText.substring(start, end);
        const label = text.substring(0, 30) + (text.length > 30 ? '...' : '');

        await docViewer.addHighlight(start, end, label);
        addSelectionBtn.style.display = 'none';
        window.getSelection().removeAllRanges();
      });

      // Hide button when clicking elsewhere
      document.addEventListener('mousedown', (e) => {
        if (e.target !== addSelectionBtn && !textView.contains(e.target)) {
          addSelectionBtn.style.display = 'none';
        }
      });
    }
  }

  // Helper to get text offset within container
  function getTextOffset(container, node, offset) {
    let totalOffset = 0;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);

    while (walker.nextNode()) {
      if (walker.currentNode === node) {
        return totalOffset + offset;
      }
      totalOffset += walker.currentNode.textContent.length;
    }
    return totalOffset;
  }

  // Make docViewer available globally for document clicks
  window.openDocViewer = (docId) => docViewer.open(docId);

  // ====== Sidebar Initialization ======
  async function initSidebarFromConfig() {
    try {
      const config = await (await fetch('/api/config')).json();

      // Sidebar model list textarea
      await refreshModelsSettings(state.currentProvider);
      await refreshModelsHeader(state.currentProvider, state.currentModel);
      
      // Update max tokens label for the current model
      await updateModelMaxTokensLabel(state.currentProvider, state.currentModel);

    } catch (err) {
      console.error('Failed to init sidebar:', err);
    }
  }

  // Idle polling: periodically check status when a model is loaded
  let _idlePollInterval = null;
  function _startIdlePoll() {
    if (_idlePollInterval) return; // already running
    _idlePollInterval = setInterval(async () => {
      try { await updateLoadUnloadButtonText(); } catch (_) {}
    }, 60000); // every 60s
  }
  function _stopIdlePoll() {
    if (_idlePollInterval) {
      clearInterval(_idlePollInterval);
      _idlePollInterval = null;
    }
  }

  // Auto-load a model on the llama.cpp server (used after model switch in dropdown)
  async function triggerModelLoad(modelName) {
    if (!modelName) return;
    console.log(`[auto-load] Triggering load for ${modelName}`);
    E.loadUnloadModelBtn.disabled = true;
    E.loadUnloadModelBtn.innerHTML = 'Loading<span class="loading-dots"></span>';
    E.ctxSizeSelect.style.display = 'none';
    try {
      const nCtx = parseInt(E.ctxSizeSelect.value) || 0;
      const loadPayload = { model: modelName, n_ctx: nCtx, n_gpu_layers: 99 };
      // Fetch per-model settings for custom n_gpu_layers
      try {
        const msRes = await fetch(`/api/models/llamacpp/settings/${encodeURIComponent(modelName)}`);
        const msData = await msRes.json();
        if (msData.success && msData.settings && msData.settings.n_gpu_layers !== undefined) {
          loadPayload.n_gpu_layers = msData.settings.n_gpu_layers;
        }
      } catch (_) {}
      const resp = await fetch('/api/llamacpp/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(loadPayload),
      });
      const json = await resp.json();
      if (json.success && json.loading) {
        // Async load started — poll status until done
        setTimeout(async () => {
          try { await updateLoadUnloadButtonText(); } catch (_) {}
        }, 3000);
      } else if (json.success) {
        // Synchronous load completed
        await updateLoadUnloadButtonText();
      } else {
        console.warn('[auto-load] Load failed:', json.error);
        E.loadUnloadModelBtn.textContent = 'Load';
        E.loadUnloadModelBtn.disabled = false;
        E.ctxSizeSelect.style.display = '';
      }
    } catch (err) {
      console.error('[auto-load] Error:', err);
      E.loadUnloadModelBtn.textContent = 'Load';
      E.loadUnloadModelBtn.disabled = false;
      E.ctxSizeSelect.style.display = '';
    }
  }

  async function updateLoadUnloadButtonText() {
    const provider = E.providerHeader.value;
    const supportsLoadUnload = (provider === 'llamacpp');
    console.log(`[btn-update] provider=${provider}, supported=${supportsLoadUnload}, disabled=${E.loadUnloadModelBtn.disabled}, current=${E.loadUnloadModelBtn.textContent.trim()}`);

    if (!supportsLoadUnload) {
      E.loadUnloadModelBtn.style.display = 'none';
      E.ctxSizeSelect.style.display = 'none';
      return;
    }

    E.loadUnloadModelBtn.style.display = '';

    try {
      if (provider === 'llamacpp') {
        // Check llama.cpp server status via our backend API
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 8000);

        const response = await fetch('/api/llamacpp/status', {
          signal: controller.signal,
          headers: { 'Content-Type': 'application/json' }
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const status = await response.json();
        console.log('[status]', JSON.stringify(status));

        // Server is starting (no model yet) — show starting state and keep polling
        if (status.starting) {
          E.loadUnloadModelBtn.innerHTML = 'Starting<span class="loading-dots"></span>';
          E.loadUnloadModelBtn.disabled = true;
          E.ctxSizeSelect.style.display = 'none';
          setTimeout(async () => {
            try { await updateLoadUnloadButtonText(); } catch (_) {}
          }, 3000);
          return;
        }

        // Server is loading a model in the background — show loading state and keep polling
        if (status.loading) {
          const modelInfo = status.loading_model ? ` ${status.loading_model}` : '';
          E.loadUnloadModelBtn.innerHTML = `Loading${modelInfo}<span class="loading-dots"></span>`;
          E.loadUnloadModelBtn.disabled = true;
          E.ctxSizeSelect.style.display = 'none';
          // Use setInterval for reliable polling (setTimeout can be throttled in background tabs)
          if (!window._loadPollInterval) {
            window._loadPollInterval = setInterval(async () => {
              try {
                await updateLoadUnloadButtonText();
              } catch (_) {}
            }, 3000);
          }
          return;
        }

        // Clear loading poll interval if we're no longer loading
        if (window._loadPollInterval) {
          clearInterval(window._loadPollInterval);
          window._loadPollInterval = null;
        }

        // Show load error if the background load failed
        if (status.load_error) {
          const err = status.load_error;
          E.loadUnloadModelBtn.textContent = 'Load';
          E.loadUnloadModelBtn.disabled = false;
          E.ctxSizeSelect.style.display = '';

          // Build error dialog
          const overlay = document.createElement('div');
          overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:10000;display:flex;align-items:center;justify-content:center';
          const dialog = document.createElement('div');
          dialog.style.cssText = 'background:var(--bg-primary,#1e1e1e);color:var(--text-primary,#e0e0e0);border:1px solid var(--border-color,#444);border-radius:10px;padding:20px 24px;max-width:520px;width:90%;max-height:80vh;overflow-y:auto;font-size:14px';

          const isOOM = err.oom || /OOM|crash|out of memory/i.test(err.error);
          const title = isOOM ? 'Load Failed — Out of Memory' : 'Model Load Failed';

          let html = `<h3 style="margin:0 0 12px;color:#ff6b6b">${title}</h3>`;
          html += `<p style="margin:0 0 8px"><strong>Model:</strong> ${err.model}</p>`;
          html += `<p style="margin:0 0 12px"><strong>Error:</strong> ${err.error}</p>`;

          if (isOOM) {
            html += `<div style="background:rgba(255,170,0,0.1);border:1px solid rgba(255,170,0,0.3);border-radius:6px;padding:10px 12px;margin:0 0 12px">`;
            html += `<strong style="color:#ffaa00">Suggestion:</strong> This model needs more VRAM than available. Try:`;
            html += `<ul style="margin:6px 0 0;padding-left:20px">`;
            html += `<li>Reduce <strong>GPU layers (ngl)</strong> in model settings to offload layers to system RAM</li>`;
            html += `<li>Use a smaller quantization (e.g. Q4_K_M instead of Q8_0)</li>`;
            html += `<li>Try a smaller model</li>`;
            html += `</ul></div>`;
          }

          if (err.log) {
            html += `<details style="margin:8px 0 0"><summary style="cursor:pointer;color:#888">Server log</summary>`;
            html += `<pre style="background:#111;padding:8px;border-radius:4px;overflow-x:auto;font-size:12px;margin:6px 0 0;white-space:pre-wrap">${err.log.replace(/</g,'&lt;')}</pre>`;
            html += `</details>`;
          }

          html += `<div style="margin-top:16px;text-align:right"><button id="loadErrClose" style="background:#444;color:#fff;border:none;padding:6px 18px;border-radius:5px;cursor:pointer;font-size:13px">Close</button></div>`;

          dialog.innerHTML = html;
          overlay.appendChild(dialog);
          document.body.appendChild(overlay);
          overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
          dialog.querySelector('#loadErrClose').addEventListener('click', () => overlay.remove());
          return;
        }

        // Check if model was auto-unloaded due to idle
        if (status.idle_unloaded) {
          _stopIdlePoll();
          E.loadUnloadModelBtn.textContent = 'Load';
          E.loadUnloadModelBtn.disabled = false;
          E.ctxSizeSelect.style.display = '';
          // Show subtle notification
          const note = document.createElement('div');
          note.textContent = 'Model unloaded due to inactivity';
          note.style.cssText = 'position:fixed;top:12px;right:12px;background:#333;color:#fff;padding:8px 16px;border-radius:6px;z-index:9999;font-size:13px;opacity:0;transition:opacity 0.3s';
          document.body.appendChild(note);
          requestAnimationFrame(() => { note.style.opacity = '1'; });
          setTimeout(() => { note.style.opacity = '0'; setTimeout(() => note.remove(), 400); }, 5000);
          return;
        }

        if (status.success && status.loaded_models && status.loaded_models.length > 0) {
          const loadedModel = status.loaded_models[0];
          console.log(`[btn-match] selected="${E.modelHeader.value}" loaded name="${loadedModel.name}" path="${loadedModel.path}" match=${modelMatchesLoaded(E.modelHeader.value, loadedModel)}`);
          if (!modelMatchesLoaded(E.modelHeader.value, loadedModel)) {
            E.loadUnloadModelBtn.textContent = 'Load';
            E.loadUnloadModelBtn.disabled = false;
            E.ctxSizeSelect.style.display = '';
            _stopIdlePoll();
            return;
          }
          const vramInfo = formatVramLabel(Number(loadedModel.vram_required_gb));
          E.loadUnloadModelBtn.textContent = `Unload${vramInfo}`;
          E.loadUnloadModelBtn.disabled = false;
          E.ctxSizeSelect.style.display = 'none';
          _startIdlePoll(); // Poll while model is loaded to detect idle unload
        } else if (status.success && status.server_running) {
          E.loadUnloadModelBtn.textContent = 'Load';
          E.loadUnloadModelBtn.disabled = false;
          E.ctxSizeSelect.style.display = '';
          _stopIdlePoll();
        } else if (status.ssh_enabled) {
          // Server offline but SSH enabled — auto-start it
          E.loadUnloadModelBtn.innerHTML = 'Starting<span class="loading-dots"></span>';
          E.loadUnloadModelBtn.disabled = true;
          E.ctxSizeSelect.style.display = 'none';
          _stopIdlePoll();
          // Fire-and-forget: kick off server ensure
          fetch('/api/llamacpp/server/ensure', { method: 'POST' }).catch(() => {});
          // Poll until server is ready
          setTimeout(async () => {
            try { await updateLoadUnloadButtonText(); } catch (_) {}
          }, 3000);
          return;
        } else {
          E.loadUnloadModelBtn.textContent = 'Server Offline';
          E.loadUnloadModelBtn.disabled = true;
          E.ctxSizeSelect.style.display = 'none';
          _stopIdlePoll();
        }
      }
    } catch (error) {
      console.warn(`[status] Error checking ${provider}:`, error.name, error.message);
      // On error, don't change button text — keep whatever state it was in
      // Schedule a retry instead
      setTimeout(async () => {
        try { await updateLoadUnloadButtonText(); } catch (_) {}
      }, 5000);
    }
  }

  // ====== Boot ======
  async function init() {
    console.time('TB:init');
    // Close menus when scrolling/resizing
    window.addEventListener('scroll', () => { E.chatContextMenu.style.display = 'none'; E.msgContextMenu.style.display = 'none'; E.inputContextMenu.style.display = 'none'; E.folderContextMenu.style.display = 'none'; E.moveToFolderMenu.style.display = 'none'; E.branchContextMenu.style.display = 'none'; E.statusSubMenu.style.display = 'none'; });
    window.addEventListener('resize', () => { E.chatContextMenu.style.display = 'none'; E.msgContextMenu.style.display = 'none'; E.inputContextMenu.style.display = 'none'; E.folderContextMenu.style.display = 'none'; E.moveToFolderMenu.style.display = 'none'; E.branchContextMenu.style.display = 'none'; E.statusSubMenu.style.display = 'none'; });

    // Bind UI immediately so app feels responsive
    bindMenus();
    bindSettingsPanels();
    bindContextBar();
    bindDocViewer();
    bindInput();

    // Show loading states immediately
    E.providerHeader.innerHTML = '<option>Loading...</option>';
    E.modelHeader.innerHTML = '<option>Loading...</option>';
    E.systemPromptSelect.innerHTML = '<option>Loading...</option>';

    // Show loading in chat history
    E.chatHistory.innerHTML = '<div style="padding: 16px; color: #666; text-align: center;">Loading chats...</div>';

    console.time('TB:parallelLoad');

    // Load all critical data in parallel
    const [configResult, promptsResult, historyResult, docsResult, messagesResult, foldersResult, branchTreeResult] = await Promise.allSettled([
      loadConfigAndModels(),
      loadPrompts(),
      loadHistory(),
      loadDocs(),
      loadMessages(),
      loadFolders(),
      loadBranchTree()
    ]);

    console.timeEnd('TB:parallelLoad');

    // Handle any failures gracefully
    if (configResult.status === 'rejected') console.warn('Config load failed:', configResult.reason);
    if (promptsResult.status === 'rejected') console.warn('Prompts load failed:', promptsResult.reason);
    if (historyResult.status === 'rejected') console.warn('History load failed:', historyResult.reason);
    if (docsResult.status === 'rejected') console.warn('Docs load failed:', docsResult.reason);
    if (messagesResult.status === 'rejected') console.warn('Messages load failed:', messagesResult.reason);
    if (foldersResult.status === 'rejected') console.warn('Folders load failed:', foldersResult.reason);

    // Initialize sidebar after config is loaded
    if (configResult.status === 'fulfilled') {
      await initSidebarFromConfig();
    }

    // Update context summary after docs are loaded
    if (docsResult.status === 'fulfilled') {
      await updateContextTokenSummary();
    }

    // For llamacpp: immediately check server status (fast), then refresh model list in background (slow SSH)
    if (state.currentProvider === 'llamacpp') {
      await updateLoadUnloadButtonText();
      (async () => {
        try {
          await fetch('/api/llamacpp/refresh', { method: 'POST' });
          await refreshModelsHeader('llamacpp', E.modelHeader.value);
          await refreshModelsSettings('llamacpp');
        } catch (e) {
          console.warn('Skipping initial llamacpp refresh:', e);
        }
      })();
    }

    // Sidebar buttons
    E.newChatBtn.addEventListener('click', newChat);
    E.newFolderBtn.addEventListener('click', () => createFolder('New Folder'));

    // Folder context menu handlers
    E.menuRenameFolder.addEventListener('click', () => {
      E.folderContextMenu.style.display = 'none';
      if (state.ctxMenuTarget.type === 'folder') {
        startFolderRename(state.ctxMenuTarget.folderId);
      }
    });
    E.menuAddSubfolder.addEventListener('click', () => {
      E.folderContextMenu.style.display = 'none';
      if (state.ctxMenuTarget.type === 'folder') {
        createFolder('New Subfolder', state.ctxMenuTarget.folderId);
      }
    });
    E.menuDeleteFolder.addEventListener('click', () => {
      E.folderContextMenu.style.display = 'none';
      if (state.ctxMenuTarget.type === 'folder') {
        const fid = state.ctxMenuTarget.folderId;
        if (confirm('Delete this folder? Contents will be moved to the parent level.')) {
          deleteFolder(fid, false);
        }
      }
    });

    // Folder context menu: Context Settings
    E.menuFolderContextSettings.addEventListener('click', () => {
      E.folderContextMenu.style.display = 'none';
      if (state.ctxMenuTarget.type === 'folder') {
        openFolderContextSettings(state.ctxMenuTarget.folderId);
      }
    });

    // Message context menu: Add to Folder Memory
    E.menuAddToFolderMemory.addEventListener('click', async () => {
      E.msgContextMenu.style.display = 'none';
      const idx = state.ctxMenuTarget.index;
      if (typeof idx !== 'number') return;
      const msg = state.messages[idx];
      if (!msg) return;
      const folderId = state.chatFolderMap[state.currentChatFile];
      if (!folderId) return;
      try {
        await fetch(`/api/folders/${folderId}/memory/add`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: msg.content.substring(0, 500),
            source: state.currentChatFile || 'manual',
          }),
        });
        // Brief visual confirmation
        const badge = E.folderBadge;
        if (badge && badge.style.display !== 'none') {
          const orig = badge.textContent;
          badge.textContent = orig + ' (saved)';
          setTimeout(() => { badge.textContent = orig; }, 1500);
        }
      } catch (e) {
        console.error('Add to folder memory failed:', e);
      }
    });

    // Message context menu: Save as Folder Prompt
    E.menuSaveAsFolderPrompt.addEventListener('click', async () => {
      E.msgContextMenu.style.display = 'none';
      const idx = state.ctxMenuTarget.index;
      if (typeof idx !== 'number') return;
      const msg = state.messages[idx];
      if (!msg || msg.role !== 'assistant') return;
      const folderId = state.chatFolderMap[state.currentChatFile];
      if (!folderId) return;
      const name = prompt('Name for this saved prompt (e.g. "Full context", "Compact"):');
      if (!name || !name.trim()) return;
      try {
        const res = await fetch(`/api/folders/${folderId}/prompts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name.trim(), content: msg.content }),
        });
        const data = await res.json();
        if (data.success) {
          const badge = E.folderBadge;
          if (badge && badge.style.display !== 'none') {
            const orig = badge.textContent;
            badge.textContent = orig + ' (prompt saved)';
            setTimeout(() => { badge.textContent = orig; }, 1500);
          }
        } else {
          alert('Failed to save prompt: ' + (data.error || 'Unknown error'));
        }
      } catch (e) {
        console.error('Save as folder prompt failed:', e);
      }
    });

    // Folder context settings panel
    E.closeFolderContextBtn.addEventListener('click', () => closeAllSettingsPanels());

    E.clearMemoryBtn.addEventListener('click', async () => {
      const fid = E.folderContextPanel.dataset.folderId;
      if (!fid) return;
      if (!confirm('Clear all memory notes? This cannot be undone.')) return;
      await fetch(`/api/folders/${fid}/memory/clear`, { method: 'POST' });
      renderMemoryNotes([]);
    });

    // Chat context menu: Move to Folder
    E.menuMoveToFolder.addEventListener('click', (e) => {
      E.chatContextMenu.style.display = 'none';
      if (state.ctxMenuTarget.type === 'chat') {
        showMoveToFolderMenu(e.pageX, e.pageY, state.ctxMenuTarget.filename);
      }
    });
    E.menuRemoveFromFolder.addEventListener('click', () => {
      E.chatContextMenu.style.display = 'none';
      if (state.ctxMenuTarget.type === 'chat') {
        moveChatToFolder(state.ctxMenuTarget.filename, null);
      }
    });

    // Header "Load/Unload" visible for llamacpp
    const supportsLoadUnload = (p) => (p === 'llamacpp');
    const isLlama = supportsLoadUnload(E.providerHeader.value);
    E.loadUnloadModelBtn.style.display = isLlama ? '' : 'none';
    E.ctxSizeSelect.style.display = isLlama ? '' : 'none';
    E.providerHeader.addEventListener('change', async () => {
      const show = supportsLoadUnload(E.providerHeader.value);
      E.loadUnloadModelBtn.style.display = show ? '' : 'none';
      E.ctxSizeSelect.style.display = show ? '' : 'none';
      await updateLoadUnloadButtonText();
    });
    E.loadUnloadModelBtn.addEventListener('click', async () => {
      const provider = E.providerHeader.value;
      if (!supportsLoadUnload(provider)) return;

      const btnText = E.loadUnloadModelBtn.textContent.trim();
      const isUnload = btnText.startsWith('Unload');
      const modelName = E.modelHeader.value;

      if (!modelName) return;

      console.log(`Starting ${isUnload ? 'unload' : 'load'} operation for ${provider} model: ${modelName}`);

      // Show loading state immediately
      E.loadUnloadModelBtn.disabled = true;
      if (isUnload) {
        E.loadUnloadModelBtn.innerHTML = 'Unloading<span class="loading-dots"></span>';
      } else {
        E.loadUnloadModelBtn.innerHTML = 'Loading<span class="loading-dots"></span>';
      }

      // Set up a timeout to prevent getting stuck forever
      const timeoutMs = 120000; // 120 second timeout (longer for llamacpp loading large models)
      const controller = new AbortController();
      const timeoutId = setTimeout(() => {
        controller.abort();
        console.error(`Operation timed out after ${timeoutMs/1000} seconds`);
      }, timeoutMs);

      let keepDisabledUntilStatusRefresh = false;
      try {
        let response, json;

        if (provider === 'llamacpp') {
          if (isUnload) {
            console.log('Making llamacpp unload API call...');
            response = await fetch('/api/llamacpp/unload', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ slot_id: 0 }),
              signal: controller.signal
            });
          } else {
            console.log('Making llamacpp load API call...');
            const nCtx = parseInt(E.ctxSizeSelect.value) || 0;
            // Fetch per-model settings to get custom n_gpu_layers (default 99)
            const loadPayload = { model: modelName, n_ctx: nCtx, n_gpu_layers: 99 };
            try {
              const msRes = await fetch(`/api/models/llamacpp/settings/${encodeURIComponent(modelName)}`);
              const msData = await msRes.json();
              if (msData.success && msData.settings && msData.settings.n_gpu_layers !== undefined) {
                loadPayload.n_gpu_layers = msData.settings.n_gpu_layers;
                console.log(`Using custom ngl=${loadPayload.n_gpu_layers} for ${modelName}`);
              }
            } catch (e) {
              console.warn('Could not fetch model settings for ngl:', e);
            }
            response = await fetch('/api/llamacpp/load', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(loadPayload),
              signal: controller.signal
            });
          }
        }

        clearTimeout(timeoutId);
        console.log('API response status:', response.status);

        if (!response.ok) {
          let apiErr = `HTTP ${response.status}: ${response.statusText}`;
          try {
            const errJson = await response.json();
            if (errJson && errJson.error) apiErr = errJson.error;
          } catch (_) {}
          throw new Error(apiErr);
        }

        json = await response.json();
        console.log('API response:', json);

        if (json.success) {
          if (json.loading) {
            // Async load started — let the status poller handle button state
            E.loadUnloadModelBtn.innerHTML = `Loading<span class="loading-dots"></span>`;
            E.loadUnloadModelBtn.disabled = true;
            keepDisabledUntilStatusRefresh = true;
            // Start polling status to detect when loading completes
            setTimeout(async () => {
              try { await updateLoadUnloadButtonText(); } catch (_) {}
            }, 3000);
            return;
          } else if (isUnload) {
            // Unload succeeded — set button to Load immediately
            E.loadUnloadModelBtn.textContent = 'Load';
            E.ctxSizeSelect.style.display = '';
          } else {
            // Load succeeded (synchronous, e.g. router mode) — set button to Unload
            let vramInfo = '';
            try {
              const msRes = await fetch(`/api/models/llamacpp/settings/${encodeURIComponent(modelName)}`);
              const msData = await msRes.json();
              if (msData.success && msData.settings && msData.settings.vram_required_gb !== undefined) {
                vramInfo = formatVramLabel(Number(msData.settings.vram_required_gb));
              }
            } catch (_) {}
            E.loadUnloadModelBtn.textContent = `Unload${vramInfo}`;
            E.ctxSizeSelect.style.display = 'none';

            // Save detected context_window to per-model settings
            if (json.n_ctx > 0) {
              try {
                await fetch(`/api/models/llamacpp/settings/${encodeURIComponent(modelName)}`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ context_window: json.n_ctx })
                });
                console.log(`Saved context_window=${json.n_ctx} for ${modelName}`);
              } catch (e) {
                console.warn('Failed to save context_window setting:', e);
              }
            }
          }
        } else {
          // API returned success: false
          console.error('Operation failed:', json.error || 'Unknown error');
          if (json.error) alert(json.error);
          E.loadUnloadModelBtn.textContent = 'Load';
          E.ctxSizeSelect.style.display = '';
        }

        // Also do a delayed status check to catch any state we missed
        setTimeout(async () => {
          await updateLoadUnloadButtonText();
        }, 3000);

      } catch (error) {
        clearTimeout(timeoutId);
        console.error(`Error with ${provider} control:`, error);
        alert(error.message || `Failed to ${isUnload ? 'unload' : 'load'} model`);

        if (error.name === 'AbortError') {
          console.error('Operation was aborted due to timeout');
        }

        // Restore button to a safe state — use delayed status check to get the real state
        E.loadUnloadModelBtn.textContent = 'Load';
        setTimeout(async () => {
          await updateLoadUnloadButtonText();
        }, 2000);
      } finally {
        if (!keepDisabledUntilStatusRefresh) {
          E.loadUnloadModelBtn.disabled = false;
          console.log('Button re-enabled');
        }
      }
    });
    console.timeEnd('TB:init');
  }

  // Start
  window.addEventListener('DOMContentLoaded', init);
})();  // This closes the entire IIFE that starts on line 13
