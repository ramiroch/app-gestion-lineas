"""Pruebas de core/precios.py."""

from datetime import datetime

import pytest

from core.precios import Precios, a_usd_kg, guardar_precios, leer_precios, usd


def test_unidades():
    assert a_usd_kg(12.0, "USD/kg") == 12.0
    assert a_usd_kg(2500.0, "USD/MT") == pytest.approx(2.5)
    assert a_usd_kg(None, "USD/MT") is None
    with pytest.raises(ValueError, match="Unidad de precio desconocida"):
        a_usd_kg(1.0, "USD/lb")

    assert usd(100.0, 2.5) == 250.0 and usd(100.0, None) is None


def test_guardar_y_leer(tmp_path):
    ruta = tmp_path / "precios.json"
    assert leer_precios(ruta) == Precios() and not Precios().cargados

    precios = Precios(aceite=12.0, jugo=2500.0, unidad_jugo="USD/MT")
    guardados = guardar_precios(precios, ruta, ahora=datetime(2026, 11, 5, 9, 30))
    leidos = leer_precios(ruta)
    assert leidos == guardados and leidos.modificado == "2026-11-05T09:30"
    assert leidos.cargados and (leidos.aceite_usd_kg, leidos.jugo_usd_kg) == pytest.approx((12.0, 2.5))

    with pytest.raises(ValueError, match="precio del aceite tiene que ser mayor que 0"):
        guardar_precios(Precios(aceite=-1.0), ruta)
    viejo = tmp_path / "viejo.json"                                    # un archivo con el campo que ya no existe
    viejo.write_text('{"aceite": 12.0, "brix_registrado": 66.0}', encoding="utf-8")
    assert leer_precios(viejo) == Precios(aceite=12.0)
