"""Pruebas de core/programa.py con pares inventados."""

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from core.programa import (N_MINIMO_RANKING, combinaciones, de_combinaciones, frase_programa, frase_uso, grilla,
                           mejores_y_peores, numero_programa, rallado_por_programa, uso_de_programas)


def _pares(filas):
    """filas: (eboe, programa, tph, eficiencia_pct)."""
    return pd.DataFrame(filas, columns=["eboe", "programa", "tph", "eficiencia_pct"])


@pytest.fixture
def pares():
    return _pares([
        (2, "N4", 10.0, 84.0), (2, "N4", 10.0, 85.0), (2, "N4", 10.0, 86.0),     # media 85, N 3
        (2, "N7", 13.0, 74.0), (2, "N7", 13.0, 75.0), (2, "N7", 12.6, 73.0),     # 12,6 → 13: media 74, N 3
        (2, "N5", 11.0, 90.0), (2, "N5", 11.0, 92.0),                            # N 2: poco representativa
        (1, "N3", 12.0, 80.0), (1, "N3", 12.0, 82.0), (1, "N3", 12.0, 84.0),
        (1, None, 12.0, 99.0), (1, "N3", None, 99.0),                            # sin programa o sin MTPH
    ])


def test_numero_programa():
    assert numero_programa("N4") == 4 and numero_programa("N0") == 0
    assert numero_programa(None) is None and numero_programa("X4") is None and numero_programa("N") is None


def test_combinaciones(pares):
    combos = combinaciones(pares).set_index(["eboe", "programa", "mtph"])

    assert len(combos) == 4                                          # las filas sin programa o MTPH no entran
    n4 = combos.loc[(2, "N4", 10)]
    assert (n4["n"], n4["media"], n4["desvio"]) == (3, pytest.approx(85.0), pytest.approx(1.0))
    margen = stats.t.ppf(0.975, 2) * 1.0 / math.sqrt(3)
    assert (n4["ic_inf"], n4["ic_sup"]) == pytest.approx((85 - margen, 85 + margen))
    assert combos.loc[(2, "N7", 13), "n"] == 3                        # 12,6 MTPH se redondea a 13
    assert not combos.loc[(2, "N5", 11), "representativa"]
    assert combos.loc[(2, "N4", 10), "programa_num"] == 4


def test_mejores_y_peores_sin_repetir(pares):
    tabla = mejores_y_peores(combinaciones(pares), k=5, n_minimo=3)
    eboe2 = tabla[tabla["eboe"] == 2]

    # eBOE 2 tiene 2 combinaciones representativas (N5 tiene N 2): N4 es la mejor y N7 la peor
    assert eboe2[["grupo", "programa", "puesto"]].values.tolist() == [["Mejores", "N4", 1], ["Mejores", "N7", 2]]
    assert mejores_y_peores(combinaciones(pares), k=1, n_minimo=3)[["eboe", "grupo", "programa"]].values.tolist() == [
        [1, "Mejores", "N3"], [2, "Mejores", "N4"], [2, "Peores", "N7"]]


def test_grilla_solo_celdas_representativas(pares):
    mtph, programas, eficiencia, n = grilla(combinaciones(pares), 2)

    assert mtph == [10, 13] and programas == list(range(10))           # 11 MTPH solo tiene N5 con N 2
    assert eficiencia[0, 4] == pytest.approx(85.0) and n[0, 4] == 3
    assert eficiencia[1, 7] == pytest.approx(74.0)
    assert np.isnan(eficiencia[0, 5]) and np.isnan(eficiencia).sum() == 2 * 10 - 2


def test_frase_programa(pares):
    combos = combinaciones(pares)

    assert frase_programa(combos, 2, n_minimo=3) == ("El eBOE 2 rinde mejor con N4 a 10 MTPH (85,0 %, N 3) "
                                         "y peor con N7 a 13 MTPH (74,0 %, N 3).")
    assert frase_programa(combos, 1, n_minimo=3) == "El eBOE 1 tiene una sola combinación con N ≥ 3: N3 a 12 MTPH (82,0 %, N 3)."
    assert frase_programa(combos, 9, n_minimo=3) == "El eBOE 9 no tiene combinaciones de programa y MTPH con N ≥ 3."


def test_ranking_y_frase_usan_n_minimo_10_por_defecto():
    filas = [(1, "N4", 10.0, 80.0 + i % 3) for i in range(12)]          # N 12: entra
    filas += [(1, "N7", 13.0, 70.0 + i % 3) for i in range(10)]         # N 10: entra (justo)
    filas += [(1, "N9", 11.0, 95.0)] * 9                               # N 9: no entra, aunque es la mejor
    combos = combinaciones(_pares(filas))

    assert N_MINIMO_RANKING == 10
    assert set(mejores_y_peores(combos)["programa"]) == {"N4", "N7"}
    assert frase_programa(combos, 1) == ("El eBOE 1 rinde mejor con N4 a 10 MTPH (81,0 %, N 12) "
                                         "y peor con N7 a 13 MTPH (70,9 %, N 10).")
    assert combos.set_index("programa").loc["N9", "representativa"]      # en las burbujas sí se ve normal
    assert frase_programa(combinaciones(_pares(filas[-9:])), 1) == \
        "El eBOE 1 no tiene combinaciones de programa y MTPH con N ≥ 10."


