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

  // Open external links in the user's default browser (new tab)
  marked.use({
    renderer: {
      link({ href, title, tokens }) {
        const text = this.parser.parseInline(tokens);
        const titleAttr = title ? ` title="${title}"` : '';
        return `<a href="${href}"${titleAttr} target="_blank" rel="noopener noreferrer">${text}</a>`;
      }
    }
  });

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
    activeFolder: null,  // Currently selected folder for overview view

    // prompts
    prompts: [],

    // ui caches
    menus: {
      chat: $('contextMenu'),
      message: $('messageContextMenu')
    },
    ctxMenuTarget: { type: null, index: null, filename: null },
    toolboxTarget: null,

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

    // Tools panel
    toolsSettingsPanel: $('toolsSettingsPanel'),
    closeToolsSettingsBtn: $('closeToolsSettingsBtn'),
    openToolsSettingsBtn: $('openToolsSettingsBtn'),
    toolsEnabledCheckbox: $('toolsEnabledCheckbox'),
    toolsProviderHint: $('toolsProviderHint'),
    toolsStatus: $('toolsStatus'),
    toolsToggle: $('toolsToggle'),
    toolsToggleLabel: $('toolsToggleLabel'),
    toolboxFileList: $('toolboxFileList'),
    toolboxRefreshBtn: $('toolboxRefreshBtn'),
    toolboxContextMenu: $('toolboxContextMenu'),
    toolboxCopyContents: $('toolboxCopyContents'),
    toolboxOpenNotepad: $('toolboxOpenNotepad'),
    toolboxEditInChat: $('toolboxEditInChat'),
    toolboxDeleteFile: $('toolboxDeleteFile'),

    // System Settings panel (appearance)
    systemSettingsPanel: $('systemSettingsPanel'),
    closeSystemSettingsBtn: $('closeSystemSettingsBtn'),
    lightThemeBtn: $('lightThemeBtn'),
    darkThemeBtn: $('darkThemeBtn'),
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
    menuOpenPromptBranch: $('openPromptBranchMenuItem'),
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

    // Usage details panel
    menuViewUsageDetails: $('menuViewUsageDetails'),
    usageDetailsPanel: $('usageDetailsPanel'),
    closeUsageDetailsBtn: $('closeUsageDetailsBtn'),
    usageTotalTokens: $('usageTotalTokens'),
    usageTotalCost: $('usageTotalCost'),
    usageMessageList: $('usageMessageList'),

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

  function isCurrentChatPromptBranch() {
    const folderId = state.chatFolderMap[state.currentChatFile];
    if (!folderId) return false;
    const folder = findFolderById(folderId);
    return folder && folder.prompt_branch_filename === state.currentChatFile;
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

  function messageNode(msg, index) {
  const row = el('div', `message ${msg.role}`);
  row.dataset.index = index;

  // ===== Model label for assistant messages =====
  if (msg.role === 'assistant') {
    const label = el('div', 'model-label');

    // Provider prefix (if available)
    const provider = msg.provider || '';
    const modelName = msg.model || '';

    // Build label content
    let labelContent = '';
    if (provider && modelName) {
      labelContent = `${provider}: ${modelName}`;
    } else if (modelName) {
      labelContent = modelName;
    } else {
      labelContent = 'Assistant';
    }

    // Add summary indicator
    if (msg.isSummary || msg.summary) {
      labelContent += ' (Summary)';
    }

    // Add token info if available
    if (msg.usage) {
      const totalTok = msg.usage.input_tokens + msg.usage.output_tokens;
      labelContent += ` • ${totalTok} tok`;

      // Add tokens/sec if timing available
      if (msg.timing && msg.timing.duration_ms > 0) {
        const tokensPerSec = (totalTok / (msg.timing.duration_ms / 1000)).toFixed(1);
        labelContent += ` (${tokensPerSec} t/s)`;
      }
    }

    label.textContent = labelContent;
    
    // Right-click for details menu
    label.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      state.ctxMenuTarget = { type: 'model_label', messageIndex: index, msg };
      openMenuAt(E.msgContextMenu, e.pageX, e.pageY, () => {
        // Show "View Usage Details" option for model labels
        E.menuViewUsageDetails.style.display = '';
        E.menuBranchFull.style.display = 'none';
        E.menuBranchSelected.style.display = 'none';
        E.menuSummarize.style.display = 'none';
        E.menuCopySelected.style.display = 'none';
        E.menuCopy.style.display = 'none';
        E.menuAddToFolderMemory.style.display = 'none';
        E.menuSaveAsFolderPrompt.style.display = 'none';
        E.menuDelete.style.display = 'none';
      });
    });
    
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

  // ===== Working text (intermediate LLM narration before tools) =====
  if (msg.workingText) {
    const workingWrap = el('div', 'tool-working-text collapsed');
    const workingToggle = el('div', 'tool-working-toggle');
    workingToggle.textContent = '💭 Show working notes';
    workingToggle.addEventListener('click', () => {
      const isCollapsed = workingWrap.classList.contains('collapsed');
      workingWrap.classList.toggle('collapsed');
      workingToggle.textContent = isCollapsed ? '💭 Hide working notes' : '💭 Show working notes';
    });
    workingWrap.appendChild(workingToggle);
    const workingContent = el('div', 'tool-working-content');
    try { workingContent.innerHTML = marked.parse(msg.workingText); }
    catch { workingContent.textContent = msg.workingText; }
    workingWrap.appendChild(workingContent);
    content.appendChild(workingWrap);
  }

  // ===== Tool events ABOVE the bubble =====
  if (msg.tool_events && msg.tool_events.length) {
    const toolContainer = el('div', 'tool-chips-container');
    msg.tool_events.forEach(te => {
      const block = el('div', 'tool-block ' + te.status);
      const header = el('div', 'tool-block-header');
      const icon = te.status === 'running' ? '⏳' : te.status === 'success' ? '✅' : '❌';
      header.innerHTML = `<span class="tool-icon">${icon}</span><span class="tool-name">${te.name}</span>`;
      block.appendChild(header);

      // Args
      if (te.args && Object.keys(te.args).length) {
        const argsEl = el('div', 'tool-block-args');
        argsEl.textContent = Object.entries(te.args).map(([k,v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n');
        block.appendChild(argsEl);
      }

      // Result output
      if (te.result) {
        const resultEl = el('div', 'tool-block-result');
        const r = te.result.result || te.result;  // unwrap {success, result} wrapper
        let output = '';
        if (r.error) {
          output = 'Error: ' + r.error;
        } else if (r.stdout !== undefined) {
          // run_command result
          output = r.stdout || '';
          if (r.stderr) output += (output ? '\n' : '') + 'stderr: ' + r.stderr;
          if (r.exit_code !== undefined && r.exit_code !== 0) output += '\n(exit code ' + r.exit_code + ')';
        } else if (r.content !== undefined) {
          // read_file result
          output = r.truncated ? r.content + '\n... (truncated)' : r.content;
        } else if (r.written) {
          output = 'Wrote ' + r.size + ' bytes to ' + r.written;
        } else if (r.files) {
          // list_directory result
          output = r.files.map(f => (f.type === 'dir' ? '📁 ' : '📄 ') + f.name).join('\n');
        } else {
          output = JSON.stringify(r, null, 2);
        }
        // Truncate very long output for display
        if (output.length > 2000) output = output.substring(0, 2000) + '\n... (truncated)';
        resultEl.textContent = output;
        block.appendChild(resultEl);
      }

      // Toggle expand/collapse
      block.dataset.expanded = 'false';
      const argsChild = block.querySelector('.tool-block-args');
      const resultChild = block.querySelector('.tool-block-result');
      if (argsChild) argsChild.style.display = 'none';
      if (resultChild) resultChild.style.display = 'none';
      header.style.cursor = 'pointer';
      header.addEventListener('click', () => {
        const exp = block.dataset.expanded === 'true';
        block.dataset.expanded = exp ? 'false' : 'true';
        if (argsChild) argsChild.style.display = exp ? 'none' : 'block';
        if (resultChild) resultChild.style.display = exp ? 'none' : 'block';
      });

      toolContainer.appendChild(block);
    });
    content.appendChild(toolContainer);
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
      // Restore all items to default visibility (model label menu hides these)
      E.menuBranchFull.style.display = '';
      E.menuSummarize.style.display = '';
      E.menuCopy.style.display = '';
      E.menuDelete.style.display = '';
      E.menuViewUsageDetails.style.display = 'none';

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
  
  // If viewing a folder overview, don't render messages
  if (state.activeFolder) {
    renderFolderOverview(state.activeFolder);
    return;
  }
  
  const isPromptBranch = isCurrentChatPromptBranch();

  if (!state.messages.length || (isPromptBranch && state.messages.every(m => m.role === 'system'))) {
    if (isPromptBranch) {
      E.messages.appendChild(buildPromptBranchBanner());
    } else {
      const empty = el('div', 'empty-state');
      empty.innerHTML = `
        <h2>Welcome to ThreadBear</h2>
        <p>Start a conversation by typing a message below.</p>
        <p>Press Enter to send, Shift+Enter for new line.</p>
      `;
      E.messages.appendChild(empty);
    }
    return;
  }

  if (isPromptBranch) {
    E.messages.appendChild(buildPromptBranchBanner());
  }

  state.messages.forEach((m, i) => {
    // Hide system messages in prompt branch chats (instructions are shown in banner)
    if (isPromptBranch && m.role === 'system') return;
    const row = messageNode(m, i);
    E.messages.appendChild(row);
  });

  E.messages.scrollTop = E.messages.scrollHeight;
}

  function buildPromptBranchBanner() {
    const banner = el('div', 'prompt-branch-banner');
    banner.innerHTML = `
      <div class="prompt-branch-banner-title">Folder Context Chat</div>
      <div class="prompt-branch-banner-steps">
        <div><strong>1.</strong> Describe the role, knowledge, and behavior the AI should have for chats in this folder.</div>
        <div><strong>2.</strong> Iterate with the AI until the system prompt is how you want it.</div>
        <div><strong>3.</strong> Right-click the assistant message you want to use and select <strong>"Save as Folder Prompt"</strong>.</div>
      </div>
    `;
    return banner;
  }

  // ===== Tool Chip Rendering (Phase 3) =====

  function getOrCreateToolContainer(msgIndex) {
    const msgEl = E.messages.querySelector(`.message.assistant[data-index="${msgIndex}"]`);
    if (!msgEl) return null;
    let container = msgEl.querySelector('.tool-chips-container');
    if (!container) {
      container = el('div', 'tool-chips-container');
      // Insert BEFORE the message-bubble (tools above response)
      const bubble = msgEl.querySelector('.message-bubble');
      if (bubble) {
        bubble.parentNode.insertBefore(container, bubble);
      } else {
        msgEl.querySelector('.message-content')?.appendChild(container);
      }
    }
    return container;
  }

  function appendToolChip(msgIndex, name, args, status) {
    const container = getOrCreateToolContainer(msgIndex);
    if (!container) return;

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
    container.appendChild(chip);
    E.messages.scrollTop = E.messages.scrollHeight;
  }

  function updateToolChip(msgIndex, name, result) {
    const msgEl = E.messages.querySelector(`.message.assistant[data-index="${msgIndex}"]`);
    if (!msgEl) return;
    const chip = msgEl.querySelector(`.tool-chip[data-tool-name="${name}"].running`);
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
        await loadHistory();
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
    // Also set as active folder and show overview
    state.activeFolder = folderId;
    state.currentChatFile = null;
    renderHistory();
    renderFolderOverview(folderId);
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

  function showUsageDetails(selectedMessageIndex) {
    // Calculate conversation totals
    let totalInputTokens = 0;
    let totalOutputTokens = 0;
    let totalCost = 0;

    const messageItems = [];

    state.messages.forEach((msg, idx) => {
      if (msg.role === 'assistant' && msg.usage) {
        const inputTok = msg.usage.input_tokens || 0;
        const outputTok = msg.usage.output_tokens || 0;
        const msgCost = msg.cost || 0;

        totalInputTokens += inputTok;
        totalOutputTokens += outputTok;
        totalCost += msgCost;

        messageItems.push({
          index: idx,
          model: msg.model || 'Unknown',
          provider: msg.provider || '',
          inputTokens: inputTok,
          outputTokens: outputTok,
          totalTokens: inputTok + outputTok,
          cost: msgCost,
          isTarget: idx === selectedMessageIndex,
        });
      }
    });

    // Update totals
    E.usageTotalTokens.textContent = (totalInputTokens + totalOutputTokens).toLocaleString();
    E.usageTotalCost.textContent = totalCost < 0.01 
      ? `$${totalCost.toFixed(4)}` 
      : `$${totalCost.toFixed(3)}`;

    // Build message list
    clearNode(E.usageMessageList);

    if (messageItems.length === 0) {
      const empty = el('div', 'folder-empty-message');
      empty.textContent = 'No usage data available for this conversation.';
      E.usageMessageList.appendChild(empty);
    } else {
      // Show most recent first
      messageItems.reverse().forEach(item => {
        const msgItem = el('div', 'usage-message-item' + (item.isTarget ? ' active' : ''));

        const header = el('div', 'usage-message-header');
        const modelSpan = el('span', 'usage-message-model');
        modelSpan.textContent = item.provider ? `${item.provider}: ${item.model}` : item.model;
        header.appendChild(modelSpan);

        const stats = el('div', 'usage-message-stats');

        const tokensStat = el('div', 'usage-stat');
        const tokLabel = el('div', 'usage-stat-label');
        tokLabel.textContent = 'Tokens';
        const tokValue = el('div', 'usage-stat-value');
        tokValue.textContent = item.totalTokens.toLocaleString();
        tokensStat.appendChild(tokLabel);
        tokensStat.appendChild(tokValue);
        stats.appendChild(tokensStat);

        const costStat = el('div', 'usage-stat');
        const costLabel = el('div', 'usage-stat-label');
        costLabel.textContent = 'Cost';
        const costValue = el('div', 'usage-stat-value cost');
        costValue.textContent = item.cost < 0.01 
          ? `$${item.cost.toFixed(4)}` 
          : `$${item.cost.toFixed(3)}`;
        costStat.appendChild(costLabel);
        costStat.appendChild(costValue);
        stats.appendChild(costStat);

        header.appendChild(stats);
        msgItem.appendChild(header);

        // Click to scroll to message
        msgItem.style.cursor = 'pointer';
        msgItem.addEventListener('click', () => {
          closeAllSettingsPanels();
          const messageEl = E.messages.querySelector(`.message.assistant[data-index="${item.index}"]`);
          if (messageEl) {
            messageEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // Flash highlight
            messageEl.style.transition = 'background 0.3s';
            messageEl.style.background = 'rgba(16, 163, 127, 0.2)';
            setTimeout(() => {
              messageEl.style.background = '';
            }, 1500);
          }
        });

        E.usageMessageList.appendChild(msgItem);
      });
    }

    openSettingsPanel(E.usageDetailsPanel);
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
    const promptFn = folder.prompt_branch_filename;
    const chatCount = Object.entries(state.chatFolderMap).filter(([fn, fid]) => fid === folder.id && fn !== promptFn).length;
    const fileCount = Object.values(state.fileFolderMap).filter(fid => fid === folder.id).length;
    const childCount = (folder.children || []).length;
    const totalCount = chatCount + fileCount + childCount;

    // Folder header row
    const div = el('div', 'folder-item' + (depth > 0 ? ' subfolder' : '') + (state.activeFolder === folder.id ? ' active-folder' : ''));
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

      // Prompt branch is hidden from the list — accessible via folder right-click menu
      const folderChats = state.history.filter(c => state.chatFolderMap[c.filename] === folder.id && c.filename !== promptFn);

      // Render chats (excluding prompt branch)
      folderChats.forEach(chat => {
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

    // Build set of chats that are in folders
    const chatsInFolders = new Set(Object.keys(state.chatFolderMap));

    // Build set of child chats (have parent_chat_id)
    const childChats = new Set();
    state.history.forEach(chat => {
      if (chat.parent_chat_id) childChats.add(chat.filename);
    });

    // Render folders section FIRST
    if (state.folders.length > 0) {
      state.folders.forEach(folder => {
        renderFolderNode(folder, E.chatHistory, 0);
      });

      // Divider between folders and unfiled chats
      const unfiledChats = state.history.filter(c => !chatsInFolders.has(c.filename) && !c.parent_chat_id);
      if (unfiledChats.length > 0) {
        const label = el('div', 'folder-section-label');
        label.textContent = 'Chats';
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

    // Hide model dropdown for llamacpp (models loaded manually on server)
    const hideLlamacpp = state.currentProvider === 'llamacpp';
    E.modelHeader.style.display = hideLlamacpp ? 'none' : '';
    if (E.refreshModelsBtn) E.refreshModelsBtn.style.display = hideLlamacpp ? 'none' : '';

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
    if (!hideLlamacpp) applyModelsToSelect(E.modelHeader, models, state.currentModel);

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

  async function newChatInFolder(folderId) {
    try {
      const js = await postJSON('/api/chat/new', { folder_id: folderId });
      state.currentChatFile = js.filename || js.chat_file || state.currentChatFile;
      state.activeFolder = null;
      state.messages = [];
      showInputArea(true);
      await loadHistory();
      await loadFolders();
      state.expandedFolders.add(folderId);
      renderChatTitle();
      renderHistory();
      renderMessages();
    } catch (e) {
      alert('Failed to create new chat in folder: ' + String(e));
    }
  }

  async function loadChat(filename) {
    const js = await getJSON(`/api/chat/load/${encodeURIComponent(filename)}`);
    if (js.success === false) return alert(js.error || 'Failed to load chat');
    state.currentChatFile = filename;
    state.activeFolder = null;  // Clear active folder when loading a chat
    state.messages = js.messages || [];
    renderChatTitle();
    renderHistory();
    renderMessages();
    showInputArea(true);  // Ensure input area is visible
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

  // ====== Folder Overview UI ======
  
  function showInputArea(visible) {
    const inputArea = document.querySelector('.input-area');
    if (inputArea) {
      inputArea.style.display = visible ? '' : 'none';
    }
  }

  function renderFolderOverview(folderId) {
    const folder = findFolderById(folderId);
    if (!folder) return;

    clearNode(E.messages);
    hideInputArea();

    const container = el('div', 'folder-overview-container');

    // Header with folder name and new chat button
    const header = el('div', 'folder-overview-header');
    const title = el('h2', 'folder-overview-title');
    title.textContent = folder.name;
    header.appendChild(title);

    const newChatBtn = el('button', 'control-btn folder-new-chat-btn');
    newChatBtn.textContent = '+ New Chat';
    newChatBtn.addEventListener('click', () => newChatInFolder(folderId));
    header.appendChild(newChatBtn);

    container.appendChild(header);

    // Chat list section
    const chatsSection = el('div', 'folder-section');
    const chatsHeader = el('h3', 'folder-section-header');
    chatsHeader.textContent = 'Chats';
    chatsSection.appendChild(chatsHeader);

    const chatList = el('div', 'folder-chat-list');
    const folderChats = state.history.filter(c => 
      state.chatFolderMap[c.filename] === folderId && 
      c.filename !== folder.prompt_branch_filename
    );

    if (folderChats.length === 0) {
      const empty = el('div', 'folder-empty-message');
      empty.textContent = 'No chats in this folder yet. Create one!';
      chatList.appendChild(empty);
    } else {
      folderChats.forEach(chat => {
        const chatItem = el('div', 'folder-chat-item' + (chat.filename === state.currentChatFile ? ' active' : ''));
        chatItem.textContent = chat.title || chat.filename.replace(/\.json$/, '');
        chatItem.addEventListener('click', () => loadChat(chat.filename));
        chatList.appendChild(chatItem);
      });
    }
    chatsSection.appendChild(chatList);
    container.appendChild(chatsSection);

    // Documents section with drop zone
    const docsSection = el('div', 'folder-section');
    const docsHeader = el('h3', 'folder-section-header');
    docsHeader.textContent = 'Documents';
    docsSection.appendChild(docsHeader);

    const dropZone = el('div', 'folder-drop-zone');
    dropZone.textContent = 'Drag & drop files here';
    
    const docList = el('div', 'folder-doc-list');
    const folderDocs = Object.keys(state.fileFolderMap).filter(fn => state.fileFolderMap[fn] === folderId);
    
    if (folderDocs.length === 0) {
      const empty = el('div', 'folder-empty-message');
      empty.textContent = 'No documents. Drop files here or use the upload button.';
      docList.appendChild(empty);
    } else {
      folderDocs.forEach(fn => {
        const docItem = el('div', 'folder-doc-item');
        docItem.innerHTML = `<span>📄</span><span>${fn}</span>`;
        docList.appendChild(docItem);
      });
    }

    dropZone.appendChild(docList);
    docsSection.appendChild(dropZone);
    container.appendChild(docsSection);

    // Active prompt preview section
    const promptSection = el('div', 'folder-section');
    const promptHeader = el('h3', 'folder-section-header');
    promptHeader.textContent = 'Active Prompt';
    promptSection.appendChild(promptHeader);

    const promptInfo = el('div', 'folder-prompt-info');
    const activePromptId = folder.active_prompt_id;
    const savedPrompts = folder.saved_prompts || [];
    const activePrompt = savedPrompts.find(p => p.id === activePromptId);

    if (activePrompt) {
      const promptName = el('div', 'folder-prompt-name');
      promptName.textContent = activePrompt.name;
      promptInfo.appendChild(promptName);

      const promptPreview = el('div', 'folder-prompt-preview');
      promptPreview.textContent = activePrompt.content.substring(0, 200) + (activePrompt.content.length > 200 ? '...' : '');
      promptInfo.appendChild(promptPreview);
    } else {
      const noPrompt = el('div', 'folder-empty-message');
      noPrompt.textContent = 'No active prompt. Use the prompt branch to create one.';
      promptInfo.appendChild(noPrompt);
    }
    promptSection.appendChild(promptInfo);
    container.appendChild(promptSection);

    // Memory notes section
    const memorySection = el('div', 'folder-section');
    const memoryHeader = el('h3', 'folder-section-header');
    memoryHeader.textContent = 'Memory Notes';
    memorySection.appendChild(memoryHeader);

    const memoryCount = el('div', 'folder-memory-count');
    const notesCount = folder.memory_notes ? folder.memory_notes.length : 0;
    memoryCount.textContent = notesCount + ' note' + (notesCount !== 1 ? 's' : '');
    memorySection.appendChild(memoryCount);
    container.appendChild(memorySection);

    // Setup drag and drop
    setupFolderDropZone(dropZone, folderId);

    E.messages.appendChild(container);
  }

  function hideInputArea() {
    showInputArea(false);
  }

  function setupFolderDropZone(dropZone, folderId) {
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
      dropZone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
      }, false);
    });

    dropZone.addEventListener('dragover', (e) => {
      dropZone.classList.add('drop-zone-active');
    });

    dropZone.addEventListener('dragleave', (e) => {
      if (e.target === dropZone || !dropZone.contains(e.relatedTarget)) {
        dropZone.classList.remove('drop-zone-active');
      }
    });

    dropZone.addEventListener('drop', async (e) => {
      dropZone.classList.remove('drop-zone-active');
      const files = e.dataTransfer.files;

      if (files.length > 0) {
        for (let file of files) {
          try {
            // Upload the file
            const formData = new FormData();
            formData.append('file', file);

            const uploadRes = await fetch('/api/context/docs/upload', {
              method: 'POST',
              body: formData
            });

            const uploadData = await uploadRes.json();
            if (uploadData.success && uploadData.document) {
              const doc = uploadData.document;
              // Assign to folder using the document's filename
              await fetch(`/api/folders/${folderId}/files`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: doc.name })
              });
              // Refresh folders and re-render
              await loadFolders();
              renderFolderOverview(folderId);
            }
          } catch (err) {
            console.error('Upload failed:', err);
            alert('Failed to upload file: ' + String(err));
          }
        }
      }
    });
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
      model: state.currentModel || E.modelHeader.value,
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
    const streamStartTime = Date.now();  // Track start time for tokens/sec
    state.messages.push({ 
      role: 'assistant', 
      content: '', 
      timestamp: ts, 
      model: state.currentModel,
      provider: E.providerHeader.value  // Store provider
    });
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
          // If bubble has accumulated content before tools, save it as working text
          const currentContent = (state.messages[idx].content || '').trim();
          if (currentContent && !state.messages[idx]._toolsSeen) {
            // First tool_start: move current content to workingText
            state.messages[idx].workingText = (state.messages[idx].workingText || '') +
              (state.messages[idx].workingText ? '\n\n' : '') + currentContent;
            state.messages[idx].content = '';
            // Clear the bubble visually
            if (state.currentStreamingBubble) {
              state.currentStreamingBubble.innerHTML = '';
            }
          }
          state.messages[idx]._toolsSeen = true;
          // Store in state so it survives renderMessages()
          if (!state.messages[idx].tool_events) state.messages[idx].tool_events = [];
          state.messages[idx].tool_events.push({
            name: data.name, args: data.args, status: 'running', result: null
          });
          appendToolChip(idx, data.name, data.args, 'running');
        } else if (data.type === 'tool_end') {
          // Update the matching tool event in state
          if (state.messages[idx].tool_events) {
            const te = [...state.messages[idx].tool_events].reverse()
              .find(t => t.name === data.name && t.status === 'running');
            if (te) { te.status = data.result?.success ? 'success' : 'error'; te.result = data.result; }
          }
          updateToolChip(idx, data.name, data.result);
        } else if (data.type === 'title') {
          const ti = state.history.findIndex(c => c.filename === (data.filename || state.currentChatFile));
          if (ti !== -1) state.history[ti].title = data.title;
          renderChatTitle();
          renderHistory();
        } else if (data.type === 'complete') {
          if (data.usage) {
            state.messages[idx].usage = data.usage;
            // Calculate timing for tokens/sec
            const duration = Date.now() - streamStartTime;
            state.messages[idx].timing = {
              duration_ms: duration,
              tokens_per_sec: data.usage.input_tokens && data.usage.output_tokens
                ? ((data.usage.input_tokens + data.usage.output_tokens) / (duration / 1000)).toFixed(1)
                : null
            };
          }
          if (data.cost != null) {
            state.messages[idx].cost = data.cost;
          }
          src.close();
          state.streaming = false;
          hide(E.cancelBtn);
          show(E.sendBtn);
          state.currentStreamingBubble = null;
          // Clean up streaming-only flag
          delete state.messages[idx]._toolsSeen;
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

  }

  // ====== Settings bindings ======
  // --- Settings panel overlay helpers ---
  const ALL_SETTINGS_PANELS = () => [
    E.settingsPanel, E.promptsSettingsPanel,
    E.systemSettingsPanel, E.openrouterBrowsePanel,
    E.folderContextPanel, E.usageDetailsPanel,
    E.toolsSettingsPanel,
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

    // Open System Settings
    E.openAppearanceSettingsBtn.addEventListener('click', () => {
      E.settingsDropdown.classList.remove('open');
      openSettingsPanel(E.systemSettingsPanel);
    });

    E.closeSystemSettingsBtn.addEventListener('click', () => closeAllSettingsPanels());

    // Open Tools Settings
    E.openToolsSettingsBtn.addEventListener('click', () => {
      E.settingsDropdown.classList.remove('open');
      openSettingsPanel(E.toolsSettingsPanel);
      updateToolsUI();
      loadToolbox();
    });

    E.closeToolsSettingsBtn.addEventListener('click', () => closeAllSettingsPanels());

    // ===== Browse Models Panel (multi-provider) =====
    let _browseDebounce = null;
    const BROWSE_PROVIDERS = ['openrouter', 'groq', 'google', 'mistral'];
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

      // Hide model selection for llamacpp
      const modelSelGrp = $('modelSelectionGroup');
      if (modelSelGrp) modelSelGrp.style.display = p === 'llamacpp' ? 'none' : '';

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

    // Hide model dropdown + refresh for llamacpp (models loaded manually on server)
    function updateLlamacppVisibility(provider) {
      const hide = provider === 'llamacpp';
      E.modelHeader.style.display = hide ? 'none' : '';
      if (E.refreshModelsBtn) E.refreshModelsBtn.style.display = hide ? 'none' : '';
      const modelSelGrp = $('modelSelectionGroup');
      if (modelSelGrp) modelSelGrp.style.display = hide ? 'none' : '';
    }

    // Header provider/model
    E.providerHeader.addEventListener('change', async () => {
      const p = E.providerHeader.value;
      state.currentProvider = p;  // Keep state in sync
      updateLlamacppVisibility(p);

      // Mirror to settings immediately
      E.providerSelect.value = p;

      // Persist provider
      await postJSON('/api/config/update', { provider: p });

      // Rebuild header + settings lists from backend
      if (p === 'llamacpp') {
        // Query server for the actual loaded model
        const data = await getJSON(`/api/models/llamacpp`);
        state.currentModel = data.current_model || '';
      } else {
        await refreshModelsHeader(p, null);
      }
      await refreshModelsSettings(p);
      updateToolsUI();
    });
    E.modelHeader.addEventListener('change', async () => {
      const m = E.modelHeader.value;
      const provider = E.providerHeader.value;
      state.currentModel = m;
      await postJSON('/api/config/update', { provider, model: m });

      // Update max tokens label for the selected model
      await updateModelMaxTokensLabel(provider, m);
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
        if (provider === 'llamacpp') {
          // Refresh models by querying the running llama-server
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

    // --- llama.cpp URL setting ---
    const llamacppUrlInput = $('llamacppUrlInput');
    const llamacppUrlSaveBtn = $('llamacppUrlSaveBtn');
    const llamacppUrlStatus = $('llamacppUrlStatus');

    if (llamacppUrlInput) {
      // Load URL when system settings panel opens
      E.openAppearanceSettingsBtn.addEventListener('click', async () => {
        try {
          const resp = await fetch('/api/llamacpp/url');
          const data = await resp.json();
          if (data.success) {
            llamacppUrlInput.value = data.llamacpp_url || '';
          }
        } catch (e) {
          console.warn('Failed to load llama.cpp URL:', e);
        }
      });

      if (llamacppUrlSaveBtn) {
        llamacppUrlSaveBtn.addEventListener('click', async () => {
          try {
            const resp = await fetch('/api/llamacpp/url', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ llamacpp_url: llamacppUrlInput.value.trim() }),
            });
            const json = await resp.json();
            if (llamacppUrlStatus) {
              llamacppUrlStatus.textContent = json.success ? 'Saved' : 'Error';
              setTimeout(() => { llamacppUrlStatus.textContent = ''; }, 2000);
            }
          } catch (e) {
            console.error('Failed to save llama.cpp URL:', e);
            if (llamacppUrlStatus) llamacppUrlStatus.textContent = 'Error';
          }
        });
      }
    }
  }

  // ====== Tools toggle ======
  const TOOLS_UNSUPPORTED_PROVIDERS = ['google'];

  async function loadToolsConfig() {
    try {
      const res = await fetch('/api/config/tools');
      const data = await res.json();
      if (data.success) {
        state.toolsConfig = data.config || {};
        updateToolsUI();
      }
    } catch (e) {
      console.warn('Failed to load tools config:', e);
    }
  }

  function updateToolsUI() {
    const provider = state.currentProvider;
    const unsupported = TOOLS_UNSUPPORTED_PROVIDERS.includes(provider);
    const enabled = !unsupported && !!(state.toolsConfig || {})[provider];

    // Settings panel checkbox
    if (E.toolsEnabledCheckbox) {
      E.toolsEnabledCheckbox.checked = enabled;
      E.toolsEnabledCheckbox.disabled = unsupported;
    }
    if (E.toolsProviderHint) {
      E.toolsProviderHint.textContent = unsupported
        ? 'Not supported for ' + provider[0].toUpperCase() + provider.slice(1)
        : '';
    }
    // Input area toggle
    if (E.toolsToggle) {
      E.toolsToggle.classList.toggle('active', enabled);
      E.toolsToggle.classList.toggle('unsupported', unsupported);
      E.toolsToggle.title = unsupported
        ? 'Tools not supported for ' + provider[0].toUpperCase() + provider.slice(1)
        : enabled ? 'Tools on (click to disable)' : 'Tools off (click to enable)';
    }
  }

  async function loadToolbox() {
    if (!E.toolboxFileList) return;
    try {
      const res = await fetch('/api/toolbox/files');
      const data = await res.json();
      const files = data.files || [];
      if (files.length === 0) {
        E.toolboxFileList.innerHTML = '<span style="font-size: 12px; color: var(--text-muted);">No scripts yet. Ask the LLM to write one!</span>';
        return;
      }
      E.toolboxFileList.innerHTML = files.map(f => {
        const sizeStr = f.size < 1024 ? f.size + ' B' : (f.size / 1024).toFixed(1) + ' KB';
        const dateStr = new Date(f.modified * 1000).toLocaleString();
        return `
          <div class="toolbox-file-item" data-filename="${f.name}"
               style="padding: 8px 10px; border: 1px solid var(--border-color); border-radius: 6px; font-size: 13px; cursor: context-menu;">
            <div style="font-weight: 500; color: var(--text-primary);">📄 ${f.name}</div>
            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 2px;">${sizeStr} &middot; ${dateStr}</div>
          </div>`;
      }).join('');

      // Attach right-click handlers
      E.toolboxFileList.querySelectorAll('.toolbox-file-item').forEach(item => {
        item.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          state.toolboxTarget = item.dataset.filename;
          openMenuAt(E.toolboxContextMenu, e.pageX, e.pageY);
        });
      });
    } catch (e) {
      console.warn('Failed to load toolbox:', e);
      E.toolboxFileList.innerHTML = '<span style="font-size: 12px; color: var(--text-muted);">Failed to load toolbox</span>';
    }
  }

  // Toolbox context menu actions
  if (E.toolboxCopyContents) {
    E.toolboxCopyContents.addEventListener('click', async () => {
      E.toolboxContextMenu.style.display = 'none';
      const name = state.toolboxTarget;
      if (!name) return;
      try {
        const res = await fetch('/api/toolbox/files/' + encodeURIComponent(name));
        const data = await res.json();
        if (data.success) {
          await navigator.clipboard.writeText(data.content);
        }
      } catch (e) { console.warn('Copy failed:', e); }
    });
  }

  if (E.toolboxOpenNotepad) {
    E.toolboxOpenNotepad.addEventListener('click', async () => {
      E.toolboxContextMenu.style.display = 'none';
      const name = state.toolboxTarget;
      if (!name) return;
      try {
        await fetch('/api/toolbox/open/' + encodeURIComponent(name), { method: 'POST' });
      } catch (e) { console.warn('Open failed:', e); }
    });
  }

  if (E.toolboxEditInChat) {
    E.toolboxEditInChat.addEventListener('click', async () => {
      E.toolboxContextMenu.style.display = 'none';
      const name = state.toolboxTarget;
      if (!name) return;
      try {
        const res = await fetch('/api/toolbox/files/' + encodeURIComponent(name));
        const data = await res.json();
        if (data.success) {
          await newChat();
          E.userInput.value = '```\n' + data.content + '\n```';
          E.userInput.focus();
          closeAllSettingsPanels();
        }
      } catch (e) { console.warn('Edit in chat failed:', e); }
    });
  }

  if (E.toolboxDeleteFile) {
    E.toolboxDeleteFile.addEventListener('click', async () => {
      E.toolboxContextMenu.style.display = 'none';
      const name = state.toolboxTarget;
      if (!name) return;
      if (!confirm('Delete ' + name + '?')) return;
      try {
        await fetch('/api/toolbox/files/' + encodeURIComponent(name), { method: 'DELETE' });
        loadToolbox();
      } catch (e) { console.warn('Delete failed:', e); }
    });
  }

  if (E.toolboxRefreshBtn) {
    E.toolboxRefreshBtn.addEventListener('click', () => loadToolbox());
  }

  async function toggleTools(forceState) {
    const provider = state.currentProvider;
    if (TOOLS_UNSUPPORTED_PROVIDERS.includes(provider)) return;
    const enabled = forceState !== undefined ? forceState : !(state.toolsConfig || {})[provider];
    try {
      await fetch('/api/config/tools', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, enabled }),
      });
      if (!state.toolsConfig) state.toolsConfig = {};
      state.toolsConfig[provider] = enabled;
      updateToolsUI();
      if (E.toolsStatus) {
        E.toolsStatus.textContent = enabled ? 'Tools enabled' : 'Tools disabled';
        setTimeout(() => { E.toolsStatus.textContent = ''; }, 2000);
      }
    } catch (e) {
      console.error('Failed to toggle tools:', e);
      if (E.toolsStatus) E.toolsStatus.textContent = 'Error saving';
    }
  }

  if (E.toolsEnabledCheckbox) {
    E.toolsEnabledCheckbox.addEventListener('change', () => {
      toggleTools(E.toolsEnabledCheckbox.checked);
    });
  }

  if (E.toolsToggle) {
    E.toolsToggle.addEventListener('click', () => toggleTools());
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

  // ====== Boot ======
  async function init() {
    console.time('TB:init');
    // Close menus when scrolling/resizing
    window.addEventListener('scroll', () => { E.chatContextMenu.style.display = 'none'; E.msgContextMenu.style.display = 'none'; E.inputContextMenu.style.display = 'none'; E.folderContextMenu.style.display = 'none'; E.moveToFolderMenu.style.display = 'none'; });
    window.addEventListener('resize', () => { E.chatContextMenu.style.display = 'none'; E.msgContextMenu.style.display = 'none'; E.inputContextMenu.style.display = 'none'; E.folderContextMenu.style.display = 'none'; E.moveToFolderMenu.style.display = 'none'; });

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
    const [configResult, promptsResult, historyResult, docsResult, messagesResult, foldersResult, _toolsResult] = await Promise.allSettled([
      loadConfigAndModels(),
      loadPrompts(),
      loadHistory(),
      loadDocs(),
      loadMessages(),
      loadFolders(),
      loadToolsConfig()
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

    // Folder context menu: Open Context Chat (prompt branch)
    E.menuOpenPromptBranch.addEventListener('click', () => {
      E.folderContextMenu.style.display = 'none';
      if (state.ctxMenuTarget.type !== 'folder') return;
      const folder = findFolderById(state.ctxMenuTarget.folderId);
      if (folder && folder.prompt_branch_filename) {
        loadChat(folder.prompt_branch_filename);
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

    // Usage details panel
    E.closeUsageDetailsBtn.addEventListener('click', () => closeAllSettingsPanels());

    E.menuViewUsageDetails.addEventListener('click', () => {
      E.msgContextMenu.style.display = 'none';
      if (state.ctxMenuTarget.type === 'model_label') {
        showUsageDetails(state.ctxMenuTarget.messageIndex);
      }
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

    console.timeEnd('TB:init');
  }

  // Start
  window.addEventListener('DOMContentLoaded', init);
})();  // This closes the entire IIFE that starts on line 13
