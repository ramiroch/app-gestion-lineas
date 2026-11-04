"""Pruebas de core/estadistica.py con números inventados.

Valores de referencia de Minitab (Estadística descriptiva) para los datos 1 a 10:
media 5,5 · EE media 0,957 · desvío 3,028 · Q1 2,75 · mediana 5,5 · Q3 8,25.
"""

import math

import numpy as np
import pandas as pd
import pytest

from core.estadistica import (ancho_de_clase, cuartiles, curva_normal, es_outlier_iqr, limites_iqr, media_ponderada_por_turno,
                              resumen_descriptivo)


def test_resumen_igual_que_minitab():
    r = resumen_descriptivo(range(1, 11))

    assert r["n"] == 10
    assert r["media"] == pytest.approx(5.5)
    assert r["error_estandar"] == pytest.approx(0.957, abs=0.001)
    assert r["desvio"] == pytest.approx(3.028, abs=0.001)
    assert (r["minimo"], r["q1"], r["mediana"], r["q3"], r["maximo"]) == pytest.approx((1, 2.75, 5.5, 8.25, 10))


def test_cuartiles_metodo_minitab_no_el_de_numpy_por_defecto():
    # n = 7: posición de Q1 = (7 + 1)·0,25 = 2 → 2.º valor; el método lineal de NumPy daría 2,5
    assert cuartiles([1, 2, 3, 4, 5, 6, 7]) == pytest.approx((2, 4, 6))
    assert cuartiles([]) is None


def test_resumen_ignora_vacios_y_casos_chicos():
    assert resumen_descriptivo([4.0, None, float("nan")])["n"] == 1
    uno = resumen_descriptivo([4.0])
    assert uno["media"] == 4.0 and uno["desvio"] is None and uno["error_estandar"] is None
    assert resumen_descriptivo([]) == {"n": 0, "media": None, "error_estandar": None, "desvio": None,
                                       "minimo": None, "q1": None, "mediana": None, "q3": None, "maximo": None}


def test_limites_iqr_y_outliers():
    valores = list(range(1, 11)) + [30]          # n = 11: Q1 = 3.º valor, Q3 = 9.º valor
    r = limites_iqr(valores)

    assert (r["q1"], r["q3"], r["iqr"]) == pytest.approx((3, 9, 6))
    assert (r["li"], r["ls"]) == pytest.approx((-6, 18))
    assert r["outliers"] == 1
    assert (r["bigote_inferior"], r["bigote_superior"]) == (1, 10)   # el bigote llega al último valor dentro
    assert es_outlier_iqr(valores).tolist() == [False] * 10 + [True]


def test_limites_iqr_valida_y_vacio():
    with pytest.raises(ValueError, match="mayor que 0"):
        limites_iqr([1, 2, 3], k=0)
    assert limites_iqr([])["outliers"] == 0
    assert es_outlier_iqr(pd.Series([], dtype=float)).empty


def test_ancho_de_clase_freedman_diaconis():
    # 1 a 8: Q1 = 2,25 y Q3 = 6,75 (Minitab) → IQR 4,5; n^(1/3) = 2 → 2 × 4,5 / 2 = 4,5
    assert ancho_de_clase(range(1, 9)) == pytest.approx(4.5)
    assert ancho_de_clase([5.0]) is None
    assert ancho_de_clase([3.0, 3.0, 3.0, 3.0]) is None       # IQR 0


def test_curva_normal():
    x, y = curva_normal(media=8.0, desvio=1.0, desde=3.0, hasta=13.0, puntos=1001)

    assert y.max() == pytest.approx(1 / math.sqrt(2 * math.pi), rel=1e-4)    # pico en la media
    assert np.trapezoid(y, x) == pytest.approx(1.0, abs=1e-3)                # área total ≈ 1
    with pytest.raises(ValueError, match="desvío"):
        curva_normal(8.0, 0, 3.0, 13.0)


def _datos():
    dia = pd.Timestamp(2025, 9, 1)
    return pd.DataFrame({"fecha_produccion": [dia] * 5, "turno": ["M", "M", "T", "N", "N"],
                         "eficiencia": [0.7, 0.9, 0.6, 0.5, None]})


def test_media_ponderada_por_toneladas_del_turno():
    dia = pd.Timestamp(2025, 9, 1)
    toneladas = pd.DataFrame({"fecha_produccion": [dia, dia], "turno": ["M", "T"], "fruta_t": [100.0, 300.0]})

    r = media_ponderada_por_turno(_datos(), "eficiencia", toneladas)

    # M: media 0,8 × 100 t; T: 0,6 × 300 t; N no tiene toneladas → (80 + 180) / 400
    assert r["ponderada"] is True
    assert r["valor"] == pytest.approx(0.65)
    assert r["media_simple"] == pytest.approx((0.7 + 0.9 + 0.6 + 0.5) / 4)
    assert (r["n"], r["n_ponderados"]) == (4, 3)


def test_sin_toneladas_usa_el_promedio_simple():
    vacia = pd.DataFrame({"fecha_produccion": pd.to_datetime([]), "turno": [], "fruta_t": []})

    r = media_ponderada_por_turno(_datos(), "eficiencia", vacia)

    assert r["ponderada"] is False
    assert r["valor"] == pytest.approx(0.675)
    assert r["n_ponderados"] == 0
