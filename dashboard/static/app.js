/** Paper-live dashboard client. */
(function () {
  const POLL_MS = 60_000;
  let equityChart = null;

  const $ = (id) => document.getElementById(id);

  const fmtMoney = (v, ccy) => {
    if (v == null || Number.isNaN(v)) return "—";
    return new Intl.NumberFormat("es-AR", {
      style: "currency",
      currency: ccy || "ARS",
      maximumFractionDigits: 0,
    }).format(v);
  };

  const fmtNum = (v, digits = 2) => {
    if (v == null || Number.isNaN(v)) return "—";
    return Number(v).toFixed(digits);
  };

  const fmtPct = (v) => {
    if (v == null || Number.isNaN(v)) return "—";
    return `${(Number(v) * 100).toFixed(2)}%`;
  };

  const alertIcon = (severity) => {
    const colors = {
      critical: "#ff4d4d",
      warning: "#e6a700",
      info: "#00bfff",
      ok: "#76b900",
    };
    const c = colors[severity] || colors.info;
    return `<svg class="alert-icon" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="2"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>`;
  };

  async function fetchDashboard() {
    const res = await fetch("/api/dashboard");
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  function renderAlerts(alerts) {
    const panel = $("alerts-panel");
    if (!alerts?.length) {
      panel.innerHTML = "";
      return;
    }
    panel.innerHTML = alerts
      .map(
        (a) => `
      <div class="alert ${a.severity}">
        ${alertIcon(a.severity)}
        <div class="alert-body">
          <strong>${escapeHtml(a.title)}</strong>
          <span>${escapeHtml(a.detail)}</span>
        </div>
      </div>`
      )
      .join("");
  }

  function renderMeta(meta, kpis) {
    const ccy = meta.currency || "ARS";
    const line = [
      meta.mode,
      ccy,
      meta.last_trading_day ? `último día ${meta.last_trading_day}` : "sin días",
      kpis.n_days ? `${kpis.n_days} días KPI` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    $("meta-line").textContent = line;

    $("kpi-equity").textContent = fmtMoney(meta.equity_total, ccy);
    const ret = kpis.net_return_annualized;
    $("kpi-equity-caption").textContent =
      ret != null ? `Retorno anualizado ${fmtPct(ret)}` : meta.inception_date
        ? `Desde ${meta.inception_date}`
        : "";

    const sharpe = kpis.sharpe_annualized;
    $("kpi-sharpe").textContent = sharpe != null ? fmtNum(sharpe, 2) : "n/d";
    $("kpi-sharpe-caption").textContent = kpis.sharpe_na_reason || "Sobre equity total";

    const calmar = kpis.calmar_total ?? kpis.calmar_12m_long;
    $("kpi-calmar").textContent = calmar != null ? fmtNum(calmar, 2) : "n/d";
    $("kpi-calmar-caption").textContent =
      kpis.calmar_12m_na_reason && kpis.calmar_total == null
        ? kpis.calmar_12m_na_reason
        : "Retorno / |MDD|";

    $("kpi-drawdown").textContent = fmtPct(kpis.max_drawdown);
    $("kpi-drawdown-caption").textContent =
      kpis.ts_start && kpis.ts_end ? `${kpis.ts_start} → ${kpis.ts_end}` : "";
  }

  function renderStatus(alerts, risk) {
    const pill = $("status-pill");
    const hasCritical = alerts.some((a) => a.severity === "critical");
    const hasWarn = alerts.some((a) => a.severity === "warning");
    if (hasCritical || !risk.trading_allowed) {
      pill.textContent = "Bloqueado";
      pill.className = "status-pill critical";
    } else if (hasWarn) {
      pill.textContent = "Atención";
      pill.className = "status-pill warn";
    } else {
      pill.textContent = "Operativo";
      pill.className = "status-pill ok";
    }
  }

  function renderChart(curve, ccy) {
    const canvas = $("equity-chart");
    const labels = curve.map((p) => p.date);
    const total = curve.map((p) => p.equity_total);
    const short = curve.map((p) => p.equity_short);
    const long = curve.map((p) => p.equity_long);

    $("chart-legend").innerHTML = `
      <span class="legend-item"><span class="legend-dot" style="background:#6efa5f"></span>Total</span>
      <span class="legend-item"><span class="legend-dot" style="background:#00bfff"></span>Corto</span>
      <span class="legend-item"><span class="legend-dot" style="background:#76b900"></span>Largo</span>
    `;

    const gridColor = "rgba(74, 74, 74, 0.4)";
    const data = {
      labels,
      datasets: [
        {
          label: "Equity total",
          data: total,
          borderColor: "#6efa5f",
          backgroundColor: "rgba(110, 250, 95, 0.08)",
          fill: true,
          tension: 0.35,
          pointRadius: curve.length > 30 ? 0 : 3,
          borderWidth: 2,
        },
        {
          label: "Corto",
          data: short,
          borderColor: "#00bfff",
          borderWidth: 1.5,
          tension: 0.35,
          pointRadius: 0,
        },
        {
          label: "Largo",
          data: long,
          borderColor: "#76b900",
          borderWidth: 1.5,
          tension: 0.35,
          pointRadius: 0,
        },
      ],
    };

    const options = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "rgba(26,26,26,0.95)",
          titleFont: { family: "Roboto Mono" },
          bodyFont: { family: "Roboto Mono" },
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y;
              return `${ctx.dataset.label}: ${fmtMoney(v, ccy)}`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: "#bdbdbd", font: { family: "Roboto Mono", size: 10 } },
          grid: { color: gridColor },
        },
        y: {
          ticks: {
            color: "#bdbdbd",
            font: { family: "Roboto Mono", size: 10 },
            callback: (v) => fmtMoney(v, ccy),
          },
          grid: { color: gridColor },
        },
      },
    };

    if (equityChart) {
      equityChart.data = data;
      equityChart.options = options;
      equityChart.update();
    } else {
      equityChart = new Chart(canvas, { type: "line", data, options });
    }
  }

  function renderPositions(positions, ccy) {
    $("positions-count").textContent = String(positions.length);
    const el = $("positions-table");
    if (!positions.length) {
      el.innerHTML = '<div class="empty-state">Sin posiciones abiertas</div>';
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Símbolo</th>
            <th>Bucket</th>
            <th class="num">Qty</th>
            <th class="num">Valor</th>
            <th class="num">PnL</th>
          </tr>
        </thead>
        <tbody>
          ${positions
            .map(
              (p) => `
            <tr>
              <td>${escapeHtml(p.symbol)}${p.stale ? " *" : ""}</td>
              <td>${escapeHtml(p.bucket)}</td>
              <td class="num">${fmtNum(p.qty, 4)}</td>
              <td class="num">${fmtMoney(p.market_value, ccy)}</td>
              <td class="num ${p.unrealized_pnl >= 0 ? "side-buy" : "side-sell"}">${fmtMoney(p.unrealized_pnl, ccy)}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderFills(fills, ccy) {
    const el = $("fills-table");
    if (!fills.length) {
      el.innerHTML = '<div class="empty-state">Sin operaciones registradas</div>';
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Día</th>
            <th>Símbolo</th>
            <th>Lado</th>
            <th class="num">Qty</th>
            <th class="num">Precio</th>
            <th>Motivo</th>
          </tr>
        </thead>
        <tbody>
          ${fills
            .map(
              (f) => `
            <tr>
              <td>${escapeHtml(f.trading_day)}</td>
              <td>${escapeHtml(f.symbol)}</td>
              <td class="${f.side === "BUY" ? "side-buy" : "side-sell"}">${escapeHtml(f.side)}</td>
              <td class="num">${fmtNum(f.qty, 4)}</td>
              <td class="num">${fmtMoney(f.price, ccy)}</td>
              <td>${escapeHtml(f.reason || f.engine || "—")}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderRisk(risk) {
    const badge = $("risk-badge");
    badge.textContent = risk.trading_allowed ? "Trade OK" : "Frenado";
    badge.className = `risk-badge ${risk.trading_allowed ? "allowed" : "blocked"}`;

    const list = $("risk-list");
    list.innerHTML = (risk.factors || [])
      .map(
        (f, i) => `
      <li class="risk-item" style="animation-delay:${i * 120}ms">
        <span class="risk-dot ${f.level}"></span>
        <span>${escapeHtml(f.message)}</span>
      </li>`
      )
      .join("");

    const th = risk.thresholds || {};
    $("risk-thresholds").innerHTML = `
      <div>Kill switch corto: ${fmtPct(th.short_kill_switch_monthly_dd)}</div>
      <div>Pérdida diaria corto: ${fmtPct(th.max_daily_loss_short_pct)}</div>
    `;
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function refresh() {
    try {
      const data = await fetchDashboard();
      const ccy = data.meta?.currency || "ARS";
      renderMeta(data.meta, data.kpis);
      renderAlerts(data.alerts);
      renderStatus(data.alerts, data.risk);
      renderChart(data.equity_curve || [], ccy);
      renderPositions(data.positions || [], ccy);
      renderFills(data.recent_fills || [], ccy);
      renderRisk(data.risk);
      $("footer-ts").textContent = `Actualizado ${new Date(data.generated_at).toLocaleString("es-AR")}`;
    } catch (err) {
      $("meta-line").textContent = `Error: ${err.message}`;
      $("status-pill").textContent = "Error";
      $("status-pill").className = "status-pill critical";
      renderAlerts([
        {
          severity: "critical",
          title: "No se pudo cargar el dashboard",
          detail: err.message,
        },
      ]);
    }
  }

  $("btn-refresh")?.addEventListener("click", refresh);
  refresh();
  setInterval(refresh, POLL_MS);
})();
