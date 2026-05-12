/* Search-mode dropdown (basic / advanced / profond) and download-cap slider. */

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
