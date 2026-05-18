/* Paper detail panel: fetches /api/paper?doi=... and fills #paper-detail-panel
   with title, authors, year, journal, abstract, content-type badge, references_count.
   Opened by openPaperDetail(doi); closed by closePaperDetail(). */

function openPaperDetail(doi, registryKey) {
    const panel = document.getElementById('paper-detail-panel');
    if (!panel) return;
    // When there's no DOI we can still render a useful panel from the
    // SourceReference fields stashed by the chat stream (see chat.js
    // window.__sourceRegistry). Title + authors + journal + abstract
    // (in chunk_text) + URL is enough.
    if (!doi) {
        const src = (window.__sourceRegistry || {})[registryKey];
        if (src) {
            panel.hidden = false;
            renderPaperDetailFromSource(panel, src);
            return;
        }
        panel.hidden = false;
        panel.innerHTML = '<div class="paper-detail-inner">' +
            '<button class="paper-detail-close" onclick="closePaperDetail()">&#x2715;</button>' +
            '<p class="paper-detail-error">No DOI available for this paper.</p>' +
            '</div>';
        return;
    }

    panel.hidden = false;
    panel.innerHTML = '<div class="paper-detail-inner">' +
        '<button class="paper-detail-close" onclick="closePaperDetail()">&#x2715;</button>' +
        '<p class="paper-detail-loading">Loading paper details…</p>' +
        '</div>';

    fetch('/api/paper?doi=' + encodeURIComponent(doi))
        .then(function(r) { return r.json(); })
        .then(function(data) { renderPaperDetail(panel, data); })
        .catch(function(e) {
            panel.innerHTML = '<div class="paper-detail-inner">' +
                '<button class="paper-detail-close" onclick="closePaperDetail()">&#x2715;</button>' +
                '<p class="paper-detail-error">Failed to load: ' + e.message + '</p>' +
                '</div>';
        });
}

function closePaperDetail() {
    const panel = document.getElementById('paper-detail-panel');
    if (panel) {
        panel.hidden = true;
        panel.innerHTML = '';
    }
}

function renderPaperDetail(panel, data) {
    if (data.error) {
        panel.innerHTML = '<div class="paper-detail-inner">' +
            '<button class="paper-detail-close" onclick="closePaperDetail()">&#x2715;</button>' +
            '<p class="paper-detail-error">' + escapeHtml(data.error) + '</p>' +
            '</div>';
        return;
    }

    const ctLabels = { structured: 'Structured', full_text: 'Full text', abstract: 'Abstract', none: '—' };
    const ct = data.content_type || 'none';
    const ctLabel = ctLabels[ct] || ct;
    const authors = (data.authors || []).join(', ');

    let html = '<div class="paper-detail-inner">';
    html += '<button class="paper-detail-close" onclick="closePaperDetail()">&#x2715;</button>';
    html += '<div class="paper-detail-meta-row">';
    html += '<span class="pipeline-badge pipeline-' + escapeHtml(ct) + '">' + escapeHtml(ctLabel) + '</span>';
    if (data.content_source) {
        html += '<span class="paper-detail-source">' + escapeHtml(data.content_source) + '</span>';
    }
    if (data.references_count) {
        html += '<span class="paper-detail-refs">' + data.references_count + ' refs</span>';
    }
    html += '</div>';
    html += '<h3 class="paper-detail-title">' + escapeHtml(data.title || data.doi) + '</h3>';
    if (authors) {
        html += '<p class="paper-detail-authors">' + escapeHtml(authors) + '</p>';
    }
    html += '<p class="paper-detail-bibline">';
    if (data.year) html += escapeHtml(String(data.year));
    if (data.journal) html += (data.year ? ' &middot; ' : '') + escapeHtml(data.journal);
    html += '</p>';
    if (data.doi) {
        html += '<p class="paper-detail-doi"><a href="https://doi.org/' + encodeURIComponent(data.doi) +
                '" target="_blank" rel="noopener noreferrer">doi:' + escapeHtml(data.doi) + '</a></p>';
    }
    if (data.abstract) {
        html += '<div class="paper-detail-abstract-label">Abstract</div>';
        html += '<div class="paper-detail-abstract">' + escapeHtml(data.abstract) + '</div>';
    }
    html += '</div>';

    panel.innerHTML = html;

    /* Lazily initialize Zotero button */
    if (data.doi) {
        ensureZoteroStatus().then(function(status) {
            if (status.enabled) {
                const btn = document.createElement('button');
                btn.className = 'zotero-btn';
                btn.textContent = 'Send to Zotero';
                btn.addEventListener('click', function() { sendToZotero(data.doi, btn); });
                const innerDiv = panel.querySelector('.paper-detail-inner');
                if (innerDiv) innerDiv.appendChild(btn);
            }
        });
    }

    /* Cycle C: Fetch external resources button (only when paper has a DOI
       and the current KB is known). */
    if (data.doi && typeof selectedKb !== 'undefined' && selectedKb) {
        const fetchBtn = document.createElement('button');
        fetchBtn.className = 'fetch-resources-btn';
        fetchBtn.textContent = 'Fetch external resources';
        fetchBtn.addEventListener('click', function() {
            fetchExternalResources('doi:' + data.doi, fetchBtn);
        });
        const innerDiv = panel.querySelector('.paper-detail-inner');
        if (innerDiv) innerDiv.appendChild(fetchBtn);

        const progress = document.createElement('div');
        progress.className = 'fetch-resources-progress';
        progress.id = 'fetch-resources-progress';
        progress.hidden = true;
        if (innerDiv) innerDiv.appendChild(progress);
    }
}

