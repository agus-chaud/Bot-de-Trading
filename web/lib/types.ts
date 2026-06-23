export type AlertSeverity = "critical" | "warning" | "info" | "ok";

export interface DashboardAlert {
  severity: AlertSeverity;
  code: string;
  title: string;
  detail: string;
}

export interface DataFreshness {
  status: string;
  message: string;
  commits_behind?: number;
  worktree_dirty?: boolean;
  remote_ref?: string;
  sync_hint?: string;
}

export interface DashboardMeta {
  mode: string;
  currency: string;
  starting_cash: number;
  inception_date: string | null;
  last_trading_day: string | null;
  equity_total: number | null;
  num_open_positions: number;
}

export interface EquityPoint {
  date: string;
  equity_total: number;
  equity_short: number;
  equity_long: number;
  cash: number;
  mv_us: number;
  mv_ar: number;
}

export interface PositionRow {
  symbol: string;
  qty: number;
  avg_cost: number;
  market: string;
  bucket: string;
  market_value: number;
  unrealized_pnl: number;
  stale: boolean;
}

export interface FillRow {
  trading_day: string;
  ts_fill: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  bucket: string;
  engine: string;
  reason: string | null;
  fee?: number;
  cost_total?: number;
}

export interface RiskFactor {
  level: string;
  code: string;
  message: string;
}

export interface DashboardRisk {
  trading_allowed: boolean;
  kill_switch: {
    active: boolean;
    activated_at: string | null;
    monthly_dd: number | null;
  };
  thresholds: {
    short_kill_switch_monthly_dd: number;
    max_daily_loss_short_pct: number;
  };
  latest_snapshot?: Record<string, unknown>;
  factors: RiskFactor[];
}

export interface DashboardKpis {
  status: string;
  n_days: number;
  sharpe_annualized: number | null;
  sharpe_na_reason: string | null;
  sortino_annualized?: number | null;
  max_drawdown: number | null;
  net_return_annualized: number | null;
  calmar_total: number | null;
  calmar_12m_long: number | null;
  calmar_12m_na_reason: string | null;
  hit_rate?: number | null;
  profit_factor?: number | null;
  ts_start: string | null;
  ts_end: string | null;
}

export interface RiskMatrixEntry {
  code: string;
  title: string;
  probability: string;
  impact: string;
  mitigation: string;
  severity: AlertSeverity;
  status: string;
}

export interface PositionTechnical {
  trend: string;
  momentum_pct: number | null;
  vs_sma_pct: number | null;
  last_close: number | null;
}

export interface PositionThesis {
  symbol: string;
  bucket: string;
  market: string | null;
  side: string;
  qty: number;
  market_value: number | null;
  unrealized_pnl: number;
  unrealized_pnl_pct: number | null;
  stance: string;
  technical: PositionTechnical;
  bull: string[];
  bear: string[];
}

export interface DashboardPayload {
  meta: DashboardMeta;
  data_freshness: DataFreshness;
  equity_curve: EquityPoint[];
  positions: PositionRow[];
  recent_fills: FillRow[];
  risk: DashboardRisk;
  risk_matrix?: RiskMatrixEntry[];
  position_theses?: PositionThesis[];
  kpis: DashboardKpis;
  alerts: DashboardAlert[];
  generated_at: string;
  export_version: string;
  export_source?: Record<string, string>;
}
