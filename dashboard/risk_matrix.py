"""Risk matrix for the paper-live dashboard (mejora #4).

En vez de un riesgo binario (``trading_allowed`` sí/no), expone una matriz tipo
hedge-fund: cada amenaza con **probabilidad**, **impacto** y **mitigación**, más un
estado dinámico calculado de los datos reales. Hace VISIBLES riesgos latentes —
por ejemplo el mismark por data US stale que ya envenenó una simulación.

Función pura: recibe señales ya calculadas (no toca la DB) para poder testearse.
"""

from __future__ import annotations

from typing import Any

Probability = str  # "alta" | "media" | "baja"
Status = str  # "activo" | "latente" | "controlado"


def _entry(
    *,
    code: str,
    title: str,
    probability: Probability,
    impact: str,
    mitigation: str,
    severity: str,
    status: Status,
) -> dict[str, str]:
    return {
        "code": code,
        "title": title,
        "probability": probability,
        "impact": impact,
        "mitigation": mitigation,
        "severity": severity,
        "status": status,
    }


def build_risk_matrix(
    *,
    latest_snapshot: dict[str, Any] | None,
    positions: list[dict[str, Any]],
    max_data_lag_days: int,
    fetch_issue_count: int,
    ks_active: bool,
    ks_floor: float,
) -> list[dict[str, str]]:
    """Build the risk register from current signals. Orden: severidad desc."""
    rows: list[dict[str, str]] = []

    # --- 1. Data de mercado stale -> mismark de valuación ---------------------
    if max_data_lag_days >= 5:
        prob, sev, status = "alta", "critical", "activo"
    elif max_data_lag_days >= 2:
        prob, sev, status = "media", "warning", "activo"
    else:
        prob, sev, status = "baja", "ok", "controlado"
    rows.append(
        _entry(
            code="stale_market_data",
            title="Data de mercado desactualizada",
            probability=prob,
            impact=(
                "Posiciones valuadas con precios viejos (carry-forward) → equity y "
                "KPIs envenenados; explosión de valuación en simulaciones."
            ),
            mitigation=(
                "Freshness gate + ingesta diaria; alertar si el último cierre por "
                f"venue atrasa ≥2 días (hoy: {max_data_lag_days})."
            ),
            severity=sev,
            status=status,
        )
    )

    # --- 2. Drawdown acercándose al kill switch -------------------------------
    dd = None if latest_snapshot is None else latest_snapshot.get("short_monthly_drawdown")
    if dd is not None and ks_floor < 0:
        ratio = float(dd) / ks_floor  # 1.0 = en el piso; >1 ya pasó
        if ks_active or ratio >= 1.0:
            prob, sev, status = "alta", "critical", "activo"
        elif ratio >= 0.7:
            prob, sev, status = "media", "warning", "activo"
        else:
            prob, sev, status = "baja", "ok", "controlado"
    else:
        prob, sev, status = "baja", "ok", "controlado"
    rows.append(
        _entry(
            code="drawdown_kill_switch",
            title="Drawdown cerca del kill switch",
            probability=prob,
            impact="Congelamiento del bucket corto; pérdida de capacidad de hedge.",
            mitigation=f"Kill switch automático en {ks_floor:.0%} DD mensual corto.",
            severity=sev,
            status=status,
        )
    )

    # --- 3. Concentración de cartera ------------------------------------------
    n_pos = len(positions)
    gross = sum(abs(float(p.get("market_value") or 0.0)) for p in positions)
    top_w = (
        max(abs(float(p.get("market_value") or 0.0)) for p in positions) / gross
        if positions and gross > 0
        else 0.0
    )
    if n_pos == 0:
        prob, sev, status = "baja", "ok", "controlado"
    elif top_w >= 0.6 or n_pos <= 1:
        prob, sev, status = "alta", "warning", "activo"
    elif top_w >= 0.4 or n_pos <= 3:
        prob, sev, status = "media", "info", "latente"
    else:
        prob, sev, status = "baja", "ok", "controlado"
    rows.append(
        _entry(
            code="concentration",
            title="Concentración de cartera",
            probability=prob,
            impact="Un solo nombre adverso mueve el equity de forma desproporcionada.",
            mitigation="Diversificar entre más nombres; límite de peso por posición.",
            severity=sev,
            status=f"{n_pos} posiciones · top {top_w:.0%}" if n_pos else "sin posiciones",
        )
    )

    # --- 4. Fallas de ingesta OHLCV -------------------------------------------
    if fetch_issue_count > 0:
        prob = "alta" if fetch_issue_count >= 3 else "media"
        sev, status = "warning", "activo"
    else:
        prob, sev, status = "baja", "ok", "controlado"
    rows.append(
        _entry(
            code="ingestion_failures",
            title="Fallas de ingesta de precios",
            probability=prob,
            impact="Símbolos sin barra del día → señales y valuación incompletas.",
            mitigation="Alias de tickers + reintentos; alerta de fetch_errors vigentes.",
            severity=sev,
            status=(f"{fetch_issue_count} símbolos" if fetch_issue_count else "sin fallas"),
        )
    )

    # --- 5. Cotizaciones stale en posiciones abiertas -------------------------
    stale_pos = [p for p in positions if p.get("stale")]
    if stale_pos:
        prob, sev, status = "media", "warning", "activo"
        detail = ", ".join(str(p["symbol"]) for p in stale_pos[:4])
    else:
        prob, sev, status = "baja", "ok", "controlado"
        detail = "sin posiciones stale"
    rows.append(
        _entry(
            code="stale_position_quote",
            title="Posiciones con cotización stale",
            probability=prob,
            impact="PnL no realizado calculado sobre precio viejo; riesgo mal medido.",
            mitigation="Forzar marca a último cierre válido; señalar barra imputada.",
            severity=sev,
            status=detail,
        )
    )

    severity_rank = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    rows.sort(key=lambda r: severity_rank.get(r["severity"], 9))
    return rows
