/* Chat input + send pipeline: query submission, streamed response handling,
   message rendering (markdown/code blocks), thinking-panel UI, papers-found
   curation list. */

let currentThinkingMessage = null;
let thinkingSteps = [];

// Per-turn state for inline figure-id resolution (Task 13).
let currentTurnPapers = new Set();   // paper_id strings ("doi:<doi>")
let figureIndexByPaper = {};         // paperId -> list of figure records
let activeAssistantDiv = null;
let activeAssistantText = "";

function decodeUtf8FromBase64(b64) {
    try {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return new TextDecoder().decode(bytes);
    } catch (e) {
        console.warn('base64 decode failed', e);
        return '';
    }
}

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

// Aborts the in-flight chat request so the user can stop a long-running
// query. Set in sendQuery(), consumed by stopQuery(), nulled in the
// completion/error paths. AbortController works with fetch streams: aborting
// causes the ReadableStream reader to throw an AbortError on the next read.
let currentChatAbort = null;

function stopQuery() {
    if (currentChatAbort) {
        try { currentChatAbort.abort(); } catch (_) {}
    }
    // Best-effort backend hint — even without it, the AbortController will
    // close the response stream and FastAPI's generator will get a cancel.
    try {
        if (conversationId) {
            fetch('/api/chat/cancel', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ conversation_id: conversationId }),
                keepalive: true,
            }).catch(() => {});
        }
    } catch (_) {}
    // Restore input state immediately so the user gets feedback.
    const sendBtn = document.getElementById('send-btn');
    const stopBtn = document.getElementById('stop-btn');
    const input = document.getElementById('query-input');
    if (sendBtn) sendBtn.style.display = '';
    if (stopBtn) stopBtn.style.display = 'none';
    if (input) input.disabled = false;
    isProcessing = false;
    addThinkingStep('Stopped by user.', 'result');
}

