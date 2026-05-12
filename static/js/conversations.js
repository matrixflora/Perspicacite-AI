/* Chat history sidebar: load past conversations, add to list, switch to a past conversation, select-mode for bulk delete.
   Also: conversation search (debounced) and per-conversation export-to-markdown. */

let isSelectMode = false;
let deleteMode = 'all'; // 'all', 'selected', or 'single'
let singleDeleteTarget = null;

function startNewChat() {
    // Generate a new session ID and clear conversation
    sessionId = null;
    conversationId = null;
    messages = [];

    // Clear chat container except welcome message
    const container = document.getElementById('chat-container');
    container.innerHTML = `
        <div class="message assistant">
            Hello! I'm Perspicacité — an AI literature assistant. I will:
            <br><br>
            • Search your selected Knowledge Base first (curated by you)<br>
            • Fall back to web literature search (OpenAlex) when your KB is insufficient<br>
            • Ground answers in retrieved papers and maintain conversation context<br>
            • Let you curate: add selected retrieved papers back into your KB<br><br>
            Try: "What are key metabolites in jasmine?" or "Summarize feature-based molecular networking (FBMN)"
        </div>
    `;

    // Add current chat to history
    addChatToHistory();

    // Focus input
    document.getElementById('query-input').focus();
    console.log('New chat started');
}

function addChatToHistory(convData = null) {
    const historyList = document.getElementById('chat-history-list');
    const now = new Date();
    const nowIso = now.toISOString();
    const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // Use provided data or current state
    const kbName = convData ? (convData.kb_name || 'Web Search') : (selectedKb || 'Web Search');
    const title = convData ? (convData.title || 'Untitled Chat') : `Chat ${historyList.children.length}`;
    const convId = convData ? convData.id : null;
    const createdAt = convData ? (convData.created_at || convData.updated_at) : nowIso;

    // Format date display
    let dateStr;
    if (convData && convData.updated_at) {
        dateStr = formatDate(convData.updated_at);
    } else {
        dateStr = 'Just now';
    }

    // Remove active class from all items
    historyList.querySelectorAll('.chat-history-item').forEach(item => {
        item.classList.remove('active');
    });

    // Add new history item
    const item = document.createElement('div');
    item.className = 'chat-history-item active';
    item.dataset.kb = convData ? (convData.kb_name || '') : (selectedKb || '');
    item.dataset.convId = convId || '';
    item.dataset.createdAt = createdAt;
    item.innerHTML = `
        <span class="chat-title">${title}</span>
        <span class="chat-kb">${kbName === 'default' ? 'Web Search' : kbName}</span>
        <span class="chat-date">${dateStr}</span>
        <button class="delete-chat-btn" onclick="deleteChatItem(this, event)" title="Delete this chat">×</button>
    `;
    item.onclick = function() { loadChatFromHistory(this); };
    historyList.insertBefore(item, historyList.firstChild);
}

async function loadChatFromHistory(item) {
    // Remove active class from all items
    document.querySelectorAll('.chat-history-item').forEach(i => {
        i.classList.remove('active');
    });
    item.classList.add('active');

    // Set conversation ID
    conversationId = item.dataset.convId || null;

    // Restore the KB that was used for this chat
    const kbName = item.dataset.kb;
    if (kbName !== undefined) {
        const kbSelect = document.getElementById('kb-select');
        kbSelect.value = kbName;
        selectedKb = kbName || null;
        console.log('Restored KB:', kbName || 'Web Search');

        // Update KB info display if KB exists
        if (kbName) {
            selectKB(kbName);
        } else {
            document.getElementById('kb-info').style.display = 'none';
            document.getElementById('kb-delete-btn').style.display = 'none';
        }
    }

    // Load conversation messages from backend if we have a conversation ID
    if (conversationId) {
        try {
            const response = await fetch(`/api/conversations/${conversationId}`);
            if (response.ok) {
                const conv = await response.json();

                // Clear and rebuild chat container
                const container = document.getElementById('chat-container');
                container.innerHTML = '';
                messages = [];

                // Add messages
                for (const msg of conv.messages) {
                    addMessage(msg.role, msg.content);
                    messages.push({ role: msg.role, content: msg.content });
                }

                console.log(`Loaded ${conv.messages.length} messages from conversation ${conversationId}`);
            }
        } catch (e) {
            console.error('Failed to load conversation:', e);
        }
    } else {
        // New conversation - show welcome message
        const container = document.getElementById('chat-container');
        container.innerHTML = `
            <div class="message assistant">
                <strong>Welcome to Perspicacité</strong> — Your AI research assistant!
                <br><br>
                <strong>Quick Start:</strong><br>
                • Select a <strong>Knowledge Base</strong> from the sidebar (or use Web Search)<br>
                • Choose a <strong>Search Mode</strong> below:<br>
                &nbsp;&nbsp;📚 <strong>Quick Search</strong> — Fast single-query retrieval<br>
                &nbsp;&nbsp;⚡ <strong>Advanced</strong> — Query rephrasing + hybrid retrieval<br>
                &nbsp;&nbsp;🔬 <strong>Deep Research</strong> — Multi-cycle profound research<br>
                &nbsp;&nbsp;🤖 <strong>Agentic</strong> — Intent-based with tool use<br>
                &nbsp;&nbsp;📖 <strong>Literature Survey</strong> — Systematic field mapping with themes<br><br>
                <strong>Features:</strong><br>
                • Search your curated KB first, fall back to web search<br>
                • Create custom KBs from BibTeX files<br>
                • Download papers and add to your KB<br><br>
                <strong>Try asking:</strong> "What are key metabolites in jasmine?" or "Literature survey on continuous learning in agents"
            </div>
        `;
        messages = [];
    }
}

