/* Database-source checkboxes in the sidebar (OpenAlex, arXiv, etc.). */

const defaultDatabases = ['semantic_scholar', 'openalex', 'pubmed'];

// Initialize database selector
function initDatabaseSelector() {
    const selector = document.getElementById('db-selector');
    selector.addEventListener('click', function(e) {
        const label = e.target.closest('.db-option');
        if (label) {
            const checkbox = label.querySelector('input');
            checkbox.checked = !checkbox.checked;
            label.classList.toggle('selected', checkbox.checked);
            e.preventDefault();
        }
    });
}

function toggleAllDatabases() {
    const options = document.querySelectorAll('.db-option');
    const allSelected = Array.from(options).every(opt => opt.classList.contains('selected'));
    options.forEach(opt => {
        const checkbox = opt.querySelector('input');
        checkbox.checked = !allSelected;
        opt.classList.toggle('selected', !allSelected);
    });
}

function getSelectedDatabases() {
    const selected = [];
    document.querySelectorAll('.db-option.selected').forEach(opt => {
        selected.push(opt.dataset.db);
    });
    return selected.length > 0 ? selected : defaultDatabases;
}
