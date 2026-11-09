"""Pruebas de core/spc.py: constantes, cartas (ejemplo de Montgomery), fases y reglas de Nelson."""

import numpy as np
import pandas as pd
import pytest

from core.spc import (DESCRIPCION_REGLAS, c4, carta_imr, carta_xbar_r, carta_xbar_s, constantes, d2, evaluar_carta,
                      limites_entre_dentro, limites_imr, limites_xbar_s, limites_xbar_s_para, nelson, resumen_reglas, sigma_s_barra, tabla_constantes)

# Montgomery, "Introduction to Statistical Quality Control", tabla 6.3: diámetro interno de anillos de
# pistón (mm), 25 subgrupos de 5. Resultados del libro: x̿ = 74,001, R̄ = 0,023, s̄ = 0,0094.
ANILLOS = np.array([
    [74.030, 74.002, 74.019, 73.992, 74.008], [73.995, 73.992, 74.001, 74.011, 74.004],
    [73.988, 74.024, 74.021, 74.005, 74.002], [74.002, 73.996, 73.993, 74.015, 74.009],
    [73.992, 74.007, 74.015, 73.989, 74.014], [74.009, 73.994, 73.997, 73.985, 73.993],
    [73.995, 74.006, 73.994, 74.000, 74.005], [73.985, 74.003, 73.993, 74.015, 73.988],
    [74.008, 73.995, 74.009, 74.005, 74.004], [73.998, 74.000, 73.990, 74.007, 73.995],
    [73.994, 73.998, 73.994, 73.995, 73.990], [74.004, 74.000, 74.007, 74.000, 73.996],
    [73.983, 74.002, 73.998, 73.997, 74.012], [74.006, 73.967, 73.994, 74.000, 73.984],
    [74.012, 74.014, 73.998, 73.999, 74.007], [74.000, 73.984, 74.005, 73.998, 73.996],
    [73.994, 74.012, 73.986, 74.005, 74.007], [74.006, 74.010, 74.018, 74.003, 74.000],
    [73.984, 74.002, 74.003, 74.005, 73.997], [74.000, 74.010, 74.013, 74.020, 74.003],
    [73.982, 74.001, 74.015, 74.005, 73.996], [74.004, 73.999, 73.990, 74.006, 74.009],
    [74.010, 73.989, 73.990, 74.009, 74.014], [74.015, 74.008, 73.993, 74.000, 74.010],
    [73.982, 73.984, 73.995, 74.017, 74.013],
])
MEDIAS, RANGOS, DESVIOS = ANILLOS.mean(axis=1), np.ptp(ANILLOS, axis=1), ANILLOS.std(axis=1, ddof=1)


# ---------------------------------------------------------------- constantes

@pytest.mark.parametrize("n, A2, D3, D4, A3, B3, B4, d2_, c4_", [
    # Montgomery, apéndice VI (3 o 4 decimales)
    (2, 1.880, 0, 3.267, 2.659, 0, 3.267, 1.128, 0.7979),
    (5, 0.577, 0, 2.114, 1.427, 0, 2.089, 2.326, 0.9400),
    (7, 0.419, 0.076, 1.924, 1.182, 0.118, 1.882, 2.704, 0.9594),
    (10, 0.308, 0.223, 1.777, 0.975, 0.284, 1.716, 3.078, 0.9727),
    (25, 0.153, 0.459, 1.541, 0.606, 0.565, 1.435, 3.931, 0.9896),
])
def test_constantes_como_la_tabla_del_libro(n, A2, D3, D4, A3, B3, B4, d2_, c4_):
    k = constantes(n)
    for nombre, esperado in {"A2": A2, "D3": D3, "D4": D4, "A3": A3, "B3": B3, "B4": B4, "d2": d2_}.items():
        assert k[nombre] == pytest.approx(esperado, abs=0.0011), nombre
    assert k["c4"] == pytest.approx(c4_, abs=0.0001)


def test_d2_exacto_y_tabla_completa():
    assert d2(2) == pytest.approx(2 / np.sqrt(np.pi), abs=1e-6)        # valor exacto para n = 2
    assert c4(2) == pytest.approx(np.sqrt(2 / np.pi), abs=1e-12)
    tabla = tabla_constantes()
    assert list(tabla["n"]) == list(range(2, 26))
    assert list(tabla.columns[:7]) == ["n", "A2", "A3", "D3", "D4", "B3", "B4"]
    with pytest.raises(ValueError, match="entre 2 y 25"):
        constantes(1)


