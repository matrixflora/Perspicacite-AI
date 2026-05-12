/* KB Statistics panel: fetches /api/kb/{name}/stats and renders an SVG bar
   chart (by year), plus tables for by_source, by_content_type, top_journals.
   Called from kb.js when the user clicks the "Stats" tab in the KB detail view. */

async function loadKbStats(kbName) {
    const container = document.getElementById('kb-stats-container');
    if (!container) return;
    if (!kbName) {
        container.innerHTML = '<p class="kb-stats-empty">No knowledge base selected.</p>';
        return;
    }

    container.innerHTML = '<p class="kb-stats-loading">Loading statistics…</p>';

    let data;
    try {
        const resp = await fetch('/api/kb/' + encodeURIComponent(kbName) + '/stats');
        data = await resp.json();
    } catch (e) {
        container.innerHTML = '<p class="kb-stats-error">Failed to load stats: ' + e.message + '</p>';
        return;
    }

    if (data.error) {
        container.innerHTML = '<p class="kb-stats-error">Error: ' + data.error + '</p>';
        return;
    }

    container.innerHTML = renderKbStats(data);
}

function renderKbStats(data) {
    const byYear = data.by_year || {};
    const bySource = data.by_source || {};
    const byContentType = data.by_content_type || {};
    const topJournals = data.top_journals || [];

    let html = '<div class="kb-stats-panel">';

    // Header summary
    html += '<div class="kb-stats-header">';
    html += '<span class="kb-stats-name">' + escapeHtml(data.name || '') + '</span>';
    html += '<span class="kb-stats-pill">' + (data.paper_count || 0) + ' papers</span>';
    html += '<span class="kb-stats-pill">' + (data.chunk_count || 0) + ' chunks</span>';
    if (data.embedding_model) {
        html += '<span class="kb-stats-pill kb-stats-pill-muted">' + escapeHtml(data.embedding_model) + '</span>';
    }
    html += '</div>';

    // By-year bar chart
    const years = Object.keys(byYear).sort();
    if (years.length > 0) {
        const counts = years.map(y => byYear[y]);
        const maxCount = Math.max(...counts, 1);
        const chartWidth = 260;
        const chartHeight = 80;
        const barGap = 3;
        const barWidth = Math.max(4, Math.floor((chartWidth - barGap * (years.length - 1)) / years.length));
        const labelHeight = 14;
        const svgHeight = chartHeight + labelHeight + 4;

        html += '<div class="kb-stats-section">';
        html += '<h4 class="kb-stats-section-title">Papers by Year</h4>';
        html += '<svg class="kb-stats-chart" viewBox="0 0 ' + chartWidth + ' ' + svgHeight +
                '" width="' + chartWidth + '" height="' + svgHeight +
                '" role="img" aria-label="Papers by year bar chart">';
        html += '<title>Papers by year</title>';

        years.forEach(function(year, i) {
            const count = byYear[year];
            const barH = Math.max(2, Math.round((count / maxCount) * chartHeight));
            const x = i * (barWidth + barGap);
            const y = chartHeight - barH;

            // Bar
            html += '<rect x="' + x + '" y="' + y + '" width="' + barWidth + '" height="' + barH +
                    '" rx="2" class="kb-stats-bar" />';
            // Count label above bar (only if there's room)
            if (barWidth >= 14) {
                html += '<text x="' + (x + barWidth / 2) + '" y="' + (y - 2) +
                        '" text-anchor="middle" class="kb-stats-bar-label">' + count + '</text>';
            }
            // Year label below
            if (barWidth >= 14) {
                html += '<text x="' + (x + barWidth / 2) + '" y="' + (chartHeight + labelHeight) +
                        '" text-anchor="middle" class="kb-stats-year-label">' + year + '</text>';
            }
        });

        html += '</svg>';
        if (data.scan_capped) {
            html += '<p class="kb-stats-note">* Statistics based on a sample (scan capped).</p>';
        }
        html += '</div>';
    }

    // By-source table
    if (Object.keys(bySource).length > 0) {
        html += '<div class="kb-stats-section">';
        html += '<h4 class="kb-stats-section-title">By Source</h4>';
        html += '<table class="kb-stats-table"><thead><tr><th>Source</th><th>Count</th></tr></thead><tbody>';
        Object.entries(bySource).sort((a, b) => b[1] - a[1]).forEach(function([src, cnt]) {
            html += '<tr><td>' + escapeHtml(src) + '</td><td>' + cnt + '</td></tr>';
        });
        html += '</tbody></table></div>';
    }

    // By-content-type table
    if (Object.keys(byContentType).length > 0) {
        const ctLabels = { structured: 'Structured', full_text: 'Full text', abstract: 'Abstract', none: '—' };
        html += '<div class="kb-stats-section">';
        html += '<h4 class="kb-stats-section-title">By Content Type</h4>';
        html += '<table class="kb-stats-table"><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>';
        Object.entries(byContentType).sort((a, b) => b[1] - a[1]).forEach(function([ct, cnt]) {
            const label = ctLabels[ct] || ct;
            html += '<tr><td><span class="pipeline-badge pipeline-' + escapeHtml(ct) + '">' +
                    label + '</span></td><td>' + cnt + '</td></tr>';
        });
        html += '</tbody></table></div>';
    }

    // Top journals table
    if (topJournals.length > 0) {
        html += '<div class="kb-stats-section">';
        html += '<h4 class="kb-stats-section-title">Top Journals</h4>';
        html += '<table class="kb-stats-table"><thead><tr><th>Journal</th><th>Count</th></tr></thead><tbody>';
        topJournals.slice(0, 10).forEach(function(entry) {
            html += '<tr><td>' + escapeHtml(entry.journal || '—') + '</td><td>' + entry.count + '</td></tr>';
        });
        html += '</tbody></table></div>';
    }

    if (data.created_at) {
        html += '<p class="kb-stats-note">Created: ' + escapeHtml(data.created_at.slice(0, 10)) + '</p>';
    }

    html += '</div>';
    return html;
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
