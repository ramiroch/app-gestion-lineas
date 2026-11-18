"""Pruebas de core/inicio.py: configuración del flowsheet, fecha de referencia, turnos del período, marcadores (valor,
semáforo, gris sin datos, secundaria con su nombre, alertas; un turno, un rango con todos los turnos o con un solo
tipo, ponderación por toneladas), pendientes y tendencia semanal."""

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from core.analisis import Filtros, filtrar, pares_con_emulsion, tabla_analisis
from core.carga_scott import guardar_ronda
from core.gestion_alertas import listar_alertas
from core.importar_centrifugas import REVISAR_EXTREMO
from core.inicio import (AMARILLO, EN_MARCHA, NEUTRO, ROJO, SIN_DATOS, SIN_EMULSION, VERDE, _color_fp,
                         cargar_flowsheet, carpeta_planta, color_de, datos_flowsheet, datos_periodo, fecha_referencia,
                         logo_planta, objetivo_eficiencia, pendientes, periodo_por_defecto, rondas_esperadas,
                         tarjetas_periodo, temporadas_con_datos, tendencia_semanal, turnos_con_datos,
                         turnos_del_periodo)
from core.models import (Alerta, Equipo, Linea, MuestraCentrifuga, Planta, Proceso, RangoReferencia,
                         RegistroProduccion, Turno)
from core.proceso import filtrar_produccion, medias_periodo, resumen_proceso, turnos_sin_aprobar
from core.produccion import tabla_produccion, toneladas_por_turno
from tests.test_carga_scott import _planta, ronda, sesion  # noqa: F401  (fixture)

CARPETA_APP = Path(__file__).resolve().parent.parent / "app"
DIA = date(2026, 7, 1)
CONFIG = {"imagen": "flowsheet.jpg", "ancho": 1034, "alto": 775, "marcadores": [
    {"tipo": "nombre_eboe", "equipo": "N-BOE-01", "x": 0.07, "y": 0.15},
    {"tipo": "retorno_fp_eboe", "equipo": "N-BOE-01", "x": 0.275, "y": 0.40},
    {"tipo": "entrada_eboe", "equipo": "N-BOE-01", "x": 0.19, "y": 0.05},
    {"tipo": "eboe", "equipo": "N-BOE-01", "x": 0.19, "y": 0.18},
    {"tipo": "eboe", "equipo": "N-BOE-02", "x": 0.42, "y": 0.18},
    {"tipo": "salida_eboe", "equipo": "N-BOE-01", "x": 0.19, "y": 0.29},
    {"tipo": "emulsion_eboe", "equipo": "N-BOE-01", "x": 0.14, "y": 0.33},
    {"tipo": "emulsion_eboe", "equipo": "N-BOE-02", "x": 0.36, "y": 0.33},
    {"tipo": "tanque_emulsion", "x": 0.10, "y": 0.78},
    {"tipo": "retorno_pesada", "x": 0.85, "y": 0.45},
    {"tipo": "primaria", "equipo": "N-CEN-01", "x": 0.39, "y": 0.62},
    {"tipo": "primaria_caudal", "equipo": "N-CEN-01", "x": 0.375, "y": 0.545},
    {"tipo": "primaria_descarga", "equipo": "N-CEN-01", "x": 0.335, "y": 0.675},
    {"tipo": "primaria_liviana", "equipo": "N-CEN-01", "x": 0.45, "y": 0.575},
    {"tipo": "primaria_pesada", "equipo": "N-CEN-01", "x": 0.33, "y": 0.575},
    {"tipo": "primaria", "equipo": "N-CEN-02", "x": 0.39, "y": 0.83},
    {"tipo": "primaria_descarga", "equipo": "N-CEN-02", "x": 0.335, "y": 0.885},
    {"tipo": "secundaria", "equipos": ["N-CSE-01", "N-CSE-02"], "x": 0.57, "y": 0.78},
    {"tipo": "pulidora", "equipo": "N-PUL-01", "x": 0.75, "y": 0.79}]}


@pytest.fixture
def planta(sesion):  # noqa: F811
    """La planta de la carga Scott con centrífugas, rangos de descarga y de emulsión (sin alerta) y horas de rondas."""
    planta = _planta(sesion)
    planta.horas_rondas = "05:10, 07:10, 09:10, 11:10, 13:10, 15:10, 17:10, 19:10, 21:10, 23:10, 01:10, 03:10"
    planta.umbral_paralelo_lh = 17000
    planta.tolerancia_fp_verde_pct, planta.tolerancia_fp_amarillo_pct = 10, 20
    linea = sesion.scalars(select(Linea)).one()
    for codigo, nombre, tipo in (("N-CEN-01", "ESE 230 (1)", "Centrífuga primaria"),
                                 ("N-CEN-02", "ESE 230 (2)", "Centrífuga primaria"),
                                 ("N-CSE-01", "ESB 130", "Centrífuga secundaria"),
                                 ("N-CSE-02", "MSD 50", "Centrífuga secundaria"), ("N-PUL-01", "OSD 60", "Pulidora")):
        sesion.add(Equipo(codigo=codigo, nombre=nombre, tipo=tipo, numero=1, linea=linea, volumen_descarga_l=16))
    sesion.add_all([
        RangoReferencia(planta=planta, variable="Aceite en descarga", punto_o_equipo="Centrífuga primaria", maximo=0.5),
        RangoReferencia(planta=planta, variable="Aceite en descarga", punto_o_equipo="Centrífuga secundaria",
                        maximo=0.75),
        RangoReferencia(planta=planta, variable="Aceite en emulsión", punto_o_equipo="Emulsión (licor)",
                        fruta="Naranja", minimo=0.8, maximo=1.1, genera_alerta=False)])
    sesion.commit()
    return planta


