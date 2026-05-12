/* Literature survey ("profond" mode): theme accordions, paper selection,
   "Add selected to KB", report generation and rendering. */

let currentSurveySession = null;
let surveySelectedPapers = new Set();

async function loadSurveySession(sessionId) {
    // Load literature survey session and display themes/paper selection
    try {
        const resp = await fetch(`/api/survey/${sessionId}`);
        const data = await resp.json();

        if (data.error) {
            showToast('Error: ' + data.error);
            return;
        }

        currentSurveySession = data;
        surveySelectedPapers = new Set(
            data.papers.filter(p => p.recommended).map(p => p.id)
        );

        displaySurveyInterface(data);
    } catch (e) {
        showToast('Error loading survey: ' + e.message);
    }
}

function displaySurveyInterface(data) {
    // Display the literature survey interface with themes and paper selection
    const container = document.getElementById('chat-container');

    let html = `
        <div class="survey-container">
            <h3>📖 Literature Survey: ${data.query}</h3>
            <p>Found ${data.papers_count} papers across ${data.themes_count} themes.
               ${data.papers.filter(p => p.recommended).length} papers pre-selected for deep analysis.</p>

            <div class="survey-actions">
                <button onclick="selectAllSurveyPapers(true)">Select All</button>
                <button onclick="selectAllSurveyPapers(false)">Deselect All</button>
                <button onclick="selectRecommendedPapers()">Select Recommended</button>
                <button class="primary" onclick="generateSurveyReport()">Generate Survey Report</button>
            </div>

            <div class="survey-themes">
    `;

    // Check if papers are assigned to themes
    const papersWithThemes = data.papers.filter(p => p.themes && p.themes.length > 0);

    if (data.themes && data.themes.length > 0 && papersWithThemes.length > 0) {
        // Group papers by theme
        data.themes.forEach(theme => {
            const themePapers = data.papers.filter(p => p.themes && p.themes.includes(theme.name));

            if (themePapers.length === 0) return; // Skip empty themes

            html += `
                <div class="survey-theme">
                    <div class="theme-header" onclick="toggleSurveyTheme(this)">
                        <span class="theme-toggle">▼</span>
                        <strong>${theme.name}</strong>
                        <span class="theme-count">(${themePapers.length} papers)</span>
                    </div>
                    <div class="theme-content">
                        <p class="theme-description">${theme.description}</p>
                        <div class="theme-papers">
            `;

            themePapers.forEach(paper => {
                const isSelected = surveySelectedPapers.has(paper.id);
                const recommendedBadge = paper.recommended ? '<span class="recommended-badge">⭐ Recommended</span>' : '';

                html += `
                    <div class="survey-paper ${isSelected ? 'selected' : ''}" data-paper-id="${paper.id}">
                        <input type="checkbox"
                               id="paper-${paper.id}"
                               ${isSelected ? 'checked' : ''}
                               onchange="toggleSurveyPaper('${paper.id}')">
                        <label for="paper-${paper.id}">
                            <div class="paper-title">${paper.title} ${recommendedBadge}</div>
                            <div class="paper-meta">
                                ${paper.authors.join(', ')} (${paper.year || 'N/A'}) |
                                Citations: ${paper.citation_count || 0} |
                                Relevance: ${paper.relevance_score}/5
                            </div>
                            <div class="paper-abstract">${paper.abstract}</div>
                            ${paper.reason ? `<div class="paper-reason">Why: ${paper.reason}</div>` : ''}
                        </label>
                    </div>
                `;
            });

            html += `
                        </div>
                    </div>
                </div>
            `;
        });
    }

    // Show papers without theme assignment (uncategorized)
    const papersWithoutThemes = data.papers.filter(p => !p.themes || p.themes.length === 0);
    if (papersWithoutThemes.length > 0) {
        html += `
            <div class="survey-theme uncategorized">
                <div class="theme-header" onclick="toggleSurveyTheme(this)">
                    <span class="theme-toggle">▼</span>
                    <strong>Uncategorized Papers</strong>
                    <span class="theme-count">(${papersWithoutThemes.length} papers)</span>
                </div>
                <div class="theme-content">
                    <div class="theme-papers">
        `;

        papersWithoutThemes.forEach(paper => {
            const isSelected = surveySelectedPapers.has(paper.id);
            const recommendedBadge = paper.recommended ? '<span class="recommended-badge">⭐ Recommended</span>' : '';

            html += `
                <div class="survey-paper ${isSelected ? 'selected' : ''}" data-paper-id="${paper.id}">
                    <input type="checkbox"
                           id="paper-${paper.id}"
                           ${isSelected ? 'checked' : ''}
                           onchange="toggleSurveyPaper('${paper.id}')">
                    <label for="paper-${paper.id}">
                        <div class="paper-title">${paper.title} ${recommendedBadge}</div>
                        <div class="paper-meta">
                            ${paper.authors.join(', ')} (${paper.year || 'N/A'}) |
                            Citations: ${paper.citation_count || 0} |
                            Relevance: ${paper.relevance_score}/5
                        </div>
                        <div class="paper-abstract">${paper.abstract}</div>
                        ${paper.reason ? `<div class="paper-reason">Why: ${paper.reason}</div>` : ''}
                    </label>
                </div>
            `;
        });

        html += `
                    </div>
                </div>
            </div>
        `;
    }

    html += `
            </div>

            <div class="survey-actions bottom">
                <span id="survey-selection-count">${surveySelectedPapers.size} papers selected</span>
                <button onclick="addSurveyPapersToKB()" ${!selectedKb ? 'disabled' : ''}>
                    ${selectedKb ? `Add Selected to "${selectedKb}"` : 'Select a KB first'}
                </button>
                <button class="primary" onclick="generateSurveyReport()">Generate Survey Report</button>
            </div>
        </div>
    `;

    // Append to chat
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = html;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function toggleSurveyTheme(header) {
    // Toggle survey theme expansion
    const content = header.nextElementSibling;
    const toggle = header.querySelector('.theme-toggle');
    if (content.style.display === 'none') {
        content.style.display = 'block';
        toggle.textContent = '▼';
    } else {
        content.style.display = 'none';
        toggle.textContent = '▶';
    }
}

function toggleSurveyPaper(paperId) {
    // Toggle paper selection
    if (surveySelectedPapers.has(paperId)) {
        surveySelectedPapers.delete(paperId);
    } else {
        surveySelectedPapers.add(paperId);
    }
    updateSurveySelectionDisplay();
}

function selectAllSurveyPapers(select) {
    // Select or deselect all papers
    if (select) {
        currentSurveySession.papers.forEach(p => surveySelectedPapers.add(p.id));
    } else {
        surveySelectedPapers.clear();
    }
    updateSurveySelectionDisplay();
}

function selectRecommendedPapers() {
    // Select only recommended papers
    surveySelectedPapers.clear();
    currentSurveySession.papers
        .filter(p => p.recommended)
        .forEach(p => surveySelectedPapers.add(p.id));
    updateSurveySelectionDisplay();
}

function updateSurveySelectionDisplay() {
    // Update UI to reflect current selection
    document.querySelectorAll('.survey-paper').forEach(el => {
        const paperId = el.dataset.paperId;
        const checkbox = el.querySelector('input[type="checkbox"]');
        checkbox.checked = surveySelectedPapers.has(paperId);
        el.classList.toggle('selected', surveySelectedPapers.has(paperId));
    });

    const countEl = document.getElementById('survey-selection-count');
    if (countEl) {
        countEl.textContent = `${surveySelectedPapers.size} papers selected`;
    }
}

async function addSurveyPapersToKB() {
    // Add selected survey papers to the current knowledge base
    if (!selectedKb) {
        showToast('Please select a knowledge base first');
        return;
    }
    if (surveySelectedPapers.size === 0) {
        showToast('Please select at least one paper');
        return;
    }
    if (!currentSurveySession) {
        showToast('No survey session available');
        return;
    }

    // Get selected papers from current session
    const selected = [];
    for (const paperId of surveySelectedPapers) {
        const paper = currentSurveySession.papers.find(p => p.id === paperId);
        if (paper) {
            selected.push({
                title: paper.title,
                authors: paper.authors || [],
                year: paper.year,
                doi: paper.doi,
                abstract: paper.abstract,
                citations: paper.citation_count
            });
        }
    }

    if (selected.length === 0) {
        showToast('No valid papers selected');
        return;
    }

    showToast(`Adding ${selected.length} papers to "${selectedKb}"...`);

    try {
        const resp = await fetch(`/api/kb/${selectedKb}/papers`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ papers: selected })
        });
        const data = await resp.json();
        if (data.error) {
            showToast('Error: ' + data.error);
            return;
        }
        showToast(`Added ${data.added_papers} papers to "${selectedKb}"`);
        loadKBs(); // Refresh KB list to show updated paper count
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

