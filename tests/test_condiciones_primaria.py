"""Pruebas de core/condiciones_primaria.py: filtro de balances válidos, rangos de caudal, quintiles, comparación con el
caudal nominal (ajustada) y pendientes."""

import numpy as np
import pandas as pd
import pytest

from core.condiciones_primaria import (cp_liviana_por_muestreo, datos_primarias, etiqueta_caudal, frase_nominal,
                                       frase_pendiente, hasta_nominal, pendiente, por_cuantiles, por_rango_caudal)


def _balance(azar, caida_sobre: float, n: int = 400):
    """Primarias en 2025 y 2026. Por encima de 16.000 L/h la eficiencia cae `caida_sobre` puntos; en 2026 casi todo
    va por encima. La eficiencia sube 2 puntos cada 100 L/h de fase liviana."""
    filas = []
    for temporada in ("2025", "2026"):
        for k in range(n):
            caudal = azar.uniform(13000, 19000) if temporada == "2025" else azar.uniform(15500, 20000)
            liviana = azar.uniform(150, 350)
            filas.append({"tipo": "Centrífuga primaria", "codigo": f"P{k % 2}", "equipo": f"ESE ({k % 2 + 1})",
                          "fecha_hora": pd.Timestamp(f"{temporada}-07-01") + pd.Timedelta(hours=k),
                          "fecha_produccion": pd.Timestamp(f"{temporada}-07-01") + pd.Timedelta(days=k // 20),
                          "temporada": temporada, "turno": ["M", "T", "N"][k % 3], "copiada": False,
                          "fuera_fisico": False, "caudal_emulsion": caudal, "caudal_liviana": liviana,
                          "aceite_emulsion": azar.uniform(0.7, 1.4),
                          "eficiencia_pct": 80 + 2 * (liviana - 250) / 100 - caida_sobre * (caudal > 16000)
                          + azar.normal(0, 1)})
    filas.append({**filas[0], "copiada": True, "eficiencia_pct": 10.0})            # dato inválido: no entra
    filas.append({**filas[0], "tipo": "Centrífuga secundaria"})                    # no es primaria
    return pd.DataFrame(filas)


def test_datos_primarias_solo_balances_validos_con_cp():
    balance = _balance(np.random.default_rng(1), 0, n=30)
    muestras = pd.DataFrame({"codigo": ["P0", "P0", "P1"], "fecha_hora": [balance["fecha_hora"].iloc[0]] * 2
                             + [balance["fecha_hora"].iloc[1]], "cp_liviana_bar": [1.0, 0.0, 1.2],
                             "cp_liviana_marca": [None, "no medible", None]})
    datos = datos_primarias(balance, cp_liviana_por_muestreo(muestras))
    assert len(datos) == 60 and (datos["eficiencia_pct"] > 50).all()
    assert datos["cp_liviana"].iloc[0] == 1.0 and datos["cp_liviana"].iloc[1] == 1.2      # el "no medible" no entra
    assert datos["mes"].iloc[0] == "Jul" and set(datos["aceite_rango"].dropna()) == {"1", "2", "3", "4"}


def test_rangos_de_caudal_y_cuantiles():
    assert etiqueta_caudal(15000) == "15.000–16.000"
    datos = datos_primarias(_balance(np.random.default_rng(2), 0))
    rangos = por_rango_caudal(datos)
    assert rangos["n"].sum() == len(datos) and set(rangos.loc[rangos["temporada"] == "2025", "desde"]) >= {13000, 18000}
    cuantiles = por_cuantiles(datos, "caudal_liviana")
    assert cuantiles["orden"].nunique() == 5 and cuantiles["grupo"].iloc[0].startswith("≤ ")
    medias = cuantiles[cuantiles["temporada"] == "2025"].set_index("orden")["media"]
    assert medias[5] > medias[1] + 2                                   # más fase liviana, más eficiencia


def test_hasta_el_nominal_se_repite():
    datos = datos_primarias(_balance(np.random.default_rng(3), 3))
    tabla = hasta_nominal(datos, 16000, ["equipo", "turno"]).set_index("temporada")
    assert tabla.loc["2025", "dif"] == pytest.approx(-3, abs=0.4) and tabla.loc["2025", "p"] < 0.05
    assert tabla.loc["2025", "n_hasta"] + tabla.loc["2025", "n_sobre"] == 400
    assert frase_nominal(tabla.reset_index(), 16000).startswith("Se repite")


def test_hasta_el_nominal_no_se_repite_y_pocos_datos():
    datos = datos_primarias(_balance(np.random.default_rng(4), 0))
    tabla = hasta_nominal(datos, 16000, ["equipo"], n_minimo=10)
    assert frase_nominal(tabla, 16000).startswith("No se repite")
    pocos = hasta_nominal(datos, 16000, ["equipo"], n_minimo=1000)
    assert pocos["dif"].isna().all() and frase_nominal(pocos, 16000).startswith("No hay temporadas")


def test_pendiente_ajustada():
    datos = datos_primarias(_balance(np.random.default_rng(5), 0))
    tabla = pendiente(datos, "caudal_liviana", por=100, ajuste=["equipo", "turno"])
    assert tabla["pendiente"].to_numpy() == pytest.approx([2, 2], abs=0.3) and frase_pendiente(tabla) == "sube"
    assert frase_pendiente(pendiente(datos, "aceite_emulsion", por=0.1, ajuste=["equipo"])) == "no cambia"


def test_sin_ic_con_menos_de_3_datos():
    datos = pd.DataFrame({"temporada": ["2026"] * 5, "caudal_desde": [12000, 12000, 16000, 16000, 16000],
                          "eficiencia_pct": [80.0, 60.0, 85.0, 86.0, 84.0]})
    tabla = por_rango_caudal(datos).set_index("desde")
    assert tabla.loc[12000, "n"] == 2 and pd.isna(tabla.loc[12000, "ic_inf"]) and tabla.loc[12000, "media"] == 70
    assert tabla.loc[16000, "ic_inf"] < 85 < tabla.loc[16000, "ic_sup"]