def _muestra(sesion, codigo, fecha_hora, fase, ml=None, **otros):
    """Muestra de centrífuga titulada (10 g, N 0,1, blanco 0, factor 3,5 → aceite = mL × 0,035 %)."""
    equipo = sesion.scalars(select(Equipo).where(Equipo.codigo == codigo)).one()
    titulacion = {"gramos": 10.0, "ml": ml, "normalidad": 0.1, "blanco_ml": 0.0} if ml is not None else {}
    sesion.add(MuestraCentrifuga(equipo_id=equipo.id, fecha_hora=fecha_hora, fase=fase, cargado_por="prueba",
                                 **titulacion, **otros))
    sesion.commit()


def _ronda(sesion, momento, **opciones):  # noqa: F811
    guardar_ronda(sesion, _planta(sesion), ronda(sesion, momento, **opciones), "Ana", ahora=momento)


# ---------------------------------------------------------------- configuración y semáforo

def test_cargar_flowsheet(tmp_path):
    assert carpeta_planta("Planta Ñandú", tmp_path) == tmp_path / "planta_nandu"
    assert cargar_flowsheet("Norte", tmp_path) is None                         # sin configuración: no aparece
    carpeta = tmp_path / "norte"
    carpeta.mkdir()
    (carpeta / "flowsheet.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    with pytest.raises(ValueError, match="no encuentra su imagen"):
        cargar_flowsheet("Norte", tmp_path)
    (carpeta / "flowsheet.jpg").write_bytes(b"imagen")
    config = cargar_flowsheet("Norte", tmp_path)
    assert len(config["marcadores"]) == len(CONFIG["marcadores"]) and config["ruta_imagen"].endswith("flowsheet.jpg")
    for cambio, error in (({"x": 1.2}, "entre 0 y 1"), ({"tipo": "bomba"}, "no válido"), ({"equipo": None}, "falta"),
                           ({"campana": "lejos"}, "campana")):
        malo = CONFIG | {"marcadores": [CONFIG["marcadores"][1] | cambio]}
        (carpeta / "flowsheet.json").write_text(json.dumps(malo), encoding="utf-8")
        with pytest.raises(ValueError, match=error):
            cargar_flowsheet("Norte", tmp_path)


def test_color_del_semaforo():
    inferior = {"minimo": 80, "maximo": 90, "lado": "inferior", "genera_alerta": True}
    assert color_de(None, inferior)[0] == SIN_DATOS
    assert color_de(7.5, None) == (NEUTRO, "sin rango de referencia")
    assert (color_de(75, inferior)[0], color_de(95, inferior)[0]) == (ROJO, VERDE)      # arriba de 90 no es malo
    en_revision = {"minimo": 0.8, "maximo": 1.1, "lado": "ambos", "genera_alerta": False}
    assert color_de(1.4, en_revision) == (NEUTRO, "rango en revisión (0,80–1,10)")


# ---------------------------------------------------------------- fecha, turno y marcadores

def test_fecha_de_referencia_y_turnos(sesion, planta):  # noqa: F811
    assert fecha_referencia(sesion, planta, DIA) is None
    _ronda(sesion, datetime(2026, 7, 1, 6, 10))
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "descarga", ml=10.0)
    assert fecha_referencia(sesion, planta, DIA) == DIA                        # hoy tiene datos
    assert fecha_referencia(sesion, planta, date(2026, 11, 11)) == DIA         # fuera de temporada: el último
    assert turnos_con_datos(sesion, planta, DIA) == ["M", "T"]                 # T solo con centrífugas


