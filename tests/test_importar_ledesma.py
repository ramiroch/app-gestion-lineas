"""Pruebas de core/importar_ledesma.py con datos inventados (no los reales)."""

import copy
from datetime import date, datetime, time

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.importar_ledesma import (ErrorFila, Reporte, importar_ledesma, normalizar_hora,
                                   normalizar_programa, normalizar_texto, resolver_fechas,
                                   resolver_procesos)
from core.models import (AnalisisScott, Cliente, Equipo, LecturaOperacion, Linea, Planta, Proceso,
                         PuntoMuestreo, RegistroProduccion, Turno)
from core.produccion import toneladas_turno


# ---------------------------------------------------------------- normalización

@pytest.mark.parametrize("valor, esperado, corregida", [
    (time(13, 30), time(13, 30), False),
    ("15.10", time(15, 10), True),
    ("21_10", time(21, 10), True),
    ("7.10", time(7, 10), True),
])
def test_normalizar_hora(valor, esperado, corregida):
    assert normalizar_hora(valor) == (esperado, corregida)


def test_hora_invalida():
    with pytest.raises(ErrorFila, match="no válida"):
        normalizar_hora("25.10")


@pytest.mark.parametrize("valor, programa, hay_nota", [
    ("N3", "N3", False), (0, "N0", True), (9, "N9", True), ("n4", "N4", True),
    (10, None, True), ("X", None, True), (None, None, False),
])
def test_normalizar_programa(valor, programa, hay_nota):
    resultado, nota = normalizar_programa(valor)
    assert resultado == programa
    assert (nota is not None) == hay_nota


def test_normalizar_texto():
    assert normalizar_texto("firme") == "Firme"
    assert normalizar_texto("OK") == "Ok"
    assert normalizar_texto("poco rallada") == "Poco rallada"
    assert normalizar_texto("cuesta", nombre_propio=True) == "Cuesta"
    assert normalizar_texto("N Val S7R") == "N Val S/R"


# ---------------------------------------------------------------- proceso y fecha por contexto

def test_proceso_corregido_por_contexto():
    reporte = Reporte()
    filas = [(967, {"proceso": 9, "dia": 1, "fecha": datetime(2025, 8, 10)}),
             (968, {"proceso": datetime(2025, 9, 26), "dia": 1, "fecha": datetime(2025, 8, 10)}),
             (969, {"proceso": 45926, "dia": 1, "fecha": datetime(2025, 8, 10)}),
             (976, {"proceso": 45926, "dia": 2, "fecha": datetime(2025, 8, 11)})]   # otro día: no se deduce

    resultado = resolver_procesos(filas, reporte)

    assert [(n, d["proceso"]) for n, d in resultado] == [(967, 9), (968, 9), (969, 9)]
    assert [(f, t) for _, f, t, _ in reporte.filas] == [(968, "corregida"), (969, "corregida"), (976, "rechazada")]
    assert "Proceso corregido por contexto" in reporte.filas[0][3]


@pytest.fixture
def planta_5am():
    return Planta(nombre="Planta X", hora_inicio_dia=time(5, 0), turnos=[
        Turno(nombre="M", hora_inicio=time(5, 0), hora_fin=time(12, 59)),
        Turno(nombre="T", hora_inicio=time(13, 0), hora_fin=time(20, 59)),
        Turno(nombre="N", hora_inicio=time(21, 0), hora_fin=time(4, 59))])


def fila_fecha(numero, proceso, dia, fecha, hora):
    return (numero, {"proceso": proceso, "dia": dia, "fecha": datetime.combine(fecha, time()),
                     "hora": time.fromisoformat(hora)})


