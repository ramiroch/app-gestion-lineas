"""Pruebas de core/proceso.py con datos inventados."""

from datetime import date

import pandas as pd
import pytest

from core.analisis import Filtros
from core.proceso import comparar_resumenes, filtrar_produccion, resumen_proceso

DIA = pd.Timestamp(2025, 7, 1)


def _pares():
    """eBOE 1: un par en el turno M (100 MT) y uno en T (300 MT); eBOE 2: un par en N (sin toneladas)."""
    return pd.DataFrame({
        "eboe": [1, 1, 2], "fecha_produccion": [DIA] * 3, "turno": ["M", "T", "N"],
        "aceite_fruta": [8.0, 10.0, 9.0], "aceite_salida": [2.0, 1.0, 1.8],
        "eficiencia_pct": [75.0, 90.0, 80.0], "tph": [10.0, 12.0, None],
    })


def _toneladas():
    return pd.DataFrame({"fecha_produccion": [DIA, DIA], "turno": ["M", "T"], "fruta_t": [100.0, 300.0]})


def _produccion():
    return pd.DataFrame({
        "fecha_produccion": [DIA, DIA, DIA, pd.Timestamp(2025, 7, 2)], "turno": ["M", "T", "N", "M"],
        "proceso_id": [1, 1, 1, 2], "temporada": [2025] * 4,
        "fruta_t": [100.0, 300.0, 100.0, 0.0], "aceite_envasado_kg": [500.0, 1500.0, None, 50.0],
    })


def test_resumen_por_eboe_ponderado():
    eboe1 = resumen_proceso(_pares(), _toneladas(), _produccion())["eboes"][0]

    assert eboe1["n"] == 2
    assert eboe1["eficiencia"] == pytest.approx((75 * 100 + 90 * 300) / 400)      # 86,25
    assert eboe1["aceite_entrada"] == pytest.approx((8 * 100 + 10 * 300) / 400)   # 9,5
    assert eboe1["aceite_salida"] == pytest.approx((2 * 100 + 1 * 300) / 400)     # 1,25
    assert eboe1["mtph"] == pytest.approx(11.0) and eboe1["ponderada"] is True


def test_totales_del_periodo():
    totales = resumen_proceso(_pares(), _toneladas(), _produccion())["totales"]

    # El par del turno N no tiene toneladas: la eficiencia global pondera M y T.
    assert totales["eficiencia"] == pytest.approx(86.25) and totales["n_pares"] == 3
    assert totales["fruta_t"] == pytest.approx(500.0) and totales["n_turnos"] == 3       # el de 0 MT no cuenta
    assert totales["aceite_envasado_kg"] == pytest.approx(2050.0)
    # Rendimiento con los turnos que tienen fruta y aceite: (500 + 1500) / (100 + 300) = 5 kg/MT
    assert totales["rendimiento"] == pytest.approx(5.0) and totales["n_turnos_rendimiento"] == 2
    assert totales["eficiencia_proceso"] == pytest.approx(5.0 / 9.5 * 100)


def test_sin_produccion():
    vacia = _produccion().iloc[0:0]
    totales = resumen_proceso(_pares(), _toneladas().iloc[0:0], vacia)["totales"]

    assert totales["ponderada"] is False and totales["eficiencia"] == pytest.approx((75 + 90 + 80) / 3)
    assert totales["fruta_t"] is None and totales["rendimiento"] is None and totales["eficiencia_proceso"] is None


def test_filtrar_produccion():
    produccion = _produccion()

    assert len(filtrar_produccion(produccion, Filtros())) == 4
    assert len(filtrar_produccion(produccion, Filtros(desde=date(2025, 7, 2)))) == 1
    assert len(filtrar_produccion(produccion, Filtros(turnos=["M"]))) == 2
    assert len(filtrar_produccion(produccion, Filtros(procesos=[2]))) == 1
    assert len(filtrar_produccion(produccion, Filtros(eboes=[1], programas=["N3"]))) == 4   # no le aplican


def test_comparar_resumenes():
    a = resumen_proceso(_pares(), _toneladas(), _produccion())
    pares_b = _pares().assign(eficiencia_pct=[70.0, 85.0, 80.0], aceite_fruta=[9.0, 11.0, 9.0],
                              aceite_salida=[1.0, 0.5, 1.8])
    b = resumen_proceso(pares_b, _toneladas(), _produccion())

    tabla = comparar_resumenes(a, b).set_index(["seccion", "clave"])

    eficiencia = tabla.loc[("Totales", "eficiencia")]
    assert eficiencia["diferencia"] == pytest.approx(-5.0) and eficiencia["evaluacion"] == "empeora"
    assert tabla.loc[("Totales", "aceite_entrada"), "evaluacion"] == "mejora"          # subió: mejor fruta
    assert tabla.loc[("Totales", "fruta_t"), "evaluacion"] == "igual"                  # misma producción
    assert tabla.loc[("eBOE 1", "aceite_salida"), "evaluacion"] == "mejora"            # bajó: mejora
    assert tabla.loc[("Totales", "rendimiento"), "evaluacion"] == "igual"
    assert list(tabla.index.get_level_values(0).unique()) == ["Totales", "eBOE 1", "eBOE 2"]


def test_comparar_con_valor_faltante():
    a = resumen_proceso(_pares(), _toneladas(), _produccion())
    b = resumen_proceso(_pares(), _toneladas(), _produccion().iloc[0:0])

    fila = comparar_resumenes(a, b).set_index(["seccion", "clave"]).loc[("Totales", "rendimiento")]
    assert pd.isna(fila["diferencia"]) and pd.isna(fila["evaluacion"])       # en el DataFrame, vacío = NaN
