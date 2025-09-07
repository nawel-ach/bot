// === Configuration ===
const BASE_URL = (function () {
    const host = location.hostname;
    if (/^(localhost|127\.0\.0\.1)$/.test(host)) {
        return 'http://127.0.0.1:5000';
    }
    return 'https://your-production-server.com';
})();

const CHAT_ENDPOINT = `${BASE_URL}/api/chat`;

// === Enhanced IMOBOTChat class ===
class IMOBOTChat {
    constructor() {
        this.sessionKey = 'imobot_session_id';
        this.sessionId = this._restoreOrCreateSessionId();
        this.messageCount = 0;
        this.partsFound = 0;
        this.startTime = Date.now();
        this.isTyping = false;
        
        this._bindElements();
        this._bindEvents();
        this._showWelcomeMessage();
        this._createParticles();
    }

    _restoreOrCreateSessionId() {
        try {
            const stored = localStorage.getItem(this.sessionKey);
            if (stored) return stored;
        } catch (e) {
            console.warn('LocalStorage not available:', e);
        }
        const newId = `session_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
        try { 
            localStorage.setItem(this.sessionKey, newId); 
        } catch (e) {}
        return newId;
    }

    _bindElements() {
        this.messagesContainer = document.getElementById('messages');
        this.messageInput = document.getElementById('messageInput');
        this.sendBtn = document.getElementById('sendBtn');
    }

    _bindEvents() {
        this.sendBtn.addEventListener('click', () => this.sendMessage());

        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        this.messageInput.addEventListener('input', () => {
            this._autoResize(this.messageInput);
        });

        this.messagesContainer.addEventListener('click', () => this.messageInput.focus());
    }

    _createParticles() {
        const container = document.getElementById('bgParticles');
        if (!container) return;
        
        const particleCount = 15;
        
        for (let i = 0; i < particleCount; i++) {
            const particle = document.createElement('div');
            particle.className = 'particle';
            
            const size = Math.random() * 6 + 2;
            const leftPosition = Math.random() * 100;
            const animationDelay = Math.random() * 20;
            const animationDuration = Math.random() * 10 + 15;
            
            particle.style.cssText = `
                width: ${size}px;
                height: ${size}px;
                left: ${leftPosition}%;
                animation-delay: ${animationDelay}s;
                animation-duration: ${animationDuration}s;
            `;
            
            container.appendChild(particle);
        }
    }

    _autoResize(textarea) {
        textarea.style.height = '50px';
        textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px';
    }

    _setInputAndFocus(text) {
        this.messageInput.value = text;
        this._autoResize(this.messageInput);
        this.messageInput.focus();
    }

    _escapeHtml(s = '') {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    _formatMessage(content = '') {
        let safe = this._escapeHtml(content);
        
        // Handle bold text
        safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        
        // Handle line breaks
        safe = safe.replace(/\n/g, '<br>');
        
        // Handle links
        safe = safe.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
        
        // Handle emojis and icons
        safe = safe.replace(/🚗/g, '<span style="font-size: 1.2em;">🚗</span>');
        safe = safe.replace(/🚙/g, '<span style="font-size: 1.2em;">🚙</span>');
        safe = safe.replace(/🔧/g, '<span style="font-size: 1.2em;">🔧</span>');
        safe = safe.replace(/📋/g, '<span style="font-size: 1.2em;">📋</span>');
        safe = safe.replace(/📦/g, '<span style="font-size: 1.2em;">📦</span>');
        safe = safe.replace(/✅/g, '<span style="font-size: 1.2em;">✅</span>');
        safe = safe.replace(/❌/g, '<span style="font-size: 1.2em;">❌</span>');
        safe = safe.replace(/⚠️/g, '<span style="font-size: 1.2em;">⚠️</span>');
        safe = safe.replace(/💰/g, '<span style="font-size: 1.2em;">💰</span>');
        safe = safe.replace(/📊/g, '<span style="font-size: 1.2em;">📊</span>');
        safe = safe.replace(/📱/g, '<span style="font-size: 1.2em;">📱</span>');
        safe = safe.replace(/📧/g, '<span style="font-size: 1.2em;">📧</span>');
        safe = safe.replace(/🎉/g, '<span style="font-size: 1.2em;">🎉</span>');
        safe = safe.replace(/✨/g, '<span style="font-size: 1.2em;">✨</span>');
        
        return safe;
    }

    _scrollToBottom(instant = false) {
        try {
            if (instant) {
                this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
            } else {
                this.messagesContainer.scrollTo({ 
                    top: this.messagesContainer.scrollHeight, 
                    behavior: 'smooth' 
                });
            }
        } catch (e) {
            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
        }
    }

    _createMessageElement(role, htmlContent) {
        const wrapper = document.createElement('div');
        wrapper.className = `message ${role}`;

        const avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        const icon = document.createElement('i');
        icon.className = role === 'bot' ? 'fas fa-robot' : 'fas fa-user';
        avatar.appendChild(icon);

        const content = document.createElement('div');
        content.className = 'message-content';
        content.innerHTML = htmlContent;

        wrapper.appendChild(avatar);
        wrapper.appendChild(content);
        return wrapper;
    }

    _addMessage(role, text) {
        const formatted = this._formatMessage(text || '');
        const el = this._createMessageElement(role, formatted);
        this.messagesContainer.appendChild(el);
        this._scrollToBottom();
        return el;
    }

    _showTyping() {
        if (this.isTyping) return;
        this.isTyping = true;
        this.sendBtn.disabled = true;

        const typingEl = document.createElement('div');
        typingEl.id = 'typing-indicator';
        typingEl.className = 'typing-indicator';
        typingEl.innerHTML = `
            <div class="message-avatar" style="background: linear-gradient(135deg, var(--primary), var(--primary-light)); color: white;">
                <i class="fas fa-robot"></i>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
                <span>IMOBOT is thinking</span>
                <div class="typing-dots">
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                </div>
            </div>
        `;
        this.messagesContainer.appendChild(typingEl);
        this._scrollToBottom();
    }

    _hideTyping() {
        this.isTyping = false;
        this.sendBtn.disabled = false;
        const el = document.getElementById('typing-indicator');
        if (el) el.remove();
    }

    _showToast(message, type = 'info', ms = 4000) {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.style.animation = 'slideOutRight 0.4s cubic-bezier(0.4, 0, 0.2, 1)';
            setTimeout(() => toast.remove(), 400);
        }, ms);
    }

    async sendMessage() {
        const raw = (this.messageInput.value || '').trim();
        if (!raw || this.isTyping) return;

        this._addMessage('user', raw);
        this.messageInput.value = '';
        this._autoResize(this.messageInput);
        this.messageCount++;

        this._showTyping();

        const payload = { 
            message: raw, 
            sessionId: this.sessionId 
        };

        try {
            const res = await fetch(CHAT_ENDPOINT, {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(payload),
            });

            this._hideTyping();

            if (!res.ok) {
                const errorText = await res.text().catch(() => '');
                console.error('Server error:', res.status, errorText);
                this._addMessage('bot', `⚠️ Server error (${res.status}). Please try again.`);
                this._showToast('Server error: ' + res.status, 'error');
                return;
            }

            const data = await res.json();
            this._handleServerResponse(data);
            
        } catch (err) {
            this._hideTyping();
            console.error('Network error:', err);
            this._addMessage('bot', '🔌 Connection error. Please check if the backend server is running on port 5000.');
            this._showToast('Network error - Is the backend running?', 'error');
        }
    }

    _handleServerResponse(data) {
        // Display the main reply
        if (data.reply) {
            this._addMessage('bot', data.reply);
        }

        // Handle different response types
        if (data.type === 'parts' && Array.isArray(data.data)) {
            this._displayParts(data.data);
        }

        // Display suggestions
        if (Array.isArray(data.suggestions) && data.suggestions.length) {
            this._renderSuggestions(data.suggestions);
        }

        if (!data.reply && !data.data) {
            this._addMessage('bot', 'I did not understand the response from the server. Please try rephrasing.');
        }

        this.messageCount++;
    }

    _displayParts(parts = []) {
        if (!parts.length) {
            return;
        }

        const container = document.createElement('div');
        container.className = 'parts-list';

        parts.slice(0, 3).forEach(p => {
            const card = document.createElement('div');
            card.className = 'part-card';

            const price = p.sales_price ? `${Number(p.sales_price).toFixed(2)} DZD` : 'Price on request';
            const stock = p.quantity_on_hand || 0;
            const stockClass = stock === 0 ? 'out' : (stock < 5 ? 'low' : '');
            const stockText = stock === 0 ? 'Out of stock' : `${stock} units available`;

            const partName = this._escapeHtml(p.product_name || 'Spare Part');
            const reference = this._escapeHtml(p.internal_reference || '');
            const description = this._escapeHtml(p.product_description || '');

            card.innerHTML = `
                <div class="part-name">${partName}</div>
                <div class="part-details">
                    ${reference ? `<div><i class="fas fa-barcode"></i> ${reference}</div>` : ''}
                    ${description ? `<div style="grid-column: 1/-1;"><i class="fas fa-info-circle"></i> ${description}</div>` : ''}
                    <div class="part-price"><i class="fas fa-money-bill"></i> ${price}</div>
                    <div class="part-stock ${stockClass}"><i class="fas fa-boxes"></i> ${stockText}</div>
                </div>
            `;

            container.appendChild(card);
        });

        if (parts.length > 3) {
            const more = document.createElement('div');
            more.style.cssText = 'text-align: center; padding: 12px; color: var(--text-secondary); font-weight: 600;';
            more.innerHTML = `<i class="fas fa-plus-circle"></i> ${parts.length - 3} more results available`;
            container.appendChild(more);
        }

        this.messagesContainer.appendChild(container);
        this._scrollToBottom();
    }

    _renderSuggestions(list = []) {
        if (!Array.isArray(list) || !list.length) return;

        const wrap = document.createElement('div');
        wrap.className = 'suggestions';

        list.forEach(label => {
            const btn = document.createElement('button');
            btn.className = 'suggestion-btn';
            
            // Map suggestions to appropriate icons
            let icon = 'fas fa-lightbulb';
            if (label.toLowerCase().includes('search')) icon = 'fas fa-search';
            else if (label.toLowerCase().includes('track')) icon = 'fas fa-truck';
            else if (label.toLowerCase().includes('report')) icon = 'fas fa-chart-line';
            else if (label.toLowerCase().includes('yes')) icon = 'fas fa-check';
            else if (label.toLowerCase().includes('no')) icon = 'fas fa-times';
            else if (label.toLowerCase().includes('skip')) icon = 'fas fa-forward';
            else if (label.toLowerCase().includes('order')) icon = 'fas fa-shopping-cart';
            else if (label.toLowerCase().includes('continue')) icon = 'fas fa-arrow-right';
            else if (label.toLowerCase().includes('reference')) icon = 'fas fa-barcode';
            else if (label.toLowerCase().includes('part')) icon = 'fas fa-cog';

            btn.innerHTML = `<i class="${icon}"></i> ${label}`;

            btn.addEventListener('click', () => {
                this.messageInput.value = label;
                this.sendMessage();
            });

            wrap.appendChild(btn);
        });

        this.messagesContainer.appendChild(wrap);
        this._scrollToBottom();
    }

    async _showWelcomeMessage() {
        await new Promise(r => setTimeout(r, 500));
        
        const welcomeDiv = document.createElement('div');
        welcomeDiv.className = 'welcome-message';
        welcomeDiv.innerHTML = `
            <div class="welcome-title">🚀 Welcome to IMOBOT v3.0</div>
            <div class="welcome-subtitle">
                Your Intelligent Algerian Spare Parts Assistant
            </div>
            <div style="color: var(--text-secondary); margin-top: 16px; font-size: 15px; line-height: 1.6;">
                I can help you find the perfect spare parts for your vehicle! 
                Whether you have a part reference number or just know what you need, 
                I'll guide you through the process step by step.
            </div>
            <div class="welcome-features">
                <div class="feature-item">
                    <i class="fas fa-search"></i>
                    <span>Smart Search</span>
                </div>
                <div class="feature-item">
                    <i class="fas fa-barcode"></i>
                    <span>Reference Lookup</span>
                </div>
                <div class="feature-item">
                    <i class="fas fa-car"></i>
                    <span>All Brands</span>
                </div>
                <div class="feature-item">
                    <i class="fas fa-bolt"></i>
                    <span>Fast Service</span>
                </div>
            </div>
            <div style="margin-top: 20px; padding: 16px; background: linear-gradient(135deg, rgba(99, 102, 241, 0.1), rgba(139, 92, 246, 0.1)); border-radius: 12px; border: 1px solid var(--primary);">
                <div style="font-weight: 600; color: var(--primary); margin-bottom: 8px;">
                    <i class="fas fa-info-circle"></i> How it works:
                </div>
                <div style="font-size: 14px; color: var(--text-secondary); line-height: 1.6;">
                    1. Tell me your vehicle (brand, model, year)<br>
                    2. Search by part name or reference number<br>
                    3. Check availability and pricing<br>
                    4. Place your order instantly!
                </div>
            </div>
        `;

        this.messagesContainer.appendChild(welcomeDiv);

        // Add initial bot message
        setTimeout(() => {
            this._addMessage('bot', 'Hello! 👋 I\'m IMOBOT, your personal spare parts assistant.\n\nHow can I help you today?');
            
            // Show initial suggestions
            setTimeout(() => {
                this._renderSuggestions([
                    'Search Parts',
                    'Track Order (Coming Soon)',
                    'Daily Report (Coming Soon)'
                ]);
            }, 300);
        }, 800);
    }
}

// Initialize IMOBOT when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Initialize chat
    try {
        window.imobot = new IMOBOTChat();
        console.log('🤖 IMOBOT v3.0 initialized successfully');
    } catch (e) {
        console.error('Failed to initialize IMOBOT:', e);
    }

    // Setup widget controls
    const btn = document.getElementById('chatWidgetBtn');
    const win = document.getElementById('chatWidgetWindow');
    const close = document.getElementById('chatCloseBtn');
    const resize = document.getElementById('chatResizeBtn');

    btn.addEventListener('click', () => {
        win.classList.toggle('open');
        btn.classList.remove('pulse');
        if (win.classList.contains('open')) {
            btn.style.transform = 'scale(0.9)';
        } else {
            btn.style.transform = 'scale(1)';
        }
    });
    
    close.addEventListener('click', () => {
        win.classList.remove('open');
        btn.style.transform = 'scale(1)';
        btn.classList.add('pulse');
    });
    
    resize.addEventListener('click', () => {
        win.classList.toggle('maximized');
        resize.classList.toggle('fa-expand');
        resize.classList.toggle('fa-compress');
    });

    // Add keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        // ESC to close chat
        if (e.key === 'Escape') {
            if (win.classList.contains('open')) {
                win.classList.remove('open');
                btn.classList.add('pulse');
                btn.style.transform = 'scale(1)';
            }
        }
        
        // Ctrl+Shift+C to toggle chat
        if (e.ctrlKey && e.shiftKey && e.key === 'C') {
            win.classList.toggle('open');
            btn.classList.toggle('pulse');
        }
    });

    // Add smooth scrolling
    document.documentElement.style.scrollBehavior = 'smooth';
});

// Global utility functions
window.clearChat = function() {
    if (!confirm('Are you sure you want to clear the chat history?')) return;
    location.reload();
};

// Error boundary
window.addEventListener('error', (e) => {
    console.error('Global error:', e);
});

window.addEventListener('unhandledrejection', (e) => {
    console.error('Unhandled promise rejection:', e);
});