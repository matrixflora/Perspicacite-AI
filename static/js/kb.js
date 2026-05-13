/* Knowledge Base management: list/select/delete KBs, create new (empty or
   from BibTeX file), add papers to existing KB, post-search bulk-add UI. */

let bibtexFileLoaded = false;
let kbToDelete = '';

/**
 * Start SSE-driven ingestion progress for a job.
 * Renders a .progress-bar + .progress-bar-fill + .progress-label into `container`,
 * then opens an EventSource on /api/jobs/{jobId}/events.
 * Falls back to polling /api/jobs/{jobId} if SSE fails.
 * Returns an object with a close() method.
 */
function startIngestionProgress(jobId, total, container) {
    // Build progress bar DOM
    const wrapper = document.createElement('div');
    wrapper.innerHTML =
        '<div class="progress-bar"><div class="progress-bar-fill"></div></div>' +
        '<div class="progress-label">Starting…</div>';
    container.appendChild(wrapper);

    const progressBar = wrapper.querySelector('.progress-bar-fill');
    const progressLabel = wrapper.querySelector('.progress-label');

    let pollInterval = null;
    let closed = false;

    function close() {
        closed = true;
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    }

    function startPolling(es) {
        if (pollInterval) return;
        pollInterval = setInterval(async () => {
            try {
                const r = await fetch('/api/jobs/' + jobId);
                if (!r.ok) return;
                const row = await r.json();
                if (row.total) {
                    const pct = Math.round((row.done_count / row.total) * 100);
                    progressBar.style.width = pct + '%';
                }
                if (row.status === 'done' || row.status === 'error') {
                    clearInterval(pollInterval);
                    pollInterval = null;
                    if (es) { try { es.close(); } catch (_) {} }
                    progressBar.style.width = '100%';
                    progressLabel.textContent = row.status === 'done'
                        ? 'Done · ' + (row.result?.added_papers ?? '?') + ' papers, ' + (row.result?.added_chunks ?? '?') + ' chunks'
                        : 'Error: ' + (row.error || 'unknown');
                    if (typeof loadKBs === 'function') loadKBs();
                }
            } catch (_) {}
        }, 2000);
    }

    const es = new EventSource('/api/jobs/' + jobId + '/events');
    es.onmessage = (ev) => {
        if (closed) { es.close(); return; }
        try {
            const e = JSON.parse(ev.data);
            if (e.type === 'progress') {
                const pct = total ? Math.round((e.done / total) * 100) : 0;
                progressBar.style.width = pct + '%';
                progressLabel.textContent = e.done + '/' + total + (e.status ? ' · ' + e.status : '');
            } else if (e.type === 'done') {
                progressBar.style.width = '100%';
                progressLabel.textContent = 'Done · ' + (e.result?.added_papers ?? '?') + ' papers, ' + (e.result?.added_chunks ?? '?') + ' chunks';
                es.close();
                if (typeof loadKBs === 'function') loadKBs();
            } else if (e.type === 'error') {
                progressLabel.textContent = 'Error: ' + (e.error || 'unknown');
                es.close();
            }
        } catch (_) {}
    };
    es.onerror = () => {
        if (closed) { es.close(); return; }
        // SSE failed — fall back to polling
        startPolling(es);
    };

    return { close };
}

// KB management
async function loadKBs() {
    try {
        const resp = await fetch('/api/kb');
        const kbs = await resp.json();
        const select = document.getElementById('kb-select');
        const currentVal = select.value;
        select.innerHTML = '<option value="">No KB (web search only)</option>';
        for (const kb of kbs) {
            const opt = document.createElement('option');
            opt.value = kb.name;
            opt.textContent = `${kb.name} (${kb.paper_count} papers)`;
            if (kb.description) opt.title = kb.description;
            select.appendChild(opt);
        }
        if (currentVal) select.value = currentVal;
        // Refresh advanced-options multi-KB checkbox list
        if (typeof refreshAdvancedKbList === 'function') {
            refreshAdvancedKbList();
        }
    } catch (e) {
        console.error('Failed to load KBs:', e);
    }
}

