/* Knowledge Base management: list/select/delete KBs, create new (empty or
   from BibTeX file), add papers to existing KB, post-search bulk-add UI. */

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
    } catch (e) {
        console.error('Failed to load KBs:', e);
    }
}

function selectKB(name) {
    selectedKb = name || null;
    const infoDiv = document.getElementById('kb-info');
    const deleteBtn = document.getElementById('kb-delete-btn');
    const addBibtexBtn = document.getElementById('kb-add-bibtex-btn');
    if (selectedKb) {
        fetch(`/api/kb/${selectedKb}`).then(r => r.json()).then(data => {
            if (data.error) {
                infoDiv.style.display = 'none';
                deleteBtn.style.display = 'none';
                addBibtexBtn.style.display = 'none';
                return;
            }
            infoDiv.style.display = 'block';
            infoDiv.innerHTML = `<strong>${data.name}</strong><br>` +
                (data.description ? data.description + '<br>' : '') +
                `${data.paper_count} papers, ${data.chunk_count} chunks`;
        });
        deleteBtn.style.display = 'inline';
        addBibtexBtn.style.display = 'inline';
    } else {
        infoDiv.style.display = 'none';
        deleteBtn.style.display = 'none';
        addBibtexBtn.style.display = 'none';
    }

    // Update survey "Add to KB" button if survey is visible
    updateSurveyAddToKBButton();
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
        showToast(`Importing ${entryCount} papers to "${selectedKb}"...`);

        try {
            const resp = await fetch('/api/kb/' + encodeURIComponent(selectedKb) + '/bibtex', {
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

            const added = data.added_papers || 0;
            const dupes = data.skipped_duplicates || 0;
            const pdfOk = data.pdf_download ? data.pdf_download.success : 0;
            const pdfFail = data.pdf_download ? data.pdf_download.failed : 0;

            let summary = `Added ${added} of ${entryCount} papers to "${selectedKb}"`;
            if (dupes) summary += ` (${dupes} duplicates skipped)`;
            if (pdfOk) summary += ` — ${pdfOk} PDFs downloaded`;
            showToast(summary);

            // Refresh KB info
            selectKB(selectedKb);
            await loadKBs();
            document.getElementById('kb-select').value = selectedKb;
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
        progressText.textContent = `Creating KB for ${entryCount} entries...`;

        // First create the KB
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

        progressBar.style.width = '15%';
        progressText.textContent = `Importing ${entryCount} papers...`;

        // Then add BibTeX content
        const bibtexResp = await fetch('/api/kb/' + encodeURIComponent(actualName) + '/bibtex', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ bibtex })
        });

        const bibtexData = await bibtexResp.json();
        if (!bibtexResp.ok || bibtexData.error) {
            throw new Error(bibtexData.error || 'Unknown error');
        }

        const added = bibtexData.added_papers || 0;
        const dupes = bibtexData.skipped_duplicates || 0;
        const pdfOk = bibtexData.pdf_download ? bibtexData.pdf_download.success : 0;
        const pdfFail = bibtexData.pdf_download ? bibtexData.pdf_download.failed : 0;

        progressBar.style.width = '100%';
        let summary = `${added} of ${entryCount} papers added`;
        if (dupes) summary += ` (${dupes} duplicates skipped)`;
        if (pdfOk) summary += ` — ${pdfOk} PDFs downloaded`;
        if (pdfFail) summary += ` (${pdfFail} PDFs unavailable)`;
        progressText.textContent = summary;

        showToast('KB "' + actualName + '" created with ' + (bibtexData.added_papers || 0) + ' papers');

        // Reset form
        document.getElementById('kb-bibtex-name-input').value = '';
        document.getElementById('kb-bibtex-content').value = '';
        bibtexFileLoaded = false;
        const dropZone = document.getElementById('bibtex-drop-zone');
        dropZone.querySelector('div:last-child').innerHTML = 'Drop a <b>.bib</b> file here or click to browse';
        dropZone.style.borderColor = '';
        document.getElementById('bibtex-file-input').value = '';

        await loadKBs();
        document.getElementById('kb-select').value = actualName;
        selectKB(actualName);

        // Hide progress and form after a moment
        setTimeout(() => {
            progressDiv.style.display = 'none';
            progressBar.style.width = '0%';
            document.getElementById('kb-create-form').classList.remove('visible');
        }, 2000);

    } catch (e) {
        showToast('Error: ' + e.message);
        progressDiv.style.display = 'none';
        progressBar.style.width = '0%';
    } finally {
        createBtn.disabled = false;
        createBtn.textContent = 'Create from BibTeX';
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
