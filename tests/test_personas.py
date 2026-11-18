"""Pruebas de core/personas.py (nombres inventados): anonimización, medias ajustadas por condiciones de trabajo, grupos
con pocos datos, combinaciones y lectura del operador y el químico."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from core.models import LecturaOperacion
from core.personas import (N_MINIMO, anonimizar, datos_eboe, datos_primaria, lectura_por_quimico, medias_por_grupo,
                           operadores_eboe, turno_por_temporada,
                           personas_centrifugas)
from tests.test_carga_scott import sesion  # noqa: F401  (fixture)
from tests.test_inicio import _muestra, _ronda, planta  # noqa: F401  (fixture)


def test_anonimizar_por_cantidad_de_datos():
    datos = pd.DataFrame({"operador": ["Pérez", "Gómez", "Gómez", "Gómez", "Pérez", None, "Ruiz"],
                          "analista": ["Lía", "Lía", "Ana", "Lía", "Ana", "Ana", "Ana"]})
    anonimos = anonimizar(datos, ["operador", "analista"])
    assert anonimos["operador"].tolist()[:5] == ["Operador B", "Operador A", "Operador A", "Operador A", "Operador B"]
    assert pd.isna(anonimos["operador"].iloc[5]) and anonimos["operador"].iloc[6] == "Operador C"
    assert anonimos["analista"].tolist() == ["Químico B", "Químico B", "Químico A", "Químico B", "Químico A",
                                             "Químico A", "Químico A"]
    assert "Pérez" not in anonimos.to_string() and datos["operador"].iloc[0] == "Pérez"     # no toca el original


def _pares(azar):
    """Operador X trabaja casi siempre en el eBOE 2 (3 puntos peor); a igual eBOE, X y Y rinden igual. Z tiene pocos
    datos."""
    filas = []
    for operador, eboes, n in (("X", ["2"] * 9 + ["1"], 200), ("Y", ["1"] * 9 + ["2"], 200), ("Z", ["1"], 10)):
        for k in range(n):
            eboe = eboes[k % len(eboes)]
            filas.append({"operador": operador, "eboe": eboe, "turno": ["M", "T"][k % 2], "temporada": "2026",
                          "programa_grupo": "N4–N5", "mtph_rango": "≤ 11",
                          "eficiencia_pct": 85 - 3 * (eboe == "2") + azar.normal(0, 0.5)})
    return pd.DataFrame(filas)


def test_medias_ajustadas_por_condiciones_de_trabajo():
    datos = _pares(np.random.default_rng(1))
    crudo = datos.groupby("operador")["eficiencia_pct"].mean()
    assert crudo["Y"] - crudo["X"] > 2                                # sin ajustar, X parece peor
    resultado = medias_por_grupo(datos, "eficiencia_pct", ["operador"], ["eboe", "turno", "temporada"])
    medias = resultado["medias"].set_index("grupo")
    assert set(medias.index) == {"X", "Y"} and resultado["excluidos"] == 1     # Z: N = 10 < 20
    assert medias.loc["X", "media"] == pytest.approx(medias.loc["Y", "media"], abs=0.3)   # a igual eBOE, iguales
    assert medias.loc["X", "n"] == 200 and medias["estimable"].all()
    combinado = medias_por_grupo(datos, "eficiencia_pct", ["operador", "turno"], ["eboe", "turno"])
    assert set(combinado["medias"]["grupo"]) == {"X × M", "X × T", "Y × M", "Y × T"}     # el turno no se ajusta dos veces
    assert medias_por_grupo(datos[datos["operador"] == "X"], "eficiencia_pct", ["operador"], ["eboe"])["n"] == 0


def test_datos_eboe_y_primaria():
    pares = pd.DataFrame({"eboe": ["1", "2"], "fecha_hora": pd.to_datetime(["2026-07-01 06:10"] * 2),
                          "analista": ["  Ana  María ", None], "eficiencia_pct": [85.0, 80.0]})
    operadores = pd.DataFrame({"eboe": [1, 2], "fecha_hora": pd.to_datetime(["2026-07-01 06:10"] * 2),
                               "operador": ["Pérez", " "]})
    unidos = datos_eboe(pares, operadores)
    assert unidos["operador"].tolist()[0] == "Pérez" and unidos["operador"].isna().tolist()[1]
    assert unidos["analista"].tolist()[0] == "Ana María"
    balance = pd.DataFrame({"codigo": ["C1", "C1", "C2", "C2"], "tipo": ["Centrífuga primaria"] * 3 + ["Pulidora"],
                            "fecha_hora": pd.to_datetime(["2026-07-01 06:30", "2026-07-01 08:30",
                                                          "2026-07-01 06:30", "2026-07-01 06:30"]),
                            "equipo": ["ESE (1)", "ESE (1)", "ESE (2)", "OSD"], "temporada": 2026, "turno": "M",
                            "eficiencia_pct": [84.0, 90.0, 80.0, None], "copiada": [False, True, False, False],
                            "fuera_fisico": [False, False, False, False]})
    personas = pd.DataFrame({"codigo": ["C1", "C2"], "fecha_hora": pd.to_datetime(["2026-07-01 06:30"] * 2),
                             "analista": ["Ana", "Lía"], "operador_centrifugas": ["Sosa", "Sosa"]})
    primaria = datos_primaria(balance, personas)
    assert primaria["eficiencia_pct"].tolist() == [84.0, 80.0]                  # sin la copiada ni la pulidora
    assert primaria["operador_centrifugas"].tolist() == ["Sosa", "Sosa"] and primaria["temporada"].iloc[0] == "2026"


def test_lee_operador_y_quimico(sesion, planta):  # noqa: F811
    _ronda(sesion, datetime(2026, 7, 1, 6, 10))
    for lectura in sesion.scalars(select(LecturaOperacion)):
        lectura.operador = "Pérez"
    sesion.commit()
    operadores = operadores_eboe(sesion, planta.id)
    assert set(operadores["eboe"]) == {1, 2, 3} and set(operadores["operador"]) == {"Pérez"}
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 6, 30), "emulsion", ml=20.0, analista="Lía", operador="Sosa")
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 6, 30), "descarga", ml=10.0, analista="Lía", operador="Sosa")
    personas = personas_centrifugas(sesion, planta.id)
    assert personas[["codigo", "analista", "operador_centrifugas"]].values.tolist() == [["N-CEN-01", "Lía", "Sosa"]]
    assert N_MINIMO == 20


@pytest.fixture
def base_con_operadores(sesion, planta, tmp_path, monkeypatch):  # noqa: F811
    """20 rondas con operador (rotan entre los eBOE: Pérez y Gómez) y los parámetros de fase pesada, copiadas a un
    archivo para la app (APP_DB). Devuelve una función que fija mostrar_personas y arma el AppTest."""
    import sqlite3
    from datetime import timedelta
    from pathlib import Path

    import streamlit as st
    from streamlit.testing.v1 import AppTest

    for k in range(20):
        _ronda(sesion, datetime(2026, 7, 1, 6, 10) + timedelta(hours=2 * k), salida_ml={1: 10.0 + k % 5})
    for lectura in sesion.scalars(select(LecturaOperacion)):
        lectura.operador = "Pérez" if (lectura.equipo.numero + lectura.fecha_hora.hour // 2) % 2 else "Gómez"
    planta.omega_licor_objetivo_pct, planta.omega_fp_pct = 0.9, 0.1
    planta.caudal_aspersores_lh, planta.eficiencia_ref_rango_pct = 350.0, 85.0
    carpeta_app = Path(__file__).resolve().parent.parent / "app"
    monkeypatch.chdir(carpeta_app)

    def armar(mostrar: bool):
        planta.mostrar_personas = mostrar
        sesion.commit()
        base = tmp_path / f"personas_{mostrar}.db"
        destino = sqlite3.connect(base)
        sesion.connection().connection.driver_connection.backup(destino)
        destino.close()
        monkeypatch.setenv("APP_DB", str(base))
        st.cache_resource.clear()
        st.cache_data.clear()
        return AppTest.from_file(str(carpeta_app / "app.py"), default_timeout=300)

    yield armar
    st.cache_resource.clear()
    st.cache_data.clear()


def _sistema(app, presentacion=False):
    app.run()
    if presentacion:
        app.query_params["presentacion"] = "1"
        app.session_state["modo_presentacion"] = True
    app.switch_page("pages/11_Sistema.py")
    app.query_params["pestana_sistema"] = "Equipos de trabajo"
    return app.run()


def test_equipos_de_trabajo_solo_con_permiso_y_anonimizado(base_con_operadores):
    sin_permiso = _sistema(base_con_operadores(False))
    assert not sin_permiso.exception and "Equipos de trabajo" not in [t.label for t in sin_permiso.tabs]
    con_permiso = _sistema(base_con_operadores(True))
    assert not con_permiso.exception and "Equipos de trabajo" in [t.label for t in con_permiso.tabs]
    textos = " ".join(str(t.value) for t in con_permiso.table) + " ".join(m.value for m in con_permiso.markdown)
    assert "Operador A" in textos and "Pérez" not in textos and "Gómez" not in textos     # anonimizado por defecto
    assert any("no evalúa personas" in i.value for i in con_permiso.info)
    assert not [b for b in con_permiso.get("download_button")]                            # nada para descargar
    en_presentacion = _sistema(base_con_operadores(True), presentacion=True)
    assert "Equipos de trabajo" not in [t.label for t in en_presentacion.tabs]           # nunca en presentación


def test_turno_por_temporada():
    azar = np.random.default_rng(4)
    filas = []
    for temporada in ("2025", "2026"):
        for k in range(300):
            turno = ["M", "T", "N"][k % 3]
            caida = 3 if (temporada == "2026" and turno == "T") else 0          # el T, abajo solo en 2026
            filas.append({"temporada": temporada, "turno": turno, "eboe": ["1", "2"][k % 2],
                          "eficiencia_pct": 85 - caida + azar.normal(0, 0.5)})
    tabla = turno_por_temporada(pd.DataFrame(filas), "eficiencia_pct", ["eboe", "temporada", "turno"]).set_index(
        ["temporada", "turno"])
    assert tabla.index.get_level_values("turno")[:3].tolist() == ["M", "T", "N"]          # en el orden del día
    assert tabla.loc[("2026", "M"), "media"] - tabla.loc[("2026", "T"), "media"] == pytest.approx(3, abs=0.3)
    assert abs(tabla.loc[("2025", "M"), "media"] - tabla.loc[("2025", "T"), "media"]) < 0.3
    assert tabla.loc[("2025", "T"), "n"] == 100


def test_lectura_por_quimico_marca_posible_sesgo():
    """Lía lee la entrada 0,6 kg/MT más alta con la misma fruta (misma semana, eBOE y operación); la salida, igual."""
    azar = np.random.default_rng(9)
    filas = []
    for k in range(600):
        quimico = ["Ana", "Lía", "Sol"][k % 3]
        semana = pd.Timestamp("2026-07-06") + pd.Timedelta(days=7 * (k % 8))
        fruta = 8 + 0.3 * (k % 8)                                           # cada semana, otra fruta
        filas.append({"analista": quimico, "fecha_produccion": semana, "eboe": ["1", "2"][k % 2],
                      "programa_grupo": "N4–N5", "mtph_rango": "≤ 11",
                      "aceite_fruta": fruta + 0.6 * (quimico == "Lía") + azar.normal(0, 0.2),
                      "aceite_salida": 1.2 + azar.normal(0, 0.1)})
    tabla = lectura_por_quimico(pd.DataFrame(filas)).set_index("quimico")
    assert tabla.loc["Lía", "lectura"] == "posible sesgo de medición (entrada)"
    assert tabla.loc["Lía", "dif_entrada"] == pytest.approx(0.6, abs=0.1)              # contra la lectura mediana
    assert tabla.loc["Ana", "lectura"] == "sin diferencias de lectura" and tabla.loc["Ana", "n"] == 200
