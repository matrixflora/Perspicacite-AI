/* Chat input + send pipeline: query submission, streamed response handling,
   message rendering (markdown/code blocks), thinking-panel UI, papers-found
   curation list. */

function handleInputKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendQuery();
    }
}

function handleInputChange() {
    const input = document.getElementById('query-input');
    const sendBtn = document.getElementById('send-btn');
    const hasContent = input.value.trim().length > 0;
    sendBtn.disabled = !hasContent;

    // Auto-resize textarea
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 200) + 'px';
}

// Check system status on load
async function checkStatus() {
    try {
        const response = await fetch('/api/health');
        const data = await response.json();

        if (data.initialized) {
            loadKBs();
        } else {
            setTimeout(checkStatus, 2000);
        }
    } catch (e) {
        console.error('Health check failed:', e);
    }
}

async function sendQuery() {
    if (isProcessing) return;

    const input = document.getElementById('query-input');
    const query = input.value.trim();

    if (!query) return;

    syncRagModeFromDropdown();

    isProcessing = true;
    input.value = '';
    input.style.height = 'auto';
    input.disabled = true;
    document.getElementById('send-btn').disabled = true;

    // Add user message
    addMessage('user', query);
    messages.push({role: 'user', content: query});

    // Add to chat history on first message
    if (messages.length <= 2) {
        const historyList = document.getElementById('chat-history-list');
        const activeItem = historyList.querySelector('.chat-history-item.active');
        if (activeItem) {
            // Update title with first query preview
            const preview = query.length > 25 ? query.substring(0, 25) + '...' : query;
            activeItem.querySelector('.chat-title').textContent = preview;
            // Store and display KB used
            activeItem.dataset.kb = selectedKb || '';
            activeItem.querySelector('.chat-kb').textContent = selectedKb || 'Web Search';
        }
    }

    // Clear previous thinking
    clearThinking();

    // Show immediate feedback so user knows something is happening
    const modeLabel = currentMode.charAt(0).toUpperCase() + currentMode.slice(1);
    addThinkingStep(`${modeLabel} mode — Sending query...`, 'analyzing');

    try {
        // Get download cap setting (only used in agentic mode)
        const downloadCap = parseInt(document.getElementById('download-cap-slider').value) || 10;

        // Get selected databases
        const selectedDatabases = getSelectedDatabases();

        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                query: query,
                messages: messages.slice(0, -1),
                session_id: sessionId,
                conversation_id: conversationId,
                kb_name: selectedKb,
                mode: currentMode,
                stream: true,
                max_papers_to_download: downloadCap,
                databases: selectedDatabases
            })
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let assistantMessage = '';
        let assistantDiv = null;
        // Buffer SSE lines: fetch chunks can split mid-JSON (causes "Unterminated string").
        let sseBuffer = '';

        while (true) {
            const {done, value} = await reader.read();
            if (!done && value) {
                sseBuffer += decoder.decode(value, { stream: true });
            } else if (done) {
                sseBuffer += decoder.decode(new Uint8Array(), { stream: false });
            }
            let nl;
            const LF = String.fromCharCode(10);
            while ((nl = sseBuffer.indexOf(LF)) >= 0) {
                const line = sseBuffer.slice(0, nl).replace(/\\r$/, '');
                sseBuffer = sseBuffer.slice(nl + 1);
                if (!line.startsWith('data: ')) continue;
                const payload = line.slice(6).trim();
                if (!payload) continue;
                let data;
                try {
                    data = JSON.parse(payload);
                } catch (e) {
                    console.warn('SSE JSON parse skipped:', e.message);
                    continue;
                }
                if (data.type === 'thinking') {
                    addThinkingStep(data.message, 'analyzing', data.details);
                } else if (data.type === 'source') {
                    // Source from RAG modes (basic, advanced, profound)
                    if (data.source) {
                        addThinkingStep(
                            `Source: ${data.source.title}`,
                            'result',
                            `Relevance: ${(data.source.relevance_score * 100).toFixed(1)}%`
                        );
                    }
                } else if (data.type === 'tool_call') {
                    addThinkingStep(
                        `Using tool: ${data.tool}`,
                        'tool',
                        data.description,
                        data.query || ''
                    );
                } else if (data.type === 'tool_result') {
                    addThinkingStep(
                        `Result from ${data.step}`,
                        'result',
                        data.result_summary
                    );
                } else if (data.type === 'token') {
                    const delta = data.delta_b64
                        ? decodeUtf8FromBase64(data.delta_b64)
                        : (data.delta || '');
                    if (delta) {
                        if (!assistantDiv) {
                            assistantDiv = addMessage('assistant', '');
                        }
                        assistantMessage += delta;
                        assistantDiv.innerHTML = formatMessage(assistantMessage);
                        if (data.session_id) sessionId = data.session_id;
                        if (data.conversation_id) {
                            conversationId = data.conversation_id;
                            const activeItem = document.querySelector('.chat-history-item.active');
                            if (activeItem && !activeItem.dataset.convId) {
                                activeItem.dataset.convId = conversationId;
                            }
                        }
                    }
                } else if (data.type === 'answer') {
                    if (!assistantDiv) {
                        assistantDiv = addMessage('assistant', '');
                    }
                    assistantMessage = data.content_b64
                        ? decodeUtf8FromBase64(data.content_b64)
                        : (data.content || '');
                    assistantDiv.innerHTML = formatMessage(assistantMessage);
                    sessionId = data.session_id || sessionId;
                    // Capture conversation_id from response
                    if (data.conversation_id) {
                        conversationId = data.conversation_id;
                        // Update active history item with conversation_id
                        const activeItem = document.querySelector('.chat-history-item.active');
                        if (activeItem && !activeItem.dataset.convId) {
                            activeItem.dataset.convId = conversationId;
                        }
                    }
                } else if (data.type === 'papers_found' && data.papers && data.papers.length > 0) {
                    lastFoundPapers = data.papers;
                    if (assistantDiv) {
                        showPapersCuration(assistantDiv, data.papers);
                    }
                } else if (data.type === 'status') {
                    // Status update - could be progress message or literature survey completion
                    if (data.session_id && data.papers_count !== undefined) {
                        // Literature survey complete - load the survey interface
                        setTimeout(() => loadSurveySession(data.session_id), 100);
                    } else if (data.message) {
                        // Regular status message - show as thinking step
                        addThinkingStep(data.message, 'analyzing');
                    }
                }
                if (data.details && data.details.includes('Intent:')) {
                    const intentMatch = data.details.match(/Intent: (\\w+)/);
                    if (intentMatch) {
                        showIntent(intentMatch[1]);
                    }
                }
            }
            if (done) break;
        }

        if (assistantMessage) {
            messages.push({role: 'assistant', content: assistantMessage});
        }

    } catch (error) {
        addMessage('assistant', '❌ Error: ' + error.message);
    }

    isProcessing = false;
    input.disabled = false;
    handleInputChange();  // Update button state based on content
    input.focus();
}