def test_marcadores_del_turno(sesion, planta, tmp_path):  # noqa: F811
    _ronda(sesion, datetime(2026, 7, 1, 14, 10), salida_ml={2: 30.0})          # eBOE 2: eficiencia 70 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "descarga", ml=10.0, t_descarga_s=200.0)   # 0,35 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "emulsion", ml=20.0, caudal_lh=15000.0)  # 0,70 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "fase_liviana", aceite_pct_medido=70.0)
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "fase_pesada", ml=2.0)        # 0,07 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 16, 30), "emulsion", ml=24.0, caudal_lh=16000.0)  # 0,84 %
    _muestra(sesion, "N-CSE-02", datetime(2026, 7, 1, 14, 30), "descarga", ml=40.0)          # MSD 50: 1,40 %
    _muestra(sesion, "N-PUL-01", datetime(2026, 7, 1, 14, 30), "descarga", t_descarga_s=2700.0)   # 45 min
    sesion.add(Alerta(origen="nelson", regla="1", variable="eficiencia", ambito="equipo:2",
                      equipo_id=sesion.scalars(select(Equipo).where(Equipo.codigo == "N-BOE-02")).one().id,
                      fecha_produccion=DIA, turno_id=sesion.scalars(select(Turno).where(Turno.nombre == "T")).one().id,
                      fecha_hora=datetime(2026, 7, 1, 14, 10), valor=70.0, estado="abierta", creada_por="prueba"))
    sesion.add(Alerta(origen="rango", regla="rango", variable="ph", ambito=f"planta:{planta.id}", planta_id=planta.id,
                      fecha_produccion=DIA, turno_id=sesion.scalars(select(Turno).where(Turno.nombre == "T")).one().id,
                      fecha_hora=datetime(2026, 7, 1, 14, 10), valor=7.5, estado="en análisis", creada_por="prueba"))
    sesion.commit()
    # un día y un turno: lo mismo que el flowsheet de un turno
    marcadores = {(m["tipo"], m["nombre"]): m for m in datos_flowsheet(sesion, planta, CONFIG, DIA, DIA, "T")}

    entrada = marcadores[("entrada_eboe", "Entrada eBOE 1")]
    assert (entrada["texto"], entrada["color"]) == ("7,00 kg/MT", NEUTRO) and "Frutos en 10 kg: 50" in entrada["hover"]
    eboe1, eboe2 = marcadores[("eboe", "eBOE 1")], marcadores[("eboe", "eBOE 2")]
    assert (eboe1["texto"], eboe1["color"], eboe1["alertas"]) == ("ef. 90,0 %", VERDE, 0)       # solo la eficiencia
    assert (eboe2["color"], eboe2["alertas"], eboe2["ambitos"], eboe2["destino"]) == (
        ROJO, 1, ["eBOE 2"], {"pagina": "control", "variable": "eficiencia", "equipo": "N-BOE-02", "eboe": 2})
    salida = marcadores[("salida_eboe", "Salida eBOE 1")]
    assert (salida["texto"], salida["color"], salida["alertas"]) == ("0,70 kg/MT", NEUTRO, 0)
    assert "N = 1 muestra en 1 turno" in salida["hover"] and "promedio simple" in salida["hover"]
    emulsion = marcadores[("emulsion_eboe", "Emulsión eBOE 1")]                  # rebalse del eBOE (laboratorio)
    assert (emulsion["texto"], emulsion["color"]) == ("0,88 %", NEUTRO) and "rango en revisión" in emulsion["hover"]
    assert (emulsion["alertas"], emulsion["ambitos"]) == (1, ["Planta"])        # el pH es de la planta
    tanque = marcadores[("tanque_emulsion", "Tanque de emulsión")]               # primarias (centrífugas), mediana
    assert (tanque["texto"], tanque["destino"]["variable"]) == ("0,77 %", "aceite_emulsion_primaria")
    assert "15.500 L/h" in tanque["hover"] and "N = 2 muestras en 1 turno" in tanque["hover"]
    nombre = marcadores[("nombre_eboe", "eBOE 1")]                              # sin círculo: nombre y MTPH
    assert (nombre["texto"], nombre["color"]) == ("<b>eBOE 1</b><br>11,0 MTPH", None)
    fase_pesada = marcadores[("retorno_fp_eboe", "Fase pesada a eBOE 1")]
    assert fase_pesada["texto"].startswith("9,5 m³/h<br>") and "calc." in fase_pesada["texto"]
    assert "Calculado (retorno de FP a eBOE" in fase_pesada["hover"]
    primaria = marcadores[("primaria", "ESE 230 (1)")]                           # el cuerpo: solo el estado
    assert (primaria["texto"], primaria["color"]) == ("ESE 230 (1)", EN_MARCHA)
    assert marcadores[("primaria_caudal", "Caudal ESE 230 (1)")]["texto"] == "15.500 L/h"
    descarga = marcadores[("primaria_descarga", "Descarga ESE 230 (1)")]
    assert (descarga["texto"], descarga["color"]) == ("0,35 %", VERDE)
    liviana = marcadores[("primaria_liviana", "Fase liviana ESE 230 (1)")]
    assert liviana["texto"].endswith(" · 70 %") and "balance" in liviana["hover"]
    assert marcadores[("primaria_pesada", "Fase pesada ESE 230 (1)")]["texto"].endswith(" L/h")
    parada = marcadores[("primaria", "ESE 230 (2)")]
    assert (parada["texto"], parada["color"]) == ("ESE 230 (2) · sin datos", SIN_DATOS)    # no anduvo: gris
    linea_parada = marcadores[("primaria_descarga", "ESE 230 (2)")]
    assert (linea_parada["texto"], linea_parada["color"]) == ("", SIN_DATOS)           # sus datos de línea: vacíos
    retorno = marcadores[("retorno_pesada", "Retorno de fase pesada")]               # aceite en fase pesada
    assert retorno["texto"] == "0,07 %" and "Caudal total de fase pesada" in retorno["hover"]
    secundaria = marcadores[("secundaria", "MSD 50")]                            # la que anduvo en el turno
    assert (secundaria["texto"], secundaria["color"]) == ("MSD 50 · 1,40 %", ROJO)
    assert marcadores[("pulidora", "OSD 60")]["texto"] == "45 min"


def _turno(sesion, nombre):  # noqa: F811
    return sesion.scalars(select(Turno).where(Turno.nombre == nombre)).one()


