/* Paper detail panel: fetches /api/paper?doi=... and fills #paper-detail-panel
   with title, authors, year, journal, abstract, content-type badge, references_count.
   Opened by openPaperDetail(doi); closed by closePaperDetail(). */

function openPaperDetail(doi) {
    const panel = document.getElementById('paper-detail-panel');
    if (!panel) return;
    if (!doi) {
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
}