function addMessage(role, content) {
    const container = document.getElementById('chat-container');
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = formatMessage(content);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

function formatMessage(content) {
    if (!content) return '';

    // Escape HTML entities first
    let formatted = content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Split into lines for processing
    let lines = formatted.split('\n');
    let result = [];
    let inList = false;

    for (let line of lines) {
        let trimmed = line.trim();

        // Headers
        if (trimmed.startsWith('### ')) {
            if (inList) { result.push('</ul>'); inList = false; }
            result.push('<h3 style="color: var(--primary); margin: 16px 0 8px 0; font-size: 1.1em;">' + trimmed.substring(4) + '</h3>');
            continue;
        }
        if (trimmed.startsWith('## ')) {
            if (inList) { result.push('</ul>'); inList = false; }
            result.push('<h2 style="color: var(--primary); margin: 20px 0 10px 0; font-size: 1.2em; border-bottom: 1px solid var(--border);">' + trimmed.substring(3) + '</h2>');
            continue;
        }
        if (trimmed.startsWith('# ')) {
            if (inList) { result.push('</ul>'); inList = false; }
            result.push('<h1 style="color: var(--primary); margin: 24px 0 12px 0; font-size: 1.4em;">' + trimmed.substring(2) + '</h1>');
            continue;
        }

        // List items
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
            if (!inList) { result.push('<ul style="margin: 8px 0; padding-left: 20px;">'); inList = true; }
            let item = trimmed.substring(2);
            // Process bold/italic within list item
            item = item.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            item = item.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            result.push('<li style="margin: 4px 0;">' + item + '</li>');
            continue;
        }

        // End list if we hit a non-list line
        if (inList && trimmed) {
            result.push('</ul>');
            inList = false;
        }

        // Empty line = paragraph break
        if (!trimmed) {
            result.push('<br>');
            continue;
        }

        // Regular paragraph with inline formatting
        let para = trimmed;
        para = para.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        para = para.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        para = para.replace(/`([^`]+)`/g, '<code style="background: #e2e8f0; padding: 2px 6px; border-radius: 4px; font-size: 0.9em;">$1</code>');
        result.push('<p style="margin: 8px 0; line-height: 1.6;">' + para + '</p>');
    }

    // Close any open list
    if (inList) result.push('</ul>');

    return result.join('');
}

function createThinkingMessage() {
    const container = document.getElementById('chat-container');
    const modeEl = document.getElementById('mode-dropdown');
    const mode = modeEl ? modeEl.value : 'basic';
    const modeLabels = {
        'basic': '📖 Basic Retrieval',
        'advanced': '🔍 Advanced Analysis',
        'profound': '🔬 Profound Research',
        'agentic': '🤖 Agent Thinking',
    };
    const label = modeLabels[mode] || '🧠 Processing';
    const div = document.createElement('div');
    div.className = 'message assistant thinking-message';
    div.innerHTML = `
        <div class="thinking-header-bar" onclick="toggleThinkingMessage(this.parentElement)">
            <span class="thinking-toggle">▶</span>
            <span>${label}</span>
            <span class="thinking-count"></span>
        </div>
        <div class="thinking-content collapsed">
            <div class="thinking-steps"></div>
        </div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    currentThinkingMessage = div;
    thinkingSteps = [];
    return div;
}