@pytest.fixture
def periodo(sesion, planta):  # noqa: F811
    """Dos días: M y T del 01/07 y M del 02/07. eBOE 1: eficiencia 90, 70 y 80 % (salida 0,70, 2,10 y 1,40 kg/MT) con
    100, 300 y 100 t aprobadas. Tanque de emulsión (mediana de cada turno): 0,70 / 0,91 / 1,40 %. ESE 230 (2) solo
    anduvo el 02/07 M. Secundarias: ESB 130 el M (0,35 %) y MSD 50 el T (dos muestras, 0,70 %)."""
    siguiente = DIA + timedelta(days=1)
    for momento, salida in ((datetime(2026, 7, 1, 6, 10), 10.0), (datetime(2026, 7, 1, 14, 10), 30.0),
                            (datetime(2026, 7, 2, 6, 10), 20.0)):
        _ronda(sesion, momento, salida_ml={1: salida})
    for dia, turno, toneladas in ((DIA, "M", 100.0), (DIA, "T", 300.0), (siguiente, "M", 100.0)):
        sesion.add(RegistroProduccion(planta_id=planta.id, fecha_produccion=dia, turno_id=_turno(sesion, turno).id,
                                      fruta_t=toneladas, estado="aprobado", origen_carga="app", cargado_por="Ana"))
    sesion.commit()
    for codigo, momento, ml in (("N-CEN-01", datetime(2026, 7, 1, 6, 30), 20.0),      # 0,70 %
                                ("N-CEN-01", datetime(2026, 7, 1, 14, 30), 24.0),     # 0,84 %
                                ("N-CEN-01", datetime(2026, 7, 1, 16, 30), 28.0),     # 0,98 %
                                ("N-CEN-02", datetime(2026, 7, 2, 6, 30), 40.0)):     # 1,40 %
        _muestra(sesion, codigo, momento, "emulsion", ml=ml, caudal_lh=15000.0)
    _muestra(sesion, "N-CSE-01", datetime(2026, 7, 1, 6, 30), "descarga", ml=10.0)
    _muestra(sesion, "N-CSE-02", datetime(2026, 7, 1, 14, 30), "descarga", ml=20.0)
    _muestra(sesion, "N-CSE-02", datetime(2026, 7, 1, 16, 30), "descarga", ml=20.0)
    return DIA, siguiente


def _por_tipo(sesion, planta, desde, hasta, turno=None):  # noqa: F811
    return {(m["tipo"], m["nombre"]): m for m in datos_flowsheet(sesion, planta, CONFIG, desde, hasta, turno)}


def test_rango_con_todos_los_turnos(sesion, planta, periodo):  # noqa: F811
    desde, hasta = periodo
    assert turnos_del_periodo(sesion, planta, desde, hasta) == [(DIA, "M"), (DIA, "T"), (hasta, "M")]
    marcadores = _por_tipo(sesion, planta, desde, hasta)
    eboe = marcadores[("eboe", "eBOE 1")]                    # (90 × 100 + 70 × 300 + 80 × 100) / 500 = 76,0
    assert eboe["texto"] == "ef. 76,0 %" and "ponderado por toneladas aprobadas" in eboe["hover"]
    assert "N = 3 pares en 3 turnos" in eboe["hover"]
    salida = marcadores[("salida_eboe", "Salida eBOE 1")]    # (0,70 × 100 + 2,10 × 300 + 1,40 × 100) / 500 = 1,68
    assert salida["texto"] == "1,68 kg/MT"
    tanque = marcadores[("tanque_emulsion", "Tanque de emulsión")]
    assert tanque["texto"] == "0,91 %" and "N = 4 muestras en 3 turnos" in tanque["hover"]   # mediana de 0,70/0,91/1,40
    assert marcadores[("primaria", "ESE 230 (2)")]["color"] == EN_MARCHA        # anduvo en algún turno del período
    assert "En paralelo: 0 % de los turnos (0 de 2)" in marcadores[("primaria", "ESE 230 (1)")]["hover"]
    assert marcadores[("secundaria", "MSD 50 / ESB 130")]["texto"].startswith("MSD 50 / ESB 130 · ")  # las dos


def test_destino_del_clic_de_cada_marcador(sesion, planta, periodo):  # noqa: F811
    destinos = {(m["tipo"], m.get("nombre")): m["destino"] for m in datos_flowsheet(sesion, planta, CONFIG, *periodo)}
    control = lambda variable, equipo, eboe=None: {"pagina": "control", "variable": variable, "equipo": equipo,  # noqa: E731
                                                   "eboe": eboe}
    assert destinos[("nombre_eboe", "eBOE 1")] == {"pagina": "analisis", "eboe": 1}       # el nombre: Análisis
    assert destinos[("entrada_eboe", "Entrada eBOE 1")] == control("aceite_entrada", "N-BOE-01", 1)
    assert destinos[("eboe", "eBOE 2")] == control("eficiencia", "N-BOE-02", 2)
    assert destinos[("salida_eboe", "Salida eBOE 1")] == control("aceite_salida", "N-BOE-01", 1)
    assert destinos[("emulsion_eboe", "Emulsión eBOE 1")] == control("aceite_emulsion", "N-BOE-01", 1)
    assert destinos[("retorno_fp_eboe", "Fase pesada a eBOE 1")] == control("caudal_fp_lab", "N-BOE-01", 1)
    assert destinos[("tanque_emulsion", "Tanque de emulsión")] == control("aceite_emulsion_primaria", None)
    assert destinos[("retorno_pesada", "Retorno de fase pesada")] == control("aceite_pesada", None)
    assert destinos[("primaria_descarga", "Descarga ESE 230 (1)")] == control("aceite_descarga", "N-CEN-01")
    assert destinos[("primaria_liviana", "Fase liviana ESE 230 (1)")] == control("aceite_liviana", "N-CEN-01")
    assert destinos[("primaria_pesada", "Fase pesada ESE 230 (1)")] == control("caudal_pesada", "N-CEN-01")
    assert destinos[("secundaria", "MSD 50 / ESB 130")] == control("aceite_descarga", "N-CSE-02")  # la más muestreada
    assert destinos[("primaria", "ESE 230 (1)")] == {"pagina": "centrifugas"}                 # el cuerpo: Centrífugas
    assert destinos[("pulidora", "OSD 60")] == {"pagina": "centrifugas"}


