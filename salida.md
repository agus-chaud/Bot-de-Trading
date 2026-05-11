# KPI report (`report_kpis_v3`)

- **spec_id**: rpt_kpi.v1
- **run_id**: kpi_golden_60d
- **window**: 2024-01-02 → 2024-03-25
- **trading_days_per_year**: 252

## Segmento total

| Métrica | Valor |
|--------|-------|
| Retorno neto anualizado | 35.7432% |
| Max drawdown | -3.8159% |
| Sharpe (anual) | 2.2993 |
| Sortino (anual) | 6.2943 |
| Hit rate (round-trips) | 0.5000 |
| Profit factor | 2.0827 |
| Round-trips (`n_round_trips`) | 4 |
| Sesiones (`n_trading_days`) | 60 |

## Costos por motor

- **short**: 15.000000 USD
- **long**: 7.650000 USD

## Mandato: drift 30/70 y 20/80 (último día de la ventana)

- **snapshot `ts`**: 2024-03-25
| Eje | Drift (pp) |
|-----|------------|
| Corto vs objetivo | -0.3978 |
| Largo vs objetivo | -0.0134 |
| AR vs objetivo | 0.0000 |
| US vs objetivo | 0.0000 |
- **Bandas (± medio ancho pp, metadata)**: declaradas; snapshot dentro de umbral en todos los ejes con banda.

- **Objetivos usados**: corto 0.3, largo 0.7; AR 0.2, US 0.8 (fracciones sobre `equity_total`).
- **Serie diaria**: campo `mandate_drift.series` en el JSON de salida.

## Bloque largo (v3)

- **MDD_12m rolling (último)**: NA (insufficient_history)
- **Calmar_12m (último)**: NA (insufficient_history)
- **turnover_long_monthly (último mes `2024-03`)**: 0.0589

## Alpha vs benchmark mixto (alineado)

| Segmento | Alpha simple | Obs. inner join |
|----------|--------------|-----------------|
| Total | 0.0124 | 59 |
| Largo | 0.0122 | 59 |