function toggleThinkingMessage(messageDiv) {
    const content = messageDiv.querySelector('.thinking-content');
    const toggle = messageDiv.querySelector('.thinking-toggle');
    if (content.classList.contains('collapsed')) {
        content.classList.remove('collapsed');
        toggle.textContent = '▼';
    } else {
        content.classList.add('collapsed');
        toggle.textContent = '▶';
    }
}

function addThinkingStep(message, type, details, query) {
    // Create thinking message if not exists
    if (!currentThinkingMessage) {
        createThinkingMessage();
    }

    const icons = {
        analyzing: '🧠',
        planning: '📋',
        tool: '🔧',
        result: '📄',
        complete: '✅'
    };

    thinkingSteps.push({ message, type, details, query });

    // Update the count
    const countSpan = currentThinkingMessage.querySelector('.thinking-count');
    countSpan.textContent = `(${thinkingSteps.length} steps)`;

    // Add step to content
    const stepsContainer = currentThinkingMessage.querySelector('.thinking-steps');
    const stepDiv = document.createElement('div');
    stepDiv.className = `thinking-step ${type}`;

    let queryInfo = '';
    if (query) {
        queryInfo = `<div class="query-info">Query: <code>${query}</code></div>`;
    }

    stepDiv.innerHTML = `
        <span class="icon">${icons[type] || '•'}</span>
        <div class="content">
            <div>${message}</div>
            ${details ? `<div class="details">${details}</div>` : ''}
            ${queryInfo}
        </div>
    `;

    stepsContainer.appendChild(stepDiv);

    // Auto-scroll chat
    const container = document.getElementById('chat-container');
    container.scrollTop = container.scrollHeight;
}

function clearThinking() {
    currentThinkingMessage = null;
    thinkingSteps = [];
}

// Paper curation
function showPapersCuration(parentDiv, papers) {
    const section = document.createElement('div');
    section.className = 'papers-found';

    let html = '<h4>📄 Papers Found (' + papers.length + ')</h4>';
    papers.forEach((p, i) => {
        const authors = (p.authors || []).join(', ');
        const year = p.year || '?';
        const citations = p.citations != null ? ` | Cited: ${p.citations}` : '';
        html += `
            <div class="paper-item">
                <input type="checkbox" id="paper-${i}" checked data-index="${i}">
                <label for="paper-${i}">
                    ${p.title}
                    <div class="paper-meta">${authors} (${year})${citations}</div>
                </label>
            </div>`;
    });

    // Two buttons: "Add to existing KB" and "Create new KB with these papers"
    const addToKbDisabled = selectedKb ? '' : 'disabled title="Select a KB first"';
    const addToKbLabel = selectedKb ? `Add selected to "${selectedKb}"` : 'Select a KB first';
    html += `
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
            <button class="add-to-kb-btn" onclick="addToKB(this)" ${addToKbDisabled}>${addToKbLabel}</button>
            <button class="add-to-kb-btn" onclick="createKBFromSelectedPapers()">Create new KB with selected papers</button>
        </div>`;

    section.innerHTML = html;
    parentDiv.appendChild(section);

    const container = document.getElementById('chat-container');
    container.scrollTop = container.scrollHeight;
}