def test_fecha_de_produccion_calendario_y_arranque(planta_5am):
    reporte = Reporte()
    f24, f25 = date(2025, 6, 24), date(2025, 6, 25)
    filas = [
        fila_fecha(4, 1, 1, f24, "21:10"),     # de día / noche: Fecha + Hora
        fila_fecha(5, 1, 1, f24, "01:10"),     # Fecha de producción → muestra del 25/06
        fila_fecha(6, 2, 1, f24, "23:10"),
        fila_fecha(7, 2, 1, f25, "01:10"),     # trae la fecha del calendario → no se suma el día
        fila_fecha(8, 2, 1, f25, "03:10"),     # sigue siendo calendario (compara con la producción)
        fila_fecha(9, 3, 1, f24, "00:40"),     # arranque de proceso de madrugada → aviso
        fila_fecha(10, 1, 1, date(2025, 6, 28), "02:00"),   # fuera de secuencia → rechazada
    ]

    resultado = {n: d["fecha_hora"] for n, d in resolver_fechas(filas, planta_5am, reporte)}

    assert resultado == {
        4: datetime(2025, 6, 24, 21, 10), 5: datetime(2025, 6, 25, 1, 10), 6: datetime(2025, 6, 24, 23, 10),
        7: datetime(2025, 6, 25, 1, 10), 8: datetime(2025, 6, 25, 3, 10), 9: datetime(2025, 6, 25, 0, 40)}
    tipos = {f: (t, m) for _, f, t, m in reporte.filas}
    assert tipos[7][0] == tipos[8][0] == "corregida" and "Fecha de calendario corregida" in tipos[7][1]
    assert tipos[9][0] == "aviso" and "Madrugada sin filas anteriores" in tipos[9][1]
    assert tipos[10][0] == "rechazada" and "fuera de secuencia" in tipos[10][1]
    assert 5 not in tipos   # la regla general no se anota


def test_mismo_proceso_y_dia_en_otra_temporada_no_se_mezcla(planta_5am):
    reporte = Reporte()
    filas = [fila_fecha(4, 7, 1, date(2025, 7, 26), "23:10"),      # proceso 7, día 1 de 2025
             fila_fecha(5, 7, 1, date(2026, 8, 12), "23:10"),      # proceso 7, día 1 de 2026
             fila_fecha(6, 7, 1, date(2026, 8, 13), "01:10")]      # calendario respecto de 2026

    resultado = {n: d["fecha_hora"] for n, d in resolver_fechas(filas, planta_5am, reporte)}

    assert resultado[6] == datetime(2026, 8, 13, 1, 10)
    assert [(f, t) for _, f, t, _ in reporte.filas] == [(6, "corregida")]


def test_peso_fruta_cero_no_invalida_el_analisis(sesion, tmp_path):
    filas = copy.deepcopy(FILAS_BOE)
    filas[0]["peso_fruta_kg"] = 0

    _, reporte = importar(sesion, escribir_excel(tmp_path / "m.xlsx", filas_boe=filas))

    fila4 = sesion.scalars(select(AnalisisScott).where(AnalisisScott.origen == "m.xlsx › BOE › fila 4")).first()
    assert fila4 is not None and fila4.peso_fruta_kg is None
    assert any(f == 4 and t == "corregida" and "peso_fruta_kg 0 → vacío" in m for _, f, t, m in reporte.filas)


def test_entrada_sin_salida_de_la_misma_hora_avisa(sesion, tmp_path):
    filas = copy.deepcopy(FILAS_BOE)
    filas[1]["hora"] = time(21, 0)          # la Salida de la fila 5 quedó con otra hora

    _, reporte = importar(sesion, escribir_excel(tmp_path / "m.xlsx", filas_boe=filas))

    avisos = [m for _, f, t, m in reporte.filas if f == 4 and t == "aviso"]
    assert any("Entrada sin la Salida" in m and "21:00" in m for m in avisos)
    assert not any("Entrada sin la Salida" in m for _, f, _, m in reporte.filas if f != 4)


def test_lectura_solo_con_mediciones(sesion, tmp_path):
    filas = copy.deepcopy(FILAS_BOE)
    filas[0] |= {"programa": None, "flip_gate": None, "tph": None, "caudal": None}   # solo operador

    _, reporte = importar(sesion, escribir_excel(tmp_path / "m.xlsx", filas_boe=filas))

    assert sesion.scalar(select(func.count()).select_from(LecturaOperacion)) == 4
    assert any(f == 4 and "Sin datos de operación" in m for _, f, _, m in reporte.filas)


# ---------------------------------------------------------------- importación completa

ENCABEZADO_BOE = ["PROCESO", "Día", "Especie", "Fecha", "Hora", "Químico", "Operador", "Turno", "eBOE",
                  "pH", "°Brix", "Muestra\n[g]", "Vol\n[ml]", "Aceite\n[%]", "Sólidos Centrífugables\n[%]",
                  "Programa", "Flip\nGate", "Ton/H", "Caudal fase pesada [m3/h]", "Lugar de muestreo",
                  "Calidad de fruta", "Pinchado", "Peso fruta\n[Kg]", "Número de frutos", "Peso muestra [g]",
                  "Vol\n[ml]2", "Aceite en fruta [kg/MT]", "N\nBrO3", "N° de Ticket", "Licuadora"]
