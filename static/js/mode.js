/* Search-mode dropdown (basic / advanced / profond / contradiction) and download-cap slider. */

/* Available RAG modes with labels and descriptions.
   Add "contradiction" here so it appears in the mode picker and is a valid currentMode value. */
const RAG_MODES = [
    { value: 'basic',             label: 'Quick Search',            desc: 'Fast single-pass retrieval' },
    { value: 'advanced',          label: 'Advanced (Hybrid)',        desc: 'Query expansion + BM25/vector hybrid' },
    { value: 'deep_research',     label: 'Deep Research',            desc: 'Multi-cycle iterative research' },
    { value: 'agentic',           label: 'Perspicacite Agentic',     desc: 'Intent-classified, tool-using agent' },
    { value: 'literature_survey', label: 'Literature Survey',        desc: 'Broad search + theme clustering' },
    { value: 'contradiction',     label: 'Contradiction',            desc: 'Agreement / disagreement across papers' },
];

function setMode(mode) {
    currentMode = mode;
    console.log('RAG Mode set to:', mode);

    // Show/hide download cap control for agentic mode
    const downloadCapControl = document.getElementById('download-cap-control');
    if (mode === 'agentic') {
        downloadCapControl.classList.add('visible');
    } else {
        downloadCapControl.classList.remove('visible');
    }
}

/**
 * Align currentMode with the mode <select>. The API body uses currentMode; the
 * dropdown is the user's source of truth. After bfcache or soft reload, browsers
 * may restore the select without firing onchange, leaving currentMode stuck at
 * the script default ('basic') while the UI still shows Agentic.
 */
function syncRagModeFromDropdown() {
    const el = document.getElementById('mode-dropdown');
    if (el && el.value) {
        setMode(el.value);
    }
}

function updateDownloadCapDisplay(value) {
    document.getElementById('download-cap-value').textContent = value;
}
