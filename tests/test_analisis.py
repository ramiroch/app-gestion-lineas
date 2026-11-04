"""Pruebas de core/analisis.py con una planta inventada."""

from datetime import date, datetime, time

import pandas as pd
import pytest

from core.analisis import (ATAJO_MES, ATAJO_PERSONALIZADO, ATAJO_SEMANA, Filtros, analisis_filtrados,
                           atajos, filtrar, filtros_sin_periodo, opciones, pares_con_emulsion, rango_de_atajo, tabla_analisis,
                           tabla_emulsion)
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.parametros import ParametrosIncompletos
from core.models import (AnalisisScott, Cliente, Equipo, LecturaOperacion, Linea, Planta, Proceso,
                         PuntoMuestreo, Turno)


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


@pytest.fixture
def planta(sesion):
    """Planta con 2 eBOE, turnos M/T/N y día de producción desde las 05:00.

    Con factor 70, blanco 0, N 0,1 y 10 g, el aceite en fruta es vol × 0,7 kg/t; con factor
    3,5, el aceite en emulsión es vol × 0,035 %.
    """
    planta = Planta(nombre="Planta Norte", pais="Argentina", factor_aceite_fruta=70.0, factor_aceite_emulsion=3.5,
                    hora_inicio_dia=time(5, 0),
                    cliente=Cliente(nombre="CLIENTE A", pais="Argentina"))
    for nombre, inicio, fin in (("M", time(5), time(12, 59)), ("T", time(13), time(20, 59)), ("N", time(21), time(4, 59))):
        sesion.add(Turno(nombre=nombre, hora_inicio=inicio, hora_fin=fin, planta=planta))
    linea = Linea(nombre="Línea 1", planta=planta)
    puntos = {}
    for numero in (1, 2):
        equipo = Equipo(codigo=f"PN-BOE-0{numero}", nombre=f"eBOE {numero}", tipo="BOE", numero=numero, linea=linea)
        for tipo in ("fruta entrada", "fruta salida", "emulsión"):
            puntos[(numero, tipo)] = PuntoMuestreo(tipo=tipo, nombre=tipo, equipo=equipo)
    p2025 = Proceso(numero=7, temporada=2025, planta=planta)
    p2026 = Proceso(numero=7, temporada=2026, planta=planta)   # mismo número, otra temporada

    def analisis(eboe, tipo, proceso, fecha_hora, vol_ml):
        return AnalisisScott(punto_muestreo=puntos[(eboe, tipo)], proceso=proceso, fecha_hora=fecha_hora,
                             analista="Prueba", peso_muestra_g=10.0, vol_ml=vol_ml, blanco_ml=0.0,
                             normalidad=0.1, cargado_por="prueba")

    manana, madrugada = datetime(2025, 6, 10, 8, 0), datetime(2025, 6, 11, 2, 0)   # madrugada → día 10, turno N
    sesion.add_all([
        planta,
        analisis(1, "fruta entrada", p2025, manana, 10.0), analisis(1, "fruta salida", p2025, manana, 1.0),
        analisis(1, "emulsión", p2025, manana, 5.0),                               # no es fruta: no entra
        LecturaOperacion(equipo=puntos[(1, "fruta entrada")].equipo, fecha_hora=manana, programa="N3",
                         tph=18.0, caudal_fase_pesada=9.5, cargado_por="prueba"),
        analisis(2, "fruta entrada", p2025, madrugada, 10.0),                      # sin salida: sin eficiencia
        analisis(2, "fruta entrada", p2026, datetime(2026, 7, 1, 15, 0), 12.0),
        analisis(2, "fruta salida", p2026, datetime(2026, 7, 1, 15, 0), 3.0),
    ])
    sesion.commit()
    return planta


def test_tabla_una_fila_por_analisis_de_fruta(sesion, planta):
    tabla = tabla_analisis(sesion, planta.id)

    assert len(tabla) == 5                                       # la emulsión no entra
    primera = tabla.iloc[0]
    assert (primera["eboe"], primera["lugar"], primera["turno"]) == (1, "Entrada", "M")
    assert primera["aceite_fruta"] == pytest.approx(7.0)
    assert (primera["programa"], primera["tph"], primera["caudal_fase_pesada"]) == ("N3", 18.0, 9.5)


def test_eficiencia_va_en_las_dos_filas_del_par(sesion, planta):
    tabla = tabla_analisis(sesion, planta.id)

    par = tabla[(tabla["eboe"] == 1)]
    assert par["eficiencia_par"].tolist() == pytest.approx([0.9, 0.9])     # 1 − 0,7 / 7
    sin_salida = tabla[(tabla["eboe"] == 2) & (tabla["temporada"] == 2025)]
    assert sin_salida["eficiencia_par"].isna().all()


def test_madrugada_es_del_dia_anterior_turno_n(sesion, planta):
    tabla = tabla_analisis(sesion, planta.id)

    madrugada = tabla[tabla["fecha_hora"] == datetime(2025, 6, 11, 2, 0)].iloc[0]
    assert madrugada["fecha_produccion"] == pd.Timestamp(2025, 6, 10)
    assert madrugada["turno"] == "N"


def test_filtrar_por_cada_campo(sesion, planta):
    tabla = tabla_analisis(sesion, planta.id)
    p2026 = opciones(tabla)["procesos"]["2026 · P07"]

    assert len(filtrar(tabla, Filtros())) == 5                                          # sin filtros: todo
    assert len(filtrar(tabla, Filtros(desde=date(2025, 6, 10), hasta=date(2025, 6, 10)))) == 3  # incluye la madrugada
    assert len(filtrar(tabla, Filtros(eboes=[2]))) == 3
    assert len(filtrar(tabla, Filtros(turnos=["N", "T"]))) == 3
    assert len(filtrar(tabla, Filtros(procesos=[p2026]))) == 2                          # no mezcla temporadas
    assert len(filtrar(tabla, Filtros(programas=["N3"]))) == 2
    assert len(filtrar(tabla, Filtros(eboes=[1], turnos=["N"]))) == 0                    # se combinan con "y"


def test_analisis_filtrados_lee_y_filtra(sesion, planta):
    resultado = analisis_filtrados(sesion, planta.id, Filtros(desde=date(2026, 1, 1)))

    assert resultado["temporada"].tolist() == [2026, 2026]


def test_opciones_y_atajos(sesion, planta):
    op = opciones(tabla_analisis(sesion, planta.id))

    assert op["eboes"] == [1, 2]
    assert op["turnos"] == ["M", "T", "N"]
    assert op["programas"] == ["N3"]
    assert list(op["procesos"]) == ["2025 · P07", "2026 · P07"]
    assert op["ultima_fecha"] == date(2026, 7, 1)
    assert atajos(op) == ["Temporada 2026", "Temporada 2025", ATAJO_MES, ATAJO_SEMANA, ATAJO_PERSONALIZADO]
    assert rango_de_atajo("Temporada 2025", op) == (date(2025, 6, 10), date(2025, 6, 10))
    assert rango_de_atajo(ATAJO_SEMANA, op) == (date(2026, 6, 25), date(2026, 7, 1))
    assert rango_de_atajo(ATAJO_MES, op) == (date(2026, 6, 2), date(2026, 7, 1))
    assert rango_de_atajo(ATAJO_PERSONALIZADO, op) is None
    with pytest.raises(ValueError, match="no válido"):
        rango_de_atajo("Temporada 1999", op)


def test_planta_sin_analisis_da_tabla_vacia(sesion):
    planta = Planta(nombre="Vacía", pais="Argentina", hora_inicio_dia=time(5, 0),
                    cliente=Cliente(nombre="CLIENTE B", pais="Argentina"))
    sesion.add(planta)
    sesion.commit()

    tabla = tabla_analisis(sesion, planta.id)

    assert tabla.empty and list(tabla.columns)[:3] == ["analisis_id", "fecha_hora", "fecha_produccion"]
    assert opciones(tabla)["ultima_fecha"] is None


def test_tabla_emulsion_se_filtra_igual(sesion, planta):
    emulsion = tabla_emulsion(sesion, planta.id)

    assert len(emulsion) == 1
    fila = emulsion.iloc[0]
    assert fila["aceite_emulsion"] == pytest.approx(0.175)                # 5 × 0,1 × 3,5 / 10
    assert (fila["eboe"], fila["turno"], fila["temporada"], fila["programa"]) == (1, "M", 2025, "N3")
    assert len(filtrar(emulsion, Filtros(eboes=[1], turnos=["M"]))) == 1
    assert len(filtrar(emulsion, Filtros(eboes=[2]))) == 0


def test_emulsion_sin_factor_da_error_claro(sesion, planta):
    planta.factor_aceite_emulsion = None
    sesion.commit()

    with pytest.raises(ParametrosIncompletos, match="emulsión"):
        tabla_emulsion(sesion, planta.id)


def test_pares_con_emulsion(sesion, planta):
    pares = pares_con_emulsion(tabla_analisis(sesion, planta.id), tabla_emulsion(sesion, planta.id))

    # 2 pares completos: eBOE 1 (2025, con emulsión) y eBOE 2 (2026, sin emulsión)
    assert pares["eboe"].tolist() == [1, 2]
    assert pares["eficiencia_pct"].tolist() == pytest.approx([90.0, 75.0])          # 1 − 0,7/7 y 1 − 2,1/8,4
    assert pares["aceite_emulsion"].iloc[0] == pytest.approx(0.175)
    assert pd.isna(pares["aceite_emulsion"].iloc[1])
    assert pares["caudal_fase_pesada"].iloc[0] == 9.5


def test_filtros_sin_periodo_saca_fechas_y_procesos():
    filtros = Filtros(desde=date(2026, 7, 1), hasta=date(2026, 10, 26), eboes=[1], turnos=["N"],
                      procesos=[5], programas=["N3"])

    sin_periodo = filtros_sin_periodo(filtros)

    assert sin_periodo == Filtros(eboes=[1], turnos=["N"], programas=["N3"])
    assert filtros.desde == date(2026, 7, 1)                                   # no cambia el original


def test_planta_inexistente(sesion):
    with pytest.raises(ValueError, match="No existe la planta"):
        tabla_analisis(sesion, 999)