CLAVES_BOE = ["proceso", "dia", "fruta", "fecha", "hora", "analista", "operador", "turno", "eboe", "ph",
              "brix", "emulsion_g", "emulsion_ml", "aceite_calculado", "solidos", "programa", "flip_gate",
              "tph", "caudal", "lugar", "calidad", "rallado", "peso_fruta_kg", "n_frutos", "fruta_g",
              "fruta_ml", "aceite_fruta_calculado", "normalidad", "ticket", "licuadora"]
D24, D25, D26 = datetime(2025, 6, 24), datetime(2025, 6, 25), datetime(2025, 6, 26)


def boe(proceso, fecha, hora, eboe, lugar, dia=1, **otros):
    """Fila de BOE con datos crudos de fruta; Entrada con emulsión y operación salvo que se pisen."""
    fila = {"proceso": proceso, "dia": dia, "fruta": "N Val S/R", "fecha": fecha, "hora": hora,
            "analista": "Pérez", "eboe": eboe, "lugar": lugar, "calidad": "Firme", "peso_fruta_kg": 10.0,
            "n_frutos": 50, "fruta_g": 10.0, "fruta_ml": 12.2 if lugar == "Entrada" else 1.2,
            "normalidad": 0.1 if lugar == "Entrada" else 0.01, "aceite_fruta_calculado": 99.9}
    if lugar == "Entrada":
        fila |= {"emulsion_g": 3.5, "emulsion_ml": 8.2, "ph": 4.1, "brix": 3.0, "operador": "Gómez",
                 "programa": "N3", "flip_gate": 4, "tph": 12, "caudal": 10.5}
    else:
        fila |= {"rallado": "Ok"}
    return fila | otros


FILAS_BOE = [
    boe(1, D24, time(21, 10), 1, "Entrada", programa=0, calidad="firme"),        # fila 4
    boe(1, D24, time(21, 10), 1, "Salida", rallado="OK"),                         # fila 5
    boe(1, D24, "01.10", 1, "Entrada", programa="n4"),                            # fila 6: hora texto
    boe(1, D24, "01.10", 1, "Salida"),                                            # fila 7
    boe(45926, D24, time(3, 10), 2, "Entrada"),                                   # fila 8: proceso roto
    boe(D26, D24, time(3, 10), 2, "Salida"),                                      # fila 9: proceso = fecha
    boe(1, D25, time(4, 10), 1, "Entrada"),                                       # fila 10: fecha calendario
    boe(1, D25, time(4, 10), 1, "Salida"),                                        # fila 11
    {"eboe": 2, "lugar": "Salida", "normalidad": 0.01},                           # fila 12: casi vacía
    boe(2, D26, time(0, 40), 1, "Entrada", emulsion_g=None, emulsion_ml=None),    # fila 13: arranque
    boe(2, D26, time(0, 40), 1, "Salida", fruta_g=None),                          # fila 14: sin fruta
]

ENCABEZADO_RESUMEN = ["PROCESO", "Día", "Fecha", "Turno", "Aceite\nen\nfruta\n[kg/MT]",
                      "Aceite\nresidual\n[kg/MT]", "Eficiencia\neBOE", "Rend\neBOE\n[kg/MT]",
                      "Fruta\nprocesada\n[Ton]", "Aceite\nenvasado\n[kg]", "Rend\nde aceite\nturno\n[kg/tn]",
                      "Eficiencia\ndel\nproceso\n[%]", "Jugo\nconc\n[kg]", "Rend\njugo\nconc", "Rend\nTeórico",
                      "Reproc\n[kg]", "QF\nN°3", "Brix\nFritz", "BOE1\nT/H", "BOE2\nT/H", "BOE3\nT/H",
                      "Eficiencia\nCentrifuga\n1°(%)", None, "PROCESO"]   # X: otra tabla (no se lee)