async function sendQuery() {
    if (isProcessing) return;

    const input = document.getElementById('query-input');
    const query = input.value.trim();

    if (!query) return;

    syncRagModeFromDropdown();

    // Arm the abort controller so the Stop button can cancel the fetch.
    currentChatAbort = new AbortController();

    isProcessing = true;
    input.value = '';
    input.style.height = 'auto';
    input.disabled = true;
    document.getElementById('send-btn').disabled = true;
    // Swap Send → Stop while the request is in flight.
    document.getElementById('send-btn').style.display = 'none';
    const _stopBtn = document.getElementById('stop-btn');
    if (_stopBtn) _stopBtn.style.display = '';
    document.body.classList.add('chat-active');
    startStatusBar();
    // Seed an input-token estimate from the user message + conversation
    // history that gets sent in this turn. The actual prompt the server
    // assembles (system prompt + retrieved context) will be larger; this
    // is just an early proxy so the ↑ counter isn't stuck at 0.
    const histChars = messages.reduce((n, m) => n + (m.content || '').length, 0);
    setInputTokenEstimate(query + ' ' + (histChars ? '_'.repeat(histChars) : ''));

    // Reset per-turn figure-id resolution state (Task 13)
    currentTurnPapers = new Set();
    activeAssistantDiv = null;
    activeAssistantText = "";

    // Sub-project C (2026-05-15): clear attachment panels from previous turn
    clearAttachmentsPanels();

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

        // Collect advanced query options (only include when changed from defaults)
        const advancedBody = getAdvancedQueryOptions();

        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            signal: currentChatAbort ? currentChatAbort.signal : undefined,
            body: JSON.stringify(Object.assign({
                query: query,
                messages: messages.slice(0, -1),
                session_id: sessionId,
                conversation_id: conversationId,
                kb_name: advancedBody.kb_names ? undefined : selectedKb,
                mode: currentMode,
                stream: true,
                max_papers: Math.max(1, Math.min(10, downloadCap)),
                max_papers_to_download: downloadCap,
                databases: selectedDatabases
            }, advancedBody))
        });

        if (!response.ok) {
            let detail = '';
            try { detail = (await response.text()).slice(0, 500); } catch (_) {}
            throw new Error(`Server returned HTTP ${response.status} ${response.statusText}${detail ? ' — ' + detail : ''}`);
        }

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
                // Phase progress: prefer explicit phase_transition events from the
                // backend; fall back to heuristic classification on other events.
                try {
                    if (data.type === 'phase_transition' && data.phase) {
                        markAgenticPhase(data.phase);
                    } else {
                        const phase = _classifyPhase(data.type, data.message || data.details || '');
                        if (phase) markAgenticPhase(phase);
                    }
                } catch (_) {}
                if (data.type === 'phase_transition') {
                    // Already handled above; no chat-stream rendering needed.
                    continue;
                } else if (data.type === 'intent_result') {
                    renderIntentResult(data);
                } else if (data.type === 'plan') {
                    renderPlanResult(data);
                } else if (data.type === 'thinking') {
                    addThinkingStep(data.message, 'analyzing', data.details);
                    if (data.message) setStatusLabel(String(data.message).slice(0, 80));
                } else if (data.type === 'source') {
                    // Source from RAG modes (basic, advanced, deep_research)
                    if (data.source) {
                        const src = data.source;
                        if (src.doi) {
                            currentTurnPapers.add("doi:" + src.doi);
                        }
                        const ct = src.content_type || 'none';
                        const ctLabels = { structured: 'Structured', full_text: 'Full text', abstract: 'Abstract', none: '—' };
                        const ctLabel = ctLabels[ct] || ct;
                        const badgeHtml = '<span class="pipeline-badge pipeline-' + ct + '">' + ctLabel + '</span>';
                        let kbTagHtml = '';
                        if (src.kb_name) {
                            kbTagHtml = '<span class="source-kb-tag">' + src.kb_name + '</span>';
                        }
                        // Provider tag(s). Multi-DB matches (sources_all
                        // length > 1) render as a chip GROUP so the user
                        // sees that the same paper was returned by several
                        // databases. Falls back to the single source string
                        // (incl. "scilex (multi-DB)" with source_apis
                        // tooltip) for legacy single-source cases.
                        let providerHtml = '';
                        const allSrcs = Array.isArray(src.sources_all) ? src.sources_all : [];
                        if (allSrcs.length > 1) {
                            providerHtml = ' ' + allSrcs.map(function (s) {
                                const lbl = String(s).replace(/_/g, ' ');
                                return '<span class="source-provider-tag" title="Returned by ' +
                                    _escapeHtml(lbl) + '">' + _escapeHtml(lbl) + '</span>';
                            }).join(' ');
                        } else if (src.source) {
                            const provLabel = String(src.source).replace(/_/g, ' ');
                            const tipText = (src.source_apis && src.source_apis.length)
                                ? ('Queried in parallel: ' + src.source_apis.join(', ') +
                                   '. SciLEx doesn\'t expose which specific API returned this paper.')
                                : ('Returned by ' + provLabel);
                            providerHtml = src.url
                                ? (' <a class="source-provider-tag" href="' + src.url +
                                   '" target="_blank" rel="noopener noreferrer" title="' + tipText + '">' +
                                   provLabel + ' ↗</a>')
                                : (' <span class="source-provider-tag" title="' + tipText + '">' + provLabel + '</span>');
                        }
                        // Enrichment chips: which secondary sources cleaned up
                        // this record (Crossref canonical bibliographic patch,
                        // OpenAlex abstract fill, Unpaywall OA detection).
                        // Distinct visual treatment from the upstream-source
                        // chips so the user can tell "where it came from"
                        // (sources_all) vs "what enriched it" (enrichment_sources).
                        let enrichHtml = '';
                        const enrich = Array.isArray(src.enrichment_sources) ? src.enrichment_sources : [];
                        if (enrich.length) {
                            const enrichLabel = {
                                crossref: 'Crossref',
                                openalex: 'OpenAlex',
                                unpaywall: 'Unpaywall',
                            };
                            enrichHtml = ' ' + enrich.map(function (e) {
                                const key = String(e).toLowerCase();
                                const lbl = enrichLabel[key] || key;
                                return '<span class="source-enrichment-tag" title="Metadata enriched by ' +
                                    _escapeHtml(lbl) + '">+' + _escapeHtml(lbl) + '</span>';
                            }).join(' ');
                        }
                        // Compact relevance chip next to the details button.
                        const relPct = (src.relevance_score * 100).toFixed(1) + '%';
                        const relHtml = ' <span class="source-relevance" title="Blended relevance: 60% MiniLM (semantic, query vs title+abstract) + 25% log citation count + 15% BM25 (lexical)">' + relPct + '</span>';
                        // Details button always shown. When there's a DOI we
                        // route through /api/paper; otherwise we render an
                        // inline panel from the source data already in hand.
                        // Stash the source in a turn-scoped registry keyed
                        // by paper_id so we don't have to embed a giant
                        // JSON blob in the onclick handler.
                        const srcKey = (src.paper_id || src.doi || src.title || ('p' + Date.now()));
                        if (!window.__sourceRegistry) window.__sourceRegistry = {};
                        window.__sourceRegistry[srcKey] = src;
                        const detailsArg = src.doi
                            ? ("'" + src.doi.replace(/'/g, "\\'") + "'")
                            : ("null, '" + srcKey.replace(/'/g, "\\'") + "'");
                        const detailsLink = ' <button class="paper-detail-link" onclick="openPaperDetail(' +
                            detailsArg + ')" title="View abstract and metadata">details</button>';
                        addThinkingStep(
                            'Source: ' + src.title + ' ' + badgeHtml + kbTagHtml + providerHtml + enrichHtml + relHtml + detailsLink,
                            'result',
                            ''   // no longer needed — relevance now inline above
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
                            activeAssistantDiv = assistantDiv;
                        }
                        assistantMessage += delta;
                        activeAssistantText = assistantMessage;
                        assistantDiv.innerHTML = formatMessage(assistantMessage);
                        bumpTokenCounter(delta.length);
                        setStatusLabel('Generating answer…');
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
                        activeAssistantDiv = assistantDiv;
                    }
                    assistantMessage = data.content_b64
                        ? decodeUtf8FromBase64(data.content_b64)
                        : (data.content || '');
                    activeAssistantText = assistantMessage;
                    assistantDiv.innerHTML = formatMessage(assistantMessage);
                    // Token deltas may have arrived in one ``answer`` payload
                    // (some modes don't stream content). Update the counter
                    // from the final length so the user sees a non-zero total.
                    if (_tokensOut === 0 && assistantMessage) {
                        _tokensOut = Math.max(1, Math.round(assistantMessage.length / 4));
                        updateTokenDisplay();
                    }
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
                    // Stash message_id for provenance disclosure
                    if (data.message_id && assistantDiv) {
                        assistantDiv.dataset.messageId = data.message_id;
                    }
                } else if (data.type === 'papers_found' && data.papers && data.papers.length > 0) {
                    lastFoundPapers = data.papers;
                    if (assistantDiv) {
                        showPapersCuration(assistantDiv, data.papers);
                    }
                } else if (data.type === 'status') {
                    // Status update - could be progress message, literature
                    // survey completion, or a structured kind (query_rephrased
                    // / provider_progress / batch_progress) we render richly.
                    if (data.session_id && data.papers_count !== undefined) {
                        // Literature survey complete - load the survey interface
                        setTimeout(() => loadSurveySession(data.session_id), 100);
                    } else if (data.kind === 'query_rephrased') {
                        renderQueryRephrased(data);
                    } else if (data.kind === 'provider_progress') {
                        renderProviderProgress(data);
                    } else if (data.kind === 'batch_progress') {
                        renderBatchProgress(data);
                    } else if (data.message) {
                        // Regular status message - show as thinking step
                        addThinkingStep(data.message, 'analyzing');
                    }
                } else if (data.type === 'code_excerpt') {
                    // Sub-project C (2026-05-15): render code-excerpt attachment
                    try { renderCodeExcerpt(data); }
                    catch (e) { console.error('renderCodeExcerpt failed:', e); }
                } else if (data.type === 'figure_ref') {
                    // Sub-project C (2026-05-15): render figure-ref attachment
                    try { renderFigureRef(data); }
                    catch (e) { console.error('renderFigureRef failed:', e); }
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

        // Resolve any pdf_pN_iM figure-id tokens into inline thumbnails (Task 13)
        try {
            await resolveFigureIdsInAssistantMessage();
        } catch (e) {
            console.warn('figure-id resolution failed', e);
        }

        // Attach provenance disclosure once stream is complete
        if (assistantDiv && assistantDiv.dataset.messageId && conversationId) {
            if (typeof window.attachProvenance === 'function') {
                window.attachProvenance(assistantDiv, assistantDiv.dataset.messageId, conversationId);
            }
        }

        if (assistantMessage) {
            messages.push({role: 'assistant', content: assistantMessage});
        }

    } catch (error) {
        // "Failed to fetch" is the browser's opaque message for any network-level
        // failure (server down/restarting, DNS, blocked, CORS). Surface something
        // actionable instead, including the URL so the user can sanity-check.
        const isNetworkErr =
            (error && error.name === 'TypeError' && /failed to fetch|networkerror|load failed/i.test(error.message || ''));
        const msg = isNetworkErr
            ? `❌ Could not reach the server at ${location.origin}/api/chat. It may be restarting or unreachable. Check the server console and retry. (original: ${error.message})`
            : `❌ Error: ${error.message || String(error)}`;
        // AbortError = the user clicked Stop. Surfacing it as a server error
        // would be misleading; stopQuery() already added a "Stopped by user"
        // line to the thinking strip.
        if (!(error && (error.name === 'AbortError' || /aborted/i.test(error.message || '')))) {
            addMessage('assistant', msg);
            console.error('sendQuery failed:', error);
        }
    }

    isProcessing = false;
    input.disabled = false;
    document.body.classList.remove('chat-active');
    stopStatusBar();
    stopThinkingTicker();
    markAgenticPhase('done');
    // Restore Send/Stop button visibility.
    const _sendBtnFinal = document.getElementById('send-btn');
    const _stopBtnFinal = document.getElementById('stop-btn');
    if (_sendBtnFinal) _sendBtnFinal.style.display = '';
    if (_stopBtnFinal) _stopBtnFinal.style.display = 'none';
    currentChatAbort = null;
    handleInputChange();  // Update button state based on content
    input.focus();
}

// --- Chat status bar (heartbeat + elapsed time + token counter) -----------
// Drives the bottom-of-thread indicator added in Sub-project D: shows live
// activity instead of just a small dot near the send button. Tokens are
// rolled up from incremental "token" SSE deltas (rough character-count)
// plus any explicit usage payloads carried on `done`/`status` events.
let _statusTimerId = null;
let _statusStartedAt = 0;
let _tokensIn = 0;   // estimated input (prompt + history)
let _tokensOut = 0;  // streamed output

function startStatusBar() {
    _statusStartedAt = performance.now();
    _tokensIn = 0; _tokensOut = 0;
    setStatusLabel('Sending query…');
    updateTokenDisplay();
    if (_statusTimerId) clearInterval(_statusTimerId);
    _statusTimerId = setInterval(() => {
        const elapsedSec = (performance.now() - _statusStartedAt) / 1000;
        const el = document.getElementById('chat-elapsed');
        if (el) el.textContent = elapsedSec < 60
            ? elapsedSec.toFixed(1) + 's'
            : Math.floor(elapsedSec / 60) + 'm' + Math.round(elapsedSec % 60) + 's';
    }, 200);
}

function stopStatusBar() {
    if (_statusTimerId) { clearInterval(_statusTimerId); _statusTimerId = null; }
    setStatusLabel('Idle');
}

function setStatusLabel(text) {
    const el = document.getElementById('chat-status-label');
    if (el) el.textContent = text || 'Working…';
}

function setInputTokenEstimate(text) {
    // Rough: ~4 chars/tok. Used at request time before we hear from the server.
    if (!text) return;
    _tokensIn = Math.max(1, Math.round(text.length / 4));
    updateTokenDisplay();
}

function bumpOutputTokenCounter(deltaChars) {
    if (!deltaChars) return;
    _tokensOut += Math.max(1, Math.round(deltaChars / 4));
    updateTokenDisplay();
}

function updateTokenDisplay() {
    const elIn = document.getElementById('chat-tokens-in');
    const elOut = document.getElementById('chat-tokens-out');
    if (elIn) elIn.textContent = (_tokensIn || 0).toLocaleString();
    if (elOut) elOut.textContent = (_tokensOut || 0).toLocaleString();
}

// Back-compat shim for callers that still use the old name.
function bumpTokenCounter(deltaChars) { bumpOutputTokenCounter(deltaChars); }

// Populate the LLM-model chip in the status bar from /api/health. The
// server already reports llm.default_model in its health payload, so we
// don't need a new endpoint. Falls back silently when the field is
// missing or the request fails.
async function initLLMModelLabel() {
    const el = document.getElementById('chat-llm-model');
    if (!el) return;
    try {
        const r = await fetch('/api/health');
        if (!r.ok) return;
        const data = await r.json();
        const model = data && data.llm && data.llm.default_model;
        const provider = data && data.llm && data.llm.default_provider;
        if (model) {
            // Trim "vendor/" prefix for the chip face; keep the full
            // "provider/model" string in the tooltip.
            const short = String(model).split('/').pop();
            el.textContent = '🤖 ' + short;
            el.title = 'LLM: ' + (provider ? provider + ' · ' : '') + model;
        }
    } catch (_) { /* silent */ }
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

    // Render inline-link helper used in every text path below. Markdown
    // shape: [visible](url "optional title"). The title attribute gives
    // the on-hover tooltip with the full citation. Numbered list items
    // (e.g. "1. [..](..)") will be wrapped by the list/paragraph code
    // below since we don't render ordered lists explicitly.
    function renderLinks(s) {
        return s.replace(
            /\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g,
            function (_m, label, url, title) {
                const t = title ? ` title="${title}"` : '';
                return `<a href="${url}" target="_blank" rel="noopener noreferrer"${t}>${label}</a>`;
            }
        );
    }

    // Split into lines for processing
    let lines = formatted.split('\n');
    let result = [];
    let inList = false;
    // The References block at the end of an assistant answer is wrapped in
    // a smaller-font block so a long bibliography doesn't dominate the
    // message. Triggered by a `## References` header, ended at the next
    // header or end-of-content.
    let inReferences = false;
    function openRefBlockIfNeeded() {
        if (!inReferences) {
            result.push('<div class="references-block">');
            inReferences = true;
        }
    }
    function closeRefBlockIfOpen() {
        if (inReferences) {
            result.push('</div>');
            inReferences = false;
        }
    }

    for (let line of lines) {
        let trimmed = line.trim();

        // Headers
        if (trimmed.startsWith('### ')) {
            if (inList) { result.push('</ul>'); inList = false; }
            closeRefBlockIfOpen();
            result.push('<h3 style="color: var(--primary); margin: 16px 0 8px 0; font-size: 1.1em;">' + renderLinks(trimmed.substring(4)) + '</h3>');
            continue;
        }
        if (trimmed.startsWith('## ')) {
            if (inList) { result.push('</ul>'); inList = false; }
            closeRefBlockIfOpen();
            const headerText = trimmed.substring(3);
            result.push('<h2 style="color: var(--primary); margin: 20px 0 10px 0; font-size: 1.2em; border-bottom: 1px solid var(--border);">' + renderLinks(headerText) + '</h2>');
            if (/^references\b/i.test(headerText.trim())) {
                openRefBlockIfNeeded();
            }
            continue;
        }
        if (trimmed.startsWith('# ')) {
            if (inList) { result.push('</ul>'); inList = false; }
            closeRefBlockIfOpen();
            result.push('<h1 style="color: var(--primary); margin: 24px 0 12px 0; font-size: 1.4em;">' + renderLinks(trimmed.substring(2)) + '</h1>');
            continue;
        }

        // List items (unordered)
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
            if (!inList) { result.push('<ul style="margin: 8px 0; padding-left: 20px;">'); inList = true; }
            let item = trimmed.substring(2);
            item = item.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            item = item.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            item = renderLinks(item);
            result.push('<li style="margin: 4px 0;">' + item + '</li>');
            continue;
        }

        // Ordered list items like "1. ", "12. ", "1) " — common in the References block.
        const orderedMatch = trimmed.match(/^(\d+)[.)]\s+(.*)$/);
        if (orderedMatch) {
            if (inList) { result.push('</ul>'); inList = false; }
            let item = orderedMatch[2];
            item = item.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            item = item.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            item = renderLinks(item);
            result.push(`<div style="margin: 4px 0; padding-left: 8px; line-height: 1.6;"><span style="color: var(--muted);">${orderedMatch[1]})</span> ${item}</div>`);
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
        para = renderLinks(para);
        result.push('<p style="margin: 8px 0; line-height: 1.6;">' + para + '</p>');
    }

    // Close any open list
    if (inList) result.push('</ul>');

    return result.join('');
}

/* Heuristic phase classifier: maps an SSE event into one of five visible
   phases shown in the agentic-mode progress strip. The orchestrator does
   not emit explicit phase markers (yet) — we infer them from the
   thinking message text and event type so the user gets a live
   progress hint without a backend change. */
const AGENTIC_PHASES = [
    { id: 'classify',  label: 'Classify',  icon: '🧭', match: /intent|classif/i },
    { id: 'plan',      label: 'Plan',      icon: '📋', match: /plan|strateg|breakdown/i },
    { id: 'retrieve',  label: 'Retrieve',  icon: '🔎', match: /search|query|retriev|optimi|rewrit|paper|hit|result|aggreg|fetch|download/i },
    { id: 'synth',     label: 'Synthesize', icon: '✍️', match: /synth|summari|writ|answer|generat|reflect|map[ -]reduce/i },
    { id: 'done',      label: 'Done',      icon: '✅', match: /(complete|finished|done)/i },
];

function _classifyPhase(eventType, text) {
    if (eventType === 'tool_call' || eventType === 'tool_result' || eventType === 'source') {
        return 'retrieve';
    }
    if (eventType === 'token' || eventType === 'answer') return 'synth';
    const t = String(text || '');
    for (const p of AGENTIC_PHASES) if (p.match.test(t)) return p.id;
    return null;
}

function createThinkingMessage() {
    const container = document.getElementById('chat-container');
    const modeEl = document.getElementById('mode-dropdown');
    const mode = modeEl ? modeEl.value : 'basic';
    const modeLabels = {
        'basic': '📖 Basic Retrieval',
        'advanced': '🔍 Advanced Analysis',
        'deep_research': '🔬 Deep Research',
        'agentic': '🤖 Agent Thinking',
        'literature_survey': '📚 Literature Survey',
        'contradiction': '⚖️ Contradiction Analysis',
    };
    const label = modeLabels[mode] || '🧠 Processing';
    // Modes where multi-step reasoning is the point: auto-expand the
    // thinking panel so the user can watch progress without clicking.
    const wantsExpanded = mode === 'agentic' || mode === 'deep_research' || mode === 'literature_survey';
    const wantsPhaseStrip = mode === 'agentic' || mode === 'deep_research';

    const phaseStripHtml = wantsPhaseStrip
        ? `<div class="thinking-phase-strip" aria-label="Agentic phases">
             ${AGENTIC_PHASES.map(p =>
               `<span class="phase-pill" data-phase="${p.id}" title="${p.label}">${p.icon}<span class="phase-label">${p.label}</span></span>`
             ).join('<span class="phase-sep">→</span>')}
           </div>`
        : '';

    const div = document.createElement('div');
    div.className = 'message assistant thinking-message';
    if (wantsExpanded) div.classList.add('mode-expanded');
    div.dataset.mode = mode;
    div.innerHTML = `
        <div class="thinking-header-bar" onclick="toggleThinkingMessage(this.parentElement)">
            <span class="thinking-toggle">${wantsExpanded ? '▼' : '▶'}</span>
            <span class="thinking-label">${label}</span>
            <span class="thinking-dots" aria-hidden="true"></span>
            <span class="thinking-elapsed" aria-label="Elapsed time">0s</span>
            <span class="thinking-count"></span>
        </div>
        ${phaseStripHtml}
        <div class="thinking-content${wantsExpanded ? '' : ' collapsed'}">
            <div class="thinking-steps"></div>
        </div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    currentThinkingMessage = div;
    currentThinkingMessage.dataset.startedAt = String(Date.now());
    thinkingSteps = [];
    // Drive the elapsed counter; stops when stopThinkingTicker() runs.
    _startThinkingTicker(div);
    return div;
}

let _thinkingTickerId = null;
function _startThinkingTicker(div) {
    if (_thinkingTickerId) clearInterval(_thinkingTickerId);
    const t0 = parseInt(div.dataset.startedAt, 10);
    const elapsedEl = div.querySelector('.thinking-elapsed');
    _thinkingTickerId = setInterval(() => {
        if (!div.isConnected || !elapsedEl) {
            clearInterval(_thinkingTickerId); _thinkingTickerId = null; return;
        }
        const s = Math.round((Date.now() - t0) / 1000);
        elapsedEl.textContent = (s < 60 ? `${s}s` : `${Math.floor(s/60)}m${s%60}s`);
    }, 500);
}
function stopThinkingTicker() {
    if (_thinkingTickerId) { clearInterval(_thinkingTickerId); _thinkingTickerId = null; }
}

function markAgenticPhase(phaseId) {
    if (!currentThinkingMessage || !phaseId) return;
    const strip = currentThinkingMessage.querySelector('.thinking-phase-strip');
    if (!strip) return;
    const order = AGENTIC_PHASES.map(p => p.id);
    const reached = order.indexOf(phaseId);
    if (reached < 0) return;
    strip.querySelectorAll('.phase-pill').forEach((pill, i) => {
        pill.classList.toggle('active', i === reached);
        pill.classList.toggle('done',   i < reached);
    });
}

/* ── Structured agentic events (intent_result, plan) ─────────────────────── */

function _escapeHtml(s) {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderIntentResult(ev) {
    if (!currentThinkingMessage) createThinkingMessage();
    const stepsContainer = currentThinkingMessage.querySelector('.thinking-steps');
    if (!stepsContainer) return;
    const intent = String(ev.intent || 'unknown').replace(/_/g, ' ');
    const confPct = (typeof ev.confidence === 'number')
        ? Math.round(ev.confidence * 100) + '%'
        : '';
    const tools = Array.isArray(ev.suggested_tools) ? ev.suggested_tools : [];
    const toolPills = tools.length
        ? tools.map(t => `<span class="agentic-pill">${_escapeHtml(t)}</span>`).join(' ')
        : '';
    const reasoning = ev.reasoning
        ? `<div class="agentic-reasoning">${_escapeHtml(ev.reasoning)}</div>`
        : '';
    const div = document.createElement('div');
    div.className = 'thinking-step analyzing agentic-structured';
    div.innerHTML = `
        <span class="icon">🧭</span>
        <div class="content">
            <div><strong>Intent:</strong> ${_escapeHtml(intent)}${
                confPct ? ` <span class="agentic-conf">${confPct}</span>` : ''
            }${
                ev.query_complexity && ev.query_complexity !== 'simple'
                    ? ` <span class="agentic-pill agentic-pill-muted">${_escapeHtml(ev.query_complexity)}</span>`
                    : ''
            }</div>
            ${toolPills ? `<div class="agentic-pills">${toolPills}</div>` : ''}
            ${reasoning}
        </div>
    `;
    stepsContainer.appendChild(div);
    const countSpan = currentThinkingMessage.querySelector('.thinking-count');
    if (countSpan) { thinkingSteps.push({intent: ev}); countSpan.textContent = `(${thinkingSteps.length} steps)`; }
    const container = document.getElementById('chat-container');
    if (container) container.scrollTop = container.scrollHeight;
}

function renderPlanResult(ev) {
    if (!currentThinkingMessage) createThinkingMessage();
    const stepsContainer = currentThinkingMessage.querySelector('.thinking-steps');
    if (!stepsContainer) return;
    const steps = Array.isArray(ev.steps) ? ev.steps : [];
    const stepList = steps.length
        ? `<ol class="agentic-plan-list">${steps.map((s, i) => `
            <li>
                <span class="agentic-pill">${_escapeHtml(s.tool || s.type || 'step')}</span>
                <span class="agentic-plan-desc">${_escapeHtml(s.description || '')}</span>
                ${s.query ? `<div class="agentic-plan-query"><code>${_escapeHtml(s.query)}</code></div>` : ''}
            </li>
        `).join('')}</ol>`
        : '<div class="agentic-reasoning">No steps planned.</div>';
    const fromHistory = ev.can_answer_from_history
        ? '<span class="agentic-pill agentic-pill-muted">answer from history</span>'
        : '';
    const reasoning = ev.reasoning
        ? `<div class="agentic-reasoning">${_escapeHtml(ev.reasoning)}</div>`
        : '';
    const div = document.createElement('div');
    div.className = 'thinking-step planning agentic-structured';
    div.innerHTML = `
        <span class="icon">📋</span>
        <div class="content">
            <div><strong>Plan:</strong> ${steps.length} step${steps.length === 1 ? '' : 's'} ${fromHistory}</div>
            ${reasoning}
            ${stepList}
        </div>
    `;
    stepsContainer.appendChild(div);
    const countSpan = currentThinkingMessage.querySelector('.thinking-count');
    if (countSpan) { thinkingSteps.push({plan: ev}); countSpan.textContent = `(${thinkingSteps.length} steps)`; }
    const container = document.getElementById('chat-container');
    if (container) container.scrollTop = container.scrollHeight;
}

// --- About modal --------------------------------------------------------
function showAboutModal() {
    const m = document.getElementById('about-modal');
    if (m) m.classList.add('visible');
}
function hideAboutModal() {
    const m = document.getElementById('about-modal');
    if (m) m.classList.remove('visible');
}

// --- Structured live-process renderers ----------------------------------
// Show a query rewrite (conversation-aware OR keyword optimizer) as its
// own card: original on top with a strike-through, rewritten below.
function renderQueryRephrased(ev) {
    if (!currentThinkingMessage) createThinkingMessage();
    const stepsContainer = currentThinkingMessage.querySelector('.thinking-steps');
    if (!stepsContainer) return;
    const byLabel = ev.by === 'conversation_history'
        ? 'conversation context'
        : 'keyword optimizer';
    const div = document.createElement('div');
    div.className = 'thinking-step analyzing agentic-structured';
    div.setAttribute('data-depth', '1');
    div.innerHTML = `
        <span class="icon">✏️</span>
        <div class="content">
            <div><strong>Query rewritten</strong>
                <span class="agentic-pill agentic-pill-muted">${_escapeHtml(byLabel)}</span></div>
            <div class="agentic-rephrase-original">${_escapeHtml(ev.original || '')}</div>
            <div class="agentic-rephrase-arrow">↓</div>
            <div class="agentic-rephrase-rewritten">${_escapeHtml(ev.rewritten || '')}</div>
        </div>`;
    stepsContainer.appendChild(div);
    const countSpan = currentThinkingMessage.querySelector('.thinking-count');
    if (countSpan) { thinkingSteps.push({rephrase: ev}); countSpan.textContent = `(${thinkingSteps.length} steps)`; }
    const container = document.getElementById('chat-container');
    if (container) container.scrollTop = container.scrollHeight;
}

// Provider progress: start = "Querying X, Y, Z...", done = per-provider counts.
function renderProviderProgress(ev) {
    if (!currentThinkingMessage) createThinkingMessage();
    const stepsContainer = currentThinkingMessage.querySelector('.thinking-steps');
    if (!stepsContainer) return;
    const div = document.createElement('div');
    div.className = 'thinking-step ' + (ev.phase === 'done' ? 'result' : 'tool') + ' agentic-structured';
    div.setAttribute('data-depth', '1');
    let body = '';
    if (ev.phase === 'start') {
        const provs = Array.isArray(ev.providers) ? ev.providers : [];
        const pills = provs.map(p => `<span class="agentic-pill">${_escapeHtml(p)}</span>`).join(' ');
        body = `
            <span class="icon">🔎</span>
            <div class="content">
                <div><strong>Querying databases</strong></div>
                <div class="agentic-pills">${pills}</div>
            </div>`;
    } else {
        const bp = ev.by_provider || {};
        const entries = Object.entries(bp).sort((a, b) => b[1] - a[1]);
        const pills = entries.length
            ? entries.map(([k, v]) =>
                `<span class="agentic-pill">${_escapeHtml(k)} <strong>${v}</strong></span>`
              ).join(' ')
            : `<span class="agentic-pill-muted">${ev.total || 0} hits</span>`;
        body = `
            <span class="icon">📊</span>
            <div class="content">
                <div><strong>Database results</strong>
                    <span class="agentic-pill agentic-pill-muted">${ev.total || 0} total</span></div>
                <div class="agentic-pills">${pills}</div>
            </div>`;
    }
    div.innerHTML = body;
    stepsContainer.appendChild(div);
    const countSpan = currentThinkingMessage.querySelector('.thinking-count');
    if (countSpan) { thinkingSteps.push({provider: ev}); countSpan.textContent = `(${thinkingSteps.length} steps)`; }
    const container = document.getElementById('chat-container');
    if (container) container.scrollTop = container.scrollHeight;
}

// Batch progress: live updating "X/Y" with a progress bar. We KEY by stage
// so each phase (abstract_analysis, theme_assignment, …) gets its own
// in-place updating row instead of one row per tick.
const _batchProgressNodes = {};  // stage -> DOM node
function renderBatchProgress(ev) {
    if (!currentThinkingMessage) createThinkingMessage();
    const stepsContainer = currentThinkingMessage.querySelector('.thinking-steps');
    if (!stepsContainer) return;
    const stage = String(ev.stage || 'batch');
    const cur = Number(ev.current || 0), tot = Number(ev.total || 1);
    const pct = Math.max(0, Math.min(100, Math.round((cur / tot) * 100)));
    let node = _batchProgressNodes[stage];
    if (!node || !node.isConnected) {
        node = document.createElement('div');
        node.className = 'thinking-step tool agentic-structured';
        node.setAttribute('data-depth', '1');
        stepsContainer.appendChild(node);
        _batchProgressNodes[stage] = node;
    }
    // Human-friendly labels per known stage.
    const stageLabel = {
        abstract_analysis: 'Abstract analysis',
        theme_assignment: 'Theme assignment',
    }[stage] || stage.replace(/_/g, ' ');
    const unit = (stage === 'abstract_analysis') ? 'batch' : 'paper';
    const sizeBadge = (ev.batch_size && Number(ev.batch_size) > 0)
        ? `<span class="agentic-pill agentic-pill-muted">${ev.batch_size} ${unit === 'batch' ? 'papers in batch' : 'in batch'}</span>`
        : '';
    node.innerHTML = `
        <span class="icon">📚</span>
        <div class="content">
            <div><strong>${_escapeHtml(stageLabel)}: ${cur}/${tot}</strong>
                ${sizeBadge}</div>
            <div class="agentic-progress-bar">
                <div class="agentic-progress-bar-fill" style="width:${pct}%"></div>
            </div>
        </div>`;
    if (cur >= tot) {
        // Completed — drop the handle so a fresh node spawns if this stage
        // restarts later (it doesn't in normal flow, but cleanly closes it).
        delete _batchProgressNodes[stage];
    }
    const container = document.getElementById('chat-container');
    if (container) container.scrollTop = container.scrollHeight;
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

/* ── Advanced query options helper ─────────────────────────────────────── */

/**
 * Read the advanced-options disclosure block if it has been opened/interacted
 * with and return the fields to merge into the POST body.
 * Only fields that differ from defaults are included.
 */
function getAdvancedQueryOptions() {
    const body = {};

    const details = document.getElementById('advanced-options-details');
    if (!details || !details.open) {
        // Disclosure never opened → use defaults; send nothing extra
        return body;
    }

    // Vector / BM25 slider (default 0.5 / 0.5)
    const vectorSlider = document.getElementById('adv-vector-slider');
    if (vectorSlider) {
        const vw = parseFloat(vectorSlider.value);
        if (!isNaN(vw) && Math.abs(vw - 0.5) > 0.01) {
            body.vector_weight = vw;
            body.bm25_weight = parseFloat((1 - vw).toFixed(2));
        }
    }

    // Recency weight slider (default 0)
    const recencySlider = document.getElementById('adv-recency-slider');
    if (recencySlider) {
        const rw = parseFloat(recencySlider.value);
        if (!isNaN(rw) && rw > 0.005) {
            body.recency_weight = rw;
        }
    }

    // Multi-KB checkboxes
    const kbCheckboxes = document.querySelectorAll('.adv-kb-checkbox:checked');
    if (kbCheckboxes.length >= 2) {
        body.kb_names = Array.from(kbCheckboxes).map(cb => cb.value);
    } else if (kbCheckboxes.length === 1) {
        // Single selection: treat like the main KB selector
        const name = kbCheckboxes[0].value;
        if (name !== selectedKb) {
            body.kb_name = name;
        }
    }

    return body;
}

/**
 * Populate the multi-KB checkbox list inside the advanced options block
 * from the global KB list. Called after loadKBs().
 */
function refreshAdvancedKbList() {
    const container = document.getElementById('adv-kb-list');
    if (!container) return;
    const select = document.getElementById('kb-select');
    if (!select) return;

    container.innerHTML = '';
    const options = select.querySelectorAll('option');
    options.forEach(function(opt) {
        if (!opt.value) return; // skip "No KB" option
        const label = document.createElement('label');
        label.className = 'adv-kb-option';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'adv-kb-checkbox';
        cb.value = opt.value;
        cb.checked = opt.value === selectedKb;
        label.appendChild(cb);
        const span = document.createElement('span');
        span.textContent = opt.value;
        label.appendChild(span);
        container.appendChild(label);
    });
}

/* ── Paper curation ─────────────────────────────────────────────────────── */

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

/* ── Inline capsule figure thumbnails (Task 13) ─────────────────────────── */

async function resolveFigureIdsInAssistantMessage() {
    if (!activeAssistantDiv) return;
    const tokenRe = /pdf_p\d+_i\d+/g;
    const tokens = [...new Set((activeAssistantText || '').match(tokenRe) || [])];
    if (tokens.length === 0) return;

    const papers = Array.from(currentTurnPapers);
    if (papers.length === 0) return;

    // Fetch figure index for each paper in scope (cached across turns)
    await Promise.all(papers.map(async (paperId) => {
        if (figureIndexByPaper[paperId]) return;
        try {
            const r = await fetch(
                "/api/capsule/" + encodeURIComponent(paperId) + "/figures"
            );
            figureIndexByPaper[paperId] = r.ok ? await r.json() : [];
        } catch (e) {
            figureIndexByPaper[paperId] = [];
        }
    }));

    // Build token -> paperId map (only when token appears in exactly one paper)
    const tokenToPaper = {};
    for (const tok of tokens) {
        const matches = [];
        for (const paperId of papers) {
            const recs = figureIndexByPaper[paperId] || [];
            const found = recs.some(
                (r) => ("pdf_p" + (r.page || 0) + "_i" + (r.index || 0)) === tok
            );
            if (found) matches.push(paperId);
        }
        if (matches.length === 1) tokenToPaper[tok] = matches[0];
    }

    if (Object.keys(tokenToPaper).length === 0) return;

    // Rewrite plain-text tokens in the rendered HTML to <img> thumbnails.
    let rewritten = activeAssistantDiv.innerHTML;
    for (const [tok, paperId] of Object.entries(tokenToPaper)) {
        const enc = encodeURIComponent(paperId);
        const safeTok = tok.replace(/[^A-Za-z0-9_]/g, "");
        const re = new RegExp("\\b" + safeTok + "\\b", "g");
        const safePaperAttr = paperId.replace(/"/g, '&quot;');
        rewritten = rewritten.replace(re,
            '<img class="inline-figure" data-paper="' + safePaperAttr +
            '" data-fig="' + safeTok +
            '" src="/api/capsule/' + enc + '/figure/' + safeTok +
            '" alt="Figure ' + safeTok + '" loading="lazy" />'
        );
    }
    activeAssistantDiv.innerHTML = rewritten;
    activeAssistantDiv.querySelectorAll(".inline-figure").forEach((img) => {
        img.addEventListener("click", openFigureLightbox);
    });
}

function openFigureLightbox(ev) {
    const img = ev.currentTarget;
    const lightbox = document.createElement("div");
    lightbox.className = "figure-lightbox";
    const full = document.createElement("img");
    full.src = img.src;
    full.alt = "";
    lightbox.appendChild(full);
    lightbox.addEventListener("click", () => lightbox.remove());
    document.body.appendChild(lightbox);
}

// Sub-project C (2026-05-15) — render hooks for code excerpts and figure refs.
function renderCodeExcerpt(payload) {
    const panel = document.getElementById('code-excerpts-panel');
    const list = document.getElementById('code-excerpts-list');
    if (!panel || !list) return;
    panel.style.display = '';

    const wrap = document.createElement('div');
    wrap.className = 'code-excerpt';

    const header = document.createElement('div');
    header.className = 'code-excerpt-header';

    const meta = document.createElement('div');
    meta.className = 'code-excerpt-meta';

    const file = document.createElement('span');
    file.className = 'file-path';
    file.textContent = payload.file_path || '';
    meta.appendChild(file);

    if (payload.symbol_name) {
        const sym = document.createElement('span');
        sym.className = 'symbol-name';
        sym.textContent = '· ' + payload.symbol_name;
        meta.appendChild(sym);
    }

    const lines = document.createElement('span');
    lines.className = 'line-range';
    lines.textContent = '· L' + payload.start_line + '-L' + payload.end_line;
    meta.appendChild(lines);

    header.appendChild(meta);

    if (payload.source_url) {
        const link = document.createElement('a');
        link.className = 'code-excerpt-source-link';
        link.href = payload.source_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'View source →';
        header.appendChild(link);
    }

    wrap.appendChild(header);

    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.className = 'language-' + (payload.language || 'plain');
    code.textContent = payload.text || '';
    pre.appendChild(code);
    wrap.appendChild(pre);

    list.appendChild(wrap);

    if (window.Prism && window.Prism.highlightElement) {
        window.Prism.highlightElement(code);
    }
}

function renderFigureRef(payload) {
    const panel = document.getElementById('figures-panel');
    const list = document.getElementById('figures-list');
    if (!panel || !list) return;
    panel.style.display = '';
    const card = document.createElement('div');
    card.className = 'figure-card';

    if (payload.thumbnail_b64) {
        const img = document.createElement('img');
        img.src = `data:image/png;base64,${payload.thumbnail_b64}`;
        img.alt = payload.label || 'Figure thumbnail';
        img.className = 'figure-thumbnail';
        card.appendChild(img);
    }

    if (payload.label) {
        const lbl = document.createElement('div');
        lbl.className = 'figure-label';
        lbl.textContent = payload.label;
        card.appendChild(lbl);
    }
    if (payload.caption) {
        const cap = document.createElement('div');
        cap.className = 'figure-caption';
        cap.textContent = payload.caption;
        card.appendChild(cap);
    }
    list.appendChild(card);
}

function clearAttachmentsPanels() {
    const codeList = document.getElementById('code-excerpts-list');
    const figList = document.getElementById('figures-list');
    if (codeList) codeList.innerHTML = '';
    if (figList) figList.innerHTML = '';
    const codePanel = document.getElementById('code-excerpts-panel');
    const figPanel = document.getElementById('figures-panel');
    if (codePanel) codePanel.style.display = 'none';
    if (figPanel) figPanel.style.display = 'none';
}
