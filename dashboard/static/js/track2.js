(function () {
    'use strict';

    var form = document.getElementById('strategy-form');
    var btnGenerate = document.getElementById('btn-generate');
    var btnCopy = document.getElementById('btn-copy');
    var btnDownload = document.getElementById('btn-download');
    var jsonPre = document.getElementById('strategy-json');
    var summaryPlaceholder = document.getElementById('summary-placeholder');
    var summaryContent = document.getElementById('summary-content');
    var summaryNl = document.getElementById('summary-nl');
    var summaryGrid = document.getElementById('summary-grid');
    var backtestMetrics = document.getElementById('backtest-metrics');

    var lastStrategy = null;

    function formPayload() {
        var fd = new FormData(form);
        return {
            primary_asset: (fd.get('primary_asset') || 'BNB').toString().trim().toUpperCase(),
            timeframe: fd.get('timeframe') || '5m',
            risk_profile: fd.get('risk_profile') || 'conservative',
            market_regime: fd.get('market_regime') || 'bullish',
            take_profit_pct: parseFloat(fd.get('take_profit_pct')) || 12,
            stop_loss_pct: parseFloat(fd.get('stop_loss_pct')) || 6,
            backtest_limit: parseInt(fd.get('backtest_limit'), 10) || 50,
            idle_cycles: parseInt(fd.get('idle_cycles'), 10) || 0,
            focus_signals: ['technicals', 'sentiment', 'onchain', 'news'],
        };
    }

    function updateDownloadLink(payload) {
        var qs = new URLSearchParams({
            primary_asset: payload.primary_asset,
            risk_profile: payload.risk_profile,
            market_regime: payload.market_regime,
        });
        btnDownload.href = '/api/strategy-skill/download?' + qs.toString();
        btnDownload.download = 'genesis-strategy-' + payload.primary_asset.toLowerCase() + '.json';
    }

    function renderSummary(strategy) {
        summaryPlaceholder.hidden = true;
        summaryContent.hidden = false;
        summaryNl.textContent = strategy.natural_language_summary || '';

        var scope = strategy.market_scope || {};
        var entry = (strategy.entry_rules || {}).conservative || {};
        var exit = strategy.exit_rules || {};
        var perf = strategy.expected_performance || {};
        var sizing = strategy.position_sizing || {};

        var cells = [
            ['Asset', scope.primary_asset || '—'],
            ['Timeframe', scope.timeframe || '—'],
            ['Buy conviction ≥', entry.min_conviction != null ? entry.min_conviction : '—'],
            ['Sell conviction ≤', (exit.signal_sell || {}).sell_max_conviction || '—'],
            ['Take profit', (exit.take_profit || {}).value_pct + '%'],
            ['Stop loss', (exit.stop_loss || {}).value_pct + '%'],
            ['Position size', sizing.spot_stable_pct + '% stables'],
            ['Target R:R', perf.target_risk_reward_ratio || '—'],
            ['Est. win rate', (perf.estimated_win_rate_pct || '—') + '%'],
        ];

        summaryGrid.innerHTML = cells
            .map(function (row) {
                return (
                    '<div class="summary-cell"><span class="summary-cell-label">' +
                    row[0] +
                    '</span><span class="summary-cell-val">' +
                    row[1] +
                    '</span></div>'
                );
            })
            .join('');
    }

    function renderBacktest(bt) {
        if (!bt) {
            backtestMetrics.innerHTML =
                '<span class="track2-placeholder">No audit data available for backtest.</span>';
            return;
        }

        var metrics = [
            ['Audits', bt.audits_processed],
            ['Signals', bt.signals_evaluated],
            ['Buy', bt.buy_signals + ' (' + bt.buy_pct + '%)'],
            ['Sell', bt.sell_signals + ' (' + bt.sell_pct + '%)'],
            ['Hold', bt.hold_signals + ' (' + bt.hold_pct + '%)'],
            ['Round trips', bt.simulated_round_trips || 0],
            ['Est. win rate', (bt.estimated_win_rate_pct || 0) + '%'],
        ];

        backtestMetrics.innerHTML = metrics
            .map(function (row) {
                return (
                    '<div class="backtest-metric"><span class="backtest-metric-label">' +
                    row[0] +
                    '</span><span class="backtest-metric-val">' +
                    row[1] +
                    '</span></div>'
                );
            })
            .join('');
    }

    function renderJson(strategy) {
        var text = JSON.stringify(strategy, null, 2);
        jsonPre.innerHTML = '<code>' + escapeHtml(text) + '</code>';
        btnCopy.disabled = false;
        lastStrategy = strategy;
    }

    function escapeHtml(s) {
        return s
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    async function generate() {
        var payload = formPayload();
        updateDownloadLink(payload);
        btnGenerate.disabled = true;
        btnGenerate.textContent = 'Generating…';

        try {
            var res = await fetch('/api/strategy-skill/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            var data = await res.json();
            renderSummary(data.strategy);
            renderBacktest(data.backtest_preview);
            renderJson(data.strategy);
        } catch (err) {
            jsonPre.innerHTML =
                '<code class="track2-error">Failed to generate: ' + escapeHtml(String(err)) + '</code>';
        } finally {
            btnGenerate.disabled = false;
            btnGenerate.textContent = 'Generate Strategy';
        }
    }

    btnGenerate.addEventListener('click', generate);

    btnCopy.addEventListener('click', function () {
        if (!lastStrategy) return;
        navigator.clipboard.writeText(JSON.stringify(lastStrategy, null, 2)).then(function () {
            btnCopy.textContent = 'Copied!';
            setTimeout(function () {
                btnCopy.textContent = 'Copy JSON';
            }, 1500);
        });
    });

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        generate();
    });

    generate();
})();