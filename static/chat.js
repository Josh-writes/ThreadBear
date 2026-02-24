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
    menuDeleteChat: $('deleteChatMenuItem'),

    msgContextMenu: $('messageContextMenu'),
    menuBranchFull: $('branchFullMenuItem'),
    menuBranchSelected: $('branchSelectedMenuItem'),
    menuSummarize: $('summarizeResponseMenuItem'),
    menuCopySelected: $('copySelectedMenuItem'),
    menuCopy: $('copyResponseMenuItem'),
    menuDelete: $('deleteResponseMenuItem'),
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
  }

  function modelMatchesLoaded(selectedModel, loadedModel) {
    if (!selectedModel || !loadedModel) return false;
    const byName = loadedModel.name || '';
    const byPath = loadedModel.path || '';
    const byBasename = byPath.split('/').pop().split('\\').pop();
    return selectedModel === byName || selectedModel === byPath || selectedModel === byBasename;
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

  function renderHistory() {
    // Clear the chat history container
    clearNode(E.chatHistory);
    
    // Create a map of chats by filename
    const chatMap = {};
    state.history.forEach(chat => {
      chatMap[chat.filename] = chat;
    });
    
    // Create a children map, where each parent chat_id points to a list of its child chats
    const childrenMap = {};
    state.history.forEach(chat => {
      // Use chat_id as the key for parent-child relationships
      const parentId = chat.parent_chat_id;
      if (parentId) {
        if (!childrenMap[parentId]) {
          childrenMap[parentId] = [];
        }
        childrenMap[parentId].push(chat);
      }
    });
    
    // Helper function to render a chat node with proper indentation
    function renderNode(chat, depth) {
      // Create a div with class chat-item
      const div = el('div', 'chat-item');
      
      // Add side-chat class and depth for branches
      if (depth > 0) {
        div.classList.add('side-chat');
        div.setAttribute('data-depth', depth.toString());
      }
      
      // If this chat is the current one, also add the active class
      if (chat.filename === state.currentChatFile) {
        div.classList.add('active');
      }
      
      // Pick the title in this order:
      let title;
      if (chat.title) {
        // Use chat.title if available
        title = chat.title;
      } else if (chat.first_message) {
        // Otherwise, use chat.first_message (first 60 characters)
        title = chat.first_message.substring(0, 60);
      } else {
        // Otherwise, fall back to filename without .json
        title = chat.filename.replace(/\.json$/, '');
      }
      
      // Add left padding based on depth
      div.style.paddingLeft = (12 + (depth * 20)) + 'px';
      
      // Create title element
      const titleDiv = el('div', 'chat-item-title');
      titleDiv.textContent = title;
      div.appendChild(titleDiv);
      
      // Add left-click handler that calls loadChat(chat.filename)
      div.addEventListener('click', () => loadChat(chat.filename));
      
      // Add right-click handler for context menu
      div.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        state.ctxMenuTarget = { type: 'chat', filename: chat.filename };
        openMenuAt(E.chatContextMenu, e.pageX, e.pageY);
      });
      
      // Append this chat-item into the sidebar container
      E.chatHistory.appendChild(div);
      
      // If the chat has children, call renderNode(child, depth+1) for each child
      // Use chat_id to find children instead of filename
      const chatId = chat.chat_id || chat.filename;
      if (childrenMap[chatId]) {
        childrenMap[chatId].forEach(child => {
          renderNode(child, depth + 1);
        });
      }
    }
    
    // Loop through all chats in state.history that have no parent_chat_id and call renderNode(chat, 0)
    state.history.forEach(chat => {
      if (!chat.parent_chat_id) {
        renderNode(chat, 0);
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
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    menu.style.display = 'block';
    if (onOpen) onOpen();
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
        if (settings.n_gpu_layers !== undefined) {
          E.nglInput.value = settings.n_gpu_layers;
        } else {
          E.nglInput.value = '';
        }
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
          renderMessages(); // Only re-render once when model name arrives
          // Re-get the bubble reference after re-rendering
          state.currentStreamingBubble = E.messages.querySelector(`.message.assistant[data-index="${idx}"] .message-bubble`);
        } else if (data.type === 'content') {
          // Update state
          state.messages[idx].content += data.content || '';
          // Update ONLY the specific bubble DOM element directly
          if (state.currentStreamingBubble) {
            try {
              state.currentStreamingBubble.innerHTML = marked.parse(state.messages[idx].content);
            } catch {
              state.currentStreamingBubble.textContent = state.messages[idx].content;
            }
            E.messages.scrollTop = E.messages.scrollHeight;
          }
        } else if (data.type === 'complete') {
          src.close();
          state.streaming = false;
          hide(E.cancelBtn);
          show(E.sendBtn);
          state.currentStreamingBubble = null; // Clean up reference
          renderMessages(); // Final render to ensure everything is correct
          afterMessageSettled();
        } else if (data.type === 'error') {
          src.close();
          state.streaming = false;
          hide(E.cancelBtn);
          show(E.sendBtn);
          state.currentStreamingBubble = null; // Clean up reference
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
    if (!state.streaming) return;
    await fetch('/api/chat/cancel', { method: 'POST' });
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
  function bindSettingsPanels() {
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
      E.settingsPanel.classList.add('open');
      
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
    
    E.closeSettingsBtn.addEventListener('click', () => E.settingsPanel.classList.remove('open'));

    // Open System Prompts
    E.openPromptsSettingsBtn.addEventListener('click', async () => {
      E.settingsDropdown.classList.remove('open');
      E.promptsSettingsPanel.classList.add('open');
      await loadPromptsList();
    });

    E.closePromptsSettingsBtn.addEventListener('click', () => {
      E.promptsSettingsPanel.classList.remove('open');
      E.promptEditor.style.display = 'none';
    });

    // Open Appearance Settings  
    E.openAppearanceSettingsBtn.addEventListener('click', () => {
      E.settingsDropdown.classList.remove('open');
      E.systemSettingsPanel.classList.add('open');
    });

    E.closeSystemSettingsBtn.addEventListener('click', () => E.systemSettingsPanel.classList.remove('open'));

    // ===== Browse Models Panel (multi-provider) =====
    let _browseDebounce = null;
    const BROWSE_PROVIDERS = ['openrouter', 'groq'];
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
      else if (sort === 'context') list.sort((a, b) => (b.context_length || 0) - (a.context_length || 0));
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
        ctxBadge.textContent = formatContextLength(m.context_length);

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
      E.openrouterBrowsePanel.classList.add('open');
      await loadBrowseCatalog();
    });

    E.closeBrowseModelsBtn.addEventListener('click', () => {
      E.openrouterBrowsePanel.classList.remove('open');
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

        // GPU layers (llamacpp only, -1 = all, blank = default/all)
        const nglVal = E.nglInput.value.trim();
        if (nglVal !== '') {
          const ngl = parseInt(nglVal);
          if (ngl >= -1) {
            settings.n_gpu_layers = ngl;
          }
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

      if (provider === 'llamacpp') {
        try {
          const statusResp = await fetch('/api/llamacpp/status');
          const status = await statusResp.json();
          const loadedModel = (status.loaded_models || [])[0];
          if (status.success && loadedModel && !modelMatchesLoaded(m, loadedModel)) {
            E.loadUnloadModelBtn.disabled = true;
            E.loadUnloadModelBtn.innerHTML = 'Unloading<span class="loading-dots"></span>';
            await fetch('/api/llamacpp/unload', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ slot_id: 0 })
            });
          }
        } catch (err) {
          console.warn('Could not auto-unload previous model on selection change:', err);
        }
      }
      
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

  // ADD this full function (place it near other helpers)
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

        // Server is loading a model in the background — show loading state and keep polling
        if (status.loading) {
          const modelInfo = status.loading_model ? ` ${status.loading_model}` : '';
          E.loadUnloadModelBtn.innerHTML = `Loading${modelInfo}<span class="loading-dots"></span>`;
          E.loadUnloadModelBtn.disabled = true;
          E.ctxSizeSelect.style.display = 'none';
          // Keep polling until loading is done
          setTimeout(async () => {
            try { await updateLoadUnloadButtonText(); } catch (_) {}
          }, 3000);
          return;
        }

        if (status.success && status.loaded_models && status.loaded_models.length > 0) {
          const loadedModel = status.loaded_models[0];
          if (!modelMatchesLoaded(E.modelHeader.value, loadedModel)) {
            E.loadUnloadModelBtn.textContent = 'Load';
            E.loadUnloadModelBtn.disabled = false;
            E.ctxSizeSelect.style.display = '';
            return;
          }
          const vramInfo = formatVramLabel(Number(loadedModel.vram_required_gb));
          E.loadUnloadModelBtn.textContent = `Unload${vramInfo}`;
          E.loadUnloadModelBtn.disabled = false;
          E.ctxSizeSelect.style.display = 'none';
        } else if (status.success && status.server_running) {
          E.loadUnloadModelBtn.textContent = 'Load';
          E.loadUnloadModelBtn.disabled = false;
          E.ctxSizeSelect.style.display = '';
        } else if (status.ssh_enabled) {
          E.loadUnloadModelBtn.textContent = 'Start Server';
          E.loadUnloadModelBtn.disabled = false;
          E.ctxSizeSelect.style.display = 'none';
        } else {
          E.loadUnloadModelBtn.textContent = 'Server Offline';
          E.loadUnloadModelBtn.disabled = true;
          E.ctxSizeSelect.style.display = 'none';
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
    window.addEventListener('scroll', () => { E.chatContextMenu.style.display = 'none'; E.msgContextMenu.style.display = 'none'; });
    window.addEventListener('resize', () => { E.chatContextMenu.style.display = 'none'; E.msgContextMenu.style.display = 'none'; });

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
    const [configResult, promptsResult, historyResult, docsResult, messagesResult] = await Promise.allSettled([
      loadConfigAndModels(),
      loadPrompts(),
      loadHistory(),
      loadDocs(),
      loadMessages()
    ]);

    console.timeEnd('TB:parallelLoad');

    // Handle any failures gracefully
    if (configResult.status === 'rejected') console.warn('Config load failed:', configResult.reason);
    if (promptsResult.status === 'rejected') console.warn('Prompts load failed:', promptsResult.reason);
    if (historyResult.status === 'rejected') console.warn('History load failed:', historyResult.reason);
    if (docsResult.status === 'rejected') console.warn('Docs load failed:', docsResult.reason);
    if (messagesResult.status === 'rejected') console.warn('Messages load failed:', messagesResult.reason);

    // Initialize sidebar after config is loaded
    if (configResult.status === 'fulfilled') {
      await initSidebarFromConfig();
    }

    // Update context summary after docs are loaded
    if (docsResult.status === 'fulfilled') {
      await updateContextTokenSummary();
    }

    // Handle llamacpp refresh asynchronously in background
    if (state.currentProvider === 'llamacpp') {
      (async () => {
        try {
          await fetch('/api/llamacpp/refresh', { method: 'POST' });
          await refreshModelsHeader('llamacpp', E.modelHeader.value);
          await refreshModelsSettings('llamacpp');
          await updateLoadUnloadButtonText();
        } catch (e) {
          console.warn('Skipping initial llamacpp refresh:', e);
        }
      })();
    }

    // Sidebar buttons
    E.newChatBtn.addEventListener('click', newChat);

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
      const isStartServer = (btnText === 'Start Server');
      const isUnload = btnText.startsWith('Unload');
      const modelName = E.modelHeader.value;

      // "Start Server" doesn't need a model selected
      if (!isStartServer && !modelName) return;

      // Handle "Start Server" action
      if (isStartServer) {
        E.loadUnloadModelBtn.disabled = true;
        E.loadUnloadModelBtn.innerHTML = 'Starting<span class="loading-dots"></span>';
        try {
          const resp = await fetch('/api/llamacpp/server/start', { method: 'POST' });
          const json = await resp.json();
          if (json.success) {
            // Server starting in background — keep button disabled, poll status
            E.loadUnloadModelBtn.innerHTML = 'Starting<span class="loading-dots"></span>';
            // Poll status until server is ready
            setTimeout(async () => {
              try { await updateLoadUnloadButtonText(); } catch (_) {}
            }, 3000);
          } else {
            console.error('Failed to start server:', json.error);
            E.loadUnloadModelBtn.textContent = 'Start Server';
            E.loadUnloadModelBtn.disabled = false;
          }
        } catch (err) {
          console.error('Error starting server:', err);
          E.loadUnloadModelBtn.textContent = 'Start Server';
          E.loadUnloadModelBtn.disabled = false;
        }
        return;
      }

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
            // Fetch per-model settings to get custom n_gpu_layers
            const loadPayload = { model: modelName, n_ctx: nCtx };
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
