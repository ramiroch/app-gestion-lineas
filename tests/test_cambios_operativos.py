"""Pruebas de core/cambios_operativos.py: carga, edición y borrado con historial, validaciones, ubicación en los
períodos de un gráfico y comparación antes/después (con la prueba que corresponde)."""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from core.cambios import historial
from core.cambios_operativos import (antes_despues, borrar_cambio, comparar, crear_cambio, editar_cambio,
                                     equipos_de_planta, listar_cambios, marcas, periodo_de,
                                     texto_cambio)
from core.models import Cliente, Equipo, Linea, Planta
from tests.test_carga_scott import sesion  # noqa: F401  (fixture)
from tests.test_inicio import _ronda, planta  # noqa: F401  (fixture)

AHORA = datetime(2026, 7, 20, 10, 0)


def _equipo(sesion, codigo):  # noqa: F811
    return sesion.scalars(select(Equipo).where(Equipo.codigo == codigo)).one()


def test_crear_editar_y_borrar_con_historial(sesion, planta):  # noqa: F811
    eboe = _equipo(sesion, "N-BOE-02")
    cambio = crear_cambio(sesion, planta.id, datetime(2026, 7, 2, 3, 30), "programa", "  N5 → N7  ", "Ana", AHORA,
                          equipo_id=eboe.id)
    lista = listar_cambios(sesion, planta.id)
    assert len(lista) == 1 and lista["descripcion"].iloc[0] == "N5 → N7" and lista["equipo"].iloc[0] == "eBOE 2"
    assert (lista["fecha_produccion"].iloc[0], lista["turno"].iloc[0]) == (pd.Timestamp("2026-07-01"), "N")  # 03:30
    assert listar_cambios(sesion, planta.id, desde=date(2026, 7, 2)).empty
    assert (lista["origen"].iloc[0], lista["fecha_aproximada"].iloc[0]) == ("informado por planta", False)
    detectado = crear_cambio(sesion, planta.id, datetime(2026, 7, 4, 5, 0), "centrífugas", "vuelve a una",
                             "Ana", AHORA, origen="detectado en datos", fecha_aproximada=True)
    texto = texto_cambio(listar_cambios(sesion, planta.id).iloc[1])
    assert "04/07/2026 (fecha aproximada)" in texto and "[detectado en datos]" in texto
    with pytest.raises(ValueError, match="Origen"):
        crear_cambio(sesion, planta.id, datetime(2026, 7, 4, 5, 0), "otro", "x", "Ana", AHORA, origen="rumor")
    borrar_cambio(sesion, detectado.id, "prueba", "Ana", AHORA)
    assert editar_cambio(sesion, cambio.id, {"descripcion": "N5 → N8", "categoria": "programa"}, "error de tipeo",
                         "Beto", AHORA) == 1                                  # la categoría no cambió
    registro = historial(sesion, "cambio_operativo", [cambio.id])[0]
    assert (registro.campo, registro.valor_anterior, registro.valor_nuevo, registro.motivo) == \
        ("descripcion", "N5 → N7", "N5 → N8", "error de tipeo")
    borrar_cambio(sesion, cambio.id, "cargado dos veces", "Beto", AHORA)
    assert listar_cambios(sesion, planta.id).empty
    borrado = historial(sesion, "cambio_operativo", [cambio.id])[0]
    assert borrado.accion == "borrado" and "N5 → N8" in borrado.valor_anterior
    assert "eBOE 2" in equipos_de_planta(sesion, planta.id).values()


def test_validaciones(sesion, planta):  # noqa: F811
    momento = datetime(2026, 7, 2, 8, 0)
    with pytest.raises(ValueError, match="Categoría"):
        crear_cambio(sesion, planta.id, momento, "magia", "x", "Ana", AHORA)
    with pytest.raises(ValueError, match="descripción"):
        crear_cambio(sesion, planta.id, momento, "otro", "  ", "Ana", AHORA)
    with pytest.raises(ValueError, match="usuario"):
        crear_cambio(sesion, planta.id, momento, "otro", "x", "", AHORA)
    otra = Planta(nombre="Sur", pais="Uruguay", cliente=Cliente(nombre="Y", pais="Uruguay"))
    ajeno = Equipo(codigo="S-BOE-01", nombre="eBOE S", tipo="BOE", numero=1, linea=Linea(nombre="L", planta=otra))
    sesion.add(ajeno)
    sesion.commit()
    with pytest.raises(ValueError, match="no es de esta planta"):
        crear_cambio(sesion, planta.id, momento, "otro", "x", "Ana", AHORA, equipo_id=ajeno.id)
    cambio = crear_cambio(sesion, planta.id, momento, "otro", "x", "Ana", AHORA)
    with pytest.raises(ValueError, match="motivo"):
        editar_cambio(sesion, cambio.id, {"descripcion": "y"}, " ", "Ana", AHORA)
    with pytest.raises(ValueError, match="No se puede editar"):
        editar_cambio(sesion, cambio.id, {"cargado_por": "Otro"}, "porque sí", "Ana", AHORA)