def test_rango_con_un_solo_tipo_de_turno(sesion, planta, periodo):  # noqa: F811
    desde, hasta = periodo
    assert turnos_del_periodo(sesion, planta, desde, hasta, "M") == [(DIA, "M"), (hasta, "M")]
    marcadores = _por_tipo(sesion, planta, desde, hasta, "M")
    assert marcadores[("eboe", "eBOE 1")]["texto"] == "ef. 85,0 %"             # (90 × 100 + 80 × 100) / 200
    assert marcadores[("tanque_emulsion", "Tanque de emulsión")]["texto"] == "1,05 %"   # mediana de 0,70 y 1,40
    assert marcadores[("secundaria", "ESB 130")]["texto"] == "ESB 130 · 0,35 %"
    solo_t = _por_tipo(sesion, planta, desde, hasta, "T")
    assert solo_t[("primaria", "ESE 230 (2)")]["color"] == SIN_DATOS            # no anduvo en ningún T


def test_descarga_con_mediana_y_valores_extremos(sesion, planta, periodo):  # noqa: F811
    _muestra(sesion, "N-CSE-02", datetime(2026, 7, 1, 18, 30), "descarga", ml=600.0,          # 21 %: muestra extrema
             revisar=REVISAR_EXTREMO)
    secundaria = _por_tipo(sesion, planta, *periodo, "T")[("secundaria", "MSD 50")]
    assert secundaria["texto"] == "MSD 50 · 0,70 %"           # mediana de 0,70 / 0,70 / 21 (la media daría 7,47)
    assert "N muestras con valor extremo: 1" in secundaria["hover"]
    assert "valor extremo" not in _por_tipo(sesion, planta, *periodo, "M")[("secundaria", "ESB 130")]["hover"]


def test_sin_toneladas_aprobadas_promedio_simple(sesion, planta, periodo):  # noqa: F811
    for registro in sesion.scalars(select(RegistroProduccion)):
        registro.estado = "borrador"                                            # los borradores no ponderan
    sesion.commit()
    eboe = _por_tipo(sesion, planta, *periodo)[("eboe", "eBOE 1")]
    assert eboe["texto"] == "ef. 80,0 %" and "promedio simple: sin toneladas aprobadas" in eboe["hover"]


def test_alertas_superpuestas_con_el_periodo(sesion, planta):  # noqa: F811
    eboe2 = sesion.scalars(select(Equipo).where(Equipo.codigo == "N-BOE-02")).one()
    turno_m = _turno(sesion, "M")
    desde, hasta = DIA, DIA + timedelta(days=1)
    casos = {   # nombre: (estado, regla, inicio, fin)
        "empezó antes y sigue": ("histórico", "1", datetime(2026, 6, 28, 6, 10), datetime(2026, 7, 1, 6, 10)),
        "terminó antes": ("cerrada", "2", datetime(2026, 6, 26, 6, 10), datetime(2026, 6, 27, 6, 10)),
        "terminó en la madrugada del 30/06": ("cerrada", "3", datetime(2026, 6, 29, 6, 10), datetime(2026, 7, 1, 2)),
        "empezó después": ("abierta", "1", datetime(2026, 7, 3, 6, 10), None),
        "dentro": ("cerrada", "rango", datetime(2026, 7, 1, 6, 10), None)}
    ids = {}
    for nombre, (estado, regla, inicio, fin) in casos.items():
        alerta = Alerta(origen="rango" if regla == "rango" else "nelson", regla=regla, variable="eficiencia",
                        ambito=f"equipo:{eboe2.id}", equipo_id=eboe2.id, fecha_produccion=inicio.date(),
                        turno_id=turno_m.id, fecha_hora=inicio, fin=fin, valor=70.0, estado=estado, creada_por="prueba")
        sesion.add(alerta)
        sesion.flush()
        ids[nombre] = alerta.id
    sesion.commit()
    eboe = _por_tipo(sesion, planta, desde, hasta)[("eboe", "eBOE 2")]
    assert (eboe["alertas"], eboe["ambitos"]) == (2, ["eBOE 2"])
    for texto in ("2 alertas en el período", "1 histórico", "1 cerrada", "Eficiencia eBOE · regla 1: 1",
                  "Eficiencia eBOE · rango: 1"):
        assert texto in eboe["hover_alertas"]
    superpuestas = listar_alertas(sesion, planta.id, None, desde, hasta, superpuestas=True)
    assert set(superpuestas["id"]) == {ids["empezó antes y sigue"], ids["dentro"]}
    assert set(listar_alertas(sesion, planta.id, None, desde, hasta)["id"]) == {ids["dentro"]}   # por el inicio


# ---------------------------------------------------------------- pendientes y tendencia

