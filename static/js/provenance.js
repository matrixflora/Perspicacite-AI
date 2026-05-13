// static/js/provenance.js
// Provenance disclosure: lazy-loads /api/conversations/{convId}/messages/{msgId}/provenance
// and renders Request / Retrieval / Reasoning panels inside a <details> element
// appended to the assistant message bubble.

window.attachProvenance = function attachProvenance(messageEl, messageId, conversationId) {
  if (!messageEl || !messageId || !conversationId) return;
  if (messageEl.querySelector('.provenance-disclosure')) return;
  const wrapper = document.createElement('details');
  wrapper.className = 'provenance-disclosure';
  const summary = document.createElement('summary');
  summary.textContent = 'Provenance';
  wrapper.appendChild(summary);
  const body = document.createElement('div');
  body.className = 'provenance-body';
  body.innerHTML = '<em>Loading…</em>';
  wrapper.appendChild(body);
  messageEl.appendChild(wrapper);
  wrapper.addEventListener('toggle', async () => {
    if (!wrapper.open || wrapper.dataset.loaded === '1') return;
    try {
      const r = await fetch(
        '/api/conversations/' + encodeURIComponent(conversationId) +
        '/messages/' + encodeURIComponent(messageId) + '/provenance'
      );
      if (!r.ok) {
        body.innerHTML = '<em>No provenance (status ' + r.status + ')</em>';
        return;
      }
      const rec = await r.json();
      body.innerHTML = renderProvenance(rec);
      wrapper.dataset.loaded = '1';
    } catch (e) {
      body.innerHTML = '<em>Error: ' + escProv(String(e)) + '</em>';
    }
  });
};

function renderProvenance(rec) {
  const params = rec.request_params || {};
  const kbDisplay = escProv(
    params.kb_name || (params.kb_names || []).join(', ') || '?'
  );
  const req = '<details open><summary>Request</summary>' +
    '<div class="prov-block">mode=<b>' + escProv(rec.rag_mode) + '</b>' +
    ' · kb=<b>' + kbDisplay + '</b>' +
    ' · top_k=' + (params.top_k != null ? params.top_k : '-') +
    ' · recency=' + (params.recency_weight != null ? params.recency_weight : '-') +
    ' · weights v/b=' + (params.vector_weight != null ? params.vector_weight : '-') +
    ' / ' + (params.bm25_weight != null ? params.bm25_weight : '-') +
    '</div></details>';

  const events = rec.retrieval_events || [];
  const rows = events.map(function(e) {
    const score = typeof e.score === 'number' ? e.score.toFixed(3) : '-';
    return '<tr>' +
      '<td>' + (e.rank != null ? e.rank : '-') + '</td>' +
      '<td>' + escProv(e.title || '-') + '</td>' +
      '<td>' + score + '</td>' +
      '<td>' + escProv(e.kb_name || '-') + '</td>' +
      '<td>' + escProv(e.content_type || '-') + '</td>' +
      '<td>' + escProv(e.pipeline_step || '-') + '</td>' +
      '</tr>';
  }).join('');
  const ret = '<details><summary>Retrieval (' + events.length + ')</summary>' +
    '<table class="prov-table"><thead><tr>' +
    '<th>#</th><th>Title</th><th>Score</th><th>KB</th><th>Type</th><th>Source</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></details>';

  const traceItems = (rec.mode_trace || []).map(function(t) {
    return '<li><b>' + escProv(t.step) + '</b> ' +
      escProv(JSON.stringify(t.detail || {})) + '</li>';
  }).join('');

  const llmCalls = rec.llm_calls || [];
  const llmItems = llmCalls.map(function(c) {
    const latency = typeof c.latency_ms === 'number' ? Math.round(c.latency_ms) : '-';
    return '<details><summary>' +
      escProv(c.stage_label || 'llm') + ' · ' +
      escProv(c.model || '?') + ' · ' +
      (c.prompt_tokens || 0) + '/' + (c.completion_tokens || 0) + 't · ' +
      latency + 'ms</summary>' +
      '<pre class="prov-prompt">' + escProv(JSON.stringify(c.prompt_messages || [], null, 2)) + '</pre>' +
      '<pre class="prov-response">' + escProv(c.response_text || '') + '</pre>' +
      '</details>';
  }).join('');

  const reasoning = '<details><summary>Reasoning &amp; LLM calls (' + llmCalls.length + ')</summary>' +
    '<ol class="prov-trace">' + traceItems + '</ol>' +
    llmItems + '</details>';

  return req + ret + reasoning;
}

function escProv(s) {
  return String(s == null ? '' : s).replace(/[&<>]/g, function(ch) {
    return ch === '&' ? '&amp;' : ch === '<' ? '&lt;' : '&gt;';
  });
}