def test_de_combinaciones(pares):
    elegidos = de_combinaciones(pares, [(2, "N7", 13), (1, "N3", 12)])

    assert len(elegidos) == 6
    assert set(elegidos["programa"]) == {"N7", "N3"}
    assert de_combinaciones(pares, []).empty


def test_sin_datos():
    vacio = combinaciones(_pares([]))
    assert vacio.empty and mejores_y_peores(vacio).empty
    assert grilla(vacio, 1)[0] == []


def _uso():
    """eBOE 2: 60 pares en N2–N4 (20 c/u), 10 en N8, 10 en N9, 20 en N6 → 100 pares.
    Rallado de salida: en N2–N4, 2 sobre rallados de 60; en N8–N9, 3 de 20 (1 sin rallado cargado)."""
    filas = []
    for programa, cantidad in (("N2", 20), ("N3", 20), ("N4", 20), ("N6", 20), ("N8", 10), ("N9", 10)):
        filas += [{"eboe": 2, "programa": programa, "rallado_salida": "Poco rallada"} for _ in range(cantidad)]
    datos = pd.DataFrame(filas)
    datos.loc[[0, 25], "rallado_salida"] = "Sobre rallada"              # N2 y N3
    datos.loc[[80, 81, 95], "rallado_salida"] = "Sobre rallada"         # N8, N8, N9
    datos.loc[[1, 2, 3], "rallado_salida"] = "Ok"
    datos.loc[96, "rallado_salida"] = None                               # N9 sin rallado
    return datos


def test_uso_de_programas():
    uso = uso_de_programas(_uso()).set_index("programa")

    assert uso["porcentaje"].to_dict() == pytest.approx({"N2": 20, "N3": 20, "N4": 20, "N6": 20, "N8": 10, "N9": 10})
    assert uso["n_eboe"].unique().tolist() == [100]
    assert list(uso.index) == ["N2", "N3", "N4", "N6", "N8", "N9"]          # en orden de programa


def test_rallado_por_programa():
    rallado = rallado_por_programa(_uso()).set_index("programa")

    assert rallado.loc["N2", "n"] == 20
    assert (rallado.loc["N2", "pct_sobre"], rallado.loc["N2", "pct_ok"], rallado.loc["N2", "pct_poco"]) == \
        pytest.approx((5, 15, 80))
    assert rallado.loc["N9", "n"] == 9                                       # el que no tiene rallado no cuenta
    assert rallado.loc["N9", "pct_sobre"] == pytest.approx(100 / 9)


def test_frase_uso_compara_ok_y_menciona_sobre_rallado_mayor_a_1():
    # Ok: 3 de 60 en N2–N4 (5 %), 0 de 19 en N8–N9. Sobre rallado > 1 %: N2 y N3 (1 de 20), N8 (2 de 10), N9 (1 de 9).
    assert frase_uso(_uso(), 2) == (
        "El eBOE 2 trabaja el 60 % del tiempo en N2–N4; en N8–N9 el rallado es Ok en el 0,0 % de los pares "
        "(N 19), contra 5,0 % (N 60) en N2–N4. El sobre rallado supera el 1 % en N2 (5,0 %, N 20), "
        "N3 (5,0 %, N 20), N8 (20,0 %, N 10) y N9 (11,1 %, N 9).")
    assert frase_uso(_uso(), 7) == "El eBOE 7 no tiene pares con programa."


def test_frase_uso_sin_sobre_rallado_no_lo_menciona():
    datos = _uso()
    datos.loc[datos["rallado_salida"] == "Sobre rallada", "rallado_salida"] = "Ok"
    datos.loc[0, "rallado_salida"] = "Sobre rallada"                    # un solo sobre rallado,
    datos.loc[0, "programa"] = "N6"                                      # en N6: 1 de 21 = 4,8 % → se menciona
    frase = frase_uso(datos, 2)
    assert frase.endswith("El sobre rallado supera el 1 % en N6 (4,8 %, N 21).")

    datos.loc[0, "rallado_salida"] = "Ok"                                # sin sobre rallado: no se menciona
    assert "sobre rallado" not in frase_uso(datos, 2)


def test_frase_uso_ventana_con_programas_altos():
    datos = pd.DataFrame({"eboe": [1] * 10, "programa": ["N8"] * 6 + ["N9"] * 4, "rallado_salida": ["Ok"] * 10})

    assert frase_uso(datos, 1) == "El eBOE 1 trabaja el 100 % del tiempo en N7–N9."