async function fetchExternalResources(paperId, buttonEl) {
    if (typeof selectedKb === 'undefined' || !selectedKb) {
        alert('No KB selected.');
        return;
    }
    const prog = document.getElementById('fetch-resources-progress');
    if (prog) {
        prog.hidden = false;
        prog.innerHTML = '<div class="fetch-resources-header">Fetching…</div>';
    }
    if (buttonEl) {
        buttonEl.disabled = true;
        buttonEl.textContent = 'Fetching…';
    }
    try {
        const url = '/api/kb/' + encodeURIComponent(selectedKb) +
                    '/paper/' + encodeURIComponent(paperId) + '/fetch-resources';
        const r = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kinds: null, ingest: true, force: false }),
        });
        if (r.status === 503) {
            if (prog) prog.innerHTML = '<div class="fetch-resources-error">Job registry unavailable on the server.</div>';
            return;
        }
        const body = await r.json();
        const sseUrl = body.sse_url || (body.job_id ? '/api/jobs/' + body.job_id + '/events' : null);
        if (!sseUrl) {
            if (prog) prog.innerHTML = '<div class="fetch-resources-error">No SSE URL returned.</div>';
            return;
        }
        const events = [];
        const ev = new EventSource(sseUrl);
        ev.onmessage = function(e) {
            try {
                const payload = JSON.parse(e.data);
                events.push(payload);
                if (prog) {
                    const lines = events.map(function(p) {
                        if (p.type === 'progress') {
                            return '  ' + (p.kind || '') + ' ' + (p.identifier || '') + ': ' + (p.status || '');
                        }
                        if (p.type === 'done' || p.type === 'finished') {
                            return 'Done.';
                        }
                        if (p.type === 'error' || p.error) {
                            return 'Error: ' + (p.error || JSON.stringify(p));
                        }
                        return JSON.stringify(p);
                    });
                    prog.innerHTML = '<div class="fetch-resources-header">Fetching…</div><pre class="fetch-resources-log">' +
                        lines.map(escapeHtml).join('\n') + '</pre>';
                }
            } catch (err) { /* ignore parse errors */ }
        };
        ev.onerror = function() {
            ev.close();
            if (buttonEl) {
                buttonEl.disabled = false;
                buttonEl.textContent = 'Fetch external resources';
            }
        };
    } catch (e) {
        if (prog) prog.innerHTML = '<div class="fetch-resources-error">Failed: ' + escapeHtml(String(e)) + '</div>';
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.textContent = 'Fetch external resources';
        }
    }
}

/* Zotero integration (lazily initialized once per page) */
window._zoteroStatusCache = null;
async function ensureZoteroStatus() {
    if (window._zoteroStatusCache !== null) return window._zoteroStatusCache;
    try {
        const r = await fetch('/api/zotero/status');
        if (!r.ok) {
            window._zoteroStatusCache = { enabled: false };
            return window._zoteroStatusCache;
        }
        window._zoteroStatusCache = await r.json();
    } catch (e) {
        window._zoteroStatusCache = { enabled: false };
    }
    return window._zoteroStatusCache;
}

