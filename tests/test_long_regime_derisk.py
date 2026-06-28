"""Tests de comportamiento del trigger de des-riesgo por régimen (SDD long-regime-derisk, Fase 1)."""

from __future__ import annotations

from core_sim.long_regime_derisk import (
    RegimeDeriskConfig,
    regime_derisk_config_from_policy_dict,
    regime_exposure,
    rolling_percentile,
    validate_regime_derisk_config,
    vix_regime_exposure,
)


def _cfg(**over) -> RegimeDeriskConfig:
    base = dict(enabled=True, confirm_dd=-0.08, full_dd=-0.20, exposure_floor=0.40,
                breadth_min=4, breadth_total=6, velocity_max=-0.06)
    base.update(over)
    return RegimeDeriskConfig(**base)


# --- el flag apaga todo ---

def test_disabled_keeps_full_exposure_even_in_a_crash():
    cfg = _cfg(enabled=False)
    e = regime_exposure(dd_ar=-0.30, dd_global=-0.30, n_book_down=6,
                        recent_global_return=-0.20, cfg=cfg)
    assert e == 1.0


# --- el gate de 4 condiciones: la mejora que pidió el usuario ---

def test_two_factors_down_alone_is_NOT_a_global_crash():
    # GGAL y SPY caídos profundo, PERO sin amplitud ni velocidad → NO es crash → sin des-riesgo.
    cfg = _cfg()
    e = regime_exposure(dd_ar=-0.15, dd_global=-0.15, n_book_down=2,
                        recent_global_return=0.0, cfg=cfg)
    assert e == 1.0


def test_missing_breadth_is_not_a_crash():
    # Todo salvo amplitud (solo 2 nombres caídos) → no es crash.
    cfg = _cfg()
    e = regime_exposure(dd_ar=-0.15, dd_global=-0.15, n_book_down=2,
                        recent_global_return=-0.10, cfg=cfg)
    assert e == 1.0


def test_missing_velocity_is_not_a_crash():
    # Todo salvo velocidad (caída lenta, retorno reciente plano) → no es crash.
    cfg = _cfg()
    e = regime_exposure(dd_ar=-0.15, dd_global=-0.15, n_book_down=5,
                        recent_global_return=0.0, cfg=cfg)
    assert e == 1.0


# --- con las 4 condiciones, des-riesga progresivamente ---

def test_all_four_conditions_de_risk_progressively():
    cfg = _cfg()
    common = dict(n_book_down=5, recent_global_return=-0.10, cfg=cfg)
    e_shallow = regime_exposure(dd_ar=-0.08, dd_global=-0.08, **common)  # recién abre el gate
    e_mid = regime_exposure(dd_ar=-0.14, dd_global=-0.14, **common)
    e_full = regime_exposure(dd_ar=-0.20, dd_global=-0.20, **common)
    e_deeper = regime_exposure(dd_ar=-0.30, dd_global=-0.30, **common)
    assert abs(e_shallow - 1.0) < 1e-9          # sin salto al abrir el gate
    assert e_full <= e_mid <= e_shallow          # monótona: más profundo → menos exposición
    assert abs(e_full - cfg.exposure_floor) < 1e-9
    assert abs(e_deeper - cfg.exposure_floor) < 1e-9  # no baja del piso


def test_severity_uses_the_shallower_factor():
    # severity = min(|dd_ar|, |dd_global|): si un factor está poco caído, no se des-riesga fuerte
    # (ambos tienen que estar profundos para que sea grave).
    cfg = _cfg()
    e = regime_exposure(dd_ar=-0.20, dd_global=-0.08, n_book_down=5,
                        recent_global_return=-0.10, cfg=cfg)
    assert abs(e - 1.0) < 1e-9  # el global solo a -0.08 → severity=0.08 → recién abre, e=1


# --- validación de params ---

def test_validate_rejects_full_shallower_than_confirm():
    cfg = _cfg(confirm_dd=-0.10, full_dd=-0.05)  # full más shallow que confirm
    raised = False
    try:
        validate_regime_derisk_config(cfg)
    except ValueError:
        raised = True
    assert raised


def test_config_from_policy_defaults_to_disabled():
    cfg = regime_derisk_config_from_policy_dict(None)
    assert cfg.enabled is False
    assert cfg.exposure_floor == 0.40
    validate_regime_derisk_config(cfg)


# --- modo VIX (percentil del medidor de miedo) ---

def test_rolling_percentile_places_value_in_its_window():
    window = [10, 20, 30, 40, 50]
    assert rolling_percentile(window, 50) == 1.0     # el más alto
    assert rolling_percentile(window, 30) == 0.6     # 3 de 5 son <= 30
    assert rolling_percentile(window, 10) == 0.2
    assert rolling_percentile([], 99) == 0.0          # ventana vacía → sin de-risk


def test_vix_regime_exposure_dims_with_fear():
    kw = dict(start_pct=0.70, full_pct=0.95, e_min=0.40)
    assert vix_regime_exposure(0.50, **kw) == 1.0     # calma → a fondo
    assert vix_regime_exposure(0.70, **kw) == 1.0     # recién en el umbral
    assert abs(vix_regime_exposure(0.825, **kw) - 0.70) < 1e-9  # punto medio
    assert vix_regime_exposure(0.95, **kw) == 0.40    # caos → piso
    assert vix_regime_exposure(0.99, **kw) == 0.40    # no baja del piso
    # monótona: más miedo → menos exposición
    assert (vix_regime_exposure(0.95, **kw) <= vix_regime_exposure(0.825, **kw)
            <= vix_regime_exposure(0.70, **kw))


def test_validate_rejects_bad_vix_percentiles():
    cfg = RegimeDeriskConfig(signal="vix", vix_start_pct=0.9, vix_full_pct=0.8)  # start >= full
    raised = False
    try:
        validate_regime_derisk_config(cfg)
    except ValueError:
        raised = True
    assert raised