function selectKB(name) {
    selectedKb = name || null;
    const infoDiv = document.getElementById('kb-info');
    const deleteBtn = document.getElementById('kb-delete-btn');
    const addBibtexBtn = document.getElementById('kb-add-bibtex-btn');
    const addDoisBtn = document.getElementById('kb-add-dois-btn');
    const statsTabStrip = document.getElementById('kb-detail-tab-strip');
    const statsContainer = document.getElementById('kb-stats-container');
    if (selectedKb) {
        fetch(`/api/kb/${selectedKb}`).then(r => r.json()).then(data => {
            if (data.error) {
                infoDiv.style.display = 'none';
                deleteBtn.style.display = 'none';
                addBibtexBtn.style.display = 'none';
                if (addDoisBtn) addDoisBtn.style.display = 'none';
                if (statsTabStrip) statsTabStrip.style.display = 'none';
                if (statsContainer) { statsContainer.innerHTML = ''; statsContainer.style.display = 'none'; }
                return;
            }
            // Show info tab content (default tab)
            infoDiv.style.display = 'block';
            infoDiv.innerHTML = `<strong>${data.name}</strong><br>` +
                (data.description ? data.description + '<br>' : '') +
                `${data.paper_count} papers, ${data.chunk_count} chunks`;
        });
        deleteBtn.style.display = 'inline';
        addBibtexBtn.style.display = 'inline';
        if (addDoisBtn) addDoisBtn.style.display = 'inline';
        // Show the tab strip, default to "Info" tab
        if (statsTabStrip) {
            statsTabStrip.style.display = 'flex';
            switchKbDetailTab('info');
        }
        if (statsContainer) statsContainer.style.display = 'none';
    } else {
        infoDiv.style.display = 'none';
        deleteBtn.style.display = 'none';
        addBibtexBtn.style.display = 'none';
        if (addDoisBtn) addDoisBtn.style.display = 'none';
        if (statsTabStrip) statsTabStrip.style.display = 'none';
        if (statsContainer) { statsContainer.innerHTML = ''; statsContainer.style.display = 'none'; }
    }

    // Update survey "Add to KB" button if survey is visible
    updateSurveyAddToKBButton();
    // Refresh the multi-KB list in advanced options
    if (typeof refreshAdvancedKbList === 'function') {
        refreshAdvancedKbList();
    }
}

function switchKbDetailTab(which) {
    const infoDiv = document.getElementById('kb-info');
    const statsContainer = document.getElementById('kb-stats-container');
    const infoBtn = document.getElementById('kb-detail-tab-info');
    const statsBtn = document.getElementById('kb-detail-tab-stats');

    if (which === 'stats') {
        if (infoDiv) infoDiv.style.display = 'none';
        if (statsContainer) { statsContainer.style.display = 'block'; }
        if (infoBtn) infoBtn.classList.remove('active');
        if (statsBtn) statsBtn.classList.add('active');
        // Load stats lazily
        if (typeof loadKbStats === 'function' && selectedKb) {
            loadKbStats(selectedKb);
        }
    } else {
        if (infoDiv) infoDiv.style.display = 'block';
        if (statsContainer) { statsContainer.style.display = 'none'; statsContainer.innerHTML = ''; }
        if (infoBtn) infoBtn.classList.add('active');
        if (statsBtn) statsBtn.classList.remove('active');
    }
}

function updateSurveyAddToKBButton() {
    // Update the "Add Selected to KB" button in survey interface
    const surveyContainer = document.querySelector('.survey-container');
    if (!surveyContainer) return;

    const addButton = surveyContainer.querySelector('button[onclick="addSurveyPapersToKB()"]');
    if (addButton) {
        if (selectedKb) {
            addButton.disabled = false;
            addButton.textContent = `Add Selected to "${selectedKb}"`;
        } else {
            addButton.disabled = true;
            addButton.textContent = 'Select a KB first';
        }
    }
}

