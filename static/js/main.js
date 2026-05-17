/* Page bootstrap: wires up DOMContentLoaded initialization for all subsystems
   and the periodic timestamp refresh. Loaded last; depends on every prior file. */

document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    checkStatus();
    loadConversationHistory();
    initDatabaseSelector();
    setInterval(updateTimestamps, 60000);
    syncRagModeFromDropdown();
    initConversationSearch();
    initLLMModelLabel();

    // Wire up advanced-options slider display updates
    const vectorSlider = document.getElementById('adv-vector-slider');
    if (vectorSlider) {
        vectorSlider.addEventListener('input', function() {
            const vw = parseFloat(this.value);
            const bw = parseFloat((1 - vw).toFixed(2));
            const el = document.getElementById('adv-vector-value');
            if (el) el.textContent = 'vector ' + vw.toFixed(2) + ' / BM25 ' + bw.toFixed(2);
        });
    }

    const recencySlider = document.getElementById('adv-recency-slider');
    if (recencySlider) {
        recencySlider.addEventListener('input', function() {
            const el = document.getElementById('adv-recency-value');
            if (el) el.textContent = parseFloat(this.value).toFixed(2);
        });
    }
});
