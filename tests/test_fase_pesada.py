"""Pruebas de core/fase_pesada.py: balance de fase pesada del eBOE (a mano, contra la planilla de LEDESMA y con valores
inválidos), rango de fase pesada y medianas por turno."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from core.fase_pesada import caudal_fp_balance, mediana_por, rango_fase_pesada, tabla_fase_pesada

PLANILLA_2025 = Path(__file__).resolve().parent.parent / "data" / "historico" / "eficiencia eBOE Naranja 25 2.xlsm"
PARAMETROS = {"omega_licor_objetivo_pct": 0.9, "omega_fp_pct": 0.1, "caudal_aspersores_lh": 350.0,
              "eficiencia_ref_rango_pct": 85.0}


def test_balance_a_mano():
    # 10 MTPH × 7 kg/MT = 70 kg/h de aceite; licor 1 %, fase pesada sin aceite, sin aspersores:
    # 70 × 0,99 / (0,997 × 0,01) = 6.950,9 L/h
    assert caudal_fp_balance(10, 7, 1.0, 0.0, 0) == pytest.approx(70 * 0.99 / (0.997 * 0.01) / 1000)
    assert caudal_fp_balance(10, 7, 1.0, 0.0, 0) == pytest.approx(6.9509, abs=1e-4)
    # con 1.000 L/h de aspersores y 0,2 % de aceite en la fase pesada (densidad 0,852 × 0,002 + 0,997 × 0,998)
    esperado = (70 * 0.99 - 1000 * 0.997 * 0.01) / ((0.852 * 0.002 + 0.997 * 0.998) * (0.01 - 0.002)) / 1000
    assert caudal_fp_balance(10, 7, 1.0, 0.2, 1000) == pytest.approx(esperado)
    assert esperado == pytest.approx(7.4407, abs=1e-4)
    assert caudal_fp_balance(1, 0.1, 1.0, 0.0, 5000) < 0             # el agua de los aspersores ya alcanza


@pytest.mark.parametrize("argumentos, error", [
    ((10, 7, 0.1, 0.1, 350), "mayor que el de la fase pesada"),
    ((10, 7, 0.05, 0.1, 350), "mayor que el de la fase pesada"),
    ((0, 7, 0.9, 0.1, 350), "mayores que 0"),
    ((-1, 7, 0.9, 0.1, 350), "negativo"),
    ((10, -7, 0.9, 0.1, 350), "negativo"),
    ((10, 7, 0.9, -0.1, 350), "negativo"),
    ((10, 7, 0.9, 0.1, -350), "negativo"),
    ((10, None, 0.9, 0.1, 350), "Falta"),
    ((10, 7, 100, 0.1, 350), "menor que 100"),
])
def test_balance_con_valores_invalidos(argumentos, error):
    with pytest.raises(ValueError, match=error):
        caudal_fp_balance(*argumentos)


@pytest.mark.skipif(not PLANILLA_2025.exists(), reason="Falta la planilla de LEDESMA 2025 (data/ no se sube).")
def test_contra_la_planilla_de_ledesma():
    """5 filas de Entrada de la columna "Caudal fase pesada calc." (T): la planilla omite (1 − ω_licor) y usa 0,15 %
    de aceite en la densidad, así que se acepta hasta 2 % de diferencia. Los datos se leen del archivo al correr: no
    quedan en el código."""
    import openpyxl
    libro = openpyxl.load_workbook(PLANILLA_2025, read_only=True, data_only=True)
    filas = []
    for fila in libro["BOE"].iter_rows(min_row=4, values_only=True):
        # R = Ton/H (17), T = caudal calc. (19), U = lugar (20), AC = aceite en fruta (28), AD = eficiencia (29)
        if fila[20] == "Entrada" and isinstance(fila[19], (int, float)) and fila[17]:
            filas.append(fila)
        if len(filas) == 5:
            break
    libro.close()
    assert len(filas) == 5
    for fila in filas:
        nuestro = caudal_fp_balance(fila[17], fila[28] * fila[29], 0.9, 0.1, 350)
        assert nuestro == pytest.approx(fila[19], rel=0.02)
        assert nuestro < fila[19]                                       # sin (1 − ω_licor) la planilla da más


def test_rango_de_fase_pesada():
    assert rango_fase_pesada(8.0, 10.0) == pytest.approx(-20.0)
    assert rango_fase_pesada(11.5, 10.0) == pytest.approx(15.0)
    assert rango_fase_pesada(None, 10.0) is None and rango_fase_pesada(8.0, None) is None
    assert rango_fase_pesada(8.0, 0.0) is None and rango_fase_pesada(8.0, -1.0) is None   # sin referencia


def _pares():
    """Tres pares del turno M (eBOE 1 y 2) y uno del T; el último sin MTPH."""
    return pd.DataFrame({
        "eboe": [1, 2, 1, 1], "temporada": [2026] * 4,
        "fecha_hora": pd.to_datetime(["2026-07-01 06:10", "2026-07-01 06:10", "2026-07-01 08:10", "2026-07-01 14:10"]),
        "fecha_produccion": pd.to_datetime(["2026-07-01"] * 4), "turno": ["M", "M", "M", "T"],
        "aceite_fruta": [8.0, 8.0, 7.0, 7.0], "eficiencia_par": [0.85, 0.80, 0.90, 0.90],
        "tph": [11.0, 12.0, 11.0, None], "caudal_fase_pesada": [9.0, 6.0, 10.0, 8.0],
        "aceite_emulsion": [1.2, 0.7, None, 1.0]})


def test_tabla_y_medianas_por_turno():
    tabla = tabla_fase_pesada(_pares(), PARAMETROS)
    primera = tabla.iloc[0]
    requerido = caudal_fp_balance(11.0, 8.0 * 0.85, 0.9, 0.1, 350)
    assert primera["removido"] == pytest.approx(6.8)
    assert primera["requerido"] == pytest.approx(requerido)
    assert primera["coherente"] == pytest.approx(caudal_fp_balance(11.0, 6.8, 1.2, 0.1, 350))   # con el licor medido
    assert primera["rango_fp_pct"] == pytest.approx((9.0 - requerido) / requerido * 100)
    assert pd.isna(tabla.iloc[2]["coherente"]) and not pd.isna(tabla.iloc[2]["rango_fp_pct"])   # sin emulsión
    assert pd.isna(tabla.iloc[3]["requerido"]) and pd.isna(tabla.iloc[3]["rango_fp_pct"])        # sin MTPH
    turnos = mediana_por(tabla, ["fecha_produccion", "turno"])
    assert turnos["turno"].tolist() == ["M"] and turnos["n"].tolist() == [3]          # el T no tiene rango
    assert turnos["rango_fp_pct"].iloc[0] == pytest.approx(tabla["rango_fp_pct"].iloc[:3].median())
    assert mediana_por(tabla.iloc[3:], ["temporada"]).empty


def test_rango_de_referencia_y_fase_pesada_especifica():
    """Sin circularidad: el rango de referencia usa la eficiencia fija de la planta (85 %), no la del análisis."""
    tabla = tabla_fase_pesada(_pares(), PARAMETROS)
    primera, segunda = tabla.iloc[0], tabla.iloc[1]
    requerido_ref = caudal_fp_balance(11.0, 8.0 * 0.85, 0.9, 0.1, 350)
    assert primera["requerido_ref"] == pytest.approx(requerido_ref)
    assert primera["rango_fp_ref"] == pytest.approx((9.0 - requerido_ref) / requerido_ref * 100)
    assert primera["rango_fp_ref"] == pytest.approx(primera["rango_fp_pct"])     # su eficiencia ya es 85 %
    assert segunda["rango_fp_ref"] != pytest.approx(segunda["rango_fp_pct"])     # con 80 % no
    otra = tabla_fase_pesada(_pares().assign(eficiencia_par=0.5), PARAMETROS)     # cambia la eficiencia...
    assert otra["rango_fp_ref"].tolist()[:3] == pytest.approx(tabla["rango_fp_ref"].tolist()[:3])   # ...y el ref no
    assert tabla["fp_especifica"].tolist()[:3] == pytest.approx([9.0 / 11.0, 6.0 / 12.0, 10.0 / 11.0])
    assert pd.isna(tabla.iloc[3]["fp_especifica"]) and pd.isna(tabla.iloc[3]["rango_fp_ref"])    # sin MTPH