function toggleCreateKB() {
    const form = document.getElementById('kb-create-form');
    const willOpen = !form.classList.contains('visible');
    form.classList.toggle('visible');

    // Reset to "Empty KB" tab and clear inputs whenever the panel toggles.
    switchKbCreateTab('empty');
    document.getElementById('kb-empty-name-input').value = '';
    document.getElementById('kb-empty-desc-input').value = '';
    document.getElementById('kb-bibtex-name-input').value = '';
    document.getElementById('kb-bibtex-content').value = '';
    const dropZone = document.getElementById('bibtex-drop-zone');
    if (dropZone) {
        const label = dropZone.querySelector('div:nth-child(2)');
        if (label) label.innerHTML = 'Drop a <b>.bib</b> file here or click to browse';
    }
    const progress = document.getElementById('bibtex-progress');
    if (progress) progress.style.display = 'none';
    bibtexFileLoaded = false;

    if (willOpen) {
        setTimeout(() => document.getElementById('kb-empty-name-input').focus(), 50);
    }
}

function switchKbCreateTab(which) {
    const empty = which === 'empty';
    document.getElementById('kb-tab-btn-empty').classList.toggle('active', empty);
    document.getElementById('kb-tab-btn-bibtex').classList.toggle('active', !empty);
    document.getElementById('kb-tab-panel-empty').classList.toggle('active', empty);
    document.getElementById('kb-tab-panel-bibtex').classList.toggle('active', !empty);
}

async function createEmptyKB() {
    const name = document.getElementById('kb-empty-name-input').value.trim();
    const description = document.getElementById('kb-empty-desc-input').value.trim();

    if (!name) {
        showToast('Please enter a KB name');
        return;
    }

    const btn = document.getElementById('kb-empty-create-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Creating...';

    try {
        const resp = await fetch('/api/kb', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name, description })
        });

        const data = await resp.json();
        if (!resp.ok || data.error) {
            const msg = data.detail
                ? (Array.isArray(data.detail) ? data.detail.map(d => d.msg || d).join('; ') : data.detail)
                : (data.error || 'Unknown error');
            throw new Error(msg);
        }

        const actualName = data.name || name;
        showToast(`KB "${actualName}" created`);

        await loadKBs();
        document.getElementById('kb-select').value = actualName;
        selectKB(actualName);

        document.getElementById('kb-create-form').classList.remove('visible');
        document.getElementById('kb-empty-name-input').value = '';
        document.getElementById('kb-empty-desc-input').value = '';
    } catch (e) {
        showToast('Error: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

async function addToKBFromBibtex(file) {
    if (!file) return;
    if (!selectedKb) {
        showToast('Please select a knowledge base first');
        return;
    }

    const reader = new FileReader();
    reader.onload = async function(e) {
        const bibtex = e.target.result;
        const entryCount = (bibtex.match(/@\w+\s*\{/g) || []).length;
        showToast(`Starting import of ${entryCount} papers to "${selectedKb}"…`);

        // Show progress area under the Add from BibTeX button
        let progressContainer = document.getElementById('kb-add-bibtex-progress');
        if (!progressContainer) {
            progressContainer = document.createElement('div');
            progressContainer.id = 'kb-add-bibtex-progress';
            const btnRow = document.getElementById('kb-add-bibtex-btn').parentNode;
            btnRow.parentNode.insertBefore(progressContainer, btnRow.nextSibling);
        }
        progressContainer.innerHTML = '';

        try {
            const resp = await fetch('/api/kb/' + encodeURIComponent(selectedKb) + '/bibtex/async', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ bibtex })
            });

            const data = await resp.json();
            if (!resp.ok || data.error) {
                const msg = data.detail
                    ? (Array.isArray(data.detail) ? data.detail.map(d => d.msg || d).join('; ') : data.detail)
                    : (data.error || 'Unknown error');
                throw new Error(msg);
            }

            const jobId = data.job_id;
            const total = data.total || entryCount;
            startIngestionProgress(jobId, total, progressContainer);

        } catch (err) {
            showToast('Error: ' + err.message);
        }
    };
    reader.onerror = function() {
        showToast('Error reading file');
    };
    reader.readAsText(file);

    // Reset file input so the same file can be re-selected
    document.getElementById('kb-add-bibtex-file').value = '';
}

