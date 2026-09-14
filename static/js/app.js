class DashboardApp {
    constructor() {
        this.socket = null;
        this.tradingMode = "PAPER";
        this.isRunning = false;
        this.equityChart = null;
        this.winLossChart = null;
        this.symbolPnlChart = null;
        this.currentSymbol = "BTCUSDT";
        this.activeTab = "terminal";

        this.init();
    }

    init() {
        this.setupNavigation();
        this.setupEventListeners();
        this.setupWebSocket();
        this.fetchStatus();
        this.fetchBalances();
        this.fetchTrades();
        this.fetchStrategies();
        this.fetchAnalytics();
        this.fetchGroqModels();
        this.startPeriodicUpdates();
    }

    setupNavigation() {
        document.querySelectorAll(".nav-tab").forEach(tab => {
            tab.addEventListener("click", () => {
                const target = tab.getAttribute("data-tab");
                this.switchTab(target);
            });
        });
    }

    switchTab(tabId) {
        this.activeTab = tabId;
        document.querySelectorAll(".nav-tab").forEach(t => {
            if (t.getAttribute("data-tab") === tabId) {
                t.classList.add("active", "text-indigo-400", "border-b-2", "border-indigo-500");
                t.classList.remove("text-gray-400");
            } else {
                t.classList.remove("active", "text-indigo-400", "border-b-2", "border-indigo-500");
                t.classList.add("text-gray-400");
            }
        });

        document.querySelectorAll(".tab-content").forEach(c => {
            if (c.id === `tab-${tabId}`) {
                c.classList.remove("hidden");
            } else {
                c.classList.add("hidden");
            }
        });

        if (tabId === "analytics") {
            this.fetchAnalytics();
        } else if (tabId === "strategy") {
            this.fetchStrategies();
        }
    }

    setupEventListeners() {
        // Bot Start/Stop toggle
        const toggleBtn = document.getElementById("botToggleBtn");
        if (toggleBtn) {
            toggleBtn.addEventListener("click", () => this.toggleBot());
        }

        // Trading Mode toggle (Paper / Real)
        const modeToggle = document.getElementById("modeToggleSelect");
        if (modeToggle) {
            modeToggle.addEventListener("change", (e) => this.changeTradingMode(e.target.value));
        }

        // Paper Balance Reset
        const resetBalBtn = document.getElementById("resetPaperBalBtn");
        if (resetBalBtn) {
            resetBalBtn.addEventListener("click", () => this.resetPaperBalance());
        }

        // Binance Setup Form
        const binanceForm = document.getElementById("binanceAuthForm");
        if (binanceForm) {
            binanceForm.addEventListener("submit", (e) => {
                e.preventDefault();
                this.saveBinanceKeys();
            });
        }

        // Groq Setup Form
        const groqForm = document.getElementById("groqAuthForm");
        if (groqForm) {
            groqForm.addEventListener("submit", (e) => {
                e.preventDefault();
                this.saveGroqKey();
            });
        }

        // Manual Trade Form
        const tradeForm = document.getElementById("manualTradeForm");
        if (tradeForm) {
            tradeForm.addEventListener("submit", (e) => {
                e.preventDefault();
                this.executeManualOrder();
            });
        }

        // Symbol selector for chart
        const symbolSelect = document.getElementById("chartSymbolSelect");
        if (symbolSelect) {
            symbolSelect.addEventListener("change", (e) => {
                this.currentSymbol = e.target.value;
                if (window.tradingChart) {
                    window.tradingChart.loadCandles(this.currentSymbol, window.tradingChart.currentTimeframe);
                }
                this.fetchSymbolTicker(this.currentSymbol);
            });
        }

        // Timeframe buttons
        document.querySelectorAll(".tf-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".tf-btn").forEach(b => b.classList.remove("bg-indigo-600", "text-white"));
                btn.classList.add("bg-indigo-600", "text-white");
                const tf = btn.getAttribute("data-tf");
                if (window.tradingChart) {
                    window.tradingChart.loadCandles(this.currentSymbol, tf);
                }
            });
        });

        // Trade table filter
        const tradeFilter = document.getElementById("tradeModeFilter");
        if (tradeFilter) {
            tradeFilter.addEventListener("change", (e) => this.fetchTrades(e.target.value));
        }
    }

    setupWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        try {
            this.socket = new WebSocket(wsUrl);

            this.socket.onopen = () => {
                console.log("[WS] Connected to live trade stream");
                this.updateWsStatus(true);
            };

            this.socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWsMessage(data);
                } catch (e) {
                    console.error("WS Parse error:", e);
                }
            };

            this.socket.onclose = () => {
                console.warn("[WS] Disconnected, attempting reconnect in 3s...");
                this.updateWsStatus(false);
                setTimeout(() => this.setupWebSocket(), 3000);
            };

            this.socket.onerror = (err) => {
                console.error("[WS] Error:", err);
                this.updateWsStatus(false);
            };
        } catch (e) {
            console.error("Failed to connect WS:", e);
        }
    }

    updateWsStatus(connected) {
        const badge = document.getElementById("wsStatusBadge");
        if (badge) {
            if (connected) {
                badge.className = "flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-xs bg-emerald-950/60 border border-emerald-500/30 text-emerald-400";
                badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span><span>LIVE STREAM</span>`;
            } else {
                badge.className = "flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-xs bg-red-950/60 border border-red-500/30 text-red-400";
                badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-red-400"></span><span>RECONNECTING</span>`;
            }
        }
    }

    handleWsMessage(msg) {
        if (!msg) return;

        if (msg.type === "LOG_EVENT") {
            this.appendLog(msg.data);
        } else if (msg.type === "SIGNAL_UPDATE") {
            this.updateSignalCard(msg.data);
        } else if (msg.type === "TRADE_OPENED" || msg.type === "TRADE_CLOSED") {
            this.fetchTrades();
            this.fetchBalances();
            this.fetchAnalytics();
            this.showToast(
                msg.type === "TRADE_OPENED" ? "🚀 Trade Opened" : "🏁 Trade Closed",
                `${msg.data.side} ${msg.data.symbol} @ $${(msg.data.exit_price || msg.data.entry_price).toFixed(4)}`
            );
        } else if (msg.type === "TICK") {
            this.updateTickCounter(msg.data);
        }
    }

    async fetchStatus() {
        try {
            const res = await fetch("/api/status");
            const data = await res.json();
            if (data.success) {
                this.isRunning = data.bot.is_running;
                this.tradingMode = data.bot.trading_mode;
                this.updateUIStatus(data);
            }
        } catch (e) {
            console.error("Failed to fetch status:", e);
        }
    }

    updateUIStatus(data) {
        const toggleBtn = document.getElementById("botToggleBtn");
        const statusText = document.getElementById("botStatusText");
        const modeSelect = document.getElementById("modeToggleSelect");
        const scanCountText = document.getElementById("scanCountText");

        if (modeSelect) modeSelect.value = data.bot.trading_mode;

        if (toggleBtn && statusText) {
            if (data.bot.is_running) {
                toggleBtn.innerHTML = `<i data-lucide="pause" class="w-4 h-4"></i><span>Pause 24/7 Bot</span>`;
                toggleBtn.className = "px-4 py-2 rounded-xl bg-amber-600/90 hover:bg-amber-500 text-white font-medium flex items-center space-x-2 text-sm shadow-lg shadow-amber-600/20 transition cursor-pointer";
                statusText.innerHTML = `<span class="inline-block w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse mr-2"></span>24/7 Autonomous Bot: <span class="text-emerald-400 font-bold ml-1">RUNNING</span>`;
            } else {
                toggleBtn.innerHTML = `<i data-lucide="play" class="w-4 h-4"></i><span>Start 24/7 Bot</span>`;
                toggleBtn.className = "px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-medium flex items-center space-x-2 text-sm shadow-lg shadow-emerald-600/20 transition cursor-pointer";
                statusText.innerHTML = `<span class="inline-block w-2.5 h-2.5 rounded-full bg-red-400 mr-2"></span>24/7 Autonomous Bot: <span class="text-red-400 font-bold ml-1">STOPPED</span>`;
            }
        }

        if (scanCountText) {
            scanCountText.innerText = `${data.bot.scan_count} cycles`;
        }

        // Render recent logs
        if (data.bot.recent_logs) {
            const logsContainer = document.getElementById("terminalLogs");
            if (logsContainer) {
                logsContainer.innerHTML = "";
                data.bot.recent_logs.slice(0, 30).forEach(log => this.appendLog(log, false));
            }
        }

        if (window.lucide) lucide.createIcons();
    }

    async toggleBot() {
        const endpoint = this.isRunning ? "/api/bot/stop" : "/api/bot/start";
        try {
            await fetch(endpoint, { method: "POST" });
            this.fetchStatus();
        } catch (e) {
            console.error("Failed to toggle bot:", e);
        }
    }

    async changeTradingMode(mode) {
        try {
            await fetch("/api/bot/mode", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode })
            });
            this.tradingMode = mode;
            this.fetchStatus();
            this.fetchBalances();
            this.fetchTrades();
            this.fetchAnalytics();
        } catch (e) {
            console.error("Failed to change mode:", e);
        }
    }

    async resetPaperBalance() {
        if (!confirm("Are you sure you want to reset Paper USDT balance to $10,000?")) return;
        try {
            await fetch("/api/paper/reset", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ initial_usdt: 10000.0 })
            });
            this.fetchBalances();
            this.fetchAnalytics();
            this.showToast("Balance Reset", "Paper balance reset to $10,000 USDT");
        } catch (e) {
            console.error("Failed to reset balance:", e);
        }
    }

    async fetchBalances() {
        try {
            const res = await fetch("/api/balances");
            const data = await res.json();
            
            const paperUsdt = (data.paper || []).find(b => b.asset === "USDT") || { free: 10000, total: 10000 };
            
            const paperEl = document.getElementById("statPaperBalance");
            if (paperEl) paperEl.innerText = `$${paperUsdt.free.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

            // Real balance display
            const realContainer = document.getElementById("statRealBalance");
            if (realContainer) {
                if (data.real && data.real.success) {
                    const realUsdt = (data.real.balances || []).find(b => b.asset === "USDT");
                    const free = realUsdt ? realUsdt.free : 0;
                    realContainer.innerText = `$${free.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
                } else {
                    realContainer.innerText = "Connect API";
                }
            }
        } catch (e) {
            console.error("Failed to fetch balances:", e);
        }
    }

    async fetchTrades(mode = null) {
        try {
            const url = mode ? `/api/trades?mode=${mode}&limit=40` : `/api/trades?limit=40`;
            const res = await fetch(url);
            const data = await res.json();
            
            this.renderOpenPositions(data.trades.filter(t => t.status === "OPEN"));
            this.renderTradeHistory(data.trades.filter(t => t.status === "CLOSED"));
        } catch (e) {
            console.error("Failed to fetch trades:", e);
        }
    }

    renderOpenPositions(openTrades) {
        const container = document.getElementById("openPositionsTable");
        const countBadge = document.getElementById("openPositionsCount");
        const livePnlBadge = document.getElementById("openPositionsLivePnlBadge");
        const headerLivePnl = document.getElementById("statLiveUnrealizedPnl");

        if (countBadge) countBadge.innerText = openTrades.length;

        if (!container) return;

        if (openTrades.length === 0) {
            container.innerHTML = `<tr><td colspan="9" class="text-center py-6 text-gray-500 text-xs">No active positions open right now. Engine is scanning.</td></tr>`;
            if (livePnlBadge) livePnlBadge.innerText = "Live PnL: $0.00";
            if (headerLivePnl) {
                headerLivePnl.innerText = "$0.00 (0.00%)";
                headerLivePnl.className = "font-mono font-bold text-gray-300 text-sm";
            }
            return;
        }

        let totalLivePnl = 0;
        let totalEntryAmount = 0;

        container.innerHTML = openTrades.map(t => {
            const currentPrice = t.current_price || t.entry_price;
            let livePnl = t.live_pnl;
            let livePnlPct = t.live_pnl_pct;

            if (livePnl === undefined || livePnl === null) {
                if (t.side === "BUY") {
                    livePnl = (currentPrice - t.entry_price) * t.quantity - (t.fees || 0);
                    livePnlPct = ((currentPrice - t.entry_price) / t.entry_price) * 100.0;
                } else {
                    livePnl = (t.entry_price - currentPrice) * t.quantity - (t.fees || 0);
                    livePnlPct = ((t.entry_price - currentPrice) / t.entry_price) * 100.0;
                }
            }

            totalLivePnl += livePnl;
            totalEntryAmount += t.amount_usdt;

            const isPositive = livePnl >= 0;
            const pnlColor = isPositive ? "text-emerald-400" : "text-red-400";
            const pnlBg = isPositive ? "bg-emerald-500/10 border-emerald-500/20" : "bg-red-500/10 border-red-500/20";

            return `
                <tr class="border-b border-white/5 hover:bg-white/[0.02] transition font-mono text-xs">
                    <td class="py-3 px-3">
                        <div class="flex items-center space-x-2">
                            <span class="font-bold text-white">${t.symbol}</span>
                            <span class="text-[10px] px-1.5 py-0.5 rounded ${t.mode === 'REAL' ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' : 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'}">${t.mode}</span>
                        </div>
                    </td>
                    <td class="py-3 px-3">
                        <span class="px-2 py-0.5 rounded text-[11px] font-bold ${t.side === 'BUY' ? 'badge-buy' : 'badge-sell'}">${t.side}</span>
                    </td>
                    <td class="py-3 px-3 text-gray-300">$${t.entry_price.toFixed(4)}</td>
                    <td class="py-3 px-3 text-indigo-300 font-bold">$${currentPrice.toFixed(4)}</td>
                    <td class="py-3 px-3 text-gray-300">$${t.amount_usdt.toFixed(2)}</td>
                    <td class="py-3 px-3 text-red-400">$${t.stop_loss ? t.stop_loss.toFixed(4) : '-'}</td>
                    <td class="py-3 px-3 text-emerald-400">$${t.take_profit ? t.take_profit.toFixed(4) : '-'}</td>
                    <td class="py-3 px-3">
                        <span class="px-2 py-1 rounded-md border font-bold ${pnlBg} ${pnlColor} inline-flex items-center space-x-1">
                            <span>${isPositive ? '+' : ''}$${livePnl.toFixed(2)}</span>
                            <span class="text-[10px] opacity-80">(${isPositive ? '+' : ''}${livePnlPct.toFixed(2)}%)</span>
                        </span>
                    </td>
                    <td class="py-3 px-3 text-right">
                        <button onclick="window.dashboardApp.closePosition(${t.id})" class="px-2.5 py-1 rounded bg-red-600/80 hover:bg-red-500 text-white font-sans text-[11px] transition cursor-pointer">
                            Close
                        </button>
                    </td>
                </tr>
            `;
        }).join("");

        // Update live PnL badges
        const isTotalPos = totalLivePnl >= 0;
        const totalPct = totalEntryAmount > 0 ? (totalLivePnl / totalEntryAmount) * 100.0 : 0.0;
        const totalPnlStr = `${isTotalPos ? '+' : ''}$${totalLivePnl.toFixed(2)} (${isTotalPos ? '+' : ''}${totalPct.toFixed(2)}%)`;

        if (livePnlBadge) {
            livePnlBadge.innerText = `Live PnL: ${totalPnlStr}`;
            livePnlBadge.className = `text-xs px-2.5 py-0.5 rounded-full font-mono font-bold border ml-2 ${isTotalPos ? 'bg-emerald-950/60 border-emerald-500/30 text-emerald-400' : 'bg-red-950/60 border-red-500/30 text-red-400'}`;
        }

        if (headerLivePnl) {
            headerLivePnl.innerText = totalPnlStr;
            headerLivePnl.className = `font-mono font-bold text-sm ${isTotalPos ? 'text-emerald-400' : 'text-red-400'}`;
        }
    }

    renderTradeHistory(closedTrades) {
        const container = document.getElementById("tradeHistoryTable");
        if (!container) return;

        if (closedTrades.length === 0) {
            container.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-gray-500 text-xs">No closed trades recorded yet.</td></tr>`;
            return;
        }

        container.innerHTML = closedTrades.map(t => {
            const isWin = t.pnl > 0;
            return `
                <tr class="border-b border-white/5 hover:bg-white/[0.02] transition font-mono text-xs">
                    <td class="py-2.5 px-3 text-gray-400 text-[11px]">${t.exit_time ? new Date(t.exit_time).toLocaleTimeString() : '-'}</td>
                    <td class="py-2.5 px-3 font-semibold text-white">${t.symbol}</td>
                    <td class="py-2.5 px-3"><span class="px-1.5 py-0.5 rounded text-[10px] ${t.side === 'BUY' ? 'badge-buy' : 'badge-sell'}">${t.side}</span></td>
                    <td class="py-2.5 px-3 text-gray-300">$${t.entry_price.toFixed(4)}</td>
                    <td class="py-2.5 px-3 text-gray-300">$${t.exit_price ? t.exit_price.toFixed(4) : '-'}</td>
                    <td class="py-2.5 px-3 font-bold ${isWin ? 'text-emerald-400' : 'text-red-400'}">
                        ${isWin ? '+' : ''}$${t.pnl.toFixed(2)} (${isWin ? '+' : ''}${t.pnl_pct.toFixed(2)}%)
                    </td>
                    <td class="py-2.5 px-3 text-gray-400 text-[11px] max-w-[150px] truncate" title="${t.exit_reason || ''}">${t.exit_reason || 'Manual'}</td>
                    <td class="py-2.5 px-3 text-right">
                        <button onclick="window.dashboardApp.askAiAboutTrade(${t.id})" class="text-indigo-400 hover:text-indigo-300 text-[11px] font-sans">
                            AI Audit →
                        </button>
                    </td>
                </tr>
            `;
        }).join("");
    }

    async closePosition(id) {
        if (!confirm(`Close position #${id} at market price?`)) return;
        try {
            const res = await fetch(`/api/trade/close/${id}`, { method: "POST" });
            const data = await res.json();
            if (data.success) {
                this.fetchTrades();
                this.fetchBalances();
                this.fetchAnalytics();
                this.showToast("Position Closed", `Trade #${id} closed successfully`);
            } else {
                alert("Failed to close position: " + (data.error || "Unknown error"));
            }
        } catch (e) {
            console.error("Close error:", e);
        }
    }

    askAiAboutTrade(id) {
        this.switchTab("ai_coach");
        if (window.aiChat && window.aiChat.chatInput) {
            window.aiChat.chatInput.value = `Audit and analyze trade #${id}. What factors contributed to the trade outcome and what rules should we refine?`;
            window.aiChat.sendMessage();
        }
    }

    async executeManualOrder() {
        const symbol = document.getElementById("manualSymbol").value;
        const side = document.getElementById("manualSide").value;
        const amount = parseFloat(document.getElementById("manualAmount").value);
        const sl = parseFloat(document.getElementById("manualSL").value) || null;
        const tp = parseFloat(document.getElementById("manualTP").value) || null;
        const trailing = parseFloat(document.getElementById("manualTrailing").value) || null;

        if (!symbol || !amount || amount <= 0) {
            alert("Please enter a valid symbol and amount");
            return;
        }

        try {
            const res = await fetch("/api/trade/open", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    symbol,
                    side,
                    amount_usdt: amount,
                    mode: this.tradingMode,
                    stop_loss_pct: sl,
                    take_profit_pct: tp,
                    trailing_stop_pct: trailing,
                    reason: "Manual Dashboard Order"
                })
            });
            const data = await res.json();
            if (data.success) {
                this.fetchTrades();
                this.fetchBalances();
                this.showToast("Order Executed", `${side} ${symbol} for $${amount}`);
            } else {
                alert("Order Failed: " + (data.error || "Error"));
            }
        } catch (e) {
            console.error("Manual order error:", e);
        }
    }

    async fetchAnalytics() {
        try {
            const res = await fetch(`/api/analytics?mode=${this.tradingMode}`);
            const data = await res.json();
            this.renderAnalyticsOverview(data);
            this.renderEquityChart(data.equity_curve || []);
        } catch (e) {
            console.error("Failed to fetch analytics:", e);
        }
    }

    renderAnalyticsOverview(d) {
        const elWinRate = document.getElementById("metricWinRate");
        const elTotalPnl = document.getElementById("metricTotalPnl");
        const elProfitFactor = document.getElementById("metricProfitFactor");
        const elSharpe = document.getElementById("metricSharpe");
        const elMaxDD = document.getElementById("metricMaxDD");
        const elAvgWin = document.getElementById("metricAvgWin");
        const elAvgLoss = document.getElementById("metricAvgLoss");
        const elTotalTrades = document.getElementById("metricTotalTrades");

        if (elWinRate) elWinRate.innerText = `${d.win_rate_pct.toFixed(1)}%`;
        if (elTotalPnl) {
            const isPos = d.total_realized_pnl >= 0;
            elTotalPnl.innerText = `${isPos ? '+' : ''}$${d.total_realized_pnl.toFixed(2)}`;
            elTotalPnl.className = `text-2xl font-bold font-mono ${isPos ? 'text-emerald-400' : 'text-red-400'}`;
        }
        if (elProfitFactor) elProfitFactor.innerText = d.profit_factor.toFixed(2);
        if (elSharpe) elSharpe.innerText = d.sharpe_ratio.toFixed(2);
        if (elMaxDD) elMaxDD.innerText = `${d.max_drawdown_pct.toFixed(2)}% ($${d.max_drawdown_dollar.toFixed(2)})`;
        if (elAvgWin) elAvgWin.innerText = `+$${d.avg_win_dollar.toFixed(2)}`;
        if (elAvgLoss) elAvgLoss.innerText = `-$${d.avg_loss_dollar.toFixed(2)}`;
        if (elTotalTrades) elTotalTrades.innerText = `${d.closed_trades_count} (${d.win_count}W / ${d.loss_count}L)`;
    }

    renderEquityChart(curve) {
        const canvas = document.getElementById("equityChartCanvas");
        if (!canvas || typeof Chart === 'undefined') return;

        const labels = curve.map(c => new Date(c.time).toLocaleTimeString());
        const data = curve.map(c => c.equity);

        if (this.equityChart) {
            this.equityChart.destroy();
        }

        const ctx = canvas.getContext('2d');
        const gradient = ctx.createLinearGradient(0, 0, 0, 300);
        gradient.addColorStop(0, 'rgba(99, 102, 241, 0.4)');
        gradient.addColorStop(1, 'rgba(99, 102, 241, 0.0)');

        this.equityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Portfolio Equity (USDT)',
                    data: data,
                    borderColor: '#818cf8',
                    borderWidth: 2,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.3,
                    pointRadius: curve.length > 30 ? 0 : 3,
                    pointBackgroundColor: '#6366f1'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        backgroundColor: 'rgba(15, 23, 42, 0.9)',
                        titleColor: '#818cf8',
                        bodyFont: { family: 'JetBrains Mono' }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.04)' },
                        ticks: { color: '#6b7280', font: { size: 10 } }
                    },
                    y: {
                        grid: { color: 'rgba(255, 255, 255, 0.04)' },
                        ticks: { color: '#6b7280', font: { family: 'JetBrains Mono', size: 10 } }
                    }
                }
            }
        });
    }

    async saveBinanceKeys() {
        const key = document.getElementById("binanceApiKey").value.trim();
        const secret = document.getElementById("binanceSecretKey").value.trim();
        const testnet = document.getElementById("binanceTestnetCheck").checked;

        if (!key || !secret) {
            alert("Please provide both Binance API Key and Secret");
            return;
        }

        const res = await fetch("/api/auth/binance", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: key, secret_key: secret, testnet: testnet })
        });
        const data = await res.json();
        if (data.connected) {
            this.showToast("Binance Connected", `Success! Free USDT: $${data.free_usdt || 0}`);
            this.fetchBalances();
        } else {
            alert("Binance API verification failed: " + (data.error || "Unknown error"));
        }
    }

    async saveGroqKey() {
        const key = document.getElementById("groqApiKey").value.trim();
        const model = document.getElementById("groqModelSelect").value;

        if (!key) {
            alert("Please enter a Groq API Key");
            return;
        }

        const res = await fetch("/api/auth/groq", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: key, model: model })
        });
        const data = await res.json();
        if (data.success) {
            this.showToast("Groq AI Active", data.message || `Connected with model: ${data.model || model}`);
            if (data.available_models && data.available_models.length > 0) {
                this.updateGroqModelDropdown(data.available_models, data.model);
            }
            if (window.aiChat) window.aiChat.loadHistory();
        } else {
            if (data.available_models && data.available_models.length > 0) {
                this.updateGroqModelDropdown(data.available_models);
            }
            alert(`Groq Connection Failed:\n\n${data.error || "Please check that your Groq API key (starts with 'gsk_...') is valid and has active quota."}`);
        }
    }

    async fetchGroqModels(key = "") {
        try {
            const url = key ? `/api/groq/models?api_key=${encodeURIComponent(key)}` : `/api/groq/models`;
            const res = await fetch(url);
            const data = await res.json();
            if (data.models && data.models.length > 0) {
                this.updateGroqModelDropdown(data.models, data.active_model);
            }
        } catch (e) {
            console.error("Failed to fetch Groq models:", e);
        }
    }

    updateGroqModelDropdown(models, activeModel = null) {
        const select = document.getElementById("groqModelSelect");
        if (!select) return;

        const currentVal = activeModel || select.value;
        select.innerHTML = models.map(m => `
            <option value="${m}" ${m === currentVal ? 'selected' : ''}>${m}</option>
        `).join("");
    }

    async fetchStrategies() {
        try {
            const res = await fetch("/api/strategies");
            const strategies = await res.json();
            const container = document.getElementById("strategiesList");
            if (!container) return;

            container.innerHTML = strategies.map(s => `
                <div class="p-5 rounded-2xl ${s.is_active ? 'bg-indigo-950/40 border-2 border-indigo-500/50 glow-primary' : 'bg-slate-900/50 border border-white/5'} transition">
                    <div class="flex items-center justify-between mb-2">
                        <div>
                            <h3 class="font-bold text-white text-base">${s.display_name}</h3>
                            <p class="text-xs text-gray-400">${s.description}</p>
                        </div>
                        <div>
                            ${s.is_active ? 
                                `<span class="px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 text-xs font-bold">ACTIVE STRATEGY</span>` :
                                `<button onclick="window.dashboardApp.activateStrategy(${s.id})" class="px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium transition cursor-pointer">Activate</button>`
                            }
                        </div>
                    </div>
                    <div class="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs font-mono">
                        <div class="p-2.5 rounded-lg bg-black/40 border border-white/5">
                            <span class="text-gray-400 block text-[10px]">Timeframe</span>
                            <span class="font-bold text-indigo-300">${s.timeframe}</span>
                        </div>
                        <div class="p-2.5 rounded-lg bg-black/40 border border-white/5">
                            <span class="text-gray-400 block text-[10px]">Stop Loss / TP</span>
                            <span class="font-bold text-amber-300">${s.risk_settings.stop_loss_pct || 1.5}% / ${s.risk_settings.take_profit_pct || 3.0}%</span>
                        </div>
                        <div class="p-2.5 rounded-lg bg-black/40 border border-white/5">
                            <span class="text-gray-400 block text-[10px]">Version</span>
                            <span class="font-bold text-white">v${s.version}</span>
                        </div>
                        <div class="p-2.5 rounded-lg bg-black/40 border border-white/5">
                            <span class="text-gray-400 block text-[10px]">Total Trades / Win Rate</span>
                            <span class="font-bold text-emerald-300">${s.total_trades} (${s.win_rate}%)</span>
                        </div>
                    </div>
                    <div class="mt-3 p-3 rounded-lg bg-black/30 border border-white/5 text-xs text-gray-300 font-mono">
                        <span class="text-indigo-400 font-bold block mb-1">Active Parameters:</span>
                        ${JSON.stringify(s.parameters, null, 2)}
                    </div>
                </div>
            `).join("");
        } catch (e) {
            console.error("Failed to load strategies:", e);
        }
    }

    async activateStrategy(id) {
        try {
            await fetch(`/api/strategies/activate/${id}`, { method: "POST" });
            this.fetchStrategies();
            this.showToast("Strategy Switched", "Active bot strategy updated");
        } catch (e) {
            console.error("Activate strategy error:", e);
        }
    }

    appendLog(log, prepend = true) {
        const container = document.getElementById("terminalLogs");
        if (!container) return;

        const div = document.createElement("div");
        div.className = "text-xs font-mono py-1 flex items-start space-x-2 border-b border-white/[0.02]";
        
        let colorClass = "text-gray-300";
        if (log.level === "ERROR") colorClass = "text-red-400 font-bold";
        else if (log.level === "WARNING") colorClass = "text-amber-300";
        else if (log.level === "INFO" && log.message.includes("BUY")) colorClass = "text-emerald-400 font-bold";
        else if (log.level === "INFO" && log.message.includes("SELL")) colorClass = "text-amber-400 font-bold";

        div.innerHTML = `
            <span class="text-gray-500 shrink-0">[${new Date(log.timestamp).toLocaleTimeString()}]</span>
            <span class="${colorClass}">${log.message}</span>
        `;

        if (prepend) {
            container.insertBefore(div, container.firstChild);
            if (container.children.length > 50) {
                container.removeChild(container.lastChild);
            }
        } else {
            container.appendChild(div);
        }
    }

    updateSignalCard(signal) {
        const container = document.getElementById(`signal-${signal.symbol}`);
        if (!container) return;

        const badgeClass = signal.action === "BUY" ? "badge-buy" : (signal.action === "SELL" ? "badge-sell" : "badge-hold");
        container.innerHTML = `
            <div class="flex items-center justify-between">
                <span class="font-bold text-white">${signal.symbol}</span>
                <span class="px-2 py-0.5 rounded text-xs font-bold ${badgeClass}">${signal.action} (${(signal.confidence * 100).toFixed(0)}%)</span>
            </div>
            <div class="text-[11px] text-gray-400 mt-1 truncate">${signal.reason}</div>
            <div class="mt-2 flex justify-between text-[10px] font-mono text-gray-400">
                <span>RSI: ${signal.indicators?.rsi || '-'}</span>
                <span>MACD: ${signal.indicators?.macd_hist ? signal.indicators.macd_hist.toFixed(2) : '-'}</span>
                <span>$${signal.indicators?.price ? signal.indicators.price.toFixed(2) : '-'}</span>
            </div>
        `;
    }

    updateTickCounter(tick) {
        const scanEl = document.getElementById("scanCountText");
        if (scanEl) scanEl.innerText = `${tick.scan_count} scans`;
    }

    async fetchSymbolTicker(symbol) {
        try {
            const res = await fetch(`/api/market/ticker?symbol=${symbol}`);
            const data = await res.json();
            const priceEl = document.getElementById("chartCurrentPrice");
            const changeEl = document.getElementById("chartPriceChange");
            if (priceEl && data.price) {
                priceEl.innerText = `$${data.price.toFixed(data.price < 1 ? 4 : 2)}`;
            }
            if (changeEl && data.priceChangePercent !== undefined) {
                const isPos = data.priceChangePercent >= 0;
                changeEl.innerText = `${isPos ? '+' : ''}${data.priceChangePercent.toFixed(2)}%`;
                changeEl.className = `font-mono text-sm font-bold ${isPos ? 'text-emerald-400' : 'text-red-400'}`;
            }
        } catch (e) {
            console.error("Ticker fetch error:", e);
        }
    }

    showToast(title, message) {
        const container = document.getElementById("toastContainer");
        if (!container) return;

        const toast = document.createElement("div");
        toast.className = "glass-card p-3 rounded-xl shadow-2xl border border-indigo-500/30 flex items-start space-x-3 text-xs text-white max-w-sm transition transform translate-y-2 opacity-0";
        toast.innerHTML = `
            <div class="p-1.5 rounded-lg bg-indigo-600/30 text-indigo-400">
                <i data-lucide="bell" class="w-4 h-4"></i>
            </div>
            <div>
                <div class="font-bold text-indigo-200">${title}</div>
                <div class="text-gray-300 mt-0.5">${message}</div>
            </div>
        `;
        container.appendChild(toast);
        if (window.lucide) lucide.createIcons();

        setTimeout(() => {
            toast.classList.remove("translate-y-2", "opacity-0");
        }, 50);

        setTimeout(() => {
            toast.classList.add("opacity-0", "translate-y-2");
            setTimeout(() => toast.remove(), 300);
        }, 4500);
    }

    startPeriodicUpdates() {
        setInterval(() => {
            if (this.currentSymbol) {
                this.fetchSymbolTicker(this.currentSymbol);
            }
        }, 3000);

        setInterval(() => {
            this.fetchBalances();
            this.fetchTrades();
        }, 5000);
    }
}
