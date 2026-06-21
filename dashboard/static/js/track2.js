(function () {
    'use strict';

    var agentName =
        (document.body && document.body.getAttribute('data-agent-name')) || 'Genesis';

    var chatForm = document.getElementById('chat-form');
    var chatMessage = document.getElementById('chat-message');
    var btnChatSend = document.getElementById('btn-chat-send');
    var btnChatClear = document.getElementById('btn-chat-clear');
    var chatLog = document.getElementById('chat-log');
    var quickPrompts = document.getElementById('quick-prompts');
    var strategyResults = document.getElementById('strategy-results');

    var btnCopy = document.getElementById('btn-copy');
    var btnDownload = document.getElementById('btn-download');
    var fileHint = document.getElementById('file-hint');
    var jsonPre = document.getElementById('strategy-json');
    var summaryNl = document.getElementById('summary-nl');
    var summaryGrid = document.getElementById('summary-grid');
    var backtestMetrics = document.getElementById('backtest-metrics');

    var lastStrategy = null;
    var chatHistory = [];
    var welcomeShown = false;

    var WELCOME_MESSAGE =
        'Hello! I\u2019m ' +
        agentName +
        '. What would you like to do today?\n\n' +
        '\u2022 Explore the market \u2014 prices, trends, sentiment, and live CMC data\n' +
        '\u2022 Generate a strategy \u2014 describe your idea and I\u2019ll build a backtestable JSON spec\n\n' +
        'Pick a quick option below or type your own message.';

    function escapeHtml(s) {
        return s
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function appendChatBubble(role, text, meta) {
        if (!chatLog) return;
        var bubble = document.createElement('div');
        bubble.className = 'track2-chat-bubble track2-chat-' + role;
        var body = '<p>' + escapeHtml(text) + '</p>';
        if (meta) {
            body += '<span class="track2-chat-meta">' + escapeHtml(meta) + '</span>';
        }
        bubble.innerHTML = body;
        chatLog.appendChild(bubble);
        chatLog.scrollTop = chatLog.scrollHeight;
    }

    function showWelcome() {
        if (welcomeShown || !chatLog) return;
        welcomeShown = true;
        appendChatBubble('assistant', WELCOME_MESSAGE, agentName);
        chatHistory.push({ role: 'assistant', content: WELCOME_MESSAGE });
    }

    function renderSummary(strategy) {
        summaryNl.textContent = strategy.natural_language_summary || '';

        var scope = strategy.market_scope || {};
        var entry = (strategy.entry_rules || {}).conservative || {};
        var exit = strategy.exit_rules || {};
        var perf = strategy.expected_performance || {};
        var sizing = strategy.position_sizing || {};

        var cells = [
            ['Asset', scope.primary_asset || '\u2014'],
            ['Timeframe', scope.timeframe || '\u2014'],
            ['Buy conviction \u2265', entry.min_conviction != null ? entry.min_conviction : '\u2014'],
            ['Sell conviction \u2264', (exit.signal_sell || {}).sell_max_conviction || '\u2014'],
            ['Take profit', (exit.take_profit || {}).value_pct + '%'],
            ['Stop loss', (exit.stop_loss || {}).value_pct + '%'],
            ['Position size', sizing.spot_stable_pct + '% stables'],
            ['Target R:R', perf.target_risk_reward_ratio || '\u2014'],
            ['Est. win rate', (perf.estimated_win_rate_pct || '\u2014') + '%'],
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

    function strategyFilename(strategy) {
        var asset = ((strategy.market_scope || {}).primary_asset || 'strategy').toLowerCase();
        return 'genesis-strategy-' + asset + '.json';
    }

    function downloadStrategyLocal(strategy) {
        var blob = new Blob([JSON.stringify(strategy, null, 2)], { type: 'application/json' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = strategyFilename(strategy);
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    function renderStrategyOutput(data) {
        if (!data.strategy) return;

        lastStrategy = data.strategy;
        if (strategyResults) strategyResults.hidden = false;

        renderSummary(data.strategy);
        renderBacktest(data.backtest_preview);

        var text = JSON.stringify(data.strategy, null, 2);
        jsonPre.innerHTML = '<code>' + escapeHtml(text) + '</code>';

        if (btnCopy) btnCopy.disabled = false;

        var filename = data.strategy_file || strategyFilename(data.strategy);
        if (btnDownload) {
            if (data.download_url) {
                btnDownload.href = data.download_url;
                btnDownload.download = filename;
                btnDownload.hidden = false;
            } else {
                btnDownload.href = '#';
                btnDownload.hidden = false;
                btnDownload.onclick = function (e) {
                    e.preventDefault();
                    downloadStrategyLocal(data.strategy);
                };
            }
        }

        if (fileHint) {
            fileHint.hidden = false;
            fileHint.textContent = data.strategy_file
                ? 'Saved as ' + data.strategy_file + ' — download below or copy JSON.'
                : 'Download your backtestable strategy JSON below.';
        }

        if (strategyResults) {
            strategyResults.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    async function sendMessage(text) {
        text = (text || '').trim();
        if (!text) return;

        if (quickPrompts) quickPrompts.hidden = true;

        appendChatBubble('user', text);
        chatHistory.push({ role: 'user', content: text });
        if (chatMessage) chatMessage.value = '';

        btnChatSend.disabled = true;
        btnChatSend.textContent = 'Thinking\u2026';

        try {
            var res = await fetch('/api/strategy-skill/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    history: chatHistory.slice(0, -1),
                    include_backtest: true,
                }),
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            var data = await res.json();

            var meta =
                data.intent === 'generate_strategy'
                    ? 'Strategy ready'
                    : data.intent === 'answer'
                      ? 'Market insight'
                      : '';
            if (data.symbols_detected && data.symbols_detected.length) {
                meta += (meta ? ' \u00b7 ' : '') + data.symbols_detected.join(', ');
            }

            appendChatBubble('assistant', data.reply || 'No response.', meta || agentName);
            chatHistory.push({ role: 'assistant', content: data.reply || '' });

            if (data.strategy) {
                renderStrategyOutput(data);
            }
        } catch (err) {
            appendChatBubble(
                'assistant',
                'Something went wrong. Please try again. (' + String(err) + ')',
                'error'
            );
        } finally {
            btnChatSend.disabled = false;
            btnChatSend.textContent = 'Send';
        }
    }

    if (chatForm) {
        chatForm.addEventListener('submit', function (e) {
            e.preventDefault();
            sendMessage(chatMessage ? chatMessage.value : '');
        });
    }

    if (btnChatClear) {
        btnChatClear.addEventListener('click', function () {
            chatHistory = [];
            welcomeShown = false;
            if (chatLog) chatLog.innerHTML = '';
            if (quickPrompts) quickPrompts.hidden = false;
            if (strategyResults) strategyResults.hidden = true;
            lastStrategy = null;
            if (btnCopy) btnCopy.disabled = true;
            if (btnDownload) {
                btnDownload.hidden = true;
                btnDownload.onclick = null;
            }
            if (fileHint) fileHint.hidden = true;
            showWelcome();
        });
    }

    if (chatMessage) {
        chatMessage.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage(chatMessage.value);
            }
        });
    }

    if (quickPrompts) {
        quickPrompts.addEventListener('click', function (e) {
            var chip = e.target.closest('[data-prompt]');
            if (!chip) return;
            sendMessage(chip.getAttribute('data-prompt'));
        });
    }

    if (btnCopy) {
        btnCopy.addEventListener('click', function () {
            if (!lastStrategy) return;
            navigator.clipboard.writeText(JSON.stringify(lastStrategy, null, 2)).then(function () {
                btnCopy.textContent = 'Copied!';
                setTimeout(function () {
                    btnCopy.textContent = 'Copy JSON';
                }, 1500);
            });
        });
    }

    showWelcome();
})();