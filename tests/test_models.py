"""Pruebas de core/models.py y core/crear_base.py."""

from datetime import time

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import Cliente, Equipo, Linea, Planta, PuntoMuestreo

TIPOS_PUNTO = ["fruta entrada", "fruta salida", "emulsión"]


@pytest.fixture
def sesion():
    """Base vacía en memoria, con las tablas creadas. Se descarta al terminar."""
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


def crear_ledesma(sesion):
    """Carga LEDESMA: 1 línea, 3 eBOE y 3 puntos de muestreo por eBOE."""
    cliente = Cliente(nombre="LEDESMA", pais="Argentina")
    planta = Planta(nombre="LEDESMA", pais="Argentina", cliente=cliente, blanco_ml=0.2,
                    factor_aceite_fruta=68.8416, factor_aceite_emulsion=3.45,
                    hora_inicio_dia=time(5, 0))
    linea = Linea(nombre="Línea 1", planta=planta)
    for numero in [1, 2, 3]:
        equipo = Equipo(codigo=f"LED-BOE-0{numero}", nombre=f"eBOE {numero}", tipo="eBOE",
                        numero=numero, linea=linea)
        for tipo in TIPOS_PUNTO:
            PuntoMuestreo(tipo=tipo, nombre=f"eBOE {numero} – {tipo}", equipo=equipo)
    sesion.add(cliente)
    sesion.commit()
    return planta


def test_ledesma_tiene_9_puntos_de_muestreo(sesion):
    crear_ledesma(sesion)

    puntos = sesion.scalars(select(PuntoMuestreo)).all()

    assert len(puntos) == 9


def test_cada_punto_sabe_a_que_eboe_pertenece(sesion):
    crear_ledesma(sesion)

    for numero in [1, 2, 3]:
        equipo = sesion.scalars(select(Equipo).where(Equipo.numero == numero)).one()
        assert equipo.tipo == "eBOE"
        assert len(equipo.puntos_muestreo) == 3
        for punto in equipo.puntos_muestreo:
            assert punto.equipo.numero == numero
            assert punto.nombre.startswith(f"eBOE {numero}")
        assert sorted(p.tipo for p in equipo.puntos_muestreo) == sorted(TIPOS_PUNTO)


def test_planta_guarda_sus_parametros_de_calculo(sesion):
    planta = crear_ledesma(sesion)

    assert planta.blanco_ml == 0.2
    assert planta.factor_aceite_fruta == 68.8416
    assert planta.factor_aceite_emulsion == 3.45
    assert planta.hora_inicio_dia == time(5, 0)
    assert planta.pais == "Argentina"


def test_planta_sin_parametros_queda_vacia(sesion):
    planta = crear_ledesma(sesion)
    nueva = Planta(nombre="Otra", pais="Uruguay", cliente=planta.cliente)
    sesion.add(nueva)
    sesion.commit()

    assert (nueva.blanco_ml, nueva.factor_aceite_fruta, nueva.factor_aceite_emulsion,
            nueva.hora_inicio_dia) == (None, None, None, None)


def test_equipo_nuevo_queda_operativo_por_defecto(sesion):
    crear_ledesma(sesion)

    equipo = sesion.scalars(select(Equipo).where(Equipo.numero == 1)).one()

    assert equipo.estado == "Operativo"


def test_equipo_pendiente_de_instalacion(sesion):
    planta = crear_ledesma(sesion)
    desludger = Equipo(codigo="LED-DES-01", nombre="DESLUDGER N°1", tipo="desludger", numero=1,
                       estado="Pendiente de instalación", linea=planta.lineas[0])
    sesion.add(desludger)
    sesion.commit()

    guardado = sesion.scalars(select(Equipo).where(Equipo.tipo == "desludger")).one()

    assert guardado.estado == "Pendiente de instalación"
    assert guardado.nombre == "DESLUDGER N°1"
    assert guardado.linea.planta.nombre == "LEDESMA"


def test_estado_invalido_da_error():
    with pytest.raises(ValueError, match="no válido"):
        Equipo(codigo="LED-BOE-09", nombre="eBOE 9", tipo="eBOE", numero=9, estado="Roto")


def test_numero_activo_fijo_puede_repetirse(sesion):
    planta = crear_ledesma(sesion)
    for numero in [1, 2]:
        sesion.add(Equipo(codigo=f"LED-JEX-0{numero}", nombre=f"JEX N°{numero}", tipo="JEX",
                          numero=numero, numero_activo_fijo="AF-100", linea=planta.lineas[0]))
    sesion.commit()

    repetidos = sesion.scalars(select(Equipo).where(Equipo.numero_activo_fijo == "AF-100")).all()

    assert len(repetidos) == 2


def test_codigo_de_equipo_es_unico(sesion):
    planta = crear_ledesma(sesion)
    sesion.add(Equipo(codigo="LED-BOE-01", nombre="otro", tipo="eBOE", numero=9,
                      linea=planta.lineas[0]))

    with pytest.raises(IntegrityError):
        sesion.commit()


def test_codigo_de_equipo_nunca_cambia(sesion):
    crear_ledesma(sesion)
    equipo = sesion.scalars(select(Equipo).where(Equipo.codigo == "LED-BOE-01")).one()

    equipo.codigo = "LED-BOE-01"   # asignar el mismo código no es un cambio
    with pytest.raises(ValueError, match="nunca cambia"):
        equipo.codigo = "LED-BOE-99"


def test_codigo_vacio_da_error():
    with pytest.raises(ValueError, match="necesita un código"):
        Equipo(codigo="", nombre="eBOE 9", tipo="eBOE", numero=9)
