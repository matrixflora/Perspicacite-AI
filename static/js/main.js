/* Page bootstrap: wires up DOMContentLoaded initialization for all subsystems
   and the periodic timestamp refresh. Loaded last; depends on every prior file. */

document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    checkStatus();
    loadConversationHistory();
    initDatabaseSelector();
    setInterval(updateTimestamps, 60000);
    syncRagModeFromDropdown();
});