def test_rondas_que_faltan_en_el_turno_en_curso(sesion, planta):  # noqa: F811
    turno_m = sesion.scalars(select(Turno).where(Turno.nombre == "M")).one()
    assert [m.time() for m in rondas_esperadas(planta, DIA, turno_m)] == [time(5, 10), time(7, 10), time(9, 10),
                                                                          time(11, 10)]
    _ronda(sesion, datetime(2026, 7, 1, 5, 15))                                 # la de 05:10, atrasada
    _ronda(sesion, datetime(2026, 7, 1, 9, 40), entrada_ml={1: 9.0, 2: 9.2, 3: 9.4})   # la de 09:10
    rondas = pendientes(sesion, planta, datetime(2026, 7, 1, 11, 0), DIA)["rondas"]
    assert (rondas["turno"], rondas["hechas"], rondas["esperadas"]) == ("M", 2, 4)
    assert rondas["faltan"] == [datetime(2026, 7, 1, 7, 10)]                    # la de 11:10 todavía no venció
    assert pendientes(sesion, planta, datetime(2026, 7, 5, 11, 0), DIA)["rondas"] is None   # no es hoy


def test_turnos_sin_produccion_y_borradores(sesion, planta):  # noqa: F811
    _ronda(sesion, datetime(2026, 7, 1, 6, 10))
    _ronda(sesion, datetime(2026, 7, 1, 14, 10), entrada_ml={1: 9.0, 2: 9.2, 3: 9.4})
    turno_t = sesion.scalars(select(Turno).where(Turno.nombre == "T")).one()
    sesion.add(RegistroProduccion(planta_id=planta.id, fecha_produccion=DIA, turno_id=turno_t.id, fruta_t=300.0,
                                  estado="borrador", origen_carga="app", cargado_por="Ana"))
    sesion.commit()
    resultado = pendientes(sesion, planta, datetime(2026, 7, 1, 22, 0), DIA)
    assert resultado["sin_produccion"] == [(DIA, "M")] and resultado["borradores"] == 1
    assert pendientes(sesion, planta, datetime(2026, 7, 1, 12, 0), DIA)["sin_produccion"] == []   # M sigue en curso


def test_tendencia_semanal_ponderada(sesion, planta):  # noqa: F811
    turno_m = sesion.scalars(select(Turno).where(Turno.nombre == "M")).one()
    for dias_atras, salida in ((0, 10.0), (1, 30.0), (8, 20.0)):                # eficiencia 90 %, 70 % y 80 %
        dia = DIA - timedelta(days=dias_atras)
        _ronda(sesion, datetime.combine(dia, time(6, 10)), salida_ml={1: salida})
        sesion.add(RegistroProduccion(planta_id=planta.id, fecha_produccion=dia, turno_id=turno_m.id,
                                      fruta_t=100.0 if dias_atras == 0 else 300.0, estado="aprobado",
                                      origen_carga="app", cargado_por="Ana"))
    sesion.commit()
    tabla = tendencia_semanal(sesion, planta, DIA, semanas=2)
    eficiencia = tabla[(tabla["eboe"] == 1) & (tabla["variable"] == "eficiencia")].set_index("semana")
    assert eficiencia.loc[0, "valor"] == pytest.approx((90 * 100 + 70 * 300) / 400)   # 75, ponderado
    assert (eficiencia.loc[0, "n"], eficiencia.loc[0, "etiqueta"]) == (2, "25/06–01/07")
    assert eficiencia.loc[1, "valor"] == pytest.approx(80.0) and eficiencia.loc[1, "ponderada"]

    objetivo = objetivo_eficiencia(sesion, planta, DIA)                          # fruta "N Val S/R" → naranja
    assert (objetivo["minimo"], objetivo["maximo"], objetivo["lado"]) == (80, 90, "inferior")



def test_semaforo_del_caudal_de_fase_pesada(planta):
    # |lab − calc| / calc: 9,5 contra 10 → 5 % verde; contra 8,5 → 11,8 % amarillo; contra 7 → 35,7 % rojo
    assert _color_fp(planta, 9.5, 10.0)[0] == VERDE
    assert _color_fp(planta, 9.5, 8.5)[0] == AMARILLO
    assert _color_fp(planta, 9.5, 7.0) == (ROJO, "diferencia 36 % (verde ≤ 10 %, amarillo ≤ 20 %)")
    assert _color_fp(planta, 9.5, None)[0] == NEUTRO and _color_fp(planta, None, 10.0)[0] == SIN_DATOS


def test_logo_de_la_planta(tmp_path):
    assert logo_planta("Norte", tmp_path) is None                              # sin logo: se muestra el nombre
    (tmp_path / "norte").mkdir()
    (tmp_path / "norte" / "logo.png").write_bytes(b"png")
    assert logo_planta("Norte", tmp_path) == tmp_path / "norte" / "logo.png"


def _produccion_del_periodo(sesion, planta, hasta):  # noqa: F811
    """Aceite y concentrado en los turnos aprobados del período y un borrador el 02/07 T (no entra)."""
    registros = {(r.fecha_produccion, r.turno.nombre): r for r in sesion.scalars(select(RegistroProduccion))}
    registros[(DIA, "M")].aceite_envasado_kg, registros[(DIA, "M")].jugo_conc_kg = 650.0, 8000.0
    registros[(DIA, "T")].aceite_envasado_kg = 2100.0             # sin concentrado: cuenta 0 dentro de su día
    registros[(hasta, "M")].aceite_envasado_kg = 700.0            # día sin concentrado: no entra en el de jugo
    sesion.add(RegistroProduccion(planta_id=planta.id, fecha_produccion=hasta, turno_id=_turno(sesion, "T").id,
                                  fruta_t=50.0, aceite_envasado_kg=999.0, jugo_conc_kg=9999.0, estado="borrador",
                                  origen_carga="app", cargado_por="Ana"))
    sesion.commit()