# ---------------------------------------------------------------- cartas

# El libro calcula con x̿ y R̄ (o s̄) ya redondeados (74,001 + 0,577 × 0,023 = 74,0143); sin redondear
# da 74,0146. Por eso contra el libro se compara a ±0,001 y contra la fórmula, exacto.
LIBRO = 0.001


def test_xbar_r_anillos_de_piston():
    carta = carta_xbar_r(MEDIAS, RANGOS, 5)

    assert carta["lc"].iloc[0] == pytest.approx(74.001, abs=LIBRO)
    assert carta["lcs"].iloc[0] == pytest.approx(74.014, abs=LIBRO)
    assert carta["lci"].iloc[0] == pytest.approx(73.988, abs=LIBRO)
    assert carta["disp_lc"].iloc[0] == pytest.approx(0.023, abs=0.0005)
    assert carta["disp_lcs"].iloc[0] == pytest.approx(0.049, abs=0.0005)
    assert carta["disp_lci"].iloc[0] == 0
    # Sin redondear: x̿ ± A2·R̄ con R̄ = 0,02324
    assert carta["lcs"].iloc[0] == pytest.approx(RANGOS.mean() * constantes(5)["A2"] + MEDIAS.mean())
    # El libro concluye que el proceso está bajo control en la fase I.
    evaluada = evaluar_carta(carta, reglas=[1])
    assert not evaluada["alguna"].any()


def test_xbar_s_anillos_de_piston():
    carta = carta_xbar_s(MEDIAS, DESVIOS, [5] * 25)

    assert carta["lcs"].iloc[0] == pytest.approx(74.014, abs=LIBRO)
    assert carta["lci"].iloc[0] == pytest.approx(73.988, abs=LIBRO)
    assert carta["disp_lc"].iloc[0] == pytest.approx(0.0094, abs=0.00005)
    assert carta["disp_lcs"].iloc[0] == pytest.approx(0.0196, abs=0.00005)
    assert carta["disp_lci"].iloc[0] == 0
    # Con tamaños iguales, X̄-S de Minitab = x̿ ± A3·s̄ y S: B4·s̄
    assert carta["lcs"].iloc[0] == pytest.approx(MEDIAS.mean() + constantes(5)["A3"] * DESVIOS.mean())
    assert carta["disp_lcs"].iloc[0] == pytest.approx(constantes(5)["B4"] * DESVIOS.mean())


def test_xbar_s_con_tamanos_distintos():
    medias, desvios, tamanos = [10.0, 12.0, 11.0], [1.0, 2.0, np.nan], [4, 2, 1]
    h = [c4(4) ** 2 / (1 - c4(4) ** 2), c4(2) ** 2 / (1 - c4(2) ** 2)]
    sigma = (h[0] * 1.0 / c4(4) + h[1] * 2.0 / c4(2)) / sum(h)
    assert sigma_s_barra(desvios, tamanos) == pytest.approx(sigma)        # el de 1 dato no entra

    carta = carta_xbar_s(medias, desvios, tamanos)
    x_dos_barras = (4 * 10 + 2 * 12 + 1 * 11) / 7                         # media de todos los datos
    assert limites_xbar_s(medias, desvios, tamanos)["lc"] == pytest.approx(x_dos_barras)
    assert carta["lcs"].tolist() == pytest.approx([x_dos_barras + 3 * sigma / np.sqrt(m) for m in tamanos])
    assert carta["sigma"].iloc[1] == pytest.approx(sigma / np.sqrt(2))
    assert carta["disp_lc"].iloc[0] == pytest.approx(c4(4) * sigma)
    assert pd.isna(carta["disp"].iloc[2]) and pd.isna(carta["disp_lc"].iloc[2])   # sin desvío ni límites S


def test_imr():
    lim = limites_imr([1.0, 3.0, 2.0, 4.0])
    mr = (2 + 1 + 2) / 3
    assert lim["mr_lc"] == pytest.approx(mr) and lim["sigma"] == pytest.approx(mr / 1.128379, abs=1e-5)
    assert lim["lcs"] == pytest.approx(2.5 + 3 * mr / 1.128379, abs=1e-5)
    assert lim["mr_lcs"] == pytest.approx(3.267 * mr, abs=0.001) and lim["mr_lci"] == 0
    with pytest.raises(ValueError, match="al menos 2"):
        limites_imr([5.0, np.nan])


