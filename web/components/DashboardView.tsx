"use client";

import { useCallback, useMemo, useState } from "react";

import { EquityChart } from "@/components/EquityChart";
import { NeuralBackground } from "@/components/NeuralBackground";
import { fetchDashboardPayload } from "@/lib/dashboard-client";
import { fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import type { DashboardAlert, DashboardPayload } from "@/lib/types";

const ALERT_COLORS: Record<string, string> = {
  critical: "#ff4d4d",
  warning: "#e6a700",
  info: "#00bfff",
  ok: "#76b900",
};

function AlertIcon({ severity }: { severity: string }) {
  const stroke = ALERT_COLORS[severity] ?? ALERT_COLORS.info;
  return (
    <svg className="alert-icon" viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth="2">
      <path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    </svg>
  );
}

function statusFromPayload(alerts: DashboardAlert[], tradingAllowed: boolean): {
  label: string;
  className: string;
} {
  const hasCritical = alerts.some((a) => a.severity === "critical");
  const hasWarn = alerts.some((a) => a.severity === "warning");
  if (hasCritical || !tradingAllowed) {
    return { label: "Bloqueado", className: "status-pill critical" };
  }
  if (hasWarn) {
    return { label: "Atención", className: "status-pill warn" };
  }
  return { label: "Operativo", className: "status-pill ok" };
}

interface DashboardViewProps {
  initialData: DashboardPayload;
}

export function DashboardView({ initialData }: DashboardViewProps) {
  const [data, setData] = useState(initialData);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchDashboardPayload();
      setData(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setLoading(false);
    }
  }, []);

  const ccy = data.meta.currency || "ARS";
  const status = useMemo(
    () => statusFromPayload(data.alerts, data.risk.trading_allowed),
    [data.alerts, data.risk.trading_allowed],
  );

  const metaLine = [
    data.meta.mode,
    ccy,
    data.meta.last_trading_day ? `último día ${data.meta.last_trading_day}` : "sin días",
    data.kpis.n_days ? `${data.kpis.n_days} días KPI` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const calmar = data.kpis.calmar_total ?? data.kpis.calmar_12m_long;
  const displayAlerts =
    error != null
      ? [
          {
            severity: "critical" as const,
            code: "load_error",
            title: "No se pudo cargar el dashboard",
            detail: error,
          },
        ]
      : data.alerts;

  const showFreshness =
    data.data_freshness && data.data_freshness.status !== "ok" && !error;

  return (
    <>
      <NeuralBackground />
      <div className="scanline" aria-hidden="true" />

      <div className="app-shell">
        <header className="top-bar">
          <div className="brand">
            <span className="brand-mark" />
            <div>
              <h1 className="brand-title">Paper-Live Monitor</h1>
              <p className="brand-sub">{error ? `Error: ${error}` : metaLine}</p>
            </div>
          </div>
          <div className="top-actions">
            <span className={error ? "status-pill critical" : status.className}>
              {error ? "Error" : status.label}
            </span>
            <button
              type="button"
              className="btn-secondary"
              onClick={refresh}
              disabled={loading}
            >
              {loading ? "Cargando…" : "Actualizar"}
            </button>
          </div>
        </header>

        {showFreshness && (
          <section className="freshness-banner" aria-live="polite">
            <strong>DB local desactualizada</strong>
            <span>{data.data_freshness.message}</span>
            <code>{data.data_freshness.sync_hint}</code>
          </section>
        )}

        {displayAlerts.length > 0 && (
          <section className="alerts-panel" aria-live="polite">
            {displayAlerts.map((alert) => (
              <div key={`${alert.code}-${alert.title}`} className={`alert ${alert.severity}`}>
                <AlertIcon severity={alert.severity} />
                <div className="alert-body">
                  <strong>{alert.title}</strong>
                  <span>{alert.detail}</span>
                </div>
              </div>
            ))}
          </section>
        )}

        <section className="kpi-grid">
          <article className="card kpi-card">
            <span className="kpi-label">Equity total</span>
            <span className="kpi-value">{fmtMoney(data.meta.equity_total, ccy)}</span>
            <span className="kpi-caption">
              {data.kpis.net_return_annualized != null
                ? `Retorno anualizado ${fmtPct(data.kpis.net_return_annualized)}`
                : data.meta.inception_date
                  ? `Desde ${data.meta.inception_date}`
                  : ""}
            </span>
          </article>
          <article className="card kpi-card">
            <span className="kpi-label">Sharpe anualizado</span>
            <span className="kpi-value">
              {data.kpis.sharpe_annualized != null ? fmtNum(data.kpis.sharpe_annualized, 2) : "n/d"}
            </span>
            <span className="kpi-caption">{data.kpis.sharpe_na_reason || "Sobre equity total"}</span>
          </article>
          <article className="card kpi-card">
            <span className="kpi-label">Calmar</span>
            <span className="kpi-value">{calmar != null ? fmtNum(calmar, 2) : "n/d"}</span>
            <span className="kpi-caption">
              {data.kpis.calmar_12m_na_reason && data.kpis.calmar_total == null
                ? data.kpis.calmar_12m_na_reason
                : "Retorno / |MDD|"}
            </span>
          </article>
          <article className="card kpi-card">
            <span className="kpi-label">Max drawdown</span>
            <span className="kpi-value negative">{fmtPct(data.kpis.max_drawdown)}</span>
            <span className="kpi-caption">
              {data.kpis.ts_start && data.kpis.ts_end
                ? `${data.kpis.ts_start} → ${data.kpis.ts_end}`
                : ""}
            </span>
          </article>
        </section>

        <main className="dashboard-grid">
          <section className="card panel chart-panel">
            <div className="panel-header">
              <h2>Curva de equity</h2>
              <div className="chart-legend">
                <span className="legend-item">
                  <span className="legend-dot" style={{ background: "#6efa5f" }} />
                  Total
                </span>
                <span className="legend-item">
                  <span className="legend-dot" style={{ background: "#00bfff" }} />
                  Corto
                </span>
                <span className="legend-item">
                  <span className="legend-dot" style={{ background: "#76b900" }} />
                  Largo
                </span>
              </div>
            </div>
            <EquityChart curve={data.equity_curve} currency={ccy} />
          </section>

          <section className="card panel positions-panel">
            <div className="panel-header">
              <h2>Posiciones hoy</h2>
              <span className="panel-meta">{data.positions.length}</span>
            </div>
            <div className="table-wrap">
              {data.positions.length === 0 ? (
                <div className="empty-state">Sin posiciones abiertas</div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Símbolo</th>
                      <th>Bucket</th>
                      <th className="num">Qty</th>
                      <th className="num">Valor</th>
                      <th className="num">PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.positions.map((p) => (
                      <tr key={`${p.symbol}-${p.bucket}`}>
                        <td>
                          {p.symbol}
                          {p.stale ? " *" : ""}
                        </td>
                        <td>{p.bucket}</td>
                        <td className="num">{fmtNum(p.qty, 4)}</td>
                        <td className="num">{fmtMoney(p.market_value, ccy)}</td>
                        <td
                          className={`num ${p.unrealized_pnl >= 0 ? "side-buy" : "side-sell"}`}
                        >
                          {fmtMoney(p.unrealized_pnl, ccy)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section className="card panel fills-panel">
            <div className="panel-header">
              <h2>Últimas operaciones</h2>
            </div>
            <div className="table-wrap">
              {data.recent_fills.length === 0 ? (
                <div className="empty-state">Sin operaciones registradas</div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Día</th>
                      <th>Símbolo</th>
                      <th>Lado</th>
                      <th className="num">Qty</th>
                      <th className="num">Precio</th>
                      <th>Motivo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_fills.map((f, i) => (
                      <tr key={`${f.trading_day}-${f.symbol}-${i}`}>
                        <td>{f.trading_day}</td>
                        <td>{f.symbol}</td>
                        <td className={f.side === "BUY" ? "side-buy" : "side-sell"}>{f.side}</td>
                        <td className="num">{fmtNum(f.qty, 4)}</td>
                        <td className="num">{fmtMoney(f.price, ccy)}</td>
                        <td>{f.reason || f.engine || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section className="card panel risk-panel">
            <div className="panel-header">
              <h2>Por qué frenó (riesgo)</h2>
              <span
                className={`risk-badge ${data.risk.trading_allowed ? "allowed" : "blocked"}`}
              >
                {data.risk.trading_allowed ? "Trade OK" : "Frenado"}
              </span>
            </div>
            <ul className="risk-list">
              {(data.risk.factors || []).map((f, i) => (
                <li key={`${f.code}-${i}`} className="risk-item" style={{ animationDelay: `${i * 120}ms` }}>
                  <span className={`risk-dot ${f.level}`} />
                  <span>{f.message}</span>
                </li>
              ))}
            </ul>
            <div className="risk-thresholds">
              <div>
                Kill switch corto: {fmtPct(data.risk.thresholds.short_kill_switch_monthly_dd)}
              </div>
              <div>
                Pérdida diaria corto: {fmtPct(data.risk.thresholds.max_daily_loss_short_pct)}
              </div>
            </div>
          </section>
        </main>

        <footer className="footer">
          <span>
            Actualizado{" "}
            {new Date(data.generated_at).toLocaleString("es-AR")}
            {data.export_version ? ` · export v${data.export_version}` : ""}
          </span>
          <span className="footer-sep">|</span>
          <span>Modo paper-first · datos desde dashboard_payload.json</span>
        </footer>
      </div>
    </>
  );
}