// Load conversation history from backend
async function loadConversationHistory() {
    try {
        const response = await fetch('/api/conversations');
        if (!response.ok) {
            console.log('No conversation history available');
            return;
        }

        const conversations = await response.json();
        if (!conversations || conversations.length === 0) {
            console.log('No saved conversations');
            return;
        }

        // Clear the default "Current Chat" item
        const historyList = document.getElementById('chat-history-list');
        historyList.innerHTML = '';

        // Add a "New Chat" item at the top as the active item
        const newChatItem = document.createElement('div');
        newChatItem.className = 'chat-history-item active';
        newChatItem.dataset.kb = '';
        newChatItem.dataset.convId = '';
        newChatItem.dataset.createdAt = '';
        newChatItem.innerHTML = `
            <span class="chat-title">New Chat</span>
            <span class="chat-kb">${selectedKb || 'Web Search'}</span>
            <span class="chat-date">Now</span>
        `;
        newChatItem.onclick = function() {
            // Deactivate all items, activate this one
            document.querySelectorAll('.chat-history-item').forEach(i => i.classList.remove('active'));
            this.classList.add('active');
            // Reset state
            sessionId = null;
            conversationId = null;
            messages = [];
            // Show welcome message
            const container = document.getElementById('chat-container');
            container.innerHTML = `
                <div class="message assistant">
                    Hello! I'm Perspicacité — an AI literature assistant. I will:
                    <br><br>
                    • Search your selected Knowledge Base first (curated by you)<br>
                    • Fall back to web literature search (OpenAlex) when your KB is insufficient<br>
                    • Ground answers in retrieved papers and maintain conversation context<br>
                    • Let you curate: add selected retrieved papers back into your KB<br><br>
                    Try: "What are key metabolites in jasmine?" or "Summarize feature-based molecular networking (FBMN)"
                </div>
            `;
        };
        historyList.appendChild(newChatItem);

        // Add each conversation to the list
        for (const conv of conversations) {
            const item = document.createElement('div');
            item.className = 'chat-history-item';
            item.dataset.kb = conv.kb_name === 'default' ? '' : conv.kb_name;
            item.dataset.convId = conv.id;
            item.dataset.createdAt = conv.created_at || conv.updated_at;

            const title = conv.title || 'Untitled Chat';
            const kbDisplay = conv.kb_name === 'default' ? 'Web Search' : conv.kb_name;

            // Calculate relative time
            const updatedAt = conv.updated_at || conv.created_at;
            let dateStr;
            if (updatedAt) {
                const elapsed = Date.now() - new Date(updatedAt).getTime();
                const minutes = Math.floor(elapsed / 60000);
                const hours = Math.floor(elapsed / 3600000);
                const days = Math.floor(elapsed / 86400000);

                if (minutes < 1) {
                    dateStr = 'Just now';
                } else if (minutes < 60) {
                    dateStr = `${minutes}m ago`;
                } else if (hours < 24) {
                    dateStr = `${hours}h ago`;
                } else if (days < 7) {
                    dateStr = `${days}d ago`;
                } else {
                    dateStr = formatDate(updatedAt);
                }
            } else {
                dateStr = '';
            }

            item.innerHTML = `
                <span class="chat-title">${title}</span>
                <span class="chat-kb">${kbDisplay}</span>
                <span class="chat-date">${dateStr}</span>
                <button class="delete-chat-btn" onclick="deleteChatItem(this, event)" title="Delete this chat">×</button>
            `;
            item.onclick = function() { loadChatFromHistory(this); };
            historyList.appendChild(item);
        }

        // Start fresh — no conversation ID set, no KB restored
        conversationId = null;

        console.log(`Loaded ${conversations.length} conversations from history`);
    } catch (e) {
        console.error('Failed to load conversation history:', e);
    }
}

