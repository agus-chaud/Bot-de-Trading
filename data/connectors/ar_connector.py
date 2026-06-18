"""Fetch AR OHLCV bars from InvertirOnline (primary) with Byma/yfinance fallback."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

import requests
import yfinance as yf

from data.fetch_trace import (
    FETCH_STATUS_OK,
    FETCH_STATUS_SKIP,
    SKIP_BUDGET_EXHAUSTED,
    SKIP_CONNECTOR_RETURNED_NONE,
    SKIP_CREDENTIALS_MISSING,
    SKIP_DATA_ERROR,
    SKIP_EMPTY_DATA,
    SKIP_FALLBACK_USED,
    SKIP_MAX_RETRIES_EXCEEDED,
    SOURCE_BYMA,
    SOURCE_IOL,
    SymbolFetchTrace,
    VENUE_AR,
    apply_source_attribution,
)
from data.schema import OHLCVRow
from data.iol_api_meter import (
    IOL_KIND_HISTORY,
    IOL_KIND_REFRESH,
    IOL_KIND_TOKEN,
    IolJobBudgetExhausted,
    record_iol_call,
    try_consume_iol_job_slot,
)

logger = logging.getLogger(__name__)

_VENUE = "XBUE"  # ISO MIC BYMA — must match calendars / get_ohlcv (see ADR-030 US→XNYS)
_CURRENCY = "ARS"
_REQUIRED_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}

_BACKOFF_SECONDS = [1, 2, 4]
_MAX_ATTEMPTS = 3

# Auth contract (POST only — GET returns UnsupportedApiVersion in browser):
# https://api.invertironline.com/Help/Autenticacion
_IOL_TOKEN_URL = "https://api.invertironline.com/token"
_IOL_HISTORY_URL = "https://api.invertironline.com/api/v2/{mercado}/Titulos/{symbol}/Cotizacion/seriehistorica/{start}/{end}/{ajuste}"
_IOL_MERCADO = "bCBA"  # Bolsas y Mercados Argentinos via IOL
# Modo de ajuste de la serie histórica. Acciones/CEDEARs traen datos en 'ajustada';
# los títulos públicos (bonos: AL30/GD30) devuelven [] en 'ajustada' y solo entregan
# serie en 'sinAjustar'. Probamos 'ajustada' primero y caemos a 'sinAjustar' si viene
# vacío — así el mismo connector cubre acciones, CEDEARs y bonos sin lista hardcodeada.
_IOL_ADJUST_PRIMARY = "ajustada"
_IOL_ADJUST_FALLBACK = "sinAjustar"
_IOL_BEARER_TTL_SECONDS = 15 * 60
_IOL_EXPIRES_SKEW_SECONDS = 120

_iol_lock = threading.Lock()
_iol_session_user: str | None = None
_iol_access_token: str | None = None
_iol_refresh_token: str | None = None
_iol_access_until_monotonic: float = 0.0


class NetworkError(Exception):
    """Raised internally when a network-level failure occurs during fetch."""


class DataError(Exception):
    """Raised internally when the fetched payload is malformed or empty."""


class IolUnauthorized(NetworkError):
    """Bearer rejected (e.g. HTTP 401); session should refresh access token."""


def clear_iol_session_cache() -> None:
    """Drop cached IOL tokens (tests or after credential rotation)."""
    global _iol_session_user, _iol_access_token, _iol_refresh_token, _iol_access_until_monotonic
    with _iol_lock:
        _iol_session_user = None
        _iol_access_token = None
        _iol_refresh_token = None
        _iol_access_until_monotonic = 0.0


@dataclass(frozen=True)
class ArFetchResult:
    """Resultado de fetch AR con traza para fetch_log."""

    rows: Optional[list[OHLCVRow]]
    trace: SymbolFetchTrace


def _new_ar_trace(
    symbol: str,
    start_date: date,
    end_date: date,
    *,
    iol_only: bool,
) -> SymbolFetchTrace:
    return SymbolFetchTrace(
        symbol=symbol,
        venue=VENUE_AR,
        start_date=start_date,
        end_date=end_date,
        iol_only=iol_only,
    )


def _expected_dates_in_range(
    start_date: date,
    end_date: date,
    expected_dates: set[date] | None,
) -> set[date]:
    if expected_dates is not None:
        return {d for d in expected_dates if start_date <= d <= end_date}
    out: set[date] = set()
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:
            out.add(d)
        d += timedelta(days=1)
    return out


def _missing_session_dates(
    rows: list[OHLCVRow],
    scope: set[date],
) -> set[date]:
    if not scope:
        return set()
    have = {r.ts for r in rows}
    return scope - have


def _merge_ar_sources(
    iol_rows: list[OHLCVRow],
    byma_rows: list[OHLCVRow],
) -> tuple[list[OHLCVRow], dict[str, int], bool]:
    """IOL gana por fecha; Byma rellena huecos. Retorna filas, conteos y si hubo mezcla."""
    by_ts_iol = {r.ts: r for r in iol_rows}
    by_ts_byma = {r.ts: r for r in byma_rows}
    byma_fill = 0
    merged = dict(by_ts_iol)
    for ts, row in by_ts_byma.items():
        if ts not in merged:
            merged[ts] = row
            byma_fill += 1
    rows_by_source = {SOURCE_IOL: len(by_ts_iol), SOURCE_BYMA: byma_fill}
    partial = byma_fill > 0 and len(by_ts_iol) > 0
    ordered = sorted(merged.values(), key=lambda r: r.ts)
    return ordered, rows_by_source, partial


def _finalize_ar_trace(
    trace: SymbolFetchTrace,
    rows: Optional[list[OHLCVRow]],
    *,
    provider: str | None,
    used_fallback: bool = False,
    partial_fallback: bool = False,
    skip_reason: str | None = None,
    rows_by_source: dict[str, int] | None = None,
) -> ArFetchResult:
    trace.provider = provider
    if rows is None:
        trace.status = FETCH_STATUS_SKIP
        trace.skip_reason = skip_reason or SKIP_CONNECTOR_RETURNED_NONE
        if rows_by_source is not None:
            apply_source_attribution(trace, rows_by_source, partial_fallback=False)
        else:
            trace.source = provider
        return ArFetchResult(rows=None, trace=trace)
    if not rows:
        trace.status = FETCH_STATUS_SKIP
        trace.skip_reason = skip_reason or SKIP_EMPTY_DATA
        if rows_by_source is not None:
            apply_source_attribution(trace, rows_by_source, partial_fallback=False)
        else:
            trace.source = provider
        return ArFetchResult(rows=[], trace=trace)
    trace.status = FETCH_STATUS_OK
    trace.rows = len(rows)
    if used_fallback or partial_fallback:
        trace.skip_reason = SKIP_FALLBACK_USED
    if rows_by_source is not None:
        apply_source_attribution(trace, rows_by_source, partial_fallback=partial_fallback)
    else:
        counts = {SOURCE_IOL: 0, SOURCE_BYMA: 0}
        if provider == SOURCE_IOL:
            counts[SOURCE_IOL] = len(rows)
        elif provider == SOURCE_BYMA:
            counts[SOURCE_BYMA] = len(rows)
        apply_source_attribution(trace, counts, partial_fallback=partial_fallback)
    return ArFetchResult(rows=rows, trace=trace)


def fetch_ar_ohlcv(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int = 30,
    *,
    iol_only: bool = False,
    iol_meter_kind: str = IOL_KIND_HISTORY,
) -> Optional[list[OHLCVRow]]:
    """Fetch daily OHLCV bars for *symbol* from IOL (primary) or Byma/yfinance (fallback)."""
    return fetch_ar_ohlcv_with_trace(
        symbol,
        start_date,
        end_date,
        timeout,
        iol_only=iol_only,
        iol_meter_kind=iol_meter_kind,
    ).rows


def fetch_ar_ohlcv_with_trace(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int = 30,
    *,
    iol_only: bool = False,
    iol_meter_kind: str = IOL_KIND_HISTORY,
    expected_dates: set[date] | None = None,
) -> ArFetchResult:
    """Como fetch_ar_ohlcv pero incluye SymbolFetchTrace para persistir en fetch_log."""
    trace = _new_ar_trace(symbol, start_date, end_date, iol_only=iol_only)
    scope = _expected_dates_in_range(start_date, end_date, expected_dates)
    iol_user = os.environ.get("IOL_USER")
    iol_pass = os.environ.get("IOL_PASS")
    iol_partial_rows: list[OHLCVRow] | None = None
    tried_iol = False

    if not iol_user or not iol_pass:
        logger.warning(
            '{"event": "fetch_skipped", "symbol": "%s", "skip_reason": "iol_credentials_missing", "provider": "iol"}',
            symbol,
        )
        if iol_only:
            trace.attempts = 0
            return _finalize_ar_trace(
                trace,
                None,
                provider=SOURCE_IOL,
                skip_reason=SKIP_CREDENTIALS_MISSING,
                rows_by_source={SOURCE_IOL: 0, SOURCE_BYMA: 0},
            )
    else:
        tried_iol = True
        result: Optional[list[OHLCVRow]] = None
        iol_skip: str | None = None
        try:
            result, iol_attempts, iol_skip = _fetch_with_retry_iol(
                symbol,
                start_date,
                end_date,
                timeout,
                iol_user,
                iol_pass,
                iol_meter_kind,
            )
            trace.attempts += iol_attempts
        except IolJobBudgetExhausted as exc:
            logger.warning(
                '{"event": "iol_fetch_skipped_budget", "symbol": "%s", "iol_only": %s, "detail": "%s"}',
                symbol,
                str(bool(iol_only)).lower(),
                str(exc),
            )
            if iol_only:
                raise
            result = None
            trace.extra["budget_detail"] = str(exc)
        else:
            if result is not None and result:
                if expected_dates is None:
                    return _finalize_ar_trace(
                        trace,
                        result,
                        provider=SOURCE_IOL,
                        skip_reason=iol_skip,
                        rows_by_source={SOURCE_IOL: len(result), SOURCE_BYMA: 0},
                    )
                missing = _missing_session_dates(result, scope)
                if not missing:
                    return _finalize_ar_trace(
                        trace,
                        result,
                        provider=SOURCE_IOL,
                        skip_reason=iol_skip,
                        rows_by_source={SOURCE_IOL: len(result), SOURCE_BYMA: 0},
                    )
                if iol_only:
                    trace.extra["unfilled_session_dates"] = sorted(
                        d.isoformat() for d in missing
                    )
                    return _finalize_ar_trace(
                        trace,
                        result,
                        provider=SOURCE_IOL,
                        skip_reason=iol_skip,
                        rows_by_source={SOURCE_IOL: len(result), SOURCE_BYMA: 0},
                    )
                iol_partial_rows = result
            # result vacío ([]) o None: IOL no aportó nada usable (sin datos,
            # data_error, o retries agotados). En vez de retornar vacío, caemos
            # al fallback Byma de abajo (salvo iol_only). IOL devuelve [] para
            # símbolos que no sirve en seriehistorica (varios CEDEARs); eso NO
            # debe tapar datos que Byma sí tiene. La atribución de fuente en
            # fetch_log mantiene el fallback visible (complicación #4).
        if iol_only:
            return _finalize_ar_trace(
                trace,
                None,
                provider=SOURCE_IOL,
                skip_reason=SKIP_MAX_RETRIES_EXCEEDED,
                rows_by_source={SOURCE_IOL: 0, SOURCE_BYMA: 0},
            )
        if iol_partial_rows is None:
            logger.info(
                '{"event": "fallback_triggered", "symbol": "%s", "skip_reason": "iol_failed_using_byma_fallback", "source": "byma_fallback"}',
                symbol,
            )
        else:
            logger.info(
                '{"event": "partial_fallback_triggered", "symbol": "%s", "iol_rows": %d, "missing_sessions": %d}',
                symbol,
                len(iol_partial_rows),
                len(_missing_session_dates(iol_partial_rows, scope)),
            )

    byma_rows, byma_attempts, byma_skip = _fetch_with_retry_byma(
        symbol, start_date, end_date, timeout
    )
    trace.attempts += byma_attempts
    used_fallback = tried_iol

    if iol_partial_rows is not None:
        if byma_rows is None:
            trace.extra["byma_fill_failed"] = True
            return _finalize_ar_trace(
                trace,
                iol_partial_rows,
                provider=SOURCE_IOL,
                partial_fallback=True,
                skip_reason=SKIP_FALLBACK_USED,
                rows_by_source={SOURCE_IOL: len(iol_partial_rows), SOURCE_BYMA: 0},
            )
        merged, counts, partial = _merge_ar_sources(iol_partial_rows, byma_rows)
        return _finalize_ar_trace(
            trace,
            merged,
            provider=SOURCE_BYMA if not partial else SOURCE_IOL,
            used_fallback=True,
            partial_fallback=partial,
            skip_reason=byma_skip,
            rows_by_source=counts,
        )

    if byma_rows is None:
        return _finalize_ar_trace(
            trace,
            None,
            provider=SOURCE_BYMA,
            skip_reason=byma_skip or SKIP_MAX_RETRIES_EXCEEDED,
            rows_by_source={SOURCE_IOL: 0, SOURCE_BYMA: 0},
        )
    return _finalize_ar_trace(
        trace,
        byma_rows,
        provider=SOURCE_BYMA,
        used_fallback=used_fallback,
        skip_reason=byma_skip,
        rows_by_source={SOURCE_IOL: 0, SOURCE_BYMA: len(byma_rows)},
    )


# ---------------------------------------------------------------------------
# IOL provider
# ---------------------------------------------------------------------------

def _fetch_with_retry_iol(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
    iol_user: str,
    iol_pass: str,
    iol_meter_kind: str,
) -> tuple[Optional[list[OHLCVRow]], int, str | None]:
    """Retry loop for IOL. Returns (rows, attempts, skip_reason_if_not_ok)."""
    last_exc: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            token = _iol_get_access_token(iol_user, iol_pass, timeout)
            rows = _iol_fetch_once(symbol, start_date, end_date, timeout, token, iol_meter_kind)
            if not rows:
                return [], attempt + 1, SKIP_EMPTY_DATA
            return rows, attempt + 1, None
        except IolJobBudgetExhausted as exc:
            logger.warning('{"event": "iol_job_budget_exhausted", "detail": "%s"}', str(exc))
            raise
        except IolUnauthorized as exc:
            last_exc = exc
            _iol_invalidate_bearer_only()
            _log_attempt_failure(symbol, attempt, "iol_unauthorized", str(exc), "iol")
        except NetworkError as exc:
            last_exc = exc
            _log_attempt_failure(symbol, attempt, "network_error", str(exc), "iol")
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BACKOFF_SECONDS[attempt])
        except DataError as exc:
            _log_skip(symbol, "data_error", str(exc), "iol")
            return [], attempt + 1, SKIP_DATA_ERROR

    _log_skip(symbol, "max_retries_exceeded", str(last_exc), "iol")
    return None, _MAX_ATTEMPTS, SKIP_MAX_RETRIES_EXCEEDED


def _iol_invalidate_bearer_only() -> None:
    """Force a refresh/login on next token request; keep refresh_token if any."""
    global _iol_access_token, _iol_access_until_monotonic
    with _iol_lock:
        _iol_access_token = None
        _iol_access_until_monotonic = 0.0


def _iol_apply_token_payload(body: dict[str, Any], session_user: str) -> str:
    """Persist tokens from /token JSON; return access_token. Must hold _iol_lock."""
    global _iol_session_user, _iol_access_token, _iol_refresh_token, _iol_access_until_monotonic
    access = body.get("access_token")
    if not access:
        raise NetworkError("IOL token response missing access_token")
    refresh = body.get("refresh_token")
    if refresh:
        _iol_refresh_token = str(refresh)
    expires_in = body.get("expires_in")
    try:
        ttl = int(expires_in) if expires_in is not None else _IOL_BEARER_TTL_SECONDS
    except (TypeError, ValueError):
        ttl = _IOL_BEARER_TTL_SECONDS
    ttl = max(60, ttl - _IOL_EXPIRES_SKEW_SECONDS)
    _iol_session_user = session_user
    _iol_access_token = str(access)
    _iol_access_until_monotonic = time.monotonic() + float(ttl)
    return _iol_access_token


def _iol_token_request(form: dict[str, str], timeout: int) -> dict[str, Any]:
    """POST /token (password or refresh grant). Raises NetworkError on failure."""
    try:
        resp = requests.post(_IOL_TOKEN_URL, data=form, timeout=timeout)
    except Exception as exc:
        raise NetworkError(f"IOL token request failed: {exc}") from exc
    if resp.status_code != 200:
        snippet = (resp.text or "")[:280]
        raise NetworkError(f"IOL token HTTP {resp.status_code}: {snippet}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise NetworkError(f"IOL token response is not JSON: {exc}") from exc
    return body


def _iol_get_access_token(iol_user: str, iol_pass: str, timeout: int) -> str:
    """Return a valid bearer, using cache, refresh_token, or password grant (per IOL docs)."""
    global _iol_session_user, _iol_access_token, _iol_refresh_token, _iol_access_until_monotonic
    with _iol_lock:
        now = time.monotonic()
        if (
            _iol_session_user == iol_user
            and _iol_access_token
            and now < _iol_access_until_monotonic
        ):
            return _iol_access_token
        refresh = _iol_refresh_token if _iol_session_user == iol_user else None

    if refresh:
        if not try_consume_iol_job_slot():
            raise IolJobBudgetExhausted("max_calls_per_job exceeded before IOL token refresh")
        try:
            body = _iol_token_request(
                {"grant_type": "refresh_token", "refresh_token": refresh},
                timeout,
            )
        except NetworkError:
            with _iol_lock:
                if _iol_session_user == iol_user and _iol_refresh_token == refresh:
                    _iol_refresh_token = None
                    _iol_access_token = None
                    _iol_access_until_monotonic = 0.0
        else:
            record_iol_call(IOL_KIND_REFRESH)
            with _iol_lock:
                now = time.monotonic()
                if (
                    _iol_session_user == iol_user
                    and _iol_access_token
                    and now < _iol_access_until_monotonic
                ):
                    return _iol_access_token
                return _iol_apply_token_payload(body, iol_user)

    if not try_consume_iol_job_slot():
        raise IolJobBudgetExhausted("max_calls_per_job exceeded before IOL token (password grant)")
    body = _iol_token_request(
        {
            "username": iol_user,
            "password": iol_pass,
            "grant_type": "password",
        },
        timeout,
    )
    record_iol_call(IOL_KIND_TOKEN)
    with _iol_lock:
        now = time.monotonic()
        if (
            _iol_session_user == iol_user
            and _iol_access_token
            and now < _iol_access_until_monotonic
        ):
            return _iol_access_token
        return _iol_apply_token_payload(body, iol_user)


def _iol_history_get(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
    token: str,
    meter_kind: str,
    ajuste: str,
) -> list[dict]:
    """Una GET a seriehistorica con un modo de ajuste. Devuelve el payload (lista,
    posiblemente vacía). Consume slot de presupuesto y registra la llamada. Raises
    NetworkError / IolUnauthorized."""
    if not try_consume_iol_job_slot():
        raise IolJobBudgetExhausted("max_calls_per_job exceeded before IOL history GET")
    url = _IOL_HISTORY_URL.format(
        mercado=_IOL_MERCADO,
        symbol=_provider_symbol(symbol),
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),  # IOL end is exclusive
        ajuste=ajuste,
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if resp.status_code == 401:
            raise IolUnauthorized("IOL history returned HTTP 401 (bearer expired or invalid)")
        if resp.status_code == 404:
            # Ticker inexistente en IOL — no reintentar como error de red.
            record_iol_call(meter_kind)
            return []
        resp.raise_for_status()
        payload = resp.json()
    except IolUnauthorized:
        raise
    except Exception as exc:
        raise NetworkError(f"IOL history request failed: {exc}") from exc

    record_iol_call(meter_kind)
    return payload or []


def _iol_fetch_once(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
    token: str,
    meter_kind: str,
) -> list[OHLCVRow]:
    """Single IOL fetch attempt. Raises NetworkError or DataError.

    Prueba 'ajustada' (acciones/CEDEARs) y, si IOL devuelve vacío, reintenta en
    'sinAjustar' (títulos públicos / bonos como AL30/GD30). Ver complicación #12 y
    docs/plan_hedge_short.md.
    """
    payload = _iol_history_get(
        symbol, start_date, end_date, timeout, token, meter_kind, _IOL_ADJUST_PRIMARY
    )
    if not payload:
        payload = _iol_history_get(
            symbol, start_date, end_date, timeout, token, meter_kind, _IOL_ADJUST_FALLBACK
        )
    if not payload:
        return []

    try:
        rows = _normalize_iol(symbol, payload)
    except Exception as exc:
        raise DataError(f"IOL normalization failed: {exc}") from exc

    return rows


# IOL no es consistente en los nombres de campos según endpoint/versión: el
# endpoint `seriehistorica` real devuelve `fechaHora` y `volumenNominal`,
# mientras otras respuestas (y fixtures antiguos) usan `fecha` y `volumen`.
# Aceptamos alias por campo (primer nombre presente gana) en vez de clavar un
# único nombre, para no romper si IOL cambia la etiqueta. Ver complicación #3
# en docs/complicaciones-tecnicas.md.
_IOL_DATE_KEYS = ("fechaHora", "fecha")
_IOL_VOLUME_KEYS = ("volumenNominal", "volumen")  # ambos = cantidad de nominales (NO montoOperado, que es $)
_IOL_OPEN_KEYS = ("apertura",)
_IOL_HIGH_KEYS = ("maximo",)
_IOL_LOW_KEYS = ("minimo",)
_IOL_CLOSE_KEYS = ("ultimoPrecio", "cierre")

_MISSING = object()


def _first_present(item: dict, keys: tuple[str, ...]) -> Any:
    """Return the value of the first key present (non-None), else _MISSING."""
    for key in keys:
        value = item.get(key)
        if value is not None:
            return value
    return _MISSING


def _normalize_iol(symbol: str, payload: list[dict]) -> list[OHLCVRow]:
    """Convert IOL JSON response to list[OHLCVRow].

    Tolera alias de nombres de campo (ver `_IOL_*_KEYS`). Si falta algún campo
    bajo todos sus alias, el error lista las keys realmente recibidas para que
    el desajuste sea diagnosticable de un vistazo (no un 'falta volumen' opaco).
    """
    field_specs = (
        ("open", _IOL_OPEN_KEYS),
        ("high", _IOL_HIGH_KEYS),
        ("low", _IOL_LOW_KEYS),
        ("close", _IOL_CLOSE_KEYS),
        ("volume", _IOL_VOLUME_KEYS),
        ("date", _IOL_DATE_KEYS),
    )
    rows: list[OHLCVRow] = []
    for item in payload:
        resolved: dict[str, Any] = {}
        missing: list[str] = []
        for field, keys in field_specs:
            value = _first_present(item, keys)
            if value is _MISSING:
                missing.append(f"{field} (tried {'/'.join(keys)})")
            else:
                resolved[field] = value
        if missing:
            raise DataError(
                "IOL item missing required field(s): "
                f"{'; '.join(missing)}. Keys received: {sorted(item.keys())}"
            )
        bar_date = date.fromisoformat(str(resolved["date"])[:10])
        rows.append(
            OHLCVRow(
                symbol=symbol,
                ts=bar_date,
                open=float(resolved["open"]),
                high=float(resolved["high"]),
                low=float(resolved["low"]),
                close=float(resolved["close"]),
                volume=float(resolved["volume"]),
                currency=_CURRENCY,
                venue=_VENUE,
                imputed=False,
            )
        )

    # Dedup por fecha: para títulos públicos (bonos) IOL devuelve varias filas por día,
    # una por plazo de liquidación (contado inmediato, 48hs). Nos quedamos con la de
    # mayor volumen por fecha (la más representativa). Para acciones/CEDEARs (una fila
    # por día) es un no-op. Salida ordenada por fecha ascendente.
    if rows:
        best: dict[date, OHLCVRow] = {}
        for r in rows:
            cur = best.get(r.ts)
            if cur is None or r.volume > cur.volume:
                best[r.ts] = r
        rows = [best[d] for d in sorted(best)]
    return rows


# ---------------------------------------------------------------------------
# Byma / yfinance fallback
# ---------------------------------------------------------------------------

# Algunos CEDEARs en BYMA/IOL no usan el ticker US en la API (p. ej. DIS → DISN).
_AR_SYMBOL_ALIASES: dict[str, str] = {
    "DIS": "DISN",
}


def _provider_symbol(symbol: str) -> str:
    """Map internal whitelist symbol to IOL/yfinance provider ticker."""
    return _AR_SYMBOL_ALIASES.get(symbol.upper(), symbol)


def _yfinance_ba_ticker(symbol: str) -> str:
    """Map internal AR symbol to Yahoo Finance .BA ticker (BYMA)."""
    return f"{_provider_symbol(symbol)}.BA"


def _fetch_with_retry_byma(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
) -> tuple[Optional[list[OHLCVRow]], int, str | None]:
    """Retry loop for Byma via yfinance. Returns (rows, attempts, skip_reason_if_not_ok)."""
    last_exc: Exception | None = None
    yf_symbol = _yfinance_ba_ticker(symbol)

    for attempt in range(_MAX_ATTEMPTS):
        try:
            rows = _byma_fetch_once(symbol, yf_symbol, start_date, end_date, timeout)
            if not rows:
                return [], attempt + 1, SKIP_EMPTY_DATA
            return rows, attempt + 1, None
        except NetworkError as exc:
            last_exc = exc
            _log_attempt_failure(symbol, attempt, "network_error", str(exc), "byma")
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BACKOFF_SECONDS[attempt])
        except DataError as exc:
            _log_skip(symbol, "data_error", str(exc), "byma")
            return [], attempt + 1, SKIP_DATA_ERROR

    _log_skip(symbol, "max_retries_exhausted_byma", str(last_exc), "byma")
    return None, _MAX_ATTEMPTS, SKIP_MAX_RETRIES_EXCEEDED


def _byma_fetch_once(
    symbol: str,
    yf_symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
) -> list[OHLCVRow]:
    """Single Byma/yfinance fetch attempt. Raises NetworkError or DataError."""
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            auto_adjust=True,
            timeout=timeout,
        )
    except Exception as exc:
        raise NetworkError(f"yfinance request failed for {yf_symbol}: {exc}") from exc

    if df is None or df.empty:
        return []

    missing = _REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise DataError(f"Missing columns in yfinance response: {missing}")

    try:
        rows = _normalize_byma(symbol, df)
    except Exception as exc:
        raise DataError(f"Byma normalization failed: {exc}") from exc

    return rows


def _normalize_byma(symbol: str, df) -> list[OHLCVRow]:
    """Convert yfinance DataFrame to list[OHLCVRow]. symbol is already stripped of .BA."""
    rows: list[OHLCVRow] = []
    for ts, row in df.iterrows():
        bar_date = ts.date() if hasattr(ts, "date") else ts
        rows.append(
            OHLCVRow(
                symbol=symbol,
                ts=bar_date,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
                currency=_CURRENCY,
                venue=_VENUE,
                imputed=False,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log_attempt_failure(symbol: str, attempt: int, reason: str, detail: str, provider: str) -> None:
    logger.warning(
        '{"event": "fetch_attempt_failed", "symbol": "%s", "attempt": %d, "reason": "%s", "provider": "%s", "detail": "%s"}',
        symbol,
        attempt + 1,
        reason,
        provider,
        detail,
    )


def _log_skip(symbol: str, skip_reason: str, detail: str, provider: str) -> None:
    logger.error(
        '{"event": "fetch_skipped", "symbol": "%s", "skip_reason": "%s", "provider": "%s", "detail": "%s"}',
        symbol,
        skip_reason,
        provider,
        detail,
    )
