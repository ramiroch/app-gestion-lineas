"""Pruebas de core/importar_maestros.py con una planilla chica inventada (no la real)."""

import copy
import csv
from datetime import time

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.importar_maestros import guardar_reporte, importar_maestros, texto_resumen
from core.models import Cliente, Equipo, Linea, Planta, PuntoMuestreo, RangoReferencia

# Misma estructura que data/maestros_clientes.xlsx, con datos ficticios.
PLANILLA_BASE = {
    "Cliente": [
        ["nombre", "pais_casa_matriz"],
        ["CLIENTE A", "Argentina"],
        ["CLIENTE B", "Uruguay"],
    ],
    "Planta": [
        ["cliente", "nombre", "pais", "blanco_ml", "factor_aceite_fruta",
         "factor_aceite_emulsion", "hora_inicio_dia", "observaciones", "sigla"],
        ["CLIENTE A", "Planta Norte", "Argentina", 0.3, 68.0, 3.4, time(6, 0), "nota", "NOR"],
        ["CLIENTE B", "Planta Sur", "Uruguay", None, None, None, None, None, "SUR"],
    ],
    "Linea": [
        ["cliente", "planta", "nombre"],
        ["CLIENTE A", "Planta Norte", "Línea 1"],
        ["CLIENTE B", "Planta Sur", "Línea 1"],
    ],
    # Columnas (índice): 0 cliente, 1 planta, 2 linea, 3 nombre, 4 tipo, 5 numero, 6 modelo,
    # 7 serie_mc, 8 numero_serie, 9 codigo_activo_mc, 10 numero_activo_fijo, 11 estado,
    # 12 observaciones, 13 codigo
    "Equipo": [
        ["cliente", "planta", "linea", "nombre", "tipo", "numero", "modelo", "serie_mc",
         "numero_serie", "codigo_activo_mc", "numero_activo_fijo", "estado", "observaciones",
         "codigo"],
        ["CLIENTE A", "Planta Norte", "Línea 1", "BOE N°1", "BOE", 1, "6107E", None,
         "6107E-001", None, 100, "Operativo", None, "NOR-BOE-01"],
        ["CLIENTE A", "Planta Norte", "Línea 1", "BOE N°2", "BOE", 2, None, None,
         None, None, 100, "Pendiente de instalación", None, "NOR-BOE-02"],
        ["CLIENTE B", "Planta Sur", "Línea 1", "JEX N°1", "JEX", 1, "720", 750,
         "701-001", None, None, None, None, "SUR-JEX-01"],
    ],
    "PuntoMuestreo": [
        ["Nota: por ahora solo Planta Norte.", None, None, None, None],
        [None, None, None, None, None],
        ["cliente", "planta", "equipo (codigo)", "tipo", "nombre"],
        ["CLIENTE A", "Planta Norte", "NOR-BOE-01", "fruta entrada", "BOE 1 – Fruta entrada"],
        ["CLIENTE A", "Planta Norte", "NOR-BOE-01", "fruta salida", "BOE 1 – Fruta salida"],
    ],
    "RangoReferencia": [
        ["planta", "variable", "punto o equipo", "fruta", "minimo", "maximo", "unidad", "fuente"],
        ["Planta Norte", "Eficiencia BOE", "BOE (todos)", "Limón", 80, 90, "%", "prueba"],
        ["Planta Norte", "°Brix emulsión", "Emulsión", "Limón", None, 5, "°Brix", "prueba"],
    ],
}

MODELOS = [Cliente, Planta, Linea, Equipo, PuntoMuestreo, RangoReferencia]


def escribir_planilla(ruta, datos):
    """Guarda en `ruta` una planilla con una hoja por clave de `datos`."""
    libro = Workbook()
    libro.remove(libro.active)
    for hoja, filas in datos.items():
        ws = libro.create_sheet(hoja)
        for fila in filas:
            ws.append(fila)
    libro.save(ruta)
    return ruta


def fila_equipo(nombre, numero, codigo, modelo=None, planta="Planta Sur", cliente="CLIENTE B"):
    return [cliente, planta, "Línea 1", nombre, "JEX", numero, modelo, None, None, None, None,
            "Operativo", None, codigo]


@pytest.fixture
def datos():
    """Copia de la planilla base, para que cada test la pueda modificar."""
    return copy.deepcopy(PLANILLA_BASE)


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


