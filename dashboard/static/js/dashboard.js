(function () {
    'use strict';

    let livePricesTimer = null;

    function parseDisplayedNumber(el) {
        if (!el) return 0;
        const n = parseFloat(String(el.textContent).replace(/[^0-9.-]/g, ''));
        return isNaN(n) ? 0 : n;
    }

    function resolveTotalUsd(summary, portfolio) {
        const fromSummary = summary && summary.total_value_usd;
        const fromPortfolio = portfolio && portfolio.total_value_usd;
        if (fromSummary != null && fromSummary > 0) return fromSummary;
        if (fromPortfolio != null && fromPortfolio > 0) return fromPortfolio;
        if (fromSummary != null) return fromSummary;
        if (fromPortfolio != null) return fromPortfolio;
        return null;
    }

    function animateValue(el, target, prefix, suffix, decimals, duration) {
        if (!el || isNaN(target)) return;
        const start = parseDisplayedNumber(el);
        if (Math.abs(start - target) < 0.005) {
            const sign = el.dataset.negative === 'true' ? '-' : '';
            el.textContent = prefix + sign + target.toFixed(decimals) + suffix;
            return;
        }
        const startTime = performance.now();
        const signed = el.dataset.signed === 'true';
        const negative = el.dataset.negative === 'true';

        function tick(now) {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            const current = start + (target - start) * eased;
            const formatted = current.toFixed(decimals);
            let sign = '';
            if (signed) {
                sign = negative ? '-' : '+';
            } else if (negative) {
                sign = '-';
            }
            el.textContent = prefix + sign + formatted + suffix;
            if (progress < 1) requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    function initCounters() {
        document.querySelectorAll('[data-animate-value]').forEach(function (el) {
            const raw = parseFloat(el.dataset.animateValue);
            if (isNaN(raw)) return;
            const prefix = el.dataset.prefix || '';
            const suffix = el.dataset.suffix || '';
            const decimals = parseInt(el.dataset.decimals || '2', 10);
            if (raw > 0 || parseDisplayedNumber(el) <= 0) {
                animateValue(el, raw, prefix, suffix, decimals, 900);
            }
        });
    }

    function initReveal() {
        document.querySelectorAll('.reveal').forEach(function (el, i) {
            el.classList.add('visible');
            el.style.animationDelay = (i % 5) * 0.05 + 's';
        });
    }

    function initParticles() {
        const container = document.getElementById('bg-particles');
        if (!container || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        container.innerHTML = '';
        const count = window.matchMedia('(max-width: 768px)').matches ? 14 : 28;
        for (let i = 0; i < count; i++) {
            const p = document.createElement('span');
            p.className = 'bg-particle';
            p.style.left = (Math.random() * 100) + '%';
            p.style.bottom = (-10 - Math.random() * 20) + '%';
            p.style.animationDuration = (8 + Math.random() * 14) + 's';
            p.style.animationDelay = (Math.random() * 12) + 's';
            const size = (2 + Math.random() * 2) + 'px';
            p.style.width = size;
            p.style.height = size;
            container.appendChild(p);
        }
    }

    function initAutoRefresh() {
        const interval = parseInt(document.body.dataset.refreshInterval || '30000', 10);
        if (interval <= 0) return;

        setInterval(function () {
            if (document.hidden) return;
            const btn = document.getElementById('refresh-btn');
            if (btn && !btn.classList.contains('htmx-request') && !document.body.classList.contains('agent-busy')) {
                btn.click();
            }
        }, interval);
    }

    function initHtmx() {
        document.body.addEventListener('htmx:afterSwap', function () {
            initCounters();
            initAgentControls();
            initLivePrices();
            initLogicPanel();
            initTokenDetailModal();
            initReveal();
            initParticles();
        });
    }

    function formatMetricCount(n) {
        const v = parseFloat(n);
        if (isNaN(v)) return String(n);
        if (v >= 1000000) return (v / 1000000).toFixed(1) + 'M';
        if (v >= 1000) return (v / 1000).toFixed(1) + 'K';
        return String(Math.round(v));
    }

    function tokenDetailJson(token) {
        return JSON.stringify(token).replace(/</g, '\\u003c').replace(/'/g, '&#39;');
    }

    function renderMetricTile(label, value, sub) {
        return (
            '<div class="token-metric-tile">' +
            '<span class="token-metric-label">' + escapeHtml(label) + '</span>' +
            '<span class="token-metric-value">' + escapeHtml(value) + '</span>' +
            (sub ? '<span class="token-metric-sub">' + escapeHtml(sub) + '</span>' : '') +
            '</div>'
        );
    }

    function renderTokenMetrics(metrics) {
        if (!metrics || !Object.keys(metrics).length) {
            return '<p class="integration-muted token-metrics-empty">Metrics populate as CMC signals arrive during a scan.</p>';
        }
        const tiles = [];
        if (metrics.rsi14 != null) {
            tiles.push(renderMetricTile('RSI (14)', Number(metrics.rsi14).toFixed(1), 'Technical momentum'));
        }
        if (metrics.holders != null) {
            const sub = metrics.holder_change_30d != null
                ? (Number(metrics.holder_change_30d) >= 0 ? '+' : '') +
                  Number(metrics.holder_change_30d).toFixed(1) + '% 30d'
                : 'On-chain wallets';
            tiles.push(renderMetricTile('Holders', formatMetricCount(metrics.holders), sub));
        }
        if (metrics.traders != null) {
            tiles.push(renderMetricTile('Traders', formatMetricCount(metrics.traders), 'Recent activity'));
        }
        if (metrics.percent_change_24h != null) {
            const ch = Number(metrics.percent_change_24h);
            tiles.push(renderMetricTile('24h change', (ch >= 0 ? '+' : '') + ch.toFixed(2) + '%', 'Price'));
        }
        if (metrics.macd != null) {
            tiles.push(renderMetricTile('MACD', String(metrics.macd), 'Technicals'));
        }
        if (metrics.funding_rate != null) {
            tiles.push(renderMetricTile('Funding', String(metrics.funding_rate), 'Derivatives'));
        }
        return '<div class="token-metric-grid">' + tiles.join('') + '</div>';
    }

    function openTokenDetail(token) {
        const modal = document.getElementById('token-detail-modal');
        if (!modal || !token) return;

        const title = document.getElementById('token-modal-title');
        const direction = document.getElementById('token-modal-direction');
        const metricsEl = document.getElementById('token-modal-metrics');
        const barsEl = document.getElementById('token-modal-bars');
        const logicEl = document.getElementById('token-modal-logic');
        const signalsEl = document.getElementById('token-modal-signals');

        if (title) title.textContent = token.symbol || '—';
        if (direction) {
            direction.textContent = (token.direction || 'neutral') + ' · ' +
                (token.conviction != null ? token.conviction.toFixed(2) : '—');
            direction.className = 'token-modal-direction ' + (token.direction || 'neutral');
        }
        if (metricsEl) metricsEl.innerHTML = renderTokenMetrics(token.metrics || {});

        if (barsEl) {
            let bars = '';
            Object.keys(token.components || {}).forEach(function (key) {
                const val = token.components[key];
                bars +=
                    '<div class="logic-bar-row"><span class="logic-bar-label">' + escapeHtml(key) +
                    '</span><div class="logic-bar-track"><div class="logic-bar-fill" style="width:' +
                    Math.round(val * 100) + '%"></div></div><span class="logic-bar-val">' +
                    val.toFixed(2) + '</span></div>';
            });
            barsEl.innerHTML = bars || '<p class="integration-muted">No component scores yet.</p>';
        }

        if (logicEl) {
            const lines = token.logic_lines || [];
            logicEl.innerHTML = lines.length
                ? lines.map(function (line) {
                    return '<li>' + escapeHtml(line) + '</li>';
                }).join('')
                : '<li class="integration-muted">Run a cycle to see reasoning for this token.</li>';
        }

        if (signalsEl) {
            let signals = '';
            (token.signals || []).forEach(function (s) {
                let highlights = '';
                if (s.highlights && Object.keys(s.highlights).length) {
                    highlights = '<div class="logic-highlights">';
                    Object.keys(s.highlights).forEach(function (hk) {
                        highlights += '<span class="logic-highlight-item"><em>' + escapeHtml(hk) +
                            '</em>: ' + escapeHtml(String(s.highlights[hk])) + '</span>';
                    });
                    highlights += '</div>';
                }
                signals +=
                    '<div class="logic-signal"><span class="logic-signal-cat">' + escapeHtml(s.category) +
                    '</span><span class="logic-signal-val">raw=' + s.value.toFixed(2) + ' → ' +
                    s.contribution.toFixed(2) + '</span>' +
                    (s.summary ? '<span class="logic-signal-text">' + escapeHtml(s.summary) + '</span>' : '') +
                    highlights + '</div>';
            });
            signalsEl.innerHTML = signals || '<p class="integration-muted">No raw signals cached for this token.</p>';
        }

        modal.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('token-modal-open');
    }

    function closeTokenDetail() {
        const modal = document.getElementById('token-detail-modal');
        if (!modal) return;
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('token-modal-open');
    }

    function initTokenDetailModal() {
        const modal = document.getElementById('token-detail-modal');
        if (!modal || modal.dataset.bound === '1') return;
        modal.dataset.bound = '1';

        const backdrop = modal.querySelector('.token-modal-backdrop');
        const closeBtn = modal.querySelector('.token-modal-close');
        if (backdrop) backdrop.onclick = closeTokenDetail;
        if (closeBtn) closeBtn.onclick = closeTokenDetail;

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && modal.classList.contains('open')) closeTokenDetail();
        });

        const feed = document.getElementById('logic-feed');
        if (feed) {
            feed.addEventListener('click', function (e) {
                const btn = e.target.closest('.token-insight-btn');
                if (!btn) return;
                e.preventDefault();
                e.stopPropagation();
                const raw = btn.getAttribute('data-token-detail');
                if (!raw) return;
                try {
                    openTokenDetail(JSON.parse(raw));
                } catch (err) { /* ignore malformed payload */ }
            });
        }
    }

    function setMessage(text, kind) {
        const el = document.getElementById('control-message');
        if (!el) return;
        el.textContent = text || '';
        el.className = 'control-message-compact' + (kind ? ' ' + kind : '');
    }

    function updateAgentUI(status) {
        const state = status.state || 'idle';
        const pill = document.getElementById('agent-status-pill');
        const label = document.getElementById('agent-status-label');
        const btnStart = document.getElementById('btn-start');
        const btnStop = document.getElementById('btn-stop');
        const btnCycle = document.getElementById('btn-cycle');

        if (pill) {
            pill.dataset.state = state;
        }
        if (label) {
            const labels = { idle: 'Idle', running: 'Running', cycling: 'Cycling…' };
            label.textContent = labels[state] || state;
        }

        const busy = state === 'cycling';
        document.body.classList.toggle('agent-busy', busy);

        const controlsOff = status.controls_enabled === false;
        if (btnStart) btnStart.disabled = controlsOff || state === 'running' || busy;
        if (btnStop) btnStop.disabled = controlsOff || state !== 'running';
        if (btnCycle) btnCycle.disabled = controlsOff || state === 'running' || busy;

        if (status.last_cycle && status.last_cycle.cycle_id) {
            const c = status.last_cycle;
            setMessage(
                'Last cycle ' + c.cycle_id + ': ' + (c.action || '—').toUpperCase() +
                (c.asset ? ' ' + c.asset : '') +
                (c.duration_ms ? ' (' + c.duration_ms + 'ms)' : ''),
                'ok'
            );
        } else if (status.last_error) {
            setMessage(status.last_error, 'err');
        }
    }

    async function fetchAgentStatus() {
        const res = await fetch('/api/status');
        if (!res.ok) return null;
        return res.json();
    }

    async function postAgentAction(path) {
        const preStatus = await fetchAgentStatus();
        if (preStatus && preStatus.controls_enabled === false) {
            setMessage('Agent controls disabled on this public endpoint.', 'err');
            return { ok: false };
        }
        setMessage('Working…', 'busy');
        let data = { ok: false };
        try {
            const res = await fetch(path, { method: 'POST' });
            data = await res.json().catch(function () { return {}; });
            if (!res.ok) {
                const detail = data.detail || data.message || ('HTTP ' + res.status);
                setMessage(typeof detail === 'string' ? detail : 'Action blocked', 'err');
                const freshStatus = await fetchAgentStatus();
                if (freshStatus) updateAgentUI(freshStatus);
                return { ok: false, message: detail };
            }
            if (data.ok) {
                setMessage(data.message || 'Done', 'ok');
                if (data.cycle) {
                    setMessage(
                        'Cycle ' + data.cycle.cycle_id + ': ' + (data.cycle.action || '').toUpperCase() +
                        (data.cycle.asset ? ' ' + data.cycle.asset : ''),
                        'ok'
                    );
                }
                const refresh = document.getElementById('refresh-btn');
                if (refresh) refresh.click();
                pollAgentLogic();
            } else {
                setMessage(data.message || 'Action failed', 'err');
            }
        } catch (err) {
            setMessage('Network error — is the dashboard running?', 'err');
        }
        const freshStatus = await fetchAgentStatus();
        if (freshStatus) updateAgentUI(freshStatus);
        return data;
    }

    function initAgentControls() {
        const btnStart = document.getElementById('btn-start');
        const btnStop = document.getElementById('btn-stop');
        const btnCycle = document.getElementById('btn-cycle');
        if (!btnStart) return;

        btnStart.onclick = function () { postAgentAction('/api/agent/start'); };
        btnStop.onclick = function () { postAgentAction('/api/agent/stop'); };
        btnCycle.onclick = function () { postAgentAction('/api/agent/cycle'); };

        fetchAgentStatus().then(function (status) {
            if (!status) return;
            updateAgentUI(status);
            const viewOnly = document.getElementById('control-viewonly');
            if (viewOnly) viewOnly.hidden = status.controls_enabled !== false;
            if (status.state === 'cycling') pollAgentLogic();
        });

        setInterval(function () {
            if (document.hidden) return;
            fetchAgentStatus().then(function (status) {
                if (status) {
                    updateAgentUI(status);
                    if (status.state === 'cycling' || status.state === 'running') {
                        pollAgentLogic();
                    }
                }
            });
        }, 5000);
    }

    function formatPrice(price, source) {
        if (price == null || isNaN(price)) return '—';
        let text;
        if (price < 0.01) text = '$' + price.toFixed(8);
        else text = '$' + price.toFixed(4);
        if (source === 'cmc') text += ' · CMC';
        return text;
    }

    function formatUsd(val, signed) {
        if (val == null || isNaN(val)) return '—';
        const n = Number(val);
        const prefix = signed ? (n >= 0 ? '+' : '') : '';
        return prefix + '$' + Math.abs(n).toFixed(2);
    }

    function formatPct(val) {
        if (val == null || isNaN(val)) return '—';
        const n = Number(val);
        return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
    }

    function pnlClass(val) {
        if (val == null || isNaN(val)) return '';
        return Number(val) >= 0 ? 'positive' : 'negative';
    }

    function formatAmount(amount) {
        const n = Number(amount);
        if (isNaN(n)) return '—';
        return n < 1 ? n.toFixed(6) : n.toFixed(4);
    }

    function renderHoldingsRow(h) {
        const pnlPctClass = pnlClass(h.pnl_pct);
        const pnlPct = h.pnl_pct != null ? formatPct(h.pnl_pct) : '—';
        const quoteTag = h.is_quote ? '<span class="token-tag">quote</span>' : '';
        return (
            '<tr data-symbol="' + h.symbol + '">' +
            '<td><div class="token-cell"><span class="token-avatar">' + h.symbol.slice(0, 2) +
            '</span><span><span class="token-name">' + h.symbol + '</span>' + quoteTag + '</span></div></td>' +
            '<td class="cell-amount">' + formatAmount(h.amount) + '</td>' +
            '<td class="cell-price mono">' + formatPrice(h.current_price, h.price_source) + '</td>' +
            '<td class="cell-value">' + formatUsd(h.value_usd, false) + '</td>' +
            '<td class="cell-pnl-pct ' + pnlPctClass + '">' + pnlPct + '</td>' +
            '</tr>'
        );
    }

    function updateHoldingsLive(data) {
        const holdings = data.holdings || [];
        const summary = data.summary || {};
        const tbody = document.getElementById('holdings-tbody');
        if (!tbody) return;

        const activeSymbols = new Set(holdings.map(function (h) { return h.symbol; }));
        tbody.querySelectorAll('tr[data-symbol]').forEach(function (row) {
            if (!activeSymbols.has(row.dataset.symbol)) {
                row.remove();
            }
        });

        holdings.forEach(function (h) {
            let row = tbody.querySelector('tr[data-symbol="' + h.symbol + '"]');
            if (!row) {
                tbody.insertAdjacentHTML('beforeend', renderHoldingsRow(h));
                row = tbody.querySelector('tr[data-symbol="' + h.symbol + '"]');
            }
            if (!row) return;

            const amtCell = row.querySelector('.cell-amount');
            if (amtCell) amtCell.textContent = formatAmount(h.amount);

            const priceCell = row.querySelector('.cell-price');
            const valueCell = row.querySelector('.cell-value');
            const pnlPctCell = row.querySelector('.cell-pnl-pct');

            if (priceCell) {
                const next = formatPrice(h.current_price, h.price_source);
                if (priceCell.textContent.trim() !== next) {
                    priceCell.textContent = next;
                    priceCell.classList.add('price-flash');
                    setTimeout(function () { priceCell.classList.remove('price-flash'); }, 600);
                }
            }
            if (valueCell) valueCell.textContent = formatUsd(h.value_usd, false);
            if (pnlPctCell) {
                pnlPctCell.textContent = formatPct(h.pnl_pct);
                pnlPctCell.className = 'cell-pnl-pct ' + pnlClass(h.pnl_pct);
            }
        });

        const totalUsd = resolveTotalUsd(summary, data.portfolio);
        const totalEl = document.getElementById('metric-total-value');
        if (totalEl && totalUsd != null) {
            const current = parseDisplayedNumber(totalEl);
            if (totalUsd > 0 || current <= 0) {
                totalEl.textContent = '$' + Number(totalUsd).toFixed(2);
                totalEl.dataset.animateValue = String(totalUsd);
            }
        }
        const availEl = document.getElementById('metric-available');
        const availUsd = summary.available_usd != null
            ? summary.available_usd
            : (data.portfolio && data.portfolio.available_usd);
        if (availEl && availUsd != null && (availUsd > 0 || parseDisplayedNumber(availEl) <= 0)) {
            availEl.textContent = Number(availUsd).toFixed(2);
        }
        const unrealEl = document.getElementById('metric-unrealized-pnl');
        if (unrealEl && summary.unrealized_pnl_usd != null && summary.positions_with_pnl) {
            const u = Number(summary.unrealized_pnl_usd);
            unrealEl.textContent = (u >= 0 ? '+' : '') + '$' + Math.abs(u).toFixed(2);
            unrealEl.className = pnlClass(u);
        }
        const costEl = document.getElementById('metric-cost-basis');
        if (costEl && summary.cost_basis_usd != null) {
            costEl.textContent = '$' + Number(summary.cost_basis_usd).toFixed(2);
        }
        const updatedEl = document.getElementById('prices-updated-at');
        if (updatedEl && summary.updated_at) {
            updatedEl.textContent = 'Updated ' + summary.updated_at.slice(0, 19).replace('T', ' ') + ' UTC';
        }
    }

    async function pollLiveHoldings() {
        try {
            const res = await fetch('/api/holdings/live');
            if (!res.ok) return;
            const data = await res.json();
            updateHoldingsLive(data);
        } catch (e) {
            /* silent — full page refresh will recover */
        }
    }

    function formatDeadline(iso) {
        if (!iso) return '';
        return iso.slice(0, 10);
    }

    function renderCompetitionStatus(comp) {
        const el = document.getElementById('hero-compete-badge');
        if (!el || !comp) return;
        if (comp.error) {
            el.innerHTML = '<span class="hero-compete-pill muted">Compete unavailable</span>';
            return;
        }
        if (!comp.registered) {
            el.innerHTML = '<span class="hero-compete-pill warn">Not registered</span>';
            return;
        }
        const windowCls = comp.open ? 'ok' : 'muted';
        const windowText = comp.open ? 'Window open' : 'Window closed';
        let html = '<span class="hero-compete-pill ok">Registered</span>' +
            '<span class="hero-compete-pill ' + windowCls + '">' + windowText + '</span>';
        if (comp.deadline) {
            html += '<span class="hero-compete-pill muted">Due ' + formatDeadline(comp.deadline) + '</span>';
        }
        el.innerHTML = html;
    }

    async function loadWalletDetails() {
        const addrEl = document.getElementById('hero-wallet-address');
        const bscLink = document.getElementById('hero-link-bscscan');
        const hadAddress = addrEl && (addrEl.dataset.walletAddress || '').length > 0;
        const controller = new AbortController();
        const timer = setTimeout(function () { controller.abort(); }, 12000);
        try {
            const res = await fetch('/api/wallet?full=false', { signal: controller.signal });
            if (!res.ok) {
                if (addrEl && !hadAddress) addrEl.textContent = 'Wallet API error (' + res.status + ')';
                return;
            }
            const w = await res.json();
            if (w.competition) renderCompetitionStatus(w.competition);
            if (w.wallet_address && addrEl) {
                const short = w.wallet_address.slice(0, 10) + '…' + w.wallet_address.slice(-8);
                addrEl.dataset.walletAddress = w.wallet_address;
                addrEl.innerHTML = '<a href="' + escapeHtml(w.bscscan_wallet_url || '#') +
                    '" target="_blank" rel="noopener" title="' + escapeHtml(w.wallet_address) + '">' +
                    escapeHtml(short) + '</a>';
            } else if (addrEl && !hadAddress && w.twak_installed === false) {
                addrEl.textContent = 'TWAK not detected';
            } else if (addrEl && !hadAddress) {
                addrEl.textContent = w.wallet_error || 'Wallet unavailable';
            }
            if (bscLink && w.bscscan_wallet_url) {
                bscLink.href = w.bscscan_wallet_url;
                bscLink.hidden = false;
            }
        } catch (e) {
            /* Keep SSR/config address visible; only refresh compete badge in background */
        } finally {
            clearTimeout(timer);
        }
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function renderDecisionRow(d) {
        const conf = d.confidence != null
            ? '<span class="activity-conf mono">' + Math.round(d.confidence * 100) + '%</span>'
            : '';
        const reason = d.reason_short
            ? '<p class="activity-reason">' + escapeHtml(d.reason_short) + '</p>'
            : '';
        return (
            '<li class="activity-row">' +
            '<div class="activity-row-top">' +
            '<span class="action-badge ' + escapeHtml(d.action || 'hold') + ' sm">' +
            escapeHtml((d.action || 'hold').toUpperCase()) + '</span>' +
            '<strong class="activity-asset">' + escapeHtml(d.asset || '—') + '</strong>' +
            '<span class="activity-meta">#' + escapeHtml(String(d.cycle_id || '—')) + '</span>' +
            conf +
            '<span class="activity-row-end"><span class="activity-time">' +
            escapeHtml(d.time_short || '—') + '</span></span>' +
            '</div>' + reason + '</li>'
        );
    }

    function renderTradeRow(t) {
        const tx = t.bscscan_url
            ? '<a class="tx-link tx-link-prominent" href="' + escapeHtml(t.bscscan_url) +
              '" target="_blank" rel="noopener" title="View on BscScan">' +
              '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
              '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>' +
              '<polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>BscScan</a>'
            : '<span class="activity-meta muted">no tx</span>';
        const status = t.status
            ? '<span class="activity-status">' + escapeHtml(t.status) + '</span>'
            : '';
        return (
            '<li class="activity-row">' +
            '<div class="activity-row-top">' +
            '<span class="action-badge ' + escapeHtml(t.side_lower || (t.side || '').toLowerCase()) + ' sm">' +
            escapeHtml(t.side || '—') + '</span>' +
            '<strong class="activity-asset">' + escapeHtml(t.asset || '—') + '</strong>' +
            '<span class="activity-meta">$' + Number(t.amount_usd || 0).toFixed(2) + '</span>' +
            status +
            '<span class="activity-row-end"><span class="activity-time">' +
            escapeHtml(t.time_short || '—') + '</span>' + tx + '</span>' +
            '</div></li>'
        );
    }

    function updateDecisionsPanel(items) {
        const list = document.getElementById('decisions-list');
        const panel = document.getElementById('decisions-panel');
        const count = document.getElementById('decisions-count');
        if (!panel) return;
        if (count) count.textContent = String(items.length);
        if (!items.length) {
            panel.innerHTML = '<p class="integration-muted activity-empty">No decisions yet — run a cycle to populate the audit trail.</p>';
            return;
        }
        if (!list) {
            panel.innerHTML = '<ul class="activity-list" id="decisions-list"></ul>';
        }
        const ul = document.getElementById('decisions-list');
        if (ul) ul.innerHTML = items.slice(0, 8).map(renderDecisionRow).join('');
    }

    function updateTradesPanel(items) {
        const list = document.getElementById('trades-list');
        const panel = document.getElementById('trades-panel');
        const count = document.getElementById('trades-count');
        if (!panel) return;
        if (count) count.textContent = String(items.length);
        if (!items.length) {
            panel.innerHTML = '<p class="integration-muted activity-empty">No swaps yet — trades appear here after execution.</p>';
            return;
        }
        if (!list) {
            panel.innerHTML = '<ul class="activity-list" id="trades-list"></ul>';
        }
        const ul = document.getElementById('trades-list');
        if (ul) ul.innerHTML = items.slice(0, 10).map(renderTradeRow).join('');
    }

    async function pollActivityFeeds() {
        try {
            const [decRes, tradeRes] = await Promise.all([
                fetch('/api/decisions?limit=8'),
                fetch('/api/trades?limit=10'),
            ]);
            if (decRes.ok) updateDecisionsPanel(await decRes.json());
            if (tradeRes.ok) updateTradesPanel(await tradeRes.json());
        } catch (e) { /* silent */ }
    }

    function initLivePrices() {
        const interval = parseInt(document.body.dataset.liveInterval || '20000', 10);
        if (interval <= 0) return;
        if (livePricesTimer) clearInterval(livePricesTimer);
        pollLiveHoldings();
        loadWalletDetails();
        pollAgentLogic();
        pollActivityFeeds();
        livePricesTimer = setInterval(function () {
            if (!document.hidden) {
                pollLiveHoldings();
                pollActivityFeeds();
            }
        }, interval);
    }

    function applyLogicFilters() {
        const search = (document.getElementById('logic-search') || {}).value || '';
        const q = search.trim().toUpperCase();
        document.querySelectorAll('.logic-token, .logic-feed-token').forEach(function (el) {
            const sym = (el.dataset.symbol || '').toUpperCase();
            const matchSearch = !q || sym.indexOf(q) >= 0;
            el.classList.toggle('hidden-by-filter', !matchSearch);
        });
    }

    function updateCycleProgress(feed) {
        const phaseEl = document.getElementById('logic-cycle-phase');
        const metaEl = document.getElementById('logic-cycle-meta');
        const barEl = document.getElementById('logic-cycle-bar');
        const scanEl = document.getElementById('logic-scanning-label');
        const progress = feed.progress || {};
        const scanned = progress.scanned || 0;
        const total = progress.total || 0;
        const pct = total > 0 ? Math.min(100, Math.round((scanned / total) * 100)) : 0;
        const isActive = !!feed.active;

        if (phaseEl) {
            const phase = feed.phase || 'idle';
            if (isActive && phase === 'scanning' && feed.current_symbol) {
                phaseEl.textContent = 'Scanning ' + feed.current_symbol + '…';
            } else if (isActive) {
                phaseEl.textContent = String(phase).replace(/_/g, ' ');
            } else if (feed.cycle_id) {
                phaseEl.textContent = 'Last cycle #' + feed.cycle_id;
            } else {
                phaseEl.textContent = 'Waiting for cycle';
            }
        }
        if (metaEl) {
            const parts = [];
            if (feed.cycle_id) parts.push('#' + feed.cycle_id);
            if (total > 0) parts.push(scanned + '/' + total);
            if (feed.mode) parts.push(feed.mode);
            metaEl.textContent = parts.length ? parts.join(' · ') : '—';
        }
        if (barEl) {
            barEl.style.width = (isActive && total > 0 ? pct : (feed.has_data ? 100 : 0)) + '%';
        }
        if (scanEl) {
            if (isActive && feed.current_symbol) {
                scanEl.innerHTML = 'Live: analyzing <strong>' + escapeHtml(feed.current_symbol) +
                    '</strong> (' + scanned + ' of ' + total + ')';
            } else if (feed.has_data) {
                scanEl.textContent = 'Showing signals from latest scan.';
            } else {
                scanEl.textContent = 'Run a cycle to see live per-token signals.';
            }
        }
    }

    function renderLogicColumn(listId, countId, tokens, currentSymbol, isActive, emptyText) {
        const listEl = document.getElementById(listId);
        const countEl = document.getElementById(countId);
        if (!listEl) return;
        if (countEl) countEl.textContent = String(tokens.length);
        if (!tokens.length) {
            listEl.innerHTML = '<p class="integration-muted logic-empty">' + escapeHtml(emptyText) + '</p>';
            return;
        }
        listEl.innerHTML = tokens.map(function (t) {
            return renderLogicToken(t, currentSymbol, isActive);
        }).join('');
    }

    function renderLogicToken(t, currentSymbol, isActive) {
        const scanning = isActive && currentSymbol && t.symbol === currentSymbol;
        const target = t.is_decision_target ? '<span class="logic-target-badge">decision</span>' : '';
        let bars = '';
        Object.keys(t.components || {}).forEach(function (key) {
            const val = t.components[key];
            bars +=
                '<div class="logic-bar-row"><span class="logic-bar-label">' + escapeHtml(key) +
                '</span><div class="logic-bar-track"><div class="logic-bar-fill" style="width:' +
                Math.round(val * 100) + '%"></div></div><span class="logic-bar-val">' +
                val.toFixed(2) + '</span></div>';
        });
        let signals = '';
        (t.signals || []).forEach(function (s) {
            let highlights = '';
            if (s.highlights && Object.keys(s.highlights).length) {
                highlights = '<div class="logic-highlights">';
                Object.keys(s.highlights).forEach(function (hk) {
                    highlights += '<span class="logic-highlight-item"><em>' + escapeHtml(hk) +
                        '</em>: ' + escapeHtml(String(s.highlights[hk])) + '</span>';
                });
                highlights += '</div>';
            }
            signals +=
                '<div class="logic-signal"><span class="logic-signal-cat">' + escapeHtml(s.category) +
                '</span><span class="logic-signal-val">raw=' + s.value.toFixed(2) + ' → ' +
                s.contribution.toFixed(2) + '</span>' +
                (s.summary ? '<span class="logic-signal-text">' + escapeHtml(s.summary) + '</span>' : '') +
                highlights + '</div>';
        });
        const summary = t.summary
            ? '<p class="logic-summary">' + escapeHtml(t.summary) + '</p>'
            : '';
        const chip = t.chip_preview
            ? '<span class="token-chip-preview">' + escapeHtml(t.chip_preview) + '</span>'
            : '';
        const insightBtn =
            '<button type="button" class="token-insight-btn" title="Token insights" data-token-detail=\'' +
            tokenDetailJson(t) + '\' aria-label="View ' + escapeHtml(t.symbol) + ' details">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>' +
            '<rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>' +
            '</svg></button>';
        return (
            '<details class="logic-token logic-feed-token ' + t.direction +
            (t.is_decision_target ? ' is-target' : '') +
            (scanning ? ' is-scanning' : '') +
            '" data-direction="' + t.direction + '" data-symbol="' + t.symbol + '">' +
            '<summary class="logic-token-summary">' +
            '<span class="logic-symbol">' + escapeHtml(t.symbol) + '</span>' +
            '<span class="logic-conviction">=' + t.conviction.toFixed(2) + '</span>' +
            '<span class="logic-direction ' + t.direction + '">' + t.direction + '</span>' +
            chip +
            '<span class="logic-components-preview mono">' + escapeHtml(t.components_line) + '</span>' +
            insightBtn + target + '</summary><div class="logic-token-body">' + summary +
            '<div class="logic-bars">' + bars + '</div>' +
            (signals ? '<div class="logic-signals">' + signals + '</div>' : '') +
            '</div></details>'
        );
    }

    function renderAgentLogic(feed) {
        const decisionEl = document.getElementById('logic-decision');
        const cycleEl = document.getElementById('logic-cycle');
        const updatedEl = document.getElementById('logic-updated');
        if (!document.getElementById('logic-feed')) return;

        if (cycleEl) {
            cycleEl.textContent = feed.cycle_id ? '#' + feed.cycle_id : '—';
        }
        if (decisionEl) {
            if (feed.decision && feed.decision.action && !feed.active) {
                const d = feed.decision;
                const action = (d.action || 'hold').toLowerCase();
                decisionEl.innerHTML =
                    '<div class="logic-decision-main">' +
                    '<span class="action-badge ' + action + '">' + escapeHtml(d.action || '—') + '</span>' +
                    '<strong>' + escapeHtml(d.asset || '—') + '</strong>' +
                    '<span class="logic-confidence">confidence ' + (d.confidence || 0).toFixed(2) + '</span>' +
                    (d.size_usd ? '<span class="logic-size">$' + d.size_usd.toFixed(2) + '</span>' : '') +
                    '</div><p class="logic-reason">' + escapeHtml(d.reason || '') + '</p>';
            } else if (feed.active) {
                decisionEl.innerHTML = '<p class="integration-muted">Cycle in progress — decision pending…</p>';
            } else {
                decisionEl.innerHTML = '<p class="integration-muted">Run a cycle to see reasoning.</p>';
            }
        }

        const stats = feed.stats || {};
        ['all', 'bullish', 'neutral', 'bearish'].forEach(function (k) {
            const el = document.getElementById('logic-stat-' + k);
            if (el) el.textContent = stats[k === 'all' ? 'total' : k] || 0;
        });

        const categories = feed.categories || {};
        const isActive = !!feed.active;
        const current = feed.current_symbol;
        const emptySuffix = isActive ? 'Scanning…' : 'yet';

        renderLogicColumn(
            'logic-col-bullish', 'logic-col-count-bullish',
            categories.bullish || [], current, isActive,
            'No bullish signals ' + emptySuffix
        );
        renderLogicColumn(
            'logic-col-neutral', 'logic-col-count-neutral',
            categories.neutral || [], current, isActive,
            'No neutral signals ' + emptySuffix
        );
        renderLogicColumn(
            'logic-col-bearish', 'logic-col-count-bearish',
            categories.bearish || [], current, isActive,
            'No bearish signals ' + emptySuffix
        );

        updateCycleProgress(feed);

        if (updatedEl && feed.updated_at) {
            let foot = 'Updated ' + String(feed.updated_at).slice(0, 19).replace('T', ' ') + ' UTC';
            if (feed.duration_ms) foot += ' · ' + feed.duration_ms + 'ms';
            updatedEl.textContent = foot;
        }

        applyLogicFilters();
    }

    var logicPollTimer = null;

    async function pollAgentLogic() {
        try {
            const res = await fetch('/api/cycle/feed');
            if (!res.ok) return;
            const data = await res.json();
            renderAgentLogic(data);
            scheduleLogicPoll(!!data.active || data.state === 'cycling');
        } catch (e) { /* silent */ }
    }

    function scheduleLogicPoll(fast) {
        if (logicPollTimer) clearTimeout(logicPollTimer);
        logicPollTimer = setTimeout(function () {
            if (!document.hidden) pollAgentLogic();
        }, fast ? 2000 : 8000);
    }

    function initLogicPanel() {
        const search = document.getElementById('logic-search');
        if (search) search.oninput = applyLogicFilters;
        applyLogicFilters();
    }

    function init() {
        initCounters();
        initAutoRefresh();
        initHtmx();
        initAgentControls();
        initLivePrices();
        initLogicPanel();
        initTokenDetailModal();
        initReveal();
        initParticles();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();