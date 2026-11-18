"""Pruebas de core/emulsion.py: zonas °Brix–pH, dilución entre el BOE y las centrífugas (ponderada por el caudal de
fase pesada o simple) y % de muestras sobre la mediana de la temporada."""

import pandas as pd
import pytest

from core.emulsion import (ENSUCIAMIENTO, FRUTA_ROTA, NORMAL, SEPARACION, dilucion_por_turno, pct_sobre_mediana,
                           resumen_dilucion, resumen_zonas, zona_emulsion, zonas)


@pytest.mark.parametrize("brix, ph, zona", [(6.0, 4.0, FRUTA_ROTA), (6.0, 6.0, ENSUCIAMIENTO), (6.0, 6.5, ENSUCIAMIENTO),
                                            (4.0, 6.5, SEPARACION), (4.0, 6.0, NORMAL), (5.0, 5.0, NORMAL),
                                            (None, 5.0, None)])
def test_zona_emulsion(brix, ph, zona):
    assert zona_emulsion(brix, ph, brix_maximo=5.0, ph_aviso=6.0) == zona


def test_resumen_de_zonas():
    emulsion = pd.DataFrame({"temporada": [2026] * 4, "eboe": [1, 1, 1, 2], "brix": [4.0, 6.0, 4.0, None],
                             "ph": [5.0, 4.5, 6.5, 6.0]})
    tabla = resumen_zonas(zonas(emulsion, 5.0, 6.0), ["temporada", "eboe"]).set_index(["eboe", "zona"])
    assert tabla.loc[(1, NORMAL), "n"] == 1 and tabla.loc[(1, FRUTA_ROTA), "pct"] == pytest.approx(100 / 3)
    assert 2 not in tabla.index.get_level_values("eboe")                       # sin °Brix: no tiene zona


def _datos():
    dia = pd.Timestamp("2026-07-01")
    muestras = pd.DataFrame({"tipo": ["Centrífuga primaria"] * 3, "fase": ["emulsion"] * 3,
                             "fecha_produccion": [dia] * 3, "turno": ["M", "M", "T"], "aceite_pct": [0.9, 1.1, 0.8],
                             "copiada": [False, False, False]})
    emulsion = pd.DataFrame({"fecha_produccion": [dia] * 4, "turno": ["M", "M", "T", "T"], "eboe": [1, 2, 1, 2],
                             "aceite_emulsion": [1.0, 1.6, 1.0, 1.2], "temporada": [2026] * 4})
    analisis = pd.DataFrame({"lugar": ["Entrada"] * 4, "fecha_produccion": [dia] * 4, "turno": ["M", "M", "T", "T"],
                             "eboe": [1, 2, 1, 2], "caudal_fase_pesada": [6.0, 2.0, 6.0, None]})
    return muestras, emulsion, analisis


def test_dilucion_ponderada_por_el_caudal_de_fase_pesada():
    turnos = dilucion_por_turno(*_datos()).set_index("turno")
    # M: alimentación 1,0 (mediana de 0,9 y 1,1); BOE (1,0 × 6 + 1,6 × 2) / 8 = 1,15 → 0,87
    assert turnos.loc["M", "boe"] == pytest.approx(1.15) and turnos.loc["M", "ponderada"]
    assert turnos.loc["M", "dilucion"] == pytest.approx(1.0 / 1.15)
    # T: falta el caudal de un eBOE → promedio simple (1,1) y se aclara
    assert turnos.loc["T", "boe"] == pytest.approx(1.1) and not turnos.loc["T", "ponderada"]
    resumen = resumen_dilucion(turnos.reset_index(), ["temporada"])
    assert resumen["n"].iloc[0] == 2 and resumen["pct_menor_1"].iloc[0] == 100 and not resumen["ponderada"].iloc[0]


def test_dilucion_sin_las_copiadas():
    muestras, emulsion, analisis = _datos()
    muestras.loc[1, "copiada"] = True                       # la de 1,1 no entra: alimentación del M = 0,9
    turnos = dilucion_por_turno(muestras, emulsion, analisis).set_index("turno")
    assert turnos.loc["M", "alimentacion"] == pytest.approx(0.9)


def test_pct_sobre_la_mediana_de_la_temporada():
    emulsion = pd.DataFrame({"temporada": [2026] * 6, "eboe": [1, 1, 1, 2, 2, 2],
                             "fecha_produccion": pd.to_datetime(["2026-07-01"] * 3 + ["2026-08-01"] * 3),
                             "aceite_emulsion": [0.8, 0.9, 1.0, 1.1, 1.2, 1.3]})       # mediana 1,05
    por_eboe = pct_sobre_mediana(emulsion, ["temporada", "eboe"]).set_index("eboe")
    assert (por_eboe.loc[1, "pct"], por_eboe.loc[2, "pct"]) == (0, 100)
    por_mes = pct_sobre_mediana(emulsion, ["temporada", "mes"]).set_index("mes")
    assert por_mes.loc["Ago 2026", "pct"] == 100 and por_mes.loc["Jul 2026", "n"] == 3
    assert por_eboe["mediana_temporada"].iloc[0] == pytest.approx(1.05)