def contar(sesion) -> dict:
    """Cantidad de registros por tabla."""
    return {m.__name__: sesion.scalar(select(func.count()).select_from(m)) for m in MODELOS}


def equipo_por_codigo(sesion, codigo):
    return sesion.scalars(select(Equipo).where(Equipo.codigo == codigo)).one()


def test_primera_corrida_crea_todo(sesion, datos, tmp_path):
    resumen, errores, avisos = importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))

    assert errores == [] and avisos == []
    assert {hoja: c["creados"] for hoja, c in resumen.items()} == {
        "Cliente": 2, "Planta": 2, "Linea": 2, "Equipo": 3, "PuntoMuestreo": 2, "RangoReferencia": 2}
    assert contar(sesion) == {"Cliente": 2, "Planta": 2, "Linea": 2, "Equipo": 3,
                              "PuntoMuestreo": 2, "RangoReferencia": 2}
    assert equipo_por_codigo(sesion, "NOR-BOE-01").puntos_muestreo[0].tipo == "fruta entrada"


def test_correr_dos_veces_no_duplica(sesion, datos, tmp_path):
    ruta = escribir_planilla(tmp_path / "m.xlsx", datos)
    importar_maestros(sesion, ruta)
    cantidades_antes = contar(sesion)

    resumen, errores, avisos = importar_maestros(sesion, ruta)

    assert errores == []
    assert avisos == []
    assert contar(sesion) == cantidades_antes
    assert all(c["creados"] == 0 for c in resumen.values())
    assert resumen["Equipo"]["actualizados"] == 3


def test_cambiar_nombre_modelo_o_numero_actualiza_el_mismo_equipo(sesion, datos, tmp_path):
    importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))
    datos["Equipo"][1][3] = "BOE Nº 1 (renombrado)"
    datos["Equipo"][1][5] = 7
    datos["Equipo"][1][6] = "6107B"
    datos["Equipo"][1][8] = None                   # se borra su n.º de serie
    datos["Equipo"][1][11] = "Fuera de servicio"

    resumen, _, avisos = importar_maestros(sesion, escribir_planilla(tmp_path / "m2.xlsx", datos))

    boe = equipo_por_codigo(sesion, "NOR-BOE-01")
    assert (boe.nombre, boe.numero, boe.modelo) == ("BOE Nº 1 (renombrado)", 7, "6107B")
    assert boe.numero_serie is None
    assert boe.estado == "Fuera de servicio"
    assert resumen["Equipo"]["creados"] == 0 and avisos == []
    assert contar(sesion)["Equipo"] == 3


def test_mismo_nombre_con_distinto_codigo_son_equipos_distintos(sesion, datos, tmp_path):
    datos["Equipo"] += [fila_equipo("JEX", 1, "SUR-JEX-01-2", "1100"),
                        fila_equipo("JEX", 1, "SUR-JEX-01-3", "400")]
    ruta = escribir_planilla(tmp_path / "m.xlsx", datos)

    importar_maestros(sesion, ruta)
    resumen, errores, avisos = importar_maestros(sesion, ruta)

    assert errores == [] and avisos == []
    assert resumen["Equipo"] == {"creados": 0, "actualizados": 5}
    assert contar(sesion)["Equipo"] == 5


def test_equipo_que_sale_de_la_planilla_genera_aviso(sesion, datos, tmp_path):
    importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))
    del datos["Equipo"][3]   # se quita el SUR-JEX-01

    _, errores, avisos = importar_maestros(sesion, escribir_planilla(tmp_path / "m2.xlsx", datos))

    assert errores == []
    assert len(avisos) == 1
    assert "SUR-JEX-01" in avisos[0][2] and "no en la planilla" in avisos[0][2]
    assert contar(sesion)["Equipo"] == 3     # avisa, pero no borra


