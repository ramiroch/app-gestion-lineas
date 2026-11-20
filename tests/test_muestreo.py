"""Pruebas de core/muestreo.py: pares entrada/salida con el peso medio del fruto, prueba pareada por temporada y
eBOE, aviso con la tolerancia de la planta y eficiencia según el calibre de la salida."""

import numpy as np
import pandas as pd
import pytest

from core.muestreo import (AVISO, diferencia_por_eboe, eficiencia_por_calibre, frase_muestreo, grupo_calibre, pares_calibre,
                           prueba_pareada)


def _tabla(azar, agrandar: dict, n: int = 60) -> pd.DataFrame:
    """Análisis de entrada y salida de 2 eBOE en 2026. La fruta de la salida de cada eBOE pesa `agrandar[eboe]` veces
    la de la entrada (más un ruido chico)."""
    filas = []
    for eboe in (1, 2):
        for k in range(n):
            hora = pd.Timestamp("2026-07-01 05:10") + pd.Timedelta(hours=2 * k)
            peso = azar.uniform(180, 240)                      # g por fruto de la entrada
            frutos = 48
            comunes = {"eboe": eboe, "fecha_hora": hora, "fecha_produccion": hora.normalize(), "turno": "M",
                       "temporada": 2026, "eficiencia_par": 0.82, "entrada_repetida": False}
            filas.append({**comunes, "lugar": "Entrada", "n_frutos": frutos, "peso_fruta_kg": peso * frutos / 1000})
            salida = peso * agrandar[eboe] * (1 + azar.normal(0, 0.01))
            filas.append({**comunes, "lugar": "Salida", "n_frutos": frutos, "peso_fruta_kg": salida * frutos / 1000})
    return pd.DataFrame(filas)


def test_pares_calibre_peso_medio_y_errores_de_carga():
    tabla = pd.DataFrame({"eboe": [1, 1, 2, 2, 3], "fecha_hora": [pd.Timestamp("2026-07-01 05:10")] * 5,
                          "fecha_produccion": [pd.Timestamp("2026-07-01")] * 5, "turno": ["M"] * 5,
                          "temporada": [2026] * 5, "lugar": ["Entrada", "Salida", "Entrada", "Salida", "Entrada"],
                          "n_frutos": [50, 40, 50, 700, 50], "peso_fruta_kg": [10.0, 10.0, 10.0, 10.0, 10.0],
                          "eficiencia_par": [0.8, 0.8, 0.9, 0.9, None]})
    pares = pares_calibre(tabla).set_index("eboe")
    assert len(pares) == 2                                   # el eBOE 3 no tiene salida
    assert pares.loc[1, "peso_entrada_g"] == 200 and pares.loc[1, "peso_salida_g"] == 250
    assert pares.loc[1, "frutos_entrada"] == 50 and pares.loc[1, "dif_peso_pct"] == pytest.approx(25)
    assert pares.loc[1, "eficiencia_pct"] == pytest.approx(80) and not pares.loc[1, "error_carga"]
    assert pares.loc[2, "error_carga"]                       # 700 frutos en 10 kg: error de carga (se marca)


def test_prueba_pareada_y_aviso():
    pares = pares_calibre(_tabla(np.random.default_rng(1), {1: 1.08, 2: 1.0}))
    tabla = prueba_pareada(pares, "peso", tolerancia_pct=5).set_index("eboe")
    assert tabla.loc[1, "dif_pct"] == pytest.approx(8, abs=0.5) and tabla.loc[1, "p"] < 0.001
    assert tabla.loc[1, "no_comparable"] and tabla.loc[1, "pct_fuera"] > 90
    assert abs(tabla.loc[2, "dif_pct"]) < 1 and not tabla.loc[2, "no_comparable"] and tabla.loc[2, "p"] > 0.01
    assert tabla.loc[1, "prueba"] in ("t pareada", "Wilcoxon") and tabla.loc[1, "n"] == 60
    frutos = prueba_pareada(pares, "frutos", tolerancia_pct=5).set_index("eboe")
    assert frutos.loc[1, "dif"] < 0                          # fruta más grande: menos frutos en 10 kg
    frase = frase_muestreo(tabla.reset_index(), 5)
    assert frase.startswith("En 1 de 2 combinaciones") and "2026 eBOE 1 (+" in frase and AVISO in frase


def test_sin_tolerancia_no_avisa():
    pares = pares_calibre(_tabla(np.random.default_rng(2), {1: 1.08, 2: 1.0}))
    tabla = prueba_pareada(pares, "peso")
    assert tabla["no_comparable"].isna().all() and "a definir" in frase_muestreo(tabla, None)
    with pytest.raises(ValueError, match="Variable desconocida"):
        prueba_pareada(pares, "calibre")


def test_eficiencia_por_calibre():
    assert grupo_calibre(pd.Series([-6.0, 0.0, 6.0, None]), 5).tolist()[:3] == ["Salida más chica", "Comparable",
                                                                                 "Salida más grande"]
    pares = pares_calibre(_tabla(np.random.default_rng(3), {1: 1.08, 2: 1.0}))
    pares.loc[pares["eboe"] == 1, "eficiencia_pct"] = 85.0 + np.random.default_rng(4).normal(0, 1, 60)
    tabla = eficiencia_por_calibre(pares, 5)
    assert tabla["grupo"].tolist() == ["Comparable", "Salida más grande"]
    assert tabla.set_index("grupo").loc["Salida más grande", "media"] == pytest.approx(85, abs=0.5)


def test_diferencia_por_eboe_sin_errores_de_carga():
    pares = pares_calibre(_tabla(np.random.default_rng(5), {1: 1.08, 2: 1.0}))
    pares.loc[0, ["error_carga", "dif_peso_pct"]] = [True, 900.0]          # marcado: no entra
    tabla = diferencia_por_eboe(pares).set_index("grupo")
    assert tabla.loc["1", "n"] == 59 and tabla.loc["1", "media"] == pytest.approx(8, abs=0.5)
    assert tabla.loc["2", "ic_inf"] < 0 < tabla.loc["2", "ic_sup"]