async function createKBFromBibtex() {
    const name = document.getElementById('kb-bibtex-name-input').value.trim();
    const bibtex = document.getElementById('kb-bibtex-content').value.trim();

    if (!name) {
        showToast('Please enter a KB name');
        return;
    }
    if (!bibtex) {
        showToast('Please drop a .bib file first');
        return;
    }

    // Count BibTeX entries client-side for progress tracking
    const entryCount = (bibtex.match(/@\w+\s*\{/g) || []).length;

    const createBtn = document.getElementById('bibtex-create-btn');
    const progressDiv = document.getElementById('bibtex-progress');
    const progressBar = document.getElementById('bibtex-progress-bar');
    const progressText = document.getElementById('bibtex-progress-text');

    try {
        // Show progress, disable button
        createBtn.disabled = true;
        createBtn.textContent = 'Creating...';
        progressDiv.style.display = 'block';
        progressBar.style.width = '5%';
        progressText.textContent = `Creating KB for ${entryCount} entries…`;

        // First create the KB (sync — fast metadata-only operation)
        const resp = await fetch('/api/kb', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name, description: 'Created from BibTeX' })
        });

        if (!resp.ok) {
            const data = await resp.json();
            const msg = data.detail
                ? (Array.isArray(data.detail) ? data.detail.map(d => d.msg || d).join('; ') : data.detail)
                : (data.error || 'Unknown error');
            throw new Error(msg);
        }

        const kbData = await resp.json();
        const actualName = kbData.name || name;

        progressBar.style.width = '10%';
        progressText.textContent = `Starting async import of ${entryCount} papers…`;

        // Start async BibTeX ingestion job
        const bibtexResp = await fetch('/api/kb/' + encodeURIComponent(actualName) + '/bibtex/async', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ bibtex })
        });

        const bibtexData = await bibtexResp.json();
        if (!bibtexResp.ok || bibtexData.error) {
            throw new Error(bibtexData.error || 'Unknown error');
        }

        const jobId = bibtexData.job_id;
        const total = bibtexData.total || entryCount;

        // Hide the old inline progress bar and drive via SSE
        progressBar.style.width = '10%';
        progressText.textContent = '';

        // Use a wrapper div inside progressDiv for startIngestionProgress
        const sseContainer = document.createElement('div');
        progressDiv.appendChild(sseContainer);

        const handle = startIngestionProgress(jobId, total, sseContainer);

        await loadKBs();
        document.getElementById('kb-select').value = actualName;
        selectKB(actualName);

        // Reset form inputs (but leave progress visible)
        document.getElementById('kb-bibtex-name-input').value = '';
        document.getElementById('kb-bibtex-content').value = '';
        bibtexFileLoaded = false;
        const dropZone = document.getElementById('bibtex-drop-zone');
        dropZone.querySelector('div:last-child').innerHTML = 'Drop a <b>.bib</b> file here or click to browse';
        dropZone.style.borderColor = '';
        document.getElementById('bibtex-file-input').value = '';

        // Close form after a moment, keep progress bar visible until done
        setTimeout(() => {
            document.getElementById('kb-create-form').classList.remove('visible');
        }, 800);

    } catch (e) {
        showToast('Error: ' + e.message);
        progressDiv.style.display = 'none';
        progressBar.style.width = '0%';
    } finally {
        createBtn.disabled = false;
        createBtn.textContent = 'Create from BibTeX';
    }
}

/**
 * Toggle the DOI add panel for the currently selected KB.
 */
function toggleAddDoisPanel() {
    const panel = document.getElementById('kb-dois-panel');
    if (!panel) return;
    const isVisible = panel.classList.contains('visible');
    panel.classList.toggle('visible', !isVisible);
    if (!isVisible) {
        document.getElementById('kb-dois-input').focus();
    }
}

/**
 * Add DOIs to the currently selected KB via the async endpoint.
 */
async function addDoisToKB() {
    if (!selectedKb) {
        showToast('Please select a knowledge base first');
        return;
    }

    const textarea = document.getElementById('kb-dois-input');
    if (!textarea) return;
    const raw = textarea.value.trim();
    if (!raw) {
        showToast('Please enter at least one DOI');
        return;
    }

    // Parse DOIs: one per line, strip whitespace and https://doi.org/ prefix
    const dois = raw.split(/\s+/).map(d => d.trim().replace(/^https?:\/\/doi\.org\//i, '')).filter(Boolean);
    if (!dois.length) {
        showToast('No valid DOIs found');
        return;
    }

    const btn = document.getElementById('kb-dois-submit-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Submitting…'; }

    const progressContainer = document.getElementById('kb-dois-progress');
    if (progressContainer) progressContainer.innerHTML = '';

    try {
        const resp = await fetch('/api/kb/' + encodeURIComponent(selectedKb) + '/dois/async', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ dois })
        });

        const data = await resp.json();
        if (!resp.ok || data.error) {
            const msg = data.detail
                ? (Array.isArray(data.detail) ? data.detail.map(d => d.msg || d).join('; ') : data.detail)
                : (data.error || 'Unknown error');
            throw new Error(msg);
        }

        const jobId = data.job_id;
        const total = data.total || dois.length;
        showToast(`Started importing ${total} DOIs into "${selectedKb}"…`);

        if (progressContainer) {
            startIngestionProgress(jobId, total, progressContainer);
        }

        // Clear input
        textarea.value = '';
    } catch (err) {
        showToast('Error: ' + err.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Add DOIs'; }
    }
}

