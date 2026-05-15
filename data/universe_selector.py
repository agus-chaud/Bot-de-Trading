"""AR universe selection: top Merval + top CEDEAR by IOL volume (audited in universe_snapshots).

Reads candidate lists from policy symbol YAMLs; uses IOL-only history for ranking
(no yfinance distortion). Fetch and short pipeline share `resolve_ar_universe_for_short_pipeline`
plus holdings overlay for OHLCV ingest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from data.connectors.ar_connector import fetch_ar_ohlcv
from data.iol_api_meter import IOL_KIND_UNIVERSE_VOLUME, IolJobBudgetExhausted
from data.schema import OHLCVRow, UniverseSnapshotRow

logger = logging.getLogger(__name__)

_SOURCE_DYNAMIC = "dynamic"


def load_merval_and_cedear_candidates(
    repo_root: Path,
    policy_doc: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Load uppercase candidate tickers from whitelist_ar (Merval) and whitelist_cedear."""
    sym = policy_doc["symbols"]
    ar_path = repo_root / str(sym["whitelist_ar_file"])
    ced_path = repo_root / str(sym["whitelist_cedear_file"])
    with ar_path.open(encoding="utf-8") as f:
        ar_doc = yaml.safe_load(f) or {}
    with ced_path.open(encoding="utf-8") as f:
        ced_doc = yaml.safe_load(f) or {}
    merval = [str(x).strip().upper() for x in (ar_doc.get("stocks") or []) if str(x).strip()]
    cedears = [str(x).strip().upper() for x in (ced_doc.get("stocks") or []) if str(x).strip()]
    return merval, cedears


def window_start_for_volume(end_date: date, window_trading_days: int) -> date:
    """Calendar lookback wide enough to collect ~window_trading_days daily bars from IOL."""
    cal_buffer = max(45, window_trading_days * 3)
    return end_date - timedelta(days=cal_buffer)


def metrics_from_bars(rows: list[OHLCVRow], window: int) -> tuple[float, float]:
    """Return (sum_volume, avg(close*volume)) over the last *window* bars by date."""
    if not rows:
        return 0.0, 0.0
    ordered = sorted(rows, key=lambda r: r.ts)
    tail = ordered[-window:] if len(ordered) >= window else ordered
    total_vol = sum(float(r.volume) for r in tail)
    products = [float(r.close) * float(r.volume) for r in tail if r.close > 0]
    avg_notional = sum(products) / len(products) if products else 0.0
    return total_vol, avg_notional


def _rank_candidates_by_liquidity(
    candidates: list[str],
    *,
    bucket: str,
    top_n: int,
    volume_window_trading_days: int,
    end_date: date,
    timeout: int,
    schema_version: int,
    selection_date: date,
    fetch_fn: Callable[..., Optional[list[OHLCVRow]]],
) -> tuple[list[UniverseSnapshotRow], list[str], list[tuple[str, str]]]:
    """Rank by total volume (desc), avg_notional (desc), symbol (asc). Uses IOL-only fetch."""
    start = window_start_for_volume(end_date, volume_window_trading_days)
    scored: list[tuple[str, float, float]] = []
    skipped: list[tuple[str, str]] = []
    for sym in candidates:
        rows = fetch_fn(sym, start, end_date, timeout, iol_only=True)
        if rows is None:
            skipped.append((sym, "iol_fetch_failed"))
            scored.append((sym, 0.0, 0.0))
            continue
        if not rows:
            skipped.append((sym, "iol_empty"))
            scored.append((sym, 0.0, 0.0))
            continue
        total_vol, avg_not = metrics_from_bars(rows, volume_window_trading_days)
        scored.append((sym, total_vol, avg_not))

    scored.sort(key=lambda t: (-t[1], -t[2], t[0]))
    top = scored[:top_n]
    snapshot_rows: list[UniverseSnapshotRow] = []
    symbols: list[str] = []
    for rank_idx, (sym, total_vol, _avg_not) in enumerate(top, start=1):
        symbols.append(sym)
        snapshot_rows.append(
            UniverseSnapshotRow(
                selection_date=selection_date,
                bucket=bucket,
                symbol=sym,
                rank=rank_idx,
                metric_value=total_vol,
                source=_SOURCE_DYNAMIC,
                schema_version=schema_version,
            )
        )
    return snapshot_rows, symbols, skipped


def _fetch_for_universe_volume(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
    *,
    iol_only: bool = True,
) -> Optional[list[OHLCVRow]]:
    return fetch_ar_ohlcv(
        symbol,
        start_date,
        end_date,
        timeout,
        iol_only=iol_only,
        iol_meter_kind=IOL_KIND_UNIVERSE_VOLUME,
    )


@dataclass(frozen=True)
class DynamicUniverseResult:
    merval_symbols: list[str]
    cedear_symbols: list[str]
    snapshot_rows: list[UniverseSnapshotRow]
    skipped: list[tuple[str, str]]
    budget_job_aborted: bool = False


def select_dynamic_universe(
    policy_doc: dict[str, Any],
    repo_root: Path,
    *,
    selection_date: date,
    as_of_date: date,
    timeout: int = 30,
    fetch_fn: Callable[..., Optional[list[OHLCVRow]]] = _fetch_for_universe_volume,
) -> DynamicUniverseResult:
    """Compute top Merval + top CEDEAR by IOL volume. No DB persistence (caller may persist)."""
    cfg = policy_doc["symbols"]["universe_selection"]
    if not cfg.get("enabled", False):
        return DynamicUniverseResult(
            merval_symbols=[], cedear_symbols=[], snapshot_rows=[], skipped=[], budget_job_aborted=False
        )

    m_candidates, c_candidates = load_merval_and_cedear_candidates(repo_root, policy_doc)
    targets = cfg["targets"]
    window = int(cfg["volume_window_trading_days"])
    schema_version = int(policy_doc["schema_version"])

    try:
        m_rows, m_syms, m_skip = _rank_candidates_by_liquidity(
            m_candidates,
            bucket="merval",
            top_n=int(targets["merval_top_n"]),
            volume_window_trading_days=window,
            end_date=as_of_date,
            timeout=timeout,
            schema_version=schema_version,
            selection_date=selection_date,
            fetch_fn=fetch_fn,
        )
        c_rows, c_syms, c_skip = _rank_candidates_by_liquidity(
            c_candidates,
            bucket="cedear",
            top_n=int(targets["cedears_top_n"]),
            volume_window_trading_days=window,
            end_date=as_of_date,
            timeout=timeout,
            schema_version=schema_version,
            selection_date=selection_date,
            fetch_fn=fetch_fn,
        )
    except IolJobBudgetExhausted:
        logger.warning('{"event": "universe_selection_aborted", "reason": "iol_job_budget_exhausted"}')
        return DynamicUniverseResult(
            merval_symbols=[],
            cedear_symbols=[],
            snapshot_rows=[],
            skipped=[("_", "job_budget_exceeded")],
            budget_job_aborted=True,
        )
    return DynamicUniverseResult(
        merval_symbols=m_syms,
        cedear_symbols=c_syms,
        snapshot_rows=m_rows + c_rows,
        skipped=m_skip + c_skip,
        budget_job_aborted=False,
    )


def merge_fetch_universe(
    merval_symbols: list[str],
    cedear_symbols: list[str],
    open_holdings_ar: list[str] | None = None,
) -> list[str]:
    """Union of liquidity picks + open AR holdings; uppercase, sorted (deterministic)."""
    holdings = [h.strip().upper() for h in (open_holdings_ar or []) if h and str(h).strip()]
    merged = {s.strip().upper() for s in list(merval_symbols) + list(cedear_symbols) + holdings}
    return sorted(merged)


@dataclass(frozen=True)
class ShortPipelineArUniverse:
    """AR symbols for ingest + optional cap on who enters the signal/ranking pool."""

    symbols_ar_bars: list[str]
    """Full AR set for OHLCV / snapshot rows (top ∪ holdings, or static ∪ holdings)."""
    ar_signal_symbols: frozenset[str] | None
    """If set, only these AR tickers are passed into `compute_signal_candidates` / ranking."""
    universe_meta: dict[str, object]


def static_ar_symbols_from_policy(repo_root: Path, policy_doc: dict[str, Any]) -> list[str]:
    """Same AR ticker union as legacy fetch_daily: whitelist_ar stocks + inline_ar."""
    sym = policy_doc["symbols"]
    out: list[str] = []
    for raw in sym.get("inline_ar", []) or []:
        s = str(raw).strip().upper()
        if s:
            out.append(s)
    ar_path = repo_root / str(sym["whitelist_ar_file"])
    with ar_path.open(encoding="utf-8") as f:
        ar_doc = yaml.safe_load(f) or {}
    for raw in ar_doc.get("stocks", []) or []:
        s = str(raw).strip().upper()
        if s:
            out.append(s)
    return sorted(set(out))


def open_ar_position_symbols_from_ledger(ledger: Any) -> list[str]:
    """Tickers with non-zero qty and market AR on *ledger* (any bucket)."""
    positions = getattr(ledger, "positions", None) or {}
    found: set[str] = set()
    for sym, pos in positions.items():
        mkt = str(getattr(pos, "market", "")).strip().upper()
        if mkt != "AR":
            continue
        qty = float(getattr(pos, "qty", 0.0))
        if abs(qty) <= 1e-12:
            continue
        found.add(str(sym).strip().upper())
    return sorted(found)


def open_ar_position_symbols_from_db(db: Any, *, mode: str = "paper_live") -> list[str]:
    """Replay paper fills into a ledger and return open AR symbols (any bucket)."""
    ledger = db.replay_ledger_from_fills(mode=mode)
    return open_ar_position_symbols_from_ledger(ledger)


def dynamic_tops_from_db(db: Any) -> tuple[list[str], list[str], date | None]:
    """Read latest persisted liquidity tops from universe_snapshots (merval / cedear lists)."""
    sel = db.get_latest_universe_selection_date()
    if sel is None:
        return [], [], None
    rows = db.get_universe_snapshots_for_date(sel)
    merval = [r.symbol.strip().upper() for r in rows if r.bucket == "merval"]
    cedear = [r.symbol.strip().upper() for r in rows if r.bucket == "cedear"]
    return merval, cedear, sel


def resolve_ar_universe_for_short_pipeline(
    policy_doc: dict[str, Any],
    repo_root: Path,
    ledger: Any,
    db: Any | None,
) -> ShortPipelineArUniverse:
    """Single resolution for short AR symbols: bars universe vs liquidity-only signal pool."""
    cfg = policy_doc.get("symbols", {}).get("universe_selection") or {}
    holdings = open_ar_position_symbols_from_ledger(ledger)
    if not cfg.get("enabled", False):
        base = static_ar_symbols_from_policy(repo_root, policy_doc)
        bars = merge_fetch_universe(base, [], holdings)
        return ShortPipelineArUniverse(
            symbols_ar_bars=bars,
            ar_signal_symbols=None,
            universe_meta={"mode": "static_whitelist", "holdings_overlay": list(holdings)},
        )

    merval: list[str] = []
    cedear: list[str] = []
    sel_date: date | None = None
    if db is not None:
        merval, cedear, sel_date = dynamic_tops_from_db(db)
    if not merval and not cedear:
        base = static_ar_symbols_from_policy(repo_root, policy_doc)
        bars = merge_fetch_universe(base, [], holdings)
        return ShortPipelineArUniverse(
            symbols_ar_bars=bars,
            ar_signal_symbols=None,
            universe_meta={
                "mode": "fallback_static",
                "reason": "no_universe_snapshot",
                "holdings_overlay": list(holdings),
            },
        )

    bars = merge_fetch_universe(merval, cedear, holdings)
    signal = frozenset(merval + cedear)
    return ShortPipelineArUniverse(
        symbols_ar_bars=bars,
        ar_signal_symbols=signal,
        universe_meta={
            "mode": "dynamic",
            "selection_date": sel_date.isoformat() if sel_date else None,
            "holdings_overlay": list(holdings),
            "merval_top_n": len(merval),
            "cedear_top_n": len(cedear),
        },
    )


def persist_universe_snapshots(
    db: Any,
    selection_date: date,
    rows: list[UniverseSnapshotRow],
) -> None:
    """Persist rows for *selection_date*, replacing any prior snapshot for that date."""
    db.replace_universe_snapshots(selection_date, rows)