function toggleSelectMode() {
    isSelectMode = !isSelectMode;
    const btn = document.getElementById('select-mode-btn');
    const historyList = document.getElementById('chat-history-list');

    if (isSelectMode) {
        btn.classList.add('select-mode-active');
        btn.title = 'Cancel selection';
        // Add checkboxes to all items with conversation IDs
        historyList.querySelectorAll('.chat-history-item').forEach(item => {
            const convId = item.dataset.convId;
            if (convId) {
                item.classList.add('select-mode');
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'chat-select-checkbox';
                checkbox.dataset.convId = convId;
                item.insertBefore(checkbox, item.firstChild);
            }
        });
        showToast('Select chats to delete, then click 🗑️');
    } else {
        btn.classList.remove('select-mode-active');
        btn.title = 'Select multiple';
        // Remove checkboxes
        historyList.querySelectorAll('.chat-select-checkbox').forEach(cb => cb.remove());
        historyList.querySelectorAll('.chat-history-item.select-mode').forEach(item => {
            item.classList.remove('select-mode');
        });
    }
}

function showClearHistoryDialog(mode = 'all') {
    deleteMode = mode;
    const title = document.getElementById('clear-history-title');
    const message = document.getElementById('clear-history-message');
    const confirmBtn = document.getElementById('clear-history-confirm-btn');

    if (mode === 'selected') {
        const selected = document.querySelectorAll('.chat-select-checkbox:checked');
        const count = selected.length;
        if (count === 0) {
            showToast('No chats selected');
            return;
        }
        title.textContent = 'Delete Selected Chats';
        message.innerHTML = `This will permanently delete <strong>${count}</strong> selected chat${count > 1 ? 's' : ''}. This cannot be undone.`;
        confirmBtn.textContent = 'Delete Selected';
    } else if (mode === 'single') {
        title.textContent = 'Delete Chat';
        message.innerHTML = 'This will permanently delete this chat. This cannot be undone.';
        confirmBtn.textContent = 'Delete';
    } else {
        title.textContent = 'Clear Chat History';
        message.innerHTML = 'This will permanently delete <strong>all</strong> your chat conversations. This cannot be undone.';
        confirmBtn.textContent = 'Clear All';
    }

    document.getElementById('clear-history-dialog').style.display = 'flex';
}

function hideClearHistoryDialog() {
    document.getElementById('clear-history-dialog').style.display = 'none';
    singleDeleteTarget = null;
}

async function deleteChatItem(btn, event) {
    event.stopPropagation(); // Prevent chat loading
    const item = btn.closest('.chat-history-item');
    const convId = item.dataset.convId;

    if (!convId) {
        // This is the current/new chat, just clear it
        newChat();
        return;
    }

    singleDeleteTarget = item;
    showClearHistoryDialog('single');
}

async function deleteSelectedChats() {
    const checkboxes = document.querySelectorAll('.chat-select-checkbox:checked');
    const convIds = Array.from(checkboxes).map(cb => cb.dataset.convId);

    let deleted = 0;
    let failed = 0;

    for (const convId of convIds) {
        try {
            const resp = await fetch(`/api/conversations/${convId}`, { method: 'DELETE' });
            if (resp.ok) {
                deleted++;
                // Remove from UI
                const item = document.querySelector(`.chat-history-item[data-conv-id="${convId}"]`);
                if (item) item.remove();
            } else {
                failed++;
            }
        } catch (e) {
            failed++;
            console.error('Error deleting chat:', e);
        }
    }

    // Exit select mode
    toggleSelectMode();

    if (failed > 0) {
        showToast(`Deleted ${deleted} chats, ${failed} failed`);
    } else {
        showToast(`Deleted ${deleted} chat${deleted > 1 ? 's' : ''}`);
    }

    // If we deleted the active chat, start a new one
    if (conversationId && convIds.includes(conversationId)) {
        conversationId = null;
        newChat();
    }
}

