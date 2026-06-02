#!/usr/bin/env python3
"""Diagnóstico mínimo de auth IOL: login + un GET de serie histórica.

No imprime tokens ni contraseñas. Exit 0 si histórico responde 200.

Uso (misma terminal donde tenés las credenciales):
    python scripts/diagnose_iol_auth.py
    python scripts/diagnose_iol_auth.py --symbol GGAL --lookback 7
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.connectors.ar_connector import clear_iol_session_cache  # noqa: E402

_TOKEN_URL = "https://api.invertironline.com/token"
_HISTORY_URL = (
    "https://api.invertironline.com/api/v2/bCBA/Titulos/{symbol}/Cotizacion/"
    "seriehistorica/{start}/{end}/ajustada"
)
_PORTFOLIO_URL = "https://api.invertironline.com/api/micuenta/miportafolio"


def _mask(s: str | None, visible: int = 4) -> str:
    if not s:
        return "(vacío)"
    if len(s) <= visible:
        return "*" * len(s)
    return f"{s[:visible]}… ({len(s)} chars)"


def _post_token(username: str, password: str, timeout: int) -> tuple[int, dict | None, str]:
    try:
        resp = requests.post(
            _TOKEN_URL,
            data={
                "username": username,
                "password": password,
                "grant_type": "password",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return -1, None, str(exc)
    body = None
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body, (resp.text or "")[:400]


def _get_bearer(url: str, token: str, timeout: int) -> tuple[int, str]:
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return -1, str(exc)
    snippet = (resp.text or "")[:400]
    return resp.status_code, snippet


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico auth IOL (sin secretos en stdout).")
    parser.add_argument("--symbol", default="GGAL", help="Ticker AR en panel bCBA (default: GGAL)")
    parser.add_argument("--lookback", type=int, default=7, help="Días hacia atrás para histórico")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--skip-portfolio",
        action="store_true",
        help="No probar GET /api/micuenta/miportafolio",
    )
    args = parser.parse_args()

    user = os.environ.get("IOL_USER", "").strip()
    password = os.environ.get("IOL_PASS", "").strip()

    print("=" * 60)
    print("  Diagnostico IOL (InvertirOnline)")
    print("=" * 60)
    print(f"  IOL_USER: {_mask(user)}")
    print(f"  IOL_PASS: {_mask(password)}")
    print()

    if not user or not password:
        print("[FAIL] Faltan IOL_USER y/o IOL_PASS en el entorno de esta terminal.")
        print("   PowerShell (sesión actual):")
        print('     $env:IOL_USER = "tu_usuario"')
        print('     $env:IOL_PASS = "tu_password"')
        print("   Luego volvé a ejecutar este script.")
        return 1

    clear_iol_session_cache()

    print("1) POST /token (grant_type=password)")
    status, body, raw = _post_token(user, password, args.timeout)
    print(f"   HTTP status: {status}")
    if status != 200 or not isinstance(body, dict):
        print(f"   [FAIL] Login fallo. Respuesta (recorte): {raw!r}")
        return 1

    access = body.get("access_token")
    refresh = body.get("refresh_token")
    expires_in = body.get("expires_in")
    print(f"   access_token: {_mask(str(access) if access else None)}")
    print(f"   refresh_token: {_mask(str(refresh) if refresh else None)}")
    print(f"   expires_in: {expires_in!r}")
    if not access:
        print("   [FAIL] JSON sin access_token.")
        return 1
    print("   [OK] Login OK")
    print()

    token = str(access)

    if not args.skip_portfolio:
        print("2) GET /api/micuenta/miportafolio (smoke con bearer)")
        p_status, p_snip = _get_bearer(_PORTFOLIO_URL, token, args.timeout)
        print(f"   HTTP status: {p_status}")
        if p_status == 200:
            print("   [OK] Bearer aceptado en endpoint de cuenta")
        elif p_status == 401:
            print("   [FAIL] 401 en portafolio - bearer invalido desde el login")
            print(f"   Recorte: {p_snip!r}")
        else:
            print(f"   [WARN] Status inesperado. Recorte: {p_snip!r}")
        print()

    end = date.today()
    start = end - timedelta(days=args.lookback)
    history_url = _HISTORY_URL.format(
        symbol=args.symbol.upper(),
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
    )

    print(f"3) GET serie historica ({args.symbol}, {start} -> {end})")
    print(f"   URL: {history_url}")
    h_status, h_snip = _get_bearer(history_url, token, args.timeout)
    print(f"   HTTP status: {h_status}")
    if h_status == 200:
        print("   [OK] Historico OK - el bot deberia poder usar IOL para este simbolo/rango")
        return 0
    if h_status == 401:
        print("   [FAIL] 401 en historico - mismo sintoma que fetch_daily")
        print("      Posibles causas: cuenta sin permiso de datos, usuario distinto al de la web,")
        print("      o credenciales mal cargadas (setx / caracteres especiales).")
    else:
        print(f"   [WARN] Status {h_status}. Recorte: {h_snip!r}")
    print()
    print("4) POST /token (grant_type=refresh_token) — si hay refresh_token")
    if refresh:
        try:
            r_resp = requests.post(
                _TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": str(refresh)},
                timeout=args.timeout,
            )
            print(f"   HTTP status: {r_resp.status_code}")
            if r_resp.status_code == 200:
                print("   [OK] Refresh OK")
            else:
                print(f"   [FAIL] Refresh fallo. Recorte: {(r_resp.text or '')[:400]!r}")
        except requests.RequestException as exc:
            print(f"   [FAIL] Error de red en refresh: {exc}")
    else:
        print("   (sin refresh_token en respuesta de login)")

    return 1 if h_status != 200 else 0


if __name__ == "__main__":
    raise SystemExit(main())
