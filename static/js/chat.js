class AIChatManager {
    constructor() {
        this.chatMessagesContainer = document.getElementById("chatMessages");
        this.chatInput = document.getElementById("chatInput");
        this.sendBtn = document.getElementById("chatSendBtn");
        this.clearBtn = document.getElementById("clearChatBtn");
        this.memoryListContainer = document.getElementById("memoryList");
        this.sessionId = "default";
        this.isWaiting = false;

        this.init();
    }

    init() {
        if (!this.chatMessagesContainer) return;

        if (this.sendBtn && this.chatInput) {
            this.sendBtn.addEventListener("click", () => this.sendMessage());
            this.chatInput.addEventListener("keydown", (e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            });
        }

        if (this.clearBtn) {
            this.clearBtn.addEventListener("click", () => this.clearChat());
        }

        // Quick action buttons
        document.querySelectorAll(".quick-prompt-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const prompt = btn.getAttribute("data-prompt");
                if (prompt && this.chatInput) {
                    this.chatInput.value = prompt;
                    this.sendMessage();
                }
            });
        });

        this.loadHistory();
        this.loadMemories();
    }

    async loadHistory() {
        try {
            const res = await fetch(`/api/chat/history?session_id=${this.sessionId}`);
            const data = await res.json();
            if (data.messages && data.messages.length > 0) {
                this.chatMessagesContainer.innerHTML = "";
                data.messages.forEach(msg => {
                    this.appendMessage(msg.role, msg.content, msg.tool_calls);
                });
            } else {
                this.showWelcomeMessage();
            }
            this.scrollToBottom();
        } catch (e) {
            console.error("Failed to load chat history:", e);
        }
    }

    showWelcomeMessage() {
        this.chatMessagesContainer.innerHTML = `
            <div class="chat-bubble-ai p-4 mb-4 text-sm text-gray-200">
                <div class="flex items-center space-x-2 text-indigo-400 font-semibold mb-2">
                    <i data-lucide="bot" class="w-4 h-4"></i>
                    <span>DEEPALPHA AI STRATEGY ENGINE</span>
                </div>
                <p class="leading-relaxed">
                    Hello! I am your 24/7 AI Quantitative Trading Coach & Optimizer. 
                    I continuously supervise trade analytics, evaluate market regimes, and learn from past trade results to dynamically refine your trading parameters.
                </p>
                <div class="mt-3 flex flex-wrap gap-2 text-xs">
                    <button class="quick-prompt-btn px-2.5 py-1.5 rounded-lg bg-indigo-950/60 border border-indigo-700/50 text-indigo-300 hover:bg-indigo-900/80 transition" data-prompt="Analyze recent trade history and suggest parameter improvements for maximum win rate.">
                        📊 Analyze Trades & Suggest Tuning
                    </button>
                    <button class="quick-prompt-btn px-2.5 py-1.5 rounded-lg bg-indigo-950/60 border border-indigo-700/50 text-indigo-300 hover:bg-indigo-900/80 transition" data-prompt="What is the current technical setup on BTCUSDT and ETHUSDT?">
                        ⚡ Market Technical Confluence
                    </button>
                    <button class="quick-prompt-btn px-2.5 py-1.5 rounded-lg bg-indigo-950/60 border border-indigo-700/50 text-indigo-300 hover:bg-indigo-900/80 transition" data-prompt="Teach the trading bot a new rule: If 15m RSI drops below 28 with volume > 2x average, prioritize long entries with 1.2% SL.">
                        🧠 Teach New Strategy Rule
                    </button>
                </div>
            </div>
        `;
        if (window.lucide) lucide.createIcons();
        
        // Re-attach listeners to dynamically generated quick buttons
        this.chatMessagesContainer.querySelectorAll(".quick-prompt-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const prompt = btn.getAttribute("data-prompt");
                if (prompt && this.chatInput) {
                    this.chatInput.value = prompt;
                    this.sendMessage();
                }
            });
        });
    }

    async sendMessage() {
        const text = this.chatInput.value.trim();
        if (!text || this.isWaiting) return;

        this.chatInput.value = "";
        this.isWaiting = true;
        this.appendMessage("user", text);
        this.scrollToBottom();

        // Show typing indicator
        const typingId = this.showTypingIndicator();

        try {
            const res = await fetch("/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text, session_id: this.sessionId })
            });
            const data = await res.json();
            this.removeTypingIndicator(typingId);

            if (data.content) {
                this.appendMessage("assistant", data.content, data.tools_executed);
                this.loadMemories(); // Refresh memories if any were added
                if (window.dashboardApp) {
                    window.dashboardApp.fetchStatus(); // Refresh performance/status
                }
            } else {
                this.appendMessage("assistant", "⚠️ Received empty response.");
            }
        } catch (e) {
            this.removeTypingIndicator(typingId);
            this.appendMessage("assistant", `❌ Error: ${e.message}`);
        } finally {
            this.isWaiting = false;
            this.scrollToBottom();
        }
    }

    appendMessage(role, content, tools = null) {
        const div = document.createElement("div");
        div.className = `flex mb-4 ${role === "user" ? "justify-end" : "justify-start"}`;

        let toolsHtml = "";
        if (tools && tools.length > 0) {
            toolsHtml = `
                <div class="mb-2 p-2 rounded-lg bg-black/40 border border-indigo-500/20 text-xs">
                    <div class="text-indigo-400 font-medium flex items-center gap-1 mb-1">
                        <i data-lucide="cpu" class="w-3.5 h-3.5"></i>
                        <span>AI Execution Tools Triggered:</span>
                    </div>
                    ${tools.map(t => `
                        <div class="text-gray-300 font-mono text-[11px] ml-2">
                            • <span class="text-amber-400">${t.name}</span>: ${JSON.stringify(t.args)}
                        </div>
                    `).join("")}
                </div>
            `;
        }

        // Format Markdown basic tags (bold, code blocks, lists)
        let formatted = this.formatMarkdown(content);

        if (role === "user") {
            div.innerHTML = `
                <div class="chat-bubble-user p-3.5 max-w-[80%] text-sm text-white shadow-lg">
                    <div class="font-medium text-xs text-indigo-200 mb-1">You</div>
                    <div>${formatted}</div>
                </div>
            `;
        } else {
            div.innerHTML = `
                <div class="chat-bubble-ai p-4 max-w-[88%] text-sm text-gray-200 shadow-xl">
                    <div class="flex items-center space-x-2 text-indigo-400 font-semibold text-xs mb-1.5">
                        <i data-lucide="sparkles" class="w-3.5 h-3.5"></i>
                        <span>DEEPALPHA AI</span>
                    </div>
                    ${toolsHtml}
                    <div class="prose prose-invert max-w-none text-sm leading-relaxed">${formatted}</div>
                </div>
            `;
        }

        this.chatMessagesContainer.appendChild(div);
        if (window.lucide) lucide.createIcons();
    }

    formatMarkdown(text) {
        if (!text) return "";
        let escaped = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");

        // Code blocks ```code```
        escaped = escaped.replace(/```([a-z]*)\n([\s\S]*?)```/gm, '<pre class="bg-black/50 p-2.5 rounded-lg my-2 overflow-x-auto text-xs font-mono text-indigo-300 border border-white/5">$2</pre>');
        // Inline code `code`
        escaped = escaped.replace(/`([^`]+)`/g, '<code class="bg-black/40 px-1.5 py-0.5 rounded text-indigo-300 font-mono text-xs border border-white/5">$1</code>');
        // Bold **text**
        escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong class="font-bold text-white">$1</strong>');
        // Newlines
        escaped = escaped.replace(/\n/g, '<br>');

        return escaped;
    }

    showTypingIndicator() {
        const id = "typing-" + Date.now();
        const div = document.createElement("div");
        div.id = id;
        div.className = "flex mb-4 justify-start";
        div.innerHTML = `
            <div class="chat-bubble-ai p-3.5 text-sm text-gray-400 flex items-center space-x-2">
                <div class="w-2 h-2 bg-indigo-500 rounded-full animate-bounce"></div>
                <div class="w-2 h-2 bg-indigo-400 rounded-full animate-bounce [animation-delay:-.3s]"></div>
                <div class="w-2 h-2 bg-indigo-300 rounded-full animate-bounce [animation-delay:-.5s]"></div>
                <span class="text-xs text-indigo-300 font-mono ml-2">Evaluating Market & Strategy Playbook...</span>
            </div>
        `;
        this.chatMessagesContainer.appendChild(div);
        this.scrollToBottom();
        return id;
    }

    removeTypingIndicator(id) {
        const el = document.getElementById(id);
        if (el) el.remove();
    }

    scrollToBottom() {
        this.chatMessagesContainer.scrollTop = this.chatMessagesContainer.scrollHeight;
    }

    async clearChat() {
        if (!confirm("Clear AI Chat history?")) return;
        await fetch(`/api/chat/clear?session_id=${this.sessionId}`, { method: "POST" });
        this.showWelcomeMessage();
    }

    async loadMemories() {
        if (!this.memoryListContainer) return;
        try {
            const res = await fetch("/api/memories");
            const memories = await res.json();
            if (memories && memories.length > 0) {
                this.memoryListContainer.innerHTML = memories.map(m => `
                    <div class="p-3 rounded-xl bg-slate-900/60 border border-white/5 hover:border-indigo-500/30 transition">
                        <div class="flex items-center justify-between mb-1">
                            <span class="font-semibold text-xs text-white">${m.title}</span>
                            <span class="text-[10px] px-2 py-0.5 rounded font-mono ${
                                m.category === 'rule' ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' :
                                m.category === 'insight' ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30' :
                                'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                            }">${m.category.toUpperCase()} • ${m.market_condition}</span>
                        </div>
                        <p class="text-xs text-gray-300 leading-relaxed">${m.content}</p>
                        <div class="mt-2 flex items-center justify-between text-[11px] text-gray-400 font-mono">
                            <span>Confidence: ${(m.confidence_score * 100).toFixed(0)}%</span>
                            <button onclick="window.aiChat.deleteMemory(${m.id})" class="text-red-400 hover:text-red-300 text-[10px] px-1.5 py-0.5 rounded bg-red-950/40 border border-red-800/30">
                                Delete
                            </button>
                        </div>
                    </div>
                `).join("");
            } else {
                this.memoryListContainer.innerHTML = `<p class="text-xs text-gray-400 text-center py-4">No AI memories recorded yet.</p>`;
            }
        } catch (e) {
            console.error("Failed to load memories:", e);
        }
    }

    async deleteMemory(id) {
        if (!confirm("Remove this memory item?")) return;
        await fetch(`/api/memories/${id}`, { method: "DELETE" });
        this.loadMemories();
    }
}