def test_tarjetas_del_periodo(sesion, planta, periodo):  # noqa: F811
    desde, hasta = periodo
    _produccion_del_periodo(sesion, planta, hasta)
    tarjetas = tarjetas_periodo(sesion, planta, desde, hasta)
    # los 3 eBOE juntos: la media de cada turno, ponderada por 100, 300 y 100 t aprobadas
    por_turno = []
    for dia, turno, toneladas in ((DIA, "M", 100), (DIA, "T", 300), (hasta, "M", 100)):
        analisis = datos_periodo(sesion, planta, dia, dia, turno)["analisis"]
        eficiencia = analisis.loc[analisis["lugar"] == "Entrada", "eficiencia_par"].mean() * 100
        salida = analisis.loc[analisis["lugar"] == "Salida", "aceite_fruta"].mean()
        por_turno.append((eficiencia, salida, toneladas))
    assert tarjetas["eficiencia"]["valor"] == pytest.approx(sum(e * t for e, _, t in por_turno) / 500)
    assert tarjetas["salida"]["valor"] == pytest.approx(sum(s * t for _, s, t in por_turno) / 500)
    assert (tarjetas["eficiencia"]["n"], tarjetas["eficiencia"]["ponderada"]) == (9, True)    # 3 eBOE × 3 turnos
    assert (tarjetas["fruta"]["valor"], tarjetas["fruta"]["n"]) == (500, 3)                  # sin el borrador
    assert tarjetas["rendimiento_aceite"]["valor"] == pytest.approx((650 + 2100 + 700) / 500)
    assert tarjetas["rendimiento_concentrado"] == {"valor": pytest.approx(8000 / 400), "n": 1, "nota": ""}
    assert tarjetas["borradores"] == 1
    assert tarjetas["alertas"] == len(listar_alertas(sesion, planta.id, estados=("abierta", "en análisis")))
    manana = tarjetas_periodo(sesion, planta, desde, hasta, "M")                # todos los M: sin el borrador (T)
    assert (manana["fruta"]["valor"], manana["borradores"]) == (200, 0)
    assert manana["rendimiento_aceite"]["valor"] == pytest.approx((650 + 700) / 200)
    assert manana["rendimiento_concentrado"]["valor"] == pytest.approx(8000 / 100)
    vacio = tarjetas_periodo(sesion, planta, date(2024, 8, 1), date(2024, 8, 1))
    assert (vacio["eficiencia"]["valor"], vacio["eficiencia"]["n"], vacio["fruta"]["valor"]) == (None, 0, None)
    assert vacio["rendimiento_concentrado"]["nota"] == "2024: °Bx sin verificar"


def test_tarjetas_iguales_a_analisis_periodo_y_proceso(sesion, planta, periodo):  # noqa: F811
    """Los números de las tarjetas son los de Análisis › Período y › Proceso con el mismo período y turno: la página
    filtra la tabla completa con la barra (filtrar) y usa medias_periodo y resumen_proceso."""
    desde, hasta = periodo
    _produccion_del_periodo(sesion, planta, hasta)
    toneladas = toneladas_por_turno(sesion, planta.id)
    for turno in (None, "M", "T"):
        tarjetas = tarjetas_periodo(sesion, planta, desde, hasta, turno)
        filtros = Filtros(desde=desde, hasta=hasta, turnos=[] if turno is None else [turno])
        datos = filtrar(tabla_analisis(sesion, planta.id), filtros)               # lo que muestra Análisis
        en_periodo = medias_periodo(datos, toneladas)                             # pestaña Período
        produccion = filtrar_produccion(tabla_produccion(sesion, planta.id), filtros)
        totales = resumen_proceso(pares_con_emulsion(datos, SIN_EMULSION), toneladas, produccion)["totales"]
        for clave in ("entrada", "salida"):
            assert tarjetas[clave] == en_periodo[clave]
        assert tarjetas["eficiencia"]["valor"] == pytest.approx(en_periodo["eficiencia"]["valor"] * 100)
        assert tarjetas["eficiencia"]["valor"] == pytest.approx(totales["eficiencia"])   # Proceso: la misma
        assert tarjetas["fruta"]["valor"] == totales["fruta_t"]
        assert tarjetas["rendimiento_aceite"]["valor"] == totales["rendimiento"]
        assert tarjetas["rendimiento_concentrado"]["valor"] == totales["rendimiento_concentrado"]
        assert tarjetas["borradores"] == turnos_sin_aprobar(tabla_produccion(sesion, planta.id, False), filtros)