async function addToKB(btn) {
    if (!selectedKb || !lastFoundPapers.length) return;

    const section = btn.closest('.papers-found');
    const checkboxes = section.querySelectorAll('input[type="checkbox"]');
    const selected = [];
    checkboxes.forEach(cb => {
        if (cb.checked) {
            const p = lastFoundPapers[parseInt(cb.dataset.index)];
            selected.push({
                title: p.title,
                authors: p.authors || [],
                year: p.year,
                doi: p.doi,
                abstract: p.abstract,
                citations: p.citations
            });
        }
    });

    if (!selected.length) {
        showToast('No papers selected');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Adding...';

    try {
        const resp = await fetch(`/api/kb/${selectedKb}/papers`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ papers: selected })
        });
        const data = await resp.json();
        if (data.error) {
            showToast('Error: ' + data.error);
            btn.disabled = false;
            btn.textContent = `Add selected to "${selectedKb}"`;
            return;
        }
        btn.textContent = `✓ Added ${data.added_papers} papers`;
        showToast(`Added ${data.added_papers} papers to "${selectedKb}"`);
        loadKBs();
    } catch (e) {
        showToast('Error: ' + e.message);
        btn.disabled = false;
        btn.textContent = `Add selected to "${selectedKb}"`;
    }
}

async function createKBFromSelectedPapers() {
    if (!lastFoundPapers.length) {
        showToast('No papers found');
        return;
    }

    // Collect selected papers
    const section = document.querySelector('.papers-found');
    const checkboxes = section.querySelectorAll('input[type="checkbox"]');
    const selected = [];
    checkboxes.forEach(cb => {
        if (cb.checked) {
            const p = lastFoundPapers[parseInt(cb.dataset.index)];
            selected.push({
                title: p.title,
                authors: p.authors || [],
                year: p.year,
                doi: p.doi,
                abstract: p.abstract,
                citations: p.citations
            });
        }
    });

    if (!selected.length) {
        showToast('No papers selected');
        return;
    }

    // Prompt for KB name
    const name = prompt(`Create a new knowledge base with ${selected.length} paper${selected.length > 1 ? 's' : ''}. Enter KB name:`);
    if (!name || !name.trim()) {
        return;
    }

    const kbName = name.trim();
    const btn = section.querySelector('button[onclick="createKBFromSelectedPapers()"]');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Creating...';

    try {
        // First create the KB
        const createResp = await fetch('/api/kb', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name: kbName, description: `Created from ${selected.length} paper${selected.length > 1 ? 's' : ''}` })
        });

        if (!createResp.ok) {
            const data = await createResp.json();
            const msg = data.detail
                ? (Array.isArray(data.detail) ? data.detail.map(d => d.msg || d).join('; ') : data.detail)
                : (data.error || 'Unknown error');
            throw new Error(msg);
        }

        const kbData = await createResp.json();
        const actualName = kbData.name || kbName;

        // Then add papers
        const addResp = await fetch(`/api/kb/${encodeURIComponent(actualName)}/papers`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ papers: selected })
        });

        const addData = await addResp.json();
        if (addData.error) {
            throw new Error(addData.error);
        }

        btn.textContent = `✓ KB "${actualName}" created with ${addData.added_papers} paper${addData.added_papers > 1 ? 's' : ''}`;
        showToast(`KB "${actualName}" created with ${addData.added_papers} paper${addData.added_papers > 1 ? 's' : ''}`);

        // Load KBs and select the new one
        await loadKBs();
        document.getElementById('kb-select').value = actualName;
        selectKB(actualName);

    } catch (e) {
        showToast('Error: ' + e.message);
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

function showDeleteKBDialog(kbName) {
    kbToDelete = kbName;
    document.getElementById('delete-kb-name').textContent = kbName;
    document.getElementById('delete-kb-name2').textContent = kbName;
    document.getElementById('delete-kb-input').value = '';
    document.getElementById('delete-kb-confirm').disabled = true;
    document.getElementById('delete-modal').classList.add('visible');
}

function hideDeleteKBDialog() {
    document.getElementById('delete-modal').classList.remove('visible');
    kbToDelete = '';
}

function checkDeleteKBInput() {
    const input = document.getElementById('delete-kb-input').value;
    document.getElementById('delete-kb-confirm').disabled = input !== kbToDelete;
}

async function confirmDeleteKB() {
    if (kbToDelete && document.getElementById('delete-kb-input').value === kbToDelete) {
        try {
            const resp = await fetch(`/api/kb/${kbToDelete}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.error) {
                showToast('Error: ' + data.error);
                return;
            }
            showToast('KB "' + kbToDelete + '" deleted');
            hideDeleteKBDialog();
            selectedKb = null;
            document.getElementById('kb-select').value = '';
            document.getElementById('kb-info').style.display = 'none';
            await loadKBs();
        } catch (e) {
            showToast('Error deleting KB: ' + e.message);
        }
    }
}

// Wire up BibTeX drag-and-drop and file input listeners on page load.
document.addEventListener('DOMContentLoaded', function () {
    const dropZone = document.getElementById('bibtex-drop-zone');
    const fileInput = document.getElementById('bibtex-file-input');
    const hiddenInput = document.getElementById('kb-bibtex-content');

    function handleFile(file) {
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function(e) {
            hiddenInput.value = e.target.result;
            bibtexFileLoaded = true;
            dropZone.querySelector('div:last-child').innerHTML =
                '<b>' + file.name + '</b> loaded (' + Math.round(file.size / 1024) + ' KB)';
            dropZone.style.borderColor = 'var(--accent)';
        };
        reader.onerror = function() {
            showToast('Error reading file');
        };
        reader.readAsText(file);
    }

    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => handleFile(e.target.files[0]));

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'var(--accent-color)';
        dropZone.style.background = 'var(--hover-bg)';
    });
    dropZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'var(--border-color)';
        dropZone.style.background = '';
    });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = 'var(--border-color)';
        dropZone.style.background = '';
        const file = e.dataTransfer.files[0];
        if (file && (file.name.endsWith('.bib') || file.name.endsWith('.txt') || file.type === 'text/plain')) {
            handleFile(file);
        } else {
            showToast('Please drop a .bib file');
        }
    });
});

/* ── Build KBs from Zotero ────────────────────────────────────────────────── */

/**
 * Open the "Build KBs from Zotero" modal: fetches the plan from
 * GET /api/zotero/plan, renders a table of would-be KBs with editable
 * names + checkboxes, then on Execute POSTs to
 * /api/zotero/build-kbs/async and streams progress via SSE.
 */
async function openZoteroBuildModal() {
    const modal = document.getElementById('zotero-build-modal');
    const loading = document.getElementById('zotero-plan-loading');
    const table = document.getElementById('zotero-plan-table');
    const progress = document.getElementById('zotero-progress');
    const executeBtn = document.getElementById('zotero-build-execute');

    // Reset state each time the modal opens.
    modal.classList.add('visible');
    loading.classList.remove('hidden');
    loading.textContent = 'Loading plan…';
    table.classList.add('hidden');
    progress.classList.add('hidden');
    progress.textContent = '';
    executeBtn.disabled = false;

    let plan = [];
    try {
        const r = await fetch('/api/zotero/plan');
        if (r.status === 503) {
            loading.textContent =
                'Zotero is not configured (set zotero.enabled in config.yml).';
            executeBtn.disabled = true;
            return;
        }
        if (!r.ok) {
            loading.textContent = `Error: HTTP ${r.status}`;
            executeBtn.disabled = true;
            return;
        }
        const body = await r.json();
        plan = body.plan || [];
    } catch (e) {
        loading.textContent = `Error: ${e}`;
        executeBtn.disabled = true;
        return;
    }

    if (!plan.length) {
        loading.textContent = 'No collections found in your Zotero library.';
        executeBtn.disabled = true;
        return;
    }

    const tbody = document.querySelector('#zotero-plan-table tbody');
    tbody.innerHTML = '';
    plan.forEach((p, i) => {
        const tr = document.createElement('tr');
        const source = p.source_collection_name || 'Unfiled';
        // Build cells without innerHTML interpolation of user-controlled
        // strings to avoid XSS via Zotero collection names.
        const tdCheck = document.createElement('td');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = true;
        cb.dataset.i = String(i);
        tdCheck.appendChild(cb);

        const tdName = document.createElement('td');
        const nameIn = document.createElement('input');
        nameIn.type = 'text';
        nameIn.value = p.kb_name || '';
        nameIn.dataset.name = String(i);
        tdName.appendChild(nameIn);

        const tdSource = document.createElement('td');
        tdSource.textContent = source;
        const tdItems = document.createElement('td');
        tdItems.textContent = p.item_count ?? 0;
        const tdDoi = document.createElement('td');
        tdDoi.textContent = p.with_doi_count ?? 0;
        const tdPdf = document.createElement('td');
        tdPdf.textContent = p.with_pdf_count ?? 0;
        const tdNotes = document.createElement('td');
        tdNotes.textContent = p.with_notes_count ?? 0;

        tr.appendChild(tdCheck);
        tr.appendChild(tdName);
        tr.appendChild(tdSource);
        tr.appendChild(tdItems);
        tr.appendChild(tdDoi);
        tr.appendChild(tdPdf);
        tr.appendChild(tdNotes);
        tbody.appendChild(tr);
    });
    loading.classList.add('hidden');
    table.classList.remove('hidden');

    executeBtn.onclick = async () => {
        const selected = [];
        document.querySelectorAll('#zotero-plan-table tbody tr').forEach((tr, i) => {
            const cb = tr.querySelector('input[type="checkbox"]');
            const nameIn = tr.querySelector('input[type="text"]');
            if (cb && cb.checked) {
                selected.push({ ...plan[i], kb_name: (nameIn ? nameIn.value : plan[i].kb_name) });
            }
        });
        if (!selected.length) {
            showToast('Select at least one KB to build.');
            return;
        }
        executeBtn.disabled = true;
        progress.classList.remove('hidden');
        progress.textContent = `Submitting ${selected.length} KB build job(s)…\n`;
        try {
            const r = await fetch('/api/zotero/build-kbs/async', {
                method: 'POST',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify({ plan: selected }),
            });
            const body = await r.json();
            if (!r.ok || body.error) {
                const msg = body.detail
                    ? (Array.isArray(body.detail) ? body.detail.map(d => d.msg || d).join('; ') : body.detail)
                    : (body.error || `HTTP ${r.status}`);
                throw new Error(msg);
            }
            table.classList.add('hidden');
            progress.textContent += `Job ${body.job_id} started.\n`;
            const sseUrl = body.sse_url || `/api/jobs/${body.job_id}/events`;
            const ev = new EventSource(sseUrl);
            ev.onmessage = (m) => {
                progress.textContent += m.data + '\n';
                progress.scrollTop = progress.scrollHeight;
            };
            ev.addEventListener('done', () => {
                ev.close();
                progress.textContent += '\nDone. KB list refreshed.';
                if (typeof loadKBs === 'function') loadKBs();
            });
            ev.onerror = () => {
                progress.textContent += '\n(SSE stream closed)';
                ev.close();
                if (typeof loadKBs === 'function') loadKBs();
            };
        } catch (e) {
            progress.textContent += `\nError: ${e.message || e}`;
            executeBtn.disabled = false;
        }
    };
}

function closeZoteroBuildModal() {
    const modal = document.getElementById('zotero-build-modal');
    if (modal) modal.classList.remove('visible');
}

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('build-kbs-from-zotero-btn');
    if (btn) btn.addEventListener('click', openZoteroBuildModal);
    const cancel = document.getElementById('zotero-build-cancel');
    if (cancel) cancel.addEventListener('click', closeZoteroBuildModal);
});