def test_fase_i_y_fase_ii():
    """Los límites salen solo de la fase I y se extienden sobre la fase II."""
    corridas = np.concatenate([MEDIAS, MEDIAS[:5] + 0.02])
    base = np.arange(30) < 25
    carta = carta_xbar_r(corridas, np.concatenate([RANGOS, RANGOS[:5]]), 5, base=base)

    assert carta["lcs"].nunique() == 1
    assert carta["lcs"].iloc[-1] == pytest.approx(carta_xbar_r(MEDIAS, RANGOS, 5)["lcs"].iloc[0])
    assert carta["fase"].tolist() == ["I"] * 25 + ["II"] * 5
    resumen = resumen_reglas(evaluar_carta(carta, reglas=[1]), reglas=[1]).set_index("regla")
    assert resumen.loc[1, "n_I"] == 0 and resumen.loc[1, "n_II"] >= 4      # +0,02 mm sale de control

    imr = carta_imr([1.0, 3.0, 2.0, 10.0, 11.0], base=[True, True, True, False, False])
    assert imr["lc"].iloc[0] == pytest.approx(2.0)
    assert pd.isna(imr["disp"].iloc[3]) and imr["disp"].iloc[4] == pytest.approx(1.0)   # sin MR entre fases
    with pytest.raises(ValueError, match="no tiene puntos"):
        carta_imr([1.0, 2.0], base=[False, False])


# ---------------------------------------------------------------- reglas de Nelson (lc = 0, σ = 1)

def marcados(valores, regla, sigma=1.0):
    return [i for i, violadas in enumerate(nelson(valores, 0.0, sigma, [regla])) if violadas]


def test_regla_1_mas_de_3_sigma():
    assert marcados([0, 0, 3.5, 0, -3.2, 3.0], 1) == [2, 4]          # 3,0 justo en el límite no cuenta


def test_regla_2_nueve_del_mismo_lado():
    assert marcados([0.5] * 9, 2) == [8]
    assert marcados([0.5] * 8, 2) == []
    assert marcados([-0.3] * 10, 2) == [8, 9]
    assert marcados([0.5] * 4 + [0.0] + [0.5] * 4, 2) == []           # un punto en la línea corta la racha


def test_regla_3_seis_subiendo_o_bajando():
    assert marcados([0.0, 0.1, 0.2, 0.3, 0.4, 0.5], 3) == [5]
    assert marcados([0.5, 0.4, 0.3, 0.2, 0.1, 0.0], 3) == [5]
    assert marcados([0.0, 0.1, 0.2, 0.2, 0.3, 0.4], 3) == []          # un empate corta la racha


def test_regla_4_catorce_alternando():
    assert marcados([0.5, -0.5] * 7, 4) == [13]
    assert marcados([0.5, -0.5] * 6 + [0.5], 4) == []                 # 13 puntos


def test_regla_5_dos_de_tres_a_mas_de_2_sigma():
    assert marcados([0.0, 2.5, 0.5, 2.5], 5) == [3]
    assert marcados([2.5, 0.0, -2.5], 5) == []                        # lados distintos
    assert marcados([2.5, 2.5, 0.0], 5) == [1]                        # los 2 primeros ya cumplen "2 de 3"
    assert marcados([2.5, 0.0, 0.0, 2.5], 5) == []                    # 2 de 4: no


def test_regla_6_cuatro_de_cinco_a_mas_de_1_sigma():
    assert marcados([1.5, 1.5, 0.0, 1.5, 1.5], 6) == [4]
    assert marcados([1.5, 1.5, 0.0, 0.0, 1.5], 6) == []
    assert marcados([1.5] * 4, 6) == [3]


def test_regla_7_quince_cerca_de_la_linea():
    assert marcados([0.5, -0.5] * 7 + [0.2], 7) == [14]
    assert marcados([0.5] * 14, 7) == []


def test_regla_8_ocho_lejos_de_la_linea():
    assert marcados([1.5, -1.5] * 4, 8) == [7]
    assert marcados([1.5, -1.5] * 3 + [0.5, 1.5], 8) == []


def test_nelson_con_sigma_por_punto_y_vacios():
    # Con σ = 2 el 3,5 no está a más de 3 σ; con σ = 1, sí.
    assert nelson([3.5, 3.5], 0.0, [2.0, 1.0], [1]) == [[], [1]]
    # Los vacíos no tienen reglas y no cortan la racha de la regla 2.
    valores = [0.5] * 5 + [np.nan] + [0.5] * 4
    assert marcados(valores, 2) == [9]
    with pytest.raises(ValueError, match="mayor que cero"):
        nelson([1.0], 0.0, 0.0)
    with pytest.raises(ValueError, match="de 1 a 8"):
        nelson([1.0], 0.0, 1.0, [9])
    assert set(DESCRIPCION_REGLAS) == set(range(1, 9))


