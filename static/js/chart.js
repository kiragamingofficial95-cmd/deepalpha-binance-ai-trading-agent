class TradingChartManager {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.chart = null;
        this.candleSeries = null;
        this.volumeSeries = null;
        this.currentSymbol = "BTCUSDT";
        this.currentTimeframe = "15m";
        this.init();
    }

    init() {
        if (!this.container || typeof LightweightCharts === 'undefined') {
            console.warn("Chart container or LightweightCharts library not loaded");
            return;
        }

        this.container.innerHTML = "";

        this.chart = LightweightCharts.createChart(this.container, {
            width: this.container.clientWidth || 800,
            height: this.container.clientHeight || 420,
            layout: {
                background: { type: 'solid', color: 'transparent' },
                textColor: '#9ca3af',
                fontSize: 12,
                fontFamily: 'JetBrains Mono, monospace'
            },
            grid: {
                vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
                horzLines: { color: 'rgba(255, 255, 255, 0.04)' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: 'rgba(99, 102, 241, 0.5)', width: 1, style: 1 },
                horzLine: { color: 'rgba(99, 102, 241, 0.5)', width: 1, style: 1 },
            },
            timeScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                timeVisible: true,
                secondsVisible: false,
            },
            rightPriceScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                scaleMargins: {
                    top: 0.1,
                    bottom: 0.2,
                },
            }
        });

        // Candlestick series
        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: '#10b981',
            downColor: '#ef4444',
            borderVisible: false,
            wickUpColor: '#10b981',
            wickDownColor: '#ef4444'
        });

        // Volume Series
        this.volumeSeries = this.chart.addHistogramSeries({
            color: 'rgba(99, 102, 241, 0.35)',
            priceFormat: { type: 'volume' },
            priceScaleId: '',
            scaleMargins: {
                top: 0.8,
                bottom: 0,
            },
        });

        // Responsive auto-resize
        window.addEventListener('resize', () => {
            if (this.chart && this.container) {
                this.chart.applyOptions({
                    width: this.container.clientWidth,
                    height: this.container.clientHeight || 420
                });
            }
        });

        this.loadCandles(this.currentSymbol, this.currentTimeframe);
    }

    async loadCandles(symbol, timeframe) {
        this.currentSymbol = symbol;
        this.currentTimeframe = timeframe;
        try {
            const res = await fetch(`/api/market/klines?symbol=${symbol}&timeframe=${timeframe}&limit=150`);
            const data = await res.json();
            if (data.candles && data.candles.length > 0) {
                const candleData = data.candles.map(c => ({
                    time: c.time,
                    open: c.open,
                    high: c.high,
                    low: c.low,
                    close: c.close
                }));
                const volData = data.candles.map(c => ({
                    time: c.time,
                    value: c.volume,
                    color: c.close >= c.open ? 'rgba(16, 185, 129, 0.35)' : 'rgba(239, 68, 68, 0.35)'
                }));

                this.candleSeries.setData(candleData);
                this.volumeSeries.setData(volData);
                this.chart.timeScale().fitContent();
            }
        } catch (e) {
            console.error("Failed to load candles:", e);
        }
    }

    updateLastPrice(price, timestamp) {
        // Can be called on websocket tick
        if (!this.candleSeries) return;
        const timeSec = Math.floor((timestamp || Date.now()) / 1000);
        // lightweight charts tick update
    }
}
