"""Pruebas de core/volatilizacion.py: rangos de fase pesada, eficiencia eBOE por turno y por día, eficiencia de
proceso por día (la de Análisis › Proceso) y medias con IC por rango."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from core.volatilizacion import (asignar_rango, eficiencia_eboe_por_turno, eficiencias_por_dia, media_ic,
                                 medias_por_rango)

DIA_1, DIA_2 = pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-02")


def test_rangos_con_sus_bordes():
    especifica = asignar_rango(pd.Series([0.39, 0.4, 0.5, 0.79, 0.8, None]), "fp_especifica")
    assert especifica.tolist() == ["< 0,4", "0,4–0,5", "0,5–0,6", "0,6–0,8", "> 0,8", None]
    referencia = asignar_rango(pd.Series([-50, -40, -25.1, -10, 0, 14.9, 15]), "rango_fp_ref")
    assert referencia.tolist() == ["< −40", "−40 a −25", "−40 a −25", "−10 a 0", "0 a 15", "0 a 15", "> 15"]
    with pytest.raises(ValueError, match="desconocida"):
        asignar_rango(pd.Series([1.0]), "rango_fp_pct")         # el de la Etapa 0 no se usa contra la eficiencia


def _tabla():
    """Día 1: turno M con eficiencias 80 y 90 % (entrada 8) y fp 0,45 y 0,55; turno T con 70 % (entrada 7) y fp 0,7.
    Día 2: turno M con 85 % (entrada 7,5) y fp 0,3."""
    return pd.DataFrame({
        "temporada": [2026] * 4, "fecha_produccion": [DIA_1, DIA_1, DIA_1, DIA_2], "turno": ["M", "M", "T", "M"],
        "eboe": [1, 2, 1, 1], "eficiencia_par": [0.80, 0.90, 0.70, 0.85], "aceite_fruta": [8.0, 8.0, 7.0, 7.5],
        "aceite_salida": [1.6, 0.8, 2.1, 1.125], "tph": [11.0] * 4, "fp_especifica": [0.45, 0.55, 0.70, 0.30], "rango_fp_ref": [-30.0, -20.0, 5.0, -50.0]})


TONELADAS = pd.DataFrame({"fecha_produccion": [DIA_1, DIA_1, DIA_2], "turno": ["M", "T", "M"],
                          "fruta_t": [100.0, 300.0, 40.0]})
PRODUCCION = pd.DataFrame({"fecha_produccion": [DIA_1, DIA_1, DIA_2], "turno": ["M", "T", "M"],
                           "fruta_t": [100.0, 300.0, 40.0], "aceite_envasado_kg": [650.0, 2100.0, 300.0],
                           "jugo_conc_kg": [None, None, None]})


def test_eficiencia_eboe_por_turno():
    turnos = eficiencia_eboe_por_turno(_tabla(), "fp_especifica").set_index(["fecha_produccion", "turno"])
    manana = turnos.loc[(DIA_1, "M")]
    assert (manana["eficiencia"], manana["n"]) == (pytest.approx(85.0), 2)
    assert manana["valor"] == pytest.approx(0.50) and manana["rango"] == "0,5–0,6"      # mediana de 0,45 y 0,55
    assert turnos.loc[(DIA_2, "M"), "rango"] == "< 0,4"


def test_eficiencias_por_dia():
    dias = eficiencias_por_dia(_tabla(), "fp_especifica", TONELADAS, PRODUCCION).set_index("fecha_produccion")
    uno = dias.loc[DIA_1]
    assert uno["eficiencia_eboe"] == pytest.approx((85 * 100 + 70 * 300) / 400)     # ponderada por toneladas
    # proceso: (650 + 2100) / 400 = 6,875 kg/MT sobre la entrada ponderada (8 × 100 + 7 × 300) / 400 = 7,25
    assert uno["eficiencia_proceso"] == pytest.approx(6.875 / 7.25 * 100)
    assert uno["fruta_t"] == 400 and uno["valor"] == pytest.approx(0.55)
    assert uno["rendimiento"] == pytest.approx(6.875)                       # rendimiento en tambor (kg/MT)
    assert pd.isna(dias.loc[DIA_2, "eficiencia_proceso"])                   # 40 t: menos que el mínimo del día
    assert dias.loc[DIA_2, "eficiencia_eboe"] == pytest.approx(85.0)
    sin_produccion = eficiencias_por_dia(_tabla(), "fp_especifica", TONELADAS.iloc[0:0], PRODUCCION.iloc[0:0])
    assert sin_produccion["eficiencia_proceso"].isna().all() and not sin_produccion["ponderada"].any()


def test_media_ic_y_medias_por_rango():
    valores = [80.0, 84.0, 86.0, 90.0]
    resultado = media_ic(valores)
    margen = stats.t.ppf(0.975, 3) * np.std(valores, ddof=1) / 2
    assert (resultado["n"], resultado["media"]) == (4, 85.0)
    assert (resultado["ic_inf"], resultado["ic_sup"]) == (pytest.approx(85 - margen), pytest.approx(85 + margen))
    assert media_ic([5.0])["ic_inf"] is None and media_ic([])["media"] is None
    datos = pd.DataFrame({"temporada": [2026] * 5, "eficiencia": [70.0, 80.0, 90.0, 60.0, None],
                          "rango": ["> 0,8", "< 0,4", "< 0,4", "0,4–0,5", "0,4–0,5"]})
    tabla = medias_por_rango(datos, "eficiencia", "fp_especifica")
    assert tabla["grupo"].tolist() == ["< 0,4", "0,4–0,5", "> 0,8"]                # en el orden de los rangos
    assert tabla["n"].tolist() == [2, 1, 1] and tabla["media"].tolist() == [85.0, 60.0, 70.0]
