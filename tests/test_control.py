"""Pruebas de core/control.py con datos inventados."""

import numpy as np
import pandas as pd
import pytest

from core.control import (POR_MUESTRA, POR_TURNO, carta_de_control, frase_control, muestras_de,
                          subgrupos_por_turno)
from core.spc import resumen_reglas


def _fruta():
    """eBOE 1: 2 pares (turno T del 1/07) y una entrada sin salida (turno N, a las 02:00 del 2/07)."""
    horas = pd.to_datetime(["2025-07-01 14:00", "2025-07-01 14:00", "2025-07-01 16:00", "2025-07-01 16:00",
                            "2025-07-02 02:00"])
    return pd.DataFrame({
        "fecha_hora": horas, "fecha_produccion": pd.to_datetime(["2025-07-01"] * 5), "turno": ["T"] * 4 + ["N"],
        "temporada": [2025] * 5, "proceso": [3] * 5, "programa": ["N3", "N3", "N4", "N4", "N4"],
        "tph": [10.0, 10.0, 12.0, 12.0, 11.0], "eboe": [1] * 5,
        "lugar": ["Entrada", "Salida", "Entrada", "Salida", "Entrada"],
        "aceite_fruta": [8.0, 1.6, 9.0, 0.9, 7.5], "eficiencia_par": [0.8, None, 0.9, None, None],
    })


def _emulsion():
    return _fruta().iloc[[0, 2]].drop(columns=["lugar", "aceite_fruta", "eficiencia_par"]).assign(
        aceite_emulsion=[1.1, None])


def test_muestras_de_cada_variable():
    fruta, emulsion = _fruta(), _emulsion()

    assert muestras_de(fruta, emulsion, "eficiencia")["valor"].tolist() == pytest.approx([80.0, 90.0])
    assert muestras_de(fruta, emulsion, "aceite_entrada")["valor"].tolist() == [8.0, 9.0, 7.5]
    assert muestras_de(fruta, emulsion, "aceite_salida")["valor"].tolist() == [1.6, 0.9]
    assert muestras_de(fruta, emulsion, "aceite_emulsion")["valor"].tolist() == [1.1]     # el vacío no entra
    with pytest.raises(ValueError, match="Variable desconocida"):
        muestras_de(fruta, emulsion, "brix")


def test_subgrupos_por_turno():
    subgrupos = subgrupos_por_turno(muestras_de(_fruta(), _emulsion(), "aceite_entrada"))

    assert subgrupos["turno"].tolist() == ["T", "N"]                  # el N (de madrugada) va después
    t = subgrupos.iloc[0]
    assert (t["n"], t["media"], t["rango"]) == (2, 8.5, 1.0)
    assert t["desvio"] == pytest.approx(np.std([8.0, 9.0], ddof=1))
    assert (t["programa"], t["proceso"], t["mtph"]) == ("N3, N4", "3", 11.0)
    assert subgrupos.iloc[1]["n"] == 1 and pd.isna(subgrupos.iloc[1]["desvio"])


def _muestras_dos_temporadas(tamanos_2026=(3, 3, 3)):
    """eBOE 2: 4 turnos de 3 muestras en 2025 y los de 2026 corridos +5 (salen de control)."""
    filas = []
    for temporada, tamanos, corrimiento in ((2025, (3, 3, 3, 3), 0.0), (2026, tamanos_2026, 5.0)):
        for dia, n in enumerate(tamanos, start=1):
            fecha = pd.Timestamp(temporada, 7, dia)
            for k in range(n):
                filas.append({"fecha_hora": fecha + pd.Timedelta(hours=6 + k), "fecha_produccion": fecha,
                              "turno": "M", "temporada": temporada, "proceso": 1, "programa": "N3", "tph": 10.0,
                              "eboe": 2, "valor": 80.0 + corrimiento + [-1.0, 0.0, 1.0, 0.5][k % 4] + dia * 0.1})
    return pd.DataFrame(filas)


def test_carta_por_turno_xbar_r_con_fases():
    carta = carta_de_control(_muestras_dos_temporadas(), POR_TURNO, fases=(2025, 2026), reglas=[1])
    tabla = carta["tabla"]

    assert (carta["tipo"], carta["dispersion"]) == ("X̄-R", "R")
    assert tabla["punto"].tolist() == list(range(1, 8))
    assert tabla["fase"].tolist() == ["I"] * 4 + ["II"] * 3
    assert tabla["lc"].iloc[-1] == pytest.approx(tabla.loc[tabla["fase"] == "I", "valor"].mean())   # límites de 2025
    assert tabla.loc[tabla["fase"] == "II", "alguna"].all()


def test_carta_por_turno_xbar_s_y_errores():
    carta = carta_de_control(_muestras_dos_temporadas((2, 3, 1)), POR_TURNO, fases=(2025, 2026))
    assert carta["tipo"] == "X̄-S" and carta["tabla"]["n"].tolist() == [3, 3, 3, 3, 2, 3, 1]

    with pytest.raises(ValueError, match="2024 no tiene datos"):
        carta_de_control(_muestras_dos_temporadas(), POR_TURNO, fases=(2024, 2026))
    with pytest.raises(ValueError, match="un solo eBOE"):
        carta_de_control(_muestras_dos_temporadas().assign(eboe=[1, 2] * 10 + [1]), POR_TURNO)
    una_por_turno = _muestras_dos_temporadas().drop_duplicates(["fecha_produccion", "turno"])
    with pytest.raises(ValueError, match="I-MR"):
        carta_de_control(una_por_turno, POR_TURNO)


def test_carta_por_muestra_imr_sin_fases():
    carta = carta_de_control(_muestras_dos_temporadas(), POR_MUESTRA)

    assert (carta["tipo"], carta["dispersion"]) == ("I-MR", "MR")
    assert len(carta["tabla"]) == 21 and set(carta["tabla"]["fase"]) == {"I"}
    assert carta["tabla"]["mtph"].iloc[0] == 10.0 and carta["tabla"]["n"].iloc[0] == 1


def test_frases():
    fases = (2025, 2026)
    carta = carta_de_control(_muestras_dos_temporadas(), POR_TURNO, fases=fases, reglas=[1])
    resumen = resumen_reglas(carta["tabla"], reglas=[1])
    assert frase_control(resumen, 2, POR_TURNO, fases) == (
        "En 2026, el 100 % de los turnos del eBOE 2 violan alguna regla, contra el 0 % en 2025 "
        "(con los límites de 2025).")

    solo_2025 = carta_de_control(_muestras_dos_temporadas(), POR_MUESTRA, reglas=[1])
    assert frase_control(resumen_reglas(solo_2025["tabla"], [1]), 2, POR_MUESTRA).startswith(
        "En el período filtrado, el 14 % de las muestras del eBOE 2 violan")   # 3 de 21

    sin_2026 = _muestras_dos_temporadas().query("temporada == 2025")
    carta = carta_de_control(sin_2026, POR_TURNO, fases=fases, reglas=[1])
    assert frase_control(resumen_reglas(carta["tabla"], [1]), 2, POR_TURNO, fases).startswith(
        "No hay turnos de 2026")
