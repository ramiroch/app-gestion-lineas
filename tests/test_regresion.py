"""Pruebas de core/regresion.py.

Caso de libro: Montgomery y Runger, Applied Statistics and Probability for Engineers, ejemplo 11.1
(pureza de oxígeno vs nivel de hidrocarburos, n = 20): pendiente 14,947 · ordenada 74,283 · R² 0,877.
Caso a mano: x = 1…5, y = 2, 4, 5, 4, 5 → Sxy = 6, Sxx = 10 → pendiente 0,6, ordenada 2,2, R² = 3,6 / 6 = 0,6.
"""

import pandas as pd
import pytest

from core.regresion import frase_pendiente, recta, regresion_lineal, regresiones_por_grupo

HIDROCARBUROS = [0.99, 1.02, 1.15, 1.29, 1.46, 1.36, 0.87, 1.23, 1.55, 1.40,
                 1.19, 1.15, 0.98, 1.01, 1.11, 1.20, 1.26, 1.32, 1.43, 0.95]
PUREZA = [90.01, 89.05, 91.43, 93.74, 96.73, 94.45, 87.59, 91.77, 99.42, 93.65,
          93.54, 92.52, 90.56, 89.54, 89.85, 90.39, 93.25, 93.41, 94.98, 87.33]


def test_igual_que_montgomery_runger():
    r = regresion_lineal(HIDROCARBUROS, PUREZA)

    assert r["n"] == 20
    assert r["pendiente"] == pytest.approx(14.947, abs=0.001)
    assert r["ordenada"] == pytest.approx(74.283, abs=0.001)
    assert r["r2"] == pytest.approx(0.877, abs=0.001)
    assert r["p"] < 0.001
    assert r["ic_inf"] < r["pendiente"] < r["ic_sup"]


def test_caso_a_mano_y_recta():
    r = regresion_lineal([1, 2, 3, 4, 5], [2, 4, 5, 4, 5])

    assert (r["pendiente"], r["ordenada"], r["r2"]) == pytest.approx((0.6, 2.2, 0.6))
    assert recta(r, [0, 10]).tolist() == pytest.approx([2.2, 8.2])


def test_pocos_datos_o_x_constante():
    assert regresion_lineal([1, 2], [3, 4])["pendiente"] is None                 # n < 3
    assert regresion_lineal([2, 2, 2, 2], [1, 2, 3, 4])["pendiente"] is None     # x sin variación
    r = regresion_lineal([1, 2, None, 4], [2, None, 5, 8])                       # quedan 2 pares
    assert r["n"] == 2 and r["r2"] is None


def test_por_grupo_no_junta_temporadas():
    # Dentro de cada temporada la pendiente es −2; juntando las dos, el salto entre temporadas
    # haría que la pendiente común fuera positiva.
    df = pd.DataFrame({"temporada": [2025] * 4 + [2026] * 4,
                       "x": [1, 2, 3, 4, 6, 7, 8, 9],
                       "y": [10, 8, 6, 4, 30, 28, 26, 24]})

    regs = regresiones_por_grupo(df, "x", "y", ["temporada"]).set_index("temporada")

    assert regs.loc[2025, "pendiente"] == pytest.approx(-2) and regs.loc[2026, "pendiente"] == pytest.approx(-2)
    assert regresion_lineal(df["x"], df["y"])["pendiente"] > 0


def test_por_dos_grupos_y_columna_inexistente():
    df = pd.DataFrame({"eboe": [1, 1, 1, 2, 2, 2], "temporada": [2025] * 6,
                       "x": [1, 2, 3, 1, 2, 3], "y": [1, 2, 3, 3, 2, 1]})

    regs = regresiones_por_grupo(df, "x", "y", ["eboe", "temporada"])

    assert regs[["eboe", "temporada"]].values.tolist() == [[1, 2025], [2, 2025]]
    assert regs["pendiente"].tolist() == pytest.approx([1, -1])
    with pytest.raises(ValueError, match='No existe la columna "z"'):
        regresiones_por_grupo(df, "z", "y", ["eboe"])


def test_frase_pendiente():
    r = {"n": 800, "pendiente": -3.21, "ordenada": 88.0, "r2": 0.118, "p": 0.0001, "ic_inf": -4.0, "ic_sup": -2.4}

    frase = frase_pendiente(r, "2026", "1 %", "de aceite en emulsión", "puntos", "de eficiencia")

    assert frase == ("Dentro de 2026, cada 1 % más de aceite en emulsión se asocia con 3,2 puntos menos de "
                     "eficiencia (R² = 0,12: explica el 12 % de la variación; p < 0,001).")


def test_frase_no_significativa_y_sin_datos():
    r = {"n": 30, "pendiente": 0.4, "ordenada": 80.0, "r2": 0.01, "p": 0.6, "ic_inf": -1.0, "ic_sup": 1.8}
    assert frase_pendiente(r, "2025", "1 m³/h", "de caudal", "puntos", "de eficiencia").endswith(
        "0,4 puntos más de eficiencia (R² = 0,01: explica el 1 % de la variación; p = 0,600), "
        "pero la relación no es significativa.")
    vacio = regresion_lineal([1], [2])
    assert frase_pendiente(vacio, "2025", "1 %", "x", "puntos", "y") == \
        "Dentro de 2025 no hay datos suficientes para la recta (N = 1)."
    fila = regresiones_por_grupo(pd.DataFrame({"g": [1, 1], "x": [1, 2], "y": [3, 4]}), "x", "y", ["g"]).iloc[0]
    assert frase_pendiente(fila, "2025", "1 %", "x", "puntos", "y").startswith("Dentro de 2025 no hay datos")
