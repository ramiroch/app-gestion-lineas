"""Pruebas de core/consultas.py con una base en memoria y datos inventados."""

from datetime import time

import pytest
from sqlalchemy import select

from core.consultas import detalle_planta, filas_equipos, listar_clientes, listar_plantas
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import Cliente, Equipo, Linea, Planta, PuntoMuestreo


@pytest.fixture
def sesion():
    """Base en memoria: CLIENTE B (2 plantas) y CLIENTE A (1 planta)."""
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        cliente_b = Cliente(nombre="CLIENTE B", pais="Uruguay")
        sur = Planta(nombre="Sur", pais="Uruguay", cliente=cliente_b, blanco_ml=0.3)
        Planta(nombre="Este", pais="Uruguay", cliente=cliente_b)
        linea_b = Linea(nombre="Línea B", planta=sur)
        linea_a = Linea(nombre="Línea A", planta=sur)
        jex = Equipo(codigo="SUR-JEX-A02", nombre="JEX N°2", tipo="JEX", numero=2, linea=linea_a,
                     estado="Pendiente de instalación")
        boe = Equipo(codigo="SUR-BOE-A01", nombre="BOE N°1", tipo="BOE", numero=1, linea=linea_a,
                     modelo="6107E", numero_serie="6107E-001")
        Equipo(codigo="SUR-JEX-B01", nombre="JEX N°1", tipo="JEX", numero=1, linea=linea_b)
        PuntoMuestreo(tipo="fruta entrada", nombre="BOE 1 – Fruta entrada", equipo=boe)
        PuntoMuestreo(tipo="fruta salida", nombre="BOE 1 – Fruta salida", equipo=boe)
        cliente_a = Cliente(nombre="CLIENTE A", pais="Argentina")
        Planta(nombre="Norte", pais="Argentina", cliente=cliente_a)
        sesion.add_all([cliente_b, cliente_a, jex])
        sesion.commit()
        yield sesion


def id_de(sesion, modelo, nombre):
    return sesion.scalars(select(modelo).where(modelo.nombre == nombre)).one().id


def test_listar_clientes_ordenados(sesion):
    assert [c["nombre"] for c in listar_clientes(sesion)] == ["CLIENTE A", "CLIENTE B"]


def test_listar_plantas_solo_del_cliente(sesion):
    plantas = listar_plantas(sesion, id_de(sesion, Cliente, "CLIENTE B"))

    assert [p["nombre"] for p in plantas] == ["Este", "Sur"]


def test_detalle_planta_con_parametros(sesion):
    detalle = detalle_planta(sesion, id_de(sesion, Planta, "Sur"))

    assert detalle["cliente"] == "CLIENTE B"
    assert detalle["pais"] == "Uruguay"
    assert detalle["blanco_ml"] == 0.3                     # cargado
    assert detalle["factor_aceite_fruta"] is None          # no cargado: queda vacío
    assert detalle["hora_inicio_dia"] is None


def test_detalle_planta_lineas_equipos_y_puntos(sesion):
    detalle = detalle_planta(sesion, id_de(sesion, Planta, "Sur"))

    assert [l["nombre"] for l in detalle["lineas"]] == ["Línea A", "Línea B"]
    equipos_a = detalle["lineas"][0]["equipos"]
    assert [e["codigo"] for e in equipos_a] == ["SUR-BOE-A01", "SUR-JEX-A02"]   # por código
    assert [p["tipo"] for p in equipos_a[0]["puntos"]] == ["fruta entrada", "fruta salida"]
    assert equipos_a[1]["puntos"] == []
    assert equipos_a[1]["estado"] == "Pendiente de instalación"


def test_detalle_planta_sin_lineas_y_planta_inexistente(sesion):
    assert detalle_planta(sesion, id_de(sesion, Planta, "Norte"))["lineas"] == []
    assert detalle_planta(sesion, 9999) is None


def test_filas_equipos_pone_los_puntos_debajo_de_su_equipo(sesion):
    equipos = detalle_planta(sesion, id_de(sesion, Planta, "Sur"))["lineas"][0]["equipos"]

    filas = filas_equipos(equipos)

    assert [(f["codigo"], f["nombre"], f["es_punto"]) for f in filas] == [
        ("SUR-BOE-A01", "BOE N°1", False),
        ("", "↳ BOE 1 – Fruta entrada", True),
        ("", "↳ BOE 1 – Fruta salida", True),
        ("SUR-JEX-A02", "JEX N°2", False),
    ]
    assert filas[0]["modelo"] == "6107E" and filas[1]["modelo"] is None
    assert filas[1]["tipo"] == "fruta entrada"