def test_periodo_por_defecto_y_temporadas(sesion, planta):  # noqa: F811
    assert periodo_por_defecto(sesion, planta, DIA) is None and temporadas_con_datos(sesion, planta) == {}
    _ronda(sesion, datetime(2026, 7, 1, 6, 10))
    _ronda(sesion, datetime(2026, 7, 3, 2, 10))                                 # día de producción 02/07
    _muestra(sesion, "N-CEN-01", datetime(2026, 6, 28, 14, 30), "descarga", ml=10.0)        # sin proceso: su año
    anterior = Proceso(numero=3, temporada=2025, planta=planta)
    sesion.add(anterior)
    sesion.flush()
    _muestra(sesion, "N-CEN-01", datetime(2025, 9, 10, 8, 0), "descarga", ml=10.0, proceso_id=anterior.id)
    _muestra(sesion, "N-CEN-01", datetime(2025, 9, 20, 3, 0), "descarga", ml=10.0, proceso_id=anterior.id)
    hoy = date(2026, 11, 16)                                                    # fuera de temporada
    assert periodo_por_defecto(sesion, planta, hoy) == (date(2026, 6, 26), date(2026, 7, 2))   # la última semana
    assert temporadas_con_datos(sesion, planta) == {2025: (date(2025, 9, 10), date(2025, 9, 19)),
                                                    2026: (date(2026, 6, 28), date(2026, 7, 2))}


def test_pendientes_fuera_de_temporada(sesion, planta):  # noqa: F811
    _ronda(sesion, datetime(2026, 7, 1, 6, 10))
    _ronda(sesion, datetime(2026, 7, 1, 14, 10), entrada_ml={1: 9.0, 2: 9.2, 3: 9.4})
    en_marcha = pendientes(sesion, planta, datetime(2026, 7, 2, 9, 0), date(2026, 7, 2))     # 19 h después
    assert en_marcha["en_temporada"] and en_marcha["rondas"]["faltan"]
    assert en_marcha["sin_produccion"] == [(DIA, "M"), (DIA, "T")]
    fuera = pendientes(sesion, planta, datetime(2026, 7, 2, 15, 0), date(2026, 7, 2))        # 25 h después
    assert not fuera["en_temporada"] and fuera["ultima_ronda"] == datetime(2026, 7, 1, 14, 10)
    assert (fuera["rondas"], fuera["sin_produccion"]) == (None, [])


@pytest.fixture
def app_con_datos(sesion, planta, periodo, tmp_path, monkeypatch):  # noqa: F811
    """El Inicio (app/app.py) con la base de prueba copiada a un archivo (APP_DB)."""
    _produccion_del_periodo(sesion, planta, periodo[1])
    base = tmp_path / "prueba.db"
    destino = sqlite3.connect(base)
    sesion.connection().connection.driver_connection.backup(destino)
    destino.close()
    monkeypatch.setenv("APP_DB", str(base))
    monkeypatch.chdir(CARPETA_APP)
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_resource.clear()                   # motor() y plantas() quedan en caché en todo el proceso de pytest
    st.cache_data.clear()
    yield AppTest.from_file(str(CARPETA_APP / "app.py"), default_timeout=120)
    st.cache_resource.clear()
    st.cache_data.clear()


def test_inicio_con_periodo_por_defecto_y_botones_de_temporada(sesion, planta, app_con_datos):  # noqa: F811
    app = app_con_datos.run()
    assert not app.exception
    clave = f"inicio_fechas_{planta.id}"
    assert app.date_input(key=clave).value == (date(2026, 6, 26), date(2026, 7, 2))          # la última semana
    botones = [b.label for b in app.button if (b.key or "").startswith(f"inicio_temporada_{planta.id}")]
    assert botones == ["2026"]                                                  # una por temporada con datos
    etiquetas = {m.label: m.value for m in app.metric}
    assert list(etiquetas) == ["Fruta procesada", "Aceite en fruta de entrada", "Eficiencia eBOE", "Aceite en salida",
                               "Rendimiento de aceite", "Rendimiento de jugo concentrado 65 °Bx", "Alertas abiertas"]
    assert etiquetas["Fruta procesada"] == "500 t" and etiquetas["Rendimiento de aceite"] == "6,90 kg/MT"
    assert any("en borrador" in c.value for c in app.caption)                 # aviso de borradores
    assert any("Fuera de temporada: sin carga de laboratorio desde el 02/07/2026" in i.value for i in app.info)
    app.button(key=f"inicio_temporada_{planta.id}_2026").click().run()
    assert app.date_input(key=clave).value == (date(2026, 7, 1), date(2026, 7, 2))           # la temporada
    assert {m.label: m.value for m in app.metric}["Fruta procesada"] == "500 t"


def test_aviso_de_ph_en_la_emulsion(sesion, planta):  # noqa: F811
    sesion.add(RangoReferencia(planta=planta, variable="pH emulsión (aviso)", punto_o_equipo="Emulsión (licor)",
                               maximo=6.0, unidad="pH", fuente="Informe Brown 2023", lado_capacidad="superior"))
    sesion.commit()
    _ronda(sesion, datetime(2026, 7, 1, 6, 10))                                   # pH 5,5: sin aviso
    normal = _por_tipo(sesion, planta, DIA, DIA)[("emulsion_eboe", "Emulsión eBOE 1")]
    assert "⚠" not in normal["texto"] and "riesgo en la separación" not in normal["hover"]
    alto = {"g": 10.0, "ml": 25.0, "brix": 4.0, "ph": 6.6}
    _ronda(sesion, datetime(2026, 7, 2, 6, 10), e1={"emulsion": alto})
    con_aviso = _por_tipo(sesion, planta, date(2026, 7, 2), date(2026, 7, 2))[("emulsion_eboe", "Emulsión eBOE 1")]
    assert "pH 6,6 ⚠" in con_aviso["texto"] and "⚠ pH > 6: riesgo en la separación" in con_aviso["hover"]