def test_evaluar_y_resumir():
    carta = carta_imr([0.0, 1.0, 0.0, 1.0, 9.0, 0.5, 0.4, 0.6], base=[True] * 4 + [False] * 4)
    evaluada = evaluar_carta(carta)

    assert 1 in evaluada["reglas"].iloc[4]                             # el 9 en fase II
    assert evaluada["fuera_disp"].iloc[5]                              # MR de 8,5
    resumen = resumen_reglas(evaluada).set_index("regla")
    assert list(resumen.index) == [1, 2, 3, 4, 5, 6, 7, 8, "dispersion", "alguna"]
    assert resumen.loc["alguna", "n_I"] == 0 and resumen.loc["alguna", "pct_I"] == 0
    assert resumen.loc[1, "n_II"] == 1 and resumen.loc[1, "pct_II"] == pytest.approx(25.0)
    assert resumen.loc["alguna", "puntos_II"] == 4

    sin_fase_ii = resumen_reglas(evaluar_carta(carta_imr([0.0, 1.0, 0.0])), reglas=[1]).set_index("regla")
    assert pd.isna(sin_fase_ii.loc[1, "n_II"]) and pd.isna(sin_fase_ii.loc[1, "pct_II"])


def test_limites_xbar_s_para_un_tamano():
    """A mano: x̿ = 85, σ = 2, n = 4 → σ/√n = 1, X̄: 82–88; c4(4) = 0,9213, √(1 − c4²) = 0,3889:
    S: LC = 1,8426, LCS = (0,9213 + 1,1666)·2 = 4,1758, LCI = 0 (c4 − 3√(1 − c4²) < 0)."""
    lim = limites_xbar_s_para(85, 2, 4)
    assert (lim["lci"], lim["lcs"], lim["sigma_media"]) == (pytest.approx(82), pytest.approx(88), pytest.approx(1))
    assert lim["s_lc"] == pytest.approx(1.8426, abs=1e-3) and lim["s_lcs"] == pytest.approx(4.1758, abs=1e-3)
    assert lim["s_lci"] == 0
    uno = limites_xbar_s_para(85, 2, 1)
    assert (uno["lcs"], uno["s_lcs"]) == (pytest.approx(91), None)          # un dato: X̄ sí, S no
    assert limites_xbar_s_para(85, 2, 9)["s_lci"] > 0                        # con n ≥ 6 la S tiene LCI
    with pytest.raises(ValueError, match="al menos 1"):
        limites_xbar_s_para(85, 2, 0)


def test_limites_entre_dentro_a_mano():
    """A mano. Promedios de 4 turnos: 10, 12, 11, 13 → MR = 2, 1, 2; MR̄ = 5/3 = 1,6667; σ_entre = 1,6667 / d2(2),
    con d2(2) = 2/√π = 1,12838 (no el 1,128 redondeado de las tablas): 1,4770; x̄̄ = 11,5; LCS = 11,5 + 3·1,4770 =
    15,931; LCI = 7,069; LCS del MR = 3,2665·1,6667 = 5,444.
    σ_dentro (S barra) con s = 1 y 2 (n = 3), s = 1,5 (n = 2) y un turno de 1 dato que no entra:
    c4(3) = 0,8862, h = 0,7854/0,2146 = 3,660; c4(2) = 0,7979, h = 0,6366/0,3634 = 1,752;
    σ = (3,660·(1 + 2)/0,8862 + 1,752·1,5/0,7979) / (2·3,660 + 1,752) = (12,389 + 3,293) / 9,072 = 1,7287."""
    lim = limites_entre_dentro([10, 12, 11, 13], [1.0, 2.0, None, 1.5], [3, 3, 1, 2])
    assert lim["lc"] == pytest.approx(11.5) and lim["sigma"] == pytest.approx(1.4770, abs=1e-4)
    assert (lim["lcs"], lim["lci"]) == (pytest.approx(15.931, abs=1e-3), pytest.approx(7.069, abs=1e-3))
    assert lim["mr_lcs"] == pytest.approx(5.444, abs=1e-3)
    assert lim["sigma_dentro"] == pytest.approx(1.7287, abs=1e-3)
    assert limites_entre_dentro([10, 12, 11], [None] * 3, [1, 1, 1])["sigma_dentro"] is None
