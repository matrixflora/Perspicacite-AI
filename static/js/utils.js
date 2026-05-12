/* Top-level state and shared utilities. Loaded first.
   Top-level let/const declarations are visible to all later <script>
   tags via shared classic-script scope. */

let sessionId = null;
let conversationId = null;  // Persistent conversation ID from backend
let messages = [];
let isProcessing = false;
let selectedKb = null;
let lastFoundPapers = [];
let currentMode = 'basic';

// Theme management
function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);
}

function updateThemeIcon(theme) {
    const icon = document.getElementById('theme-icon');
    if (icon) {
        icon.textContent = theme === 'light' ? '🌙' : '☀️';
    }
}

function formatDate(isoString) {
    if (!isoString) return '';
    try {
        const date = new Date(isoString);
        // Check if date is valid
        if (isNaN(date.getTime())) {
            return isoString;  // Return as-is if can't parse
        }
        return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
               date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return isoString;  // Return as-is on error
    }
}

// Update relative timestamps
function updateTimestamps() {
    const items = document.querySelectorAll('.chat-history-item');
    items.forEach(item => {
        // For new chats, we use a data attribute to store creation time
        const createdAt = item.dataset.createdAt;
        if (createdAt) {
            const dateEl = item.querySelector('.chat-date');
            if (dateEl) {
                const elapsed = Date.now() - new Date(createdAt).getTime();
                const minutes = Math.floor(elapsed / 60000);
                const hours = Math.floor(elapsed / 3600000);
                const days = Math.floor(elapsed / 86400000);
                
                if (minutes < 1) {
                    dateEl.textContent = 'Just now';
                } else if (minutes < 60) {
                    dateEl.textContent = `${minutes}m ago`;
                } else if (hours < 24) {
                    dateEl.textContent = `${hours}h ago`;
                } else if (days < 7) {
                    dateEl.textContent = `${days}d ago`;
                } else {
                    dateEl.textContent = formatDate(createdAt);
                }
            }
        }
    });
}

function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}