async function generateSurveyReport() {
    // Generate deep analysis report for selected papers
    if (surveySelectedPapers.size === 0) {
        showToast('Please select at least one paper');
        return;
    }

    if (surveySelectedPapers.size > 50) {
        showToast('Maximum 50 papers allowed for deep analysis');
        return;
    }

    // Update selection on server
    try {
        const resp = await fetch(`/api/survey/${currentSurveySession.session_id}/select`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                session_id: currentSurveySession.session_id,
                selected_paper_ids: Array.from(surveySelectedPapers)
            })
        });

        const data = await resp.json();
        if (data.error) {
            showToast('Error: ' + data.error);
            return;
        }

        // Now generate the report
        showToast('Generating survey report... This may take a few minutes.');

        const genResp = await fetch(`/api/survey/${currentSurveySession.session_id}/generate`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                session_id: currentSurveySession.session_id
            })
        });

        const genData = await genResp.json();
        if (genData.error) {
            showToast('Error: ' + genData.error);
            return;
        }

        // Display the report
        displaySurveyReport(genData);

    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

function displaySurveyReport(data) {
    // Display the generated survey report
    const container = document.getElementById('chat-container');

    const div = document.createElement('div');
    div.className = 'message assistant survey-report';
    div.innerHTML = `
        <h3>📖 Literature Survey Report</h3>
        <div class="report-content">${data.answer.replace(/\n/g, '<br>')}</div>
        <div class="report-meta">
            <p>Analyzed ${data.papers_analyzed} papers across ${data.themes} themes</p>
        </div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}