def test_periodo_de():
    periodos = pd.DataFrame({"desde": pd.to_datetime(["2026-07-01", "2026-07-08"]),
                             "hasta": pd.to_datetime(["2026-07-07", "2026-07-14"]),
                             "etiqueta": ["01/07–07/07", "08/07–14/07"]})
    assert periodo_de(pd.to_datetime(["2026-07-07", "2026-07-08", "2026-08-01"]), periodos) == \
        ["01/07–07/07", "08/07–14/07", None]


def test_comparar_elige_la_prueba():
    azar = np.random.default_rng(5)
    normales = comparar(azar.normal(80, 2, 30), azar.normal(84, 2, 30))
    assert normales["prueba"] == "Welch" and normales["p"] < 0.001 and normales["n_antes"] == 30
    assert normales["diferencia"] == pytest.approx(normales["despues"] - normales["antes"])
    assert normales["pct"] == pytest.approx(normales["diferencia"] / normales["antes"] * 100)
    sesgados = comparar(azar.exponential(1, 40), azar.exponential(3, 40))
    assert sesgados["prueba"] == "Mann-Whitney"
    pocos = comparar([1.0], [2.0, 3.0])
    assert pocos["prueba"] is None and pocos["diferencia"] == pytest.approx(1.5)


def test_antes_despues(sesion, planta):  # noqa: F811
    for dia, salida in ((1, 10.0), (2, 10.0), (3, 20.0), (4, 30.0), (5, 30.0)):     # eficiencias 90, 90, 80, 70, 70
        for hora in (6, 14):
            _ronda(sesion, datetime(2026, 7, dia, hora, 10), salida_ml={1: salida, 2: salida, 3: salida})
    planta_cambio = crear_cambio(sesion, planta.id, datetime(2026, 7, 3, 9, 0), "agua/fase pesada", "baja el agua",
                                 "Ana", AHORA)
    tabla = antes_despues(sesion, planta_cambio.id, dias=2).set_index("clave")
    eficiencia = tabla.loc["eficiencia"]
    assert (eficiencia["n_antes"], eficiencia["n_despues"]) == (12, 12)       # 2 días × 2 rondas × 3 eBOE; sin el 03/07
    assert eficiencia["antes"] == pytest.approx(90, abs=0.5) and eficiencia["despues"] == pytest.approx(70, abs=1)
    assert eficiencia["ambito"] == "Planta" and eficiencia["prueba"] in ("Welch", "Mann-Whitney")
    assert tabla.loc["rendimiento", "n_antes"] == 0                           # sin producción cargada
    de_eboe = crear_cambio(sesion, planta.id, datetime(2026, 7, 3, 9, 0), "mantenimiento", "rolos",
                           "Ana", AHORA, equipo_id=_equipo(sesion, "N-BOE-01").id)
    del_eboe = antes_despues(sesion, de_eboe.id, dias=2).set_index("clave")
    assert del_eboe.loc["eficiencia", "n_antes"] == 4 and del_eboe.loc["eficiencia", "ambito"] == "eBOE 1"
    assert del_eboe.loc["eficiencia", "antes"] == pytest.approx(90.0)
    with pytest.raises(ValueError, match="al menos 1 día"):
        antes_despues(sesion, de_eboe.id, dias=0)


def test_marcas_para_los_graficos():
    cambios = pd.DataFrame({"fecha_hora": [datetime(2025, 8, 10, 8, 0), datetime(2025, 9, 1, 9, 0)],
                            "categoria": ["centrífugas", "programa"], "equipo": ["ESE 230 (2)", None],
                            "descripcion": ["reemplaza a la (1)", "N5 → N7"]})
    lista = marcas(cambios, ["Ago 2025", None])                   # el segundo cae fuera del gráfico
    assert lista == [{"x": "Ago 2025", "categoria": "centrífugas",
                      "texto": "10/08/2025 08:00 · centrífugas · ESE 230 (2): reemplaza a la (1)"}]