async function confirmClearHistory() {
    if (deleteMode === 'selected') {
        await deleteSelectedChats();
        hideClearHistoryDialog();
        return;
    }

    if (deleteMode === 'single' && singleDeleteTarget) {
        const convId = singleDeleteTarget.dataset.convId;
        if (convId) {
            try {
                const resp = await fetch(`/api/conversations/${convId}`, { method: 'DELETE' });
                if (resp.ok) {
                    singleDeleteTarget.remove();
                    showToast('Chat deleted');
                    // If we deleted the active chat, start a new one
                    if (conversationId === convId) {
                        conversationId = null;
                        newChat();
                    }
                } else {
                    showToast('Error deleting chat');
                }
            } catch (e) {
                showToast('Error: ' + e.message);
            }
        }
        hideClearHistoryDialog();
        return;
    }

    // Default: delete all
    try {
        const resp = await fetch('/api/conversations', { method: 'DELETE' });
        if (resp.ok) {
            showToast('All chat history cleared');
            // Reset UI
            document.getElementById('chat-history-list').innerHTML = `
                <div class="chat-history-item active" data-session="" data-kb="" data-created-at="" data-conv-id="">
                    <span class="chat-title">Current Chat</span>
                    <span class="chat-kb">Web Search</span>
                    <span class="chat-date">Now</span>
                    <button class="delete-chat-btn" onclick="deleteChatItem(this, event)" title="Delete this chat">×</button>
                </div>
            `;
            conversationId = null;
            newChat();
        } else {
            showToast('Error clearing history');
        }
    } catch (e) {
        showToast('Error: ' + e.message);
    }
    hideClearHistoryDialog();
}

/* ── Conversation search ─────────────────────────────────────────────────── */

let _convSearchTimer = null;

function initConversationSearch() {
    const input = document.getElementById('conv-search-input');
    const resultsBox = document.getElementById('conv-search-results');
    if (!input || !resultsBox) return;

    input.addEventListener('input', function() {
        clearTimeout(_convSearchTimer);
        const q = input.value.trim();
        if (!q) {
            resultsBox.innerHTML = '';
            resultsBox.style.display = 'none';
            return;
        }
        _convSearchTimer = setTimeout(function() {
            runConversationSearch(q, resultsBox);
        }, 250);
    });
}

async function runConversationSearch(q, resultsBox) {
    try {
        const resp = await fetch('/api/conversations/search?q=' + encodeURIComponent(q));
        if (!resp.ok) {
            resultsBox.innerHTML = '';
            resultsBox.style.display = 'none';
            return;
        }
        const data = await resp.json();
        const results = data.results || [];
        if (results.length === 0) {
            resultsBox.innerHTML = '<p style="font-size:12px;color:var(--text-muted);padding:4px;">No results.</p>';
            resultsBox.style.display = 'block';
            return;
        }

        let html = '';
        results.forEach(function(r) {
            html += '<div class="conv-search-result-item" onclick="loadConversationById(\'' +
                    escapeAttr(r.id) + '\')">';
            html += '<div class="conv-search-result-title">' + escapeHtmlConv(r.title || 'Untitled') + '</div>';
            if (r.snippet) {
                html += '<div class="conv-search-result-snippet">' + escapeHtmlConv(r.snippet) + '</div>';
            }
            html += '</div>';
        });
        resultsBox.innerHTML = html;
        resultsBox.style.display = 'block';
    } catch (e) {
        console.error('Conversation search failed:', e);
    }
}

async function loadConversationById(convId) {
    // Clear search box and results
    const input = document.getElementById('conv-search-input');
    const resultsBox = document.getElementById('conv-search-results');
    if (input) input.value = '';
    if (resultsBox) { resultsBox.innerHTML = ''; resultsBox.style.display = 'none'; }

    // Find the history item in the sidebar if it exists
    const existing = document.querySelector('.chat-history-item[data-conv-id="' + convId + '"]');
    if (existing) {
        loadChatFromHistory(existing);
        return;
    }

    // Otherwise load it directly
    try {
        const resp = await fetch('/api/conversations/' + convId);
        if (!resp.ok) { showToast('Could not load conversation'); return; }
        const conv = await resp.json();

        // Clear chat and load messages
        const container = document.getElementById('chat-container');
        container.innerHTML = '';
        messages = [];
        conversationId = convId;

        for (const msg of (conv.messages || [])) {
            addMessage(msg.role, msg.content);
            messages.push({ role: msg.role, content: msg.content });
        }
        // Activate the matching sidebar item if present
        document.querySelectorAll('.chat-history-item').forEach(function(item) {
            item.classList.toggle('active', item.dataset.convId === convId);
        });
    } catch (e) {
        showToast('Error loading conversation: ' + e.message);
    }
}

function exportConversation(convId) {
    if (!convId) { showToast('No conversation to export'); return; }
    window.location.href = '/api/conversations/' + encodeURIComponent(convId) + '/export?format=markdown';
}

function escapeHtmlConv(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function escapeAttr(str) {
    if (!str) return '';
    return String(str).replace(/'/g, "\\'");
}