async function sendToZotero(doi, buttonEl) {
    if (!doi) return;
    if (buttonEl) {
        buttonEl.disabled = true;
        buttonEl.textContent = 'Sending…';
    }
    try {
        const r = await fetch('/api/zotero/push', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dois: [doi] }),
        });
        if (r.status === 503) {
            alert('Zotero is not configured. Set zotero.enabled + api_key + library_id in config.yml.');
            return;
        }
        const data = await r.json();
        if ((data.created || []).length > 0) {
            alert('Sent to Zotero: ' + (data.created[0].key || 'OK'));
        } else if ((data.failed || []).length > 0) {
            alert('Zotero push failed: ' + (data.failed[0].reason || 'unknown'));
        } else {
            alert('Zotero push: no result');
        }
    } catch (e) {
        alert('Zotero push error: ' + e);
    } finally {
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.textContent = 'Send to Zotero';
        }
    }
}

window.sendToZotero = sendToZotero;
window.ensureZoteroStatus = ensureZoteroStatus;

// Render details panel directly from a SourceReference (no /api/paper hit).
// Used for web-fallback papers that have no DOI — we still want the user
// to see the abstract, authors, journal, and an "open in source" link.
function renderPaperDetailFromSource(panel, src) {
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    // Google Scholar packs the journal+year onto the end of the author
    // field: "LF Nothias, D Petras… - Nature Methods, 2020". Strip that
    // tail before splitting so the displayed author line is clean.
    function cleanAuthorBlob(s) {
        if (!s) return '';
        s = String(s);
        const dashIdx = s.indexOf(' - ');
        if (dashIdx !== -1) s = s.slice(0, dashIdx);
        return s.replace(/[\s…,.]+$/, '');
    }
    let authorsField;
    if (Array.isArray(src.authors) && src.authors.length) {
        authorsField = src.authors.map(cleanAuthorBlob).filter(Boolean).join(', ');
    } else if (typeof src.authors === 'string') {
        authorsField = cleanAuthorBlob(src.authors);
    } else {
        authorsField = '';
    }
    const year = src.year ? String(src.year) : '';
    const journal = src.journal ? esc(src.journal) : '';
    const provider = src.source ? esc(String(src.source).replace(/_/g, ' ')) : '';
    // Look in multiple places for the abstract: chunk_text is the legacy
    // canonical slot; SourceReference also exposes ``abstract`` via the
    // wire payload when the upstream paper had one (Crossref rescue path
    // for Google Scholar hits, OpenAlex / PubMed direct).
    const abstract = src.chunk_text || src.abstract || src.full_text || '';
    // Build the citation line piece-by-piece so we don't leave dangling
    // separators or em-dashes when fields are missing.
    const metaSegments = [];
    if (authorsField) metaSegments.push(esc(authorsField));
    if (year) metaSegments.push(esc(year));
    if (journal) metaSegments.push('<i>' + journal + '</i>');
    let html = '<div class="paper-detail-inner">';
    html += '<button class="paper-detail-close" onclick="closePaperDetail()">&#x2715;</button>';
    html += '<h3 class="paper-detail-title">' + esc(src.title || 'Untitled') + '</h3>';
    if (metaSegments.length) {
        html += '<p class="paper-detail-meta">' + metaSegments.join(' · ') + '</p>';
    }
    if (provider) {
        html += '<p class="paper-detail-meta"><b>Source provider:</b> ' + provider + '</p>';
    }
    if (src.url) {
        html += '<p><a href="' + esc(src.url) +
            '" target="_blank" rel="noopener noreferrer">Open in source ↗</a></p>';
    }
    if (abstract) {
        html += '<div class="paper-detail-abstract-label">Abstract</div>';
        html += '<div class="paper-detail-abstract">' + esc(abstract) + '</div>';
    } else {
        html += '<p class="paper-detail-meta"><i>No abstract was retrieved for this paper.</i></p>';
    }
    html += '</div>';
    panel.innerHTML = html;
}
window.openPaperDetail = openPaperDetail;
window.renderPaperDetailFromSource = renderPaperDetailFromSource;