FILAS_RESUMEN = [
    [1, 1, D24, "M"] + [None] * 18 + [None, 99],                                             # 4: sin datos
    [1, 1, D24, "T", 7.0, 0.8, 0.88, 6.2, 226, 1160, 5.1, 0.73, 14045, 16.1, None, 0, "", ""],   # 5
    [1, 2, D25, "N", None, None, None, None, 65.7, 540, None, None, 6625, None, None, 0, 1.2, 65.0],  # 6
    [2, 1, D25, "N", None, None, None, None, 53.3, 310, None, None, 1590, None, None, 0],            # 7
]


def escribir_excel(ruta, filas_boe=FILAS_BOE, filas_resumen=FILAS_RESUMEN):
    libro = Workbook()
    libro.remove(libro.active)
    hoja = libro.create_sheet("BOE")
    hoja.append(["SEGUIMIENTO DE EFICIENCIA eBOE"])
    hoja.append(["DATOS"])
    hoja.append(ENCABEZADO_BOE)
    for fila in filas_boe:
        hoja.append([fila.get(clave) for clave in CLAVES_BOE])
    hoja = libro.create_sheet("Resumen")
    hoja.append(["Naranja"])
    hoja.append(["Resumen Turno"])
    hoja.append(ENCABEZADO_RESUMEN)
    for fila in filas_resumen:
        hoja.append(fila)
    libro.save(ruta)
    return ruta


@pytest.fixture
def sesion():
    """Base en memoria con la planta inventada: 2 eBOE (tipo BOE) con sus 3 puntos y los turnos."""
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        planta = Planta(nombre="Planta X", pais="Argentina", cliente=Cliente(nombre="CLIENTE X", pais="Argentina"),
                        blanco_ml=0.2, factor_aceite_fruta=68.8416, factor_aceite_emulsion=3.45,
                        hora_inicio_dia=time(5, 0))
        linea = Linea(nombre="Línea 1", planta=planta)
        objetos = [planta, linea]
        for nombre, inicio, fin in (("M", time(5), time(12, 59)), ("T", time(13), time(20, 59)),
                                    ("N", time(21), time(4, 59))):
            objetos.append(Turno(nombre=nombre, hora_inicio=inicio, hora_fin=fin, planta=planta))
        for numero in (1, 2):
            equipo = Equipo(codigo=f"PX-BOE-0{numero}", nombre=f"BOE {numero}", tipo="BOE", numero=numero, linea=linea)
            objetos.append(equipo)
            for tipo in ("fruta entrada", "fruta salida", "emulsión"):
                objetos.append(PuntoMuestreo(tipo=tipo, nombre=f"BOE {numero} – {tipo}", equipo=equipo))
        sesion.add_all(objetos)
        sesion.commit()
        yield sesion


def importar(sesion, ruta):
    return importar_ledesma(sesion, ruta, cliente="CLIENTE X", planta="Planta X")


def contar(sesion):
    return {m.__name__: sesion.scalar(select(func.count()).select_from(m))
            for m in (Proceso, AnalisisScott, LecturaOperacion, RegistroProduccion)}