def test_celdas_vacias_y_parametros_por_defecto(sesion, datos, tmp_path):
    importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))

    norte = sesion.scalars(select(Planta).where(Planta.nombre == "Planta Norte")).one()
    sur = sesion.scalars(select(Planta).where(Planta.nombre == "Planta Sur")).one()
    assert (norte.blanco_ml, norte.factor_aceite_fruta, norte.hora_inicio_dia) == (0.3, 68.0, time(6, 0))
    assert (sur.blanco_ml, sur.factor_aceite_fruta, sur.factor_aceite_emulsion,
            sur.hora_inicio_dia) == (0.2, 68.8416, 3.45, time(5, 0))

    jex = equipo_por_codigo(sesion, "SUR-JEX-01")
    assert jex.codigo_activo_mc is None
    assert jex.estado == "Operativo"          # estado vacío → valor por defecto
    assert jex.serie_mc == "750"              # un número de Excel se guarda como texto
    brix = sesion.scalars(select(RangoReferencia).where(RangoReferencia.unidad == "°Brix")).one()
    assert (brix.minimo, brix.maximo) == (None, 5)


def test_planta_inexistente_va_al_reporte(sesion, datos, tmp_path):
    datos["Linea"].append(["CLIENTE A", "Planta Fantasma", "Línea 9"])   # fila 4 de Excel

    _, errores, _ = importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))

    assert len(errores) == 1
    hoja, fila, motivo = errores[0]
    assert (hoja, fila) == ("Linea", 4)
    assert 'La planta "Planta Fantasma"' in motivo and "no existe" in motivo
    assert contar(sesion)["Linea"] == 2

    reporte = guardar_reporte(errores, tmp_path / "reporte.csv")
    with open(reporte, encoding="utf-8-sig", newline="") as archivo:
        filas = list(csv.reader(archivo, delimiter=";"))
    assert filas[0] == ["hoja", "fila", "motivo"]
    assert filas[1][:2] == ["Linea", "4"]


def test_codigo_repetido_o_vacio_no_se_carga(sesion, datos, tmp_path):
    datos["Equipo"] += [fila_equipo("JEX A", 5, "SUR-JEX-05"),   # filas 5 y 6: mismo código
                        fila_equipo("JEX B", 6, "SUR-JEX-05"),
                        fila_equipo("JEX C", 7, None)]           # fila 7: sin código

    _, errores, _ = importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))

    assert [(hoja, fila) for hoja, fila, _ in errores] == [("Equipo", 5), ("Equipo", 6), ("Equipo", 7)]
    assert 'Hay 2 filas con el mismo codigo "SUR-JEX-05"' in errores[0][2]
    assert 'Falta "codigo"' in errores[2][2]
    assert contar(sesion)["Equipo"] == 3


def test_numero_vacio_no_se_carga(sesion, datos, tmp_path):
    datos["Equipo"].append(fila_equipo("JEX", None, "SUR-JEX-09"))

    _, errores, _ = importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))

    assert len(errores) == 1
    assert 'Falta "numero"' in errores[0][2]


def test_estado_invalido_va_al_reporte(sesion, datos, tmp_path):
    datos["Equipo"][3][11] = "Roto"

    _, errores, _ = importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))

    assert len(errores) == 1
    assert 'Estado "Roto" no válido' in errores[0][2]


def test_hijo_de_una_fila_con_error_tambien_falla(sesion, datos, tmp_path):
    datos["Equipo"][1][5] = "uno"   # n.º no numérico → el NOR-BOE-01 no se carga

    _, errores, _ = importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))

    hojas = [hoja for hoja, _, _ in errores]
    assert hojas == ["Equipo", "PuntoMuestreo", "PuntoMuestreo"]
    assert "tiene que ser un número" in errores[0][2]
    assert 'El equipo "NOR-BOE-01" no existe' in errores[1][2]


def test_punto_de_muestreo_con_equipo_de_otra_planta_va_al_reporte(sesion, datos, tmp_path):
    datos["PuntoMuestreo"].append(["CLIENTE A", "Planta Norte", "SUR-JEX-01", "emulsión", "x"])

    _, errores, _ = importar_maestros(sesion, escribir_planilla(tmp_path / "m.xlsx", datos))

    assert len(errores) == 1
    assert 'es de la planta "Planta Sur", no de "Planta Norte"' in errores[0][2]


def test_texto_resumen():
    resumen = {"Cliente": {"creados": 2, "actualizados": 1}}
    texto = texto_resumen(resumen, [("Linea", 4, "x")], [], "reporte.csv")
    assert "2 creados" in texto and "1 actualizados" in texto
    assert "Errores: 1 · Avisos: 0 (detalle en reporte.csv)" in texto
