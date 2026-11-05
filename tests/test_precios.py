"""Pruebas de core/precios.py."""

from datetime import datetime

import pytest

from core.precios import Precios, a_usd_kg, guardar_precios, kg_a_65_brix, leer_precios, usd


def test_unidades_y_brix():
    assert a_usd_kg(12.0, "USD/kg") == 12.0
    assert a_usd_kg(2500.0, "USD/MT") == pytest.approx(2.5)
    assert a_usd_kg(None, "USD/MT") is None
    with pytest.raises(ValueError, match="Unidad de precio desconocida"):
        a_usd_kg(1.0, "USD/lb")

    assert kg_a_65_brix(1000.0) == 1000.0
    assert kg_a_65_brix(1000.0, 66.0) == pytest.approx(1015.38, abs=0.01)      # 66 °Brix rinde más kg a 65
    with pytest.raises(ValueError, match="mayor que 0"):
        kg_a_65_brix(1000.0, 0)
    assert usd(100.0, 2.5) == 250.0 and usd(100.0, None) is None


def test_guardar_y_leer(tmp_path):
    ruta = tmp_path / "precios.json"
    assert leer_precios(ruta) == Precios() and not Precios().cargados

    precios = Precios(aceite=12.0, jugo=2500.0, unidad_jugo="USD/MT", brix_registrado=66.0)
    guardados = guardar_precios(precios, ruta, ahora=datetime(2026, 11, 5, 9, 30))
    leidos = leer_precios(ruta)
    assert leidos == guardados and leidos.modificado == "2026-11-05T09:30"
    assert leidos.cargados and (leidos.aceite_usd_kg, leidos.jugo_usd_kg) == pytest.approx((12.0, 2.5))

    with pytest.raises(ValueError, match="precio del aceite tiene que ser mayor que 0"):
        guardar_precios(Precios(aceite=-1.0), ruta)
    with pytest.raises(ValueError, match="°Brix"):
        guardar_precios(Precios(brix_registrado=0), ruta)