def test_importa_boe_y_resumen(sesion, tmp_path):
    resumen, reporte = importar(sesion, escribir_excel(tmp_path / "m.xlsx"))

    # fruta: filas 4–11 y 13 (la 12 está vacía y la 14 no tiene datos de fruta);
    # emulsión: Entradas 4, 6, 8 y 10 (la 13 no tiene); lecturas: Entradas 4, 6, 8, 10 y 13.
    assert contar(sesion) == {"Proceso": 2, "AnalisisScott": 13, "LecturaOperacion": 5, "RegistroProduccion": 3}
    assert resumen["AnalisisScott"] == {"creados": 13, "actualizados": 0}

    fila6 = sesion.scalars(select(AnalisisScott).where(AnalisisScott.origen == "m.xlsx › BOE › fila 6")).first()
    assert fila6.fecha_hora == datetime(2025, 6, 25, 1, 10)            # Fecha de producción + 1 día
    fila10 = sesion.scalars(select(AnalisisScott).where(AnalisisScott.origen == "m.xlsx › BOE › fila 10")).first()
    assert fila10.fecha_hora == datetime(2025, 6, 25, 4, 10)           # fecha de calendario: sin sumar
    fila8 = sesion.scalars(select(AnalisisScott).where(AnalisisScott.origen == "m.xlsx › BOE › fila 8")).first()
    assert fila8.proceso.numero == 1                                   # proceso corregido por contexto
    fila4 = sesion.scalars(select(AnalisisScott).where(AnalisisScott.origen == "m.xlsx › BOE › fila 4")).first()
    assert (fila4.calidad, fila4.blanco_ml, fila4.cargado_por) == ("Firme", 0.2, "importar_ledesma")
    lectura4 = sesion.scalars(select(LecturaOperacion).where(LecturaOperacion.origen == "m.xlsx › BOE › fila 4")).one()
    assert lectura4.programa == "N0"

    motivos = {}   # (hoja, fila, tipo) → todos los motivos juntos (una fila puede tener varios)
    for h, f, t, m in reporte.filas:
        motivos[(h, f, t)] = motivos.get((h, f, t), "") + m + " | "
    assert "Proceso corregido por contexto" in motivos[("BOE", 8, "corregida")]
    assert "Fecha de calendario corregida" in motivos[("BOE", 10, "corregida")]
    assert "Madrugada sin filas anteriores" in motivos[("BOE", 13, "aviso")]
    assert "Fila sin" in motivos[("BOE", 12, "rechazada")]
    assert "Sin datos crudos de fruta" in motivos[("BOE", 14, "rechazada")]
    assert "Turno compartido por 2 procesos (1, 2)" in motivos[("Resumen", 6, "aviso")]
    assert ("Resumen", 7, "aviso") in motivos
    assert not any(h == "Resumen" and f == 4 for h, f, _, _ in reporte.filas)   # sin datos: se saltea


def test_turno_compartido_suma_las_toneladas(sesion, tmp_path):
    importar(sesion, escribir_excel(tmp_path / "m.xlsx"))

    planta = sesion.scalars(select(Planta).where(Planta.nombre == "Planta X")).one()
    turno_n = next(t for t in planta.turnos if t.nombre == "N")
    assert toneladas_turno(sesion, planta.id, date(2025, 6, 25), turno_n.id) == pytest.approx(119.0)


def test_correr_dos_veces_no_duplica(sesion, tmp_path):
    ruta = escribir_excel(tmp_path / "m.xlsx")
    importar(sesion, ruta)
    antes = contar(sesion)

    resumen, _ = importar(sesion, ruta)

    assert contar(sesion) == antes
    assert all(cuentas["creados"] == 0 for cuentas in resumen.values())
    assert resumen["AnalisisScott"]["actualizados"] == 13


def test_misma_clave_con_datos_distintos_no_se_carga_ninguna(sesion, tmp_path):
    filas = copy.deepcopy(FILAS_BOE) + [boe(1, D24, time(21, 10), 1, "Entrada", fruta_ml=11.0)]   # fila 15

    _, reporte = importar(sesion, escribir_excel(tmp_path / "m.xlsx", filas_boe=filas))

    rechazadas = [f for h, f, t, m in reporte.filas if t == "rechazada" and "misma clave" in m]
    assert rechazadas == [4, 15]
    assert sesion.scalars(select(AnalisisScott).where(AnalisisScott.origen.like("% fila 4"))).first() is None


def test_no_pisa_ni_recrea_lo_corregido_en_la_app(sesion, tmp_path):
    from core.cambios import clave_de, fila_completa, registrar
    ruta = escribir_excel(tmp_path / "m.xlsx")
    importar(sesion, ruta)
    corregido, borrado = sesion.scalars(select(AnalisisScott).order_by(AnalisisScott.id)).all()[:2]
    ahora = datetime(2026, 11, 10, 9, 0)
    registrar(sesion, "analisis_scott", corregido.id, "edición", "tipeo", "Ramiro", ahora, "g1",
              clave_de("analisis_scott", corregido), "vol_ml", corregido.vol_ml, 99.0)
    corregido.vol_ml = 99.0
    registrar(sesion, "analisis_scott", borrado.id, "borrado", "muestra contaminada", "Ramiro", ahora, "g2",
              clave_de("analisis_scott", borrado), anterior=fila_completa(borrado))
    sesion.delete(borrado)
    sesion.commit()
    antes = contar(sesion)["AnalisisScott"]

    _, reporte = importar(sesion, ruta)

    assert sesion.get(AnalisisScott, corregido.id).vol_ml == 99.0               # no se pisa
    assert contar(sesion)["AnalisisScott"] == antes                             # el borrado no vuelve
    assert reporte.cantidad("corregido en la app") == 2
