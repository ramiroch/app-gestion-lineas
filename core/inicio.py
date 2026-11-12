"""Inicio (tarea 21): cómo vienen los equipos y qué está pendiente, en 10 segundos. Sin Streamlit.

- cargar_flowsheet(): la configuración del flowsheet de la planta (data/plantas/<planta>/flowsheet.json: imagen y
  marcadores, con x e y en fracción de la imagen, origen arriba a la izquierda). None si la planta no tiene.
- fecha_referencia(): hoy si hay análisis Scott o muestras de centrífugas; si no (fuera de temporada), el último día
  con alguno. turnos_con_datos(): los turnos de ese día con datos (el último es el que se muestra).
- datos_flowsheet(): un marcador por equipo con su valor del turno, el semáforo contra RangoReferencia (respetando el
  lado; azul neutro sin rango o con un rango sin alerta, "en revisión"; gris si el equipo no tuvo datos en el
  turno), el texto del hover, las alertas abiertas o en análisis y a qué página lleva el clic.
- resumen_alertas(), pendientes() (rondas que faltan en el turno en curso, turnos sin producción cargada,
  borradores) y tendencia_semanal() (eficiencia y aceite en salida por eBOE, ponderados por toneladas aprobadas).
"""

import json
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from core.analisis import pares_con_emulsion, tabla_analisis, tabla_emulsion
from core.analisis_centrifugas import (PRIMARIA, agua_por_eboe, balance_por_muestreo, por_muestreo,
                                       primarias_por_turno, tabla_muestras)
from core.carga_scott import semaforo
from core.db import CARPETA_PROYECTO
from core.estadistica import media_ponderada_por_turno
from core.gestion_alertas import EN_ANALISIS, listar_alertas
from core.models import (Alerta, AnalisisScott, Equipo, Linea, MuestraCentrifuga, Planta, PuntoMuestreo,
                         RangoReferencia, RegistroProduccion)
from core.operacion import especie
from core.parametros import leer_horas_rondas
from core.produccion import toneladas_por_turno
from core.turnos import fecha_produccion, fin_de_turno, inicio_de_turno, turno_de

CARPETA_PLANTAS = CARPETA_PROYECTO / "data" / "plantas"
DEL_EBOE = ("nombre_eboe", "entrada_eboe", "eboe", "salida_eboe", "emulsion_eboe", "retorno_fp_eboe")
DE_PRIMARIA = ("primaria", "primaria_caudal", "primaria_descarga", "primaria_liviana", "primaria_pesada")
TIPOS = DEL_EBOE + DE_PRIMARIA + ("tanque_emulsion", "retorno_pesada", "secundaria", "pulidora")
CON_EQUIPO = DEL_EBOE + DE_PRIMARIA + ("pulidora",)
# Variable de alerta → grupo de marcador donde va la 🔔 (los del eBOE; las de centrífugas van a su equipo)
GRUPO_ALERTA = {"eficiencia": "eboe", "aceite_salida": "salida_eboe", "aceite_emulsion": "emulsion_eboe",
                "brix": "emulsion_eboe", "ph": "emulsion_eboe"}
VERDE, AMARILLO, ROJO, NEUTRO, SIN_DATOS = "verde", "amarillo", "rojo", "neutro", "sin datos"
EN_MARCHA = "en marcha"            # estado de una primaria que anduvo en el turno (su cuerpo no lleva semáforo)
ABIERTAS = ("abierta", EN_ANALISIS)
ANTES_RONDA, DESPUES_RONDA = timedelta(minutes=15), timedelta(minutes=60)   # una ronda atrasada sigue contando
DIAS_PRODUCCION = 7
SEMANAS = 8
SIN_EMULSION = pd.DataFrame(columns=["eboe", "fecha_hora", "aceite_emulsion"])


# ---------------------------------------------------------------- configuración

def carpeta_planta(nombre: str, base: Path = CARPETA_PLANTAS) -> Path:
    """data/plantas/<nombre en minúsculas, sin tildes, espacios como _>."""
    simple = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower().strip()
    return base / simple.replace(" ", "_")


def cargar_flowsheet(nombre_planta: str, base: Path = CARPETA_PLANTAS) -> dict | None:
    """La configuración del flowsheet de la planta, o None si no tiene (la sección no aparece).

    Formato (ver docs/flowsheet_ejemplo.json): {"imagen", "ancho", "alto", "marcadores": [{"tipo" (uno de TIPOS),
    "equipo" (código; "equipos" en la secundaria), "x", "y" (0–1), "etiqueta" ("derecha", "izquierda", "arriba",
    "abajo"), "nombre" (opcional)}]}. Agrega "ruta_imagen". Error claro si está mal armada.
    """
    ruta = carpeta_planta(nombre_planta, base) / "flowsheet.json"
    if not ruta.exists():
        return None
    config = json.loads(ruta.read_text(encoding="utf-8"))
    imagen = ruta.parent / str(config.get("imagen", ""))
    if not config.get("imagen") or not imagen.exists():
        raise ValueError(f"El flowsheet de {nombre_planta} no encuentra su imagen ({imagen.name}).")
    if not (config.get("ancho", 0) > 0 and config.get("alto", 0) > 0):
        raise ValueError(f"El flowsheet de {nombre_planta} tiene que decir el ancho y el alto de la imagen (px).")
    for i, m in enumerate(config.get("marcadores", []), start=1):
        if m.get("tipo") not in TIPOS:
            raise ValueError(f'Marcador {i}: tipo "{m.get("tipo")}" no válido ({", ".join(TIPOS)}).')
        if not all(isinstance(m.get(c), (int, float)) and 0 <= m[c] <= 1 for c in ("x", "y")):
            raise ValueError(f"Marcador {i} ({m['tipo']}): x e y van en fracción de la imagen (entre 0 y 1).")
        if m["tipo"] in CON_EQUIPO and not m.get("equipo"):
            raise ValueError(f"Marcador {i} ({m['tipo']}): falta el código del equipo.")
        if m["tipo"] == "secundaria" and not m.get("equipos"):
            raise ValueError(f"Marcador {i} (secundaria): falta la lista de equipos.")
    return config | {"ruta_imagen": str(imagen)}


# ---------------------------------------------------------------- fecha y turno

def _equipos(sesion, planta_id: int):
    return select(Equipo.id).join(Linea).where(Linea.planta_id == planta_id)


def fecha_referencia(sesion, planta: Planta, hoy: date) -> date | None:
    """Hoy si hay análisis Scott o muestras de centrífugas; si no, el último día de producción con alguno (hasta
    hoy). None si la planta no tiene ninguno."""
    hasta = datetime.combine(hoy + timedelta(days=1), planta.hora_inicio_dia or time(0))
    equipos = _equipos(sesion, planta.id)
    momentos = [sesion.scalar(select(func.max(AnalisisScott.fecha_hora)).join(AnalisisScott.punto_muestreo).where(
                    PuntoMuestreo.equipo_id.in_(equipos), AnalisisScott.fecha_hora < hasta)),
                sesion.scalar(select(func.max(MuestraCentrifuga.fecha_hora)).where(
                    MuestraCentrifuga.equipo_id.in_(equipos), MuestraCentrifuga.fecha_hora < hasta))]
    dias = [fecha_produccion(planta, m) for m in momentos if m is not None]
    return max(dias) if dias else None


def _ventana(planta: Planta, dia: date) -> tuple[datetime, datetime]:
    """Inicio y fin del día de producción."""
    inicio = datetime.combine(dia, planta.hora_inicio_dia)
    return inicio, inicio + timedelta(days=1)


def turnos_con_datos(sesion, planta: Planta, dia: date) -> list[str]:
    """Turnos del día de producción con análisis Scott o muestras de centrífugas, en orden del día."""
    desde, hasta = _ventana(planta, dia)
    equipos = _equipos(sesion, planta.id)
    momentos = list(sesion.scalars(select(AnalisisScott.fecha_hora).join(AnalisisScott.punto_muestreo).where(
        PuntoMuestreo.equipo_id.in_(equipos), AnalisisScott.fecha_hora >= desde, AnalisisScott.fecha_hora < hasta)))
    momentos += list(sesion.scalars(select(MuestraCentrifuga.fecha_hora).where(
        MuestraCentrifuga.equipo_id.in_(equipos), MuestraCentrifuga.fecha_hora >= desde,
        MuestraCentrifuga.fecha_hora < hasta)))
    con_datos = {turno_de(planta, m).nombre for m in momentos}
    orden = sorted(planta.turnos, key=lambda t: inicio_de_turno(planta, dia, t))
    return [t.nombre for t in orden if t.nombre in con_datos]


# ---------------------------------------------------------------- flowsheet

def _rango(sesion, planta_id: int, variable: str, punto: str, fruta: str | None) -> dict | None:
    """Rango de RangoReferencia (variable, punto o equipo): el de la fruta si hay, si no el general."""
    rangos = sesion.scalars(select(RangoReferencia).where(RangoReferencia.planta_id == planta_id,
                                                          RangoReferencia.variable == variable,
                                                          RangoReferencia.punto_o_equipo == punto)).all()
    de_la_fruta = [r for r in rangos if fruta and r.fruta and especie(r.fruta) == especie(fruta)]
    elegido = (de_la_fruta or [r for r in rangos if r.fruta is None] or [None])[0]
    if elegido is None:
        return None
    return {"minimo": elegido.minimo, "maximo": elegido.maximo, "lado": elegido.lado_capacidad,
            "genera_alerta": elegido.genera_alerta is not False}


def color_de(valor, rango: dict | None) -> tuple[str, str]:
    """(color, nota) de un valor: gris sin datos; azul neutro sin rango o con un rango sin alerta (en revisión);
    verde o rojo contra el rango respetando el lado."""
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return SIN_DATOS, "sin datos en el turno"
    if rango is None:
        return NEUTRO, "sin rango de referencia"
    texto = (f"{'' if rango['minimo'] is None else _num(rango['minimo'])}–"
             f"{'' if rango['maximo'] is None else _num(rango['maximo'])}")
    if not rango["genera_alerta"]:
        return NEUTRO, f"rango en revisión ({texto})"
    return (VERDE if semaforo(valor, rango) == "verde" else ROJO), f"rango {texto}"


def _num(valor, decimales: int = 2) -> str:
    return f"{valor:,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _media(serie) -> float | None:
    valores = pd.to_numeric(pd.Series(serie), errors="coerce").dropna()
    return float(valores.mean()) if len(valores) else None


def datos_turno(sesion, planta: Planta, dia: date, turno: str) -> dict:
    """Los datos del turno que usa el flowsheet: análisis de fruta, emulsión y muestras de centrífugas."""
    t = next(x for x in planta.turnos if x.nombre == turno)
    desde, hasta = inicio_de_turno(planta, dia, t), fin_de_turno(planta, dia, t)
    analisis = tabla_analisis(sesion, planta.id, desde=desde, hasta=hasta, con_plc=False)
    emulsion = tabla_emulsion(sesion, planta.id, desde=desde, hasta=hasta, con_plc=False)
    muestras = tabla_muestras(sesion, planta.id, desde=desde, hasta=hasta)
    return {"analisis": analisis, "emulsion": emulsion, "muestras": muestras}


def datos_flowsheet(sesion, planta: Planta, config: dict, dia: date, turno: str) -> list[dict]:
    """Un dict por marcador de la configuración: tipo, nombre, x, y, etiqueta, valor, texto (lo que se escribe al
    lado), color (VERDE, ROJO, NEUTRO, SIN_DATOS), hover, alertas (abiertas o en análisis), ambitos (para filtrar
    Alertas al hacer clic en la 🔔) y destino del clic ({"pagina": "analisis" | "centrifugas", "eboe"}).
    Por eBOE: nombre con el MTPH (sin círculo: color None), entrada (aceite en fruta), eBOE (eficiencia), salida
    (aceite en fruta de salida), emulsión (rebalse, laboratorio del BOE) y fase pesada que entra (laboratorio contra
    agua por eBOE calculada; semáforo con las tolerancias de la planta). Por primaria: estado (EN_MARCHA o gris),
    caudal de emulsión, aceite en descarga, fase liviana y fase pesada (caudales del balance). Tanque de emulsión y
    tanque de retorno de fase pesada: medianas del turno del aceite en emulsión y en fase pesada de las primarias
    en marcha (planilla de centrífugas)."""
    ctx = datos_turno(sesion, planta, dia, turno)
    analisis, muestras = ctx["analisis"], ctx["muestras"]
    ctx["fruta"] = analisis["fruta"].dropna().mode().iloc[0] if analisis["fruta"].notna().any() else None
    ctx["equipos"] = equipos = {e.codigo: e for e in sesion.scalars(select(Equipo).join(Linea).where(
        Linea.planta_id == planta.id))}
    ctx["balance"] = balance_por_muestreo(por_muestreo(muestras)) if not muestras.empty else muestras
    ctx["agua"] = _agua(planta, analisis, muestras, ctx["balance"])
    alertas = _alertas_por_marcador(sesion, planta.id, equipos)
    resultado = []
    for m in config.get("marcadores", []):
        base = {"tipo": m["tipo"], "x": m["x"], "y": m["y"], "etiqueta": m.get("etiqueta", "derecha")}
        base |= _marcador(sesion, planta, m, ctx)
        base["nombre"] = m.get("nombre") or base.get("nombre", m["tipo"])
        if base["color"] == SIN_DATOS and not base.pop("vacio", False) and m["tipo"] != "nombre_eboe":
            base["texto"] = "sin datos"
        base.pop("vacio", None)
        claves = base.pop("claves_alertas", [])
        base["alertas"] = sum(alertas.get(clave, {}).get("n", 0) for clave in claves)
        base["ambitos"] = sorted({a for clave in claves for a in alertas.get(clave, {}).get("ambitos", set())})
        resultado.append(base)
    return resultado


def _marcador(sesion, planta: Planta, m: dict, ctx: dict) -> dict:
    """Valor, texto, color, hover, destino y claves de alertas de un marcador (ver datos_flowsheet)."""
    tipo, equipos, fruta = m["tipo"], ctx["equipos"], ctx["fruta"]
    if tipo in DEL_EBOE:
        return _del_eboe(sesion, planta, m, ctx)
    if tipo == "tanque_emulsion":
        primarias = ctx["muestras"][(ctx["muestras"]["tipo"] == PRIMARIA) & (ctx["muestras"]["fase"] == "emulsion")]
        valor = _mediana(primarias["aceite_pct"])
        color, nota = color_de(valor, _rango(sesion, planta.id, "Aceite en emulsión", PRIMARIA, fruta))
        en_marcha = ", ".join(sorted(primarias.loc[primarias["aceite_pct"].notna(), "equipo"].unique())) or "—"
        return {"nombre": "Tanque de emulsión", "valor": valor, "color": color, "destino": {"pagina": "centrifugas"},
                "texto": _texto(valor, " %"), "claves_alertas": [],
                "hover": f"<b>Tanque de emulsión</b> (alimentación a las primarias)<br>Aceite en emulsión: "
                         f"{_texto(valor, ' %')} (mediana del turno; {nota})<br>Primarias: {en_marcha}<br>Caudal de "
                         f"emulsión: {_texto(_mediana(primarias['caudal_lh']), ' L/h', 0)} · Sólidos: "
                         f"{_texto(_mediana(primarias['solidos_pct']), ' %', 1)}<br>N = "
                         f"{primarias['aceite_pct'].notna().sum()} muestras"}
    if tipo == "retorno_pesada":
        pesada = ctx["muestras"][(ctx["muestras"]["tipo"] == PRIMARIA) & (ctx["muestras"]["fase"] == "fase_pesada")]
        valor = _mediana(pesada["aceite_pct"])
        color, nota = color_de(valor, _rango(sesion, planta.id, "Aceite en fase pesada", PRIMARIA, fruta))
        balance = ctx["balance"]
        caudales = balance[(balance["tipo"] == PRIMARIA)].groupby("equipo")["caudal_pesada"].median().dropna() \
            if not balance.empty else pd.Series(dtype=float)
        return {"nombre": "Retorno de fase pesada", "valor": valor, "color": color, "destino": {"pagina": "centrifugas"},
                "texto": _texto(valor, " %"), "claves_alertas": [],
                "hover": f"<b>Tanque de retorno de fase pesada</b><br>Aceite en fase pesada: {_texto(valor, ' %', 3)} "
                         f"(mediana del turno de las primarias; {nota})<br>Caudal total de fase pesada: "
                         f"{_texto(caudales.sum() if len(caudales) else None, ' L/h', 0)}<br>N = "
                         f"{pesada['aceite_pct'].notna().sum()} muestras"}
    if tipo.startswith("primaria"):
        return _primaria(sesion, planta, m, ctx)
    # secundaria y pulidora
    codigos = m.get("equipos") or [m["equipo"]]
    muestras = ctx["muestras"]
    con_datos = muestras[muestras["codigo"].isin(codigos)]
    if tipo == "secundaria":
        nombres = con_datos["equipo"].value_counts()
        nombre = " + ".join(nombres.index) if len(nombres) else "Secundaria"
        equipo = equipos.get(con_datos["codigo"].mode().iloc[0]) if len(con_datos) else None
    else:
        equipo = equipos.get(codigos[0])
        nombre = equipo.nombre if equipo else codigos[0]
    tipo_equipo = equipo.tipo if equipo else {"secundaria": "Centrífuga secundaria", "pulidora": "Pulidora"}[tipo]
    claves = [(e.id, "centrifuga") for c, e in equipos.items() if c in codigos]
    destino = {"pagina": "centrifugas"}
    if tipo == "pulidora":
        valor = _media(con_datos.loc[con_datos["fase"] == "descarga", "t_descarga_s"] / 60)
        color, nota = color_de(valor, _rango(sesion, planta.id, "Intervalo de descarga", tipo_equipo, fruta))
        return {"nombre": nombre, "valor": valor, "color": color, "destino": destino, "claves_alertas": claves,
                "texto": _texto(valor, " min", 0),
                "hover": f"<b>{nombre}</b><br>Intervalo de descarga: {_texto(valor, ' min', 0)} ({nota})<br>"
                         f"N = {len(con_datos)} muestras"}
    descarga = con_datos[con_datos["fase"] == "descarga"]
    valor = _media(descarga["aceite_pct"])
    color, nota = color_de(valor, _rango(sesion, planta.id, "Aceite en descarga", tipo_equipo, fruta))
    return {"nombre": nombre, "valor": valor, "color": color, "destino": destino, "claves_alertas": claves,
            "texto": "—" if valor is None else f"{nombre} · {_num(valor)} %",
            "hover": f"<b>{nombre}</b><br>Aceite en descarga: {_texto(valor, ' %')} ({nota})<br>"
                     f"N = {descarga['aceite_pct'].notna().sum()} muestras"}


def _del_eboe(sesion, planta: Planta, m: dict, ctx: dict) -> dict:
    """Marcadores de un eBOE: nombre (con el MTPH), entrada, eficiencia, salida, emulsión y retorno de fase pesada."""
    tipo, analisis, fruta = m["tipo"], ctx["analisis"], ctx["fruta"]
    equipo = ctx["equipos"].get(m["equipo"])
    if equipo is None:
        return {"nombre": m["equipo"], "valor": None, "texto": "equipo inexistente", "color": SIN_DATOS,
                "hover": f"No existe el equipo {m['equipo']}.", "destino": None}
    del_eboe = analisis[analisis["eboe"] == equipo.numero]
    entradas, salidas = del_eboe[del_eboe["lugar"] == "Entrada"], del_eboe[del_eboe["lugar"] == "Salida"]
    destino = {"pagina": "analisis", "eboe": equipo.numero}
    corto = f"eBOE {equipo.numero}"
    claves = [(equipo.id, tipo)] + ([("planta", tipo)] if tipo == "emulsion_eboe" else [])
    if tipo == "nombre_eboe":
        mtph = _mediana(entradas["tph"])
        return {"nombre": corto, "valor": mtph, "color": None, "destino": destino, "claves_alertas": [],
                "texto": f"<b>{corto}</b><br>{_texto(mtph, ' MTPH', 1)}",
                "hover": f"<b>{equipo.nombre}</b><br>MTPH: {_texto(mtph, '', 1)} (mediana del turno)"}
    if tipo == "retorno_fp_eboe":
        lab, calc = _mediana(entradas["caudal_fase_pesada"]), ctx["agua"]
        color, nota = _color_fp(planta, lab, calc)
        return {"nombre": f"Fase pesada a {corto}", "valor": lab, "color": color,
                "destino": {"pagina": "centrifugas"}, "claves_alertas": [],
                "texto": f"{_texto(lab, ' m³/h', 1)}<br><span style='font-size:10px'>calc. {_texto(calc, '', 1)}</span>",
                "hover": f"<b>Fase pesada que entra al {corto}</b><br>Laboratorio del BOE: {_texto(lab, ' m³/h', 1)} "
                         f"(mediana del turno)<br>Calculado (agua por eBOE, centrífugas): {_texto(calc, ' m³/h', 1)}"
                         f"<br>{nota}"}
    if tipo == "salida_eboe":
        valor = _media(salidas["aceite_fruta"])
        color, nota = color_de(valor, None)
        return {"nombre": f"Salida {corto}", "valor": valor, "color": color, "destino": destino,
                "texto": _texto(valor, " kg/MT"), "claves_alertas": claves,
                "hover": f"<b>Salida {corto}</b><br>Aceite en fruta de salida: {_texto(valor, ' kg/MT')}<br>"
                         f"N = {len(salidas)} muestras · {nota}"}
    if tipo == "emulsion_eboe":
        de_emulsion = ctx["emulsion"][ctx["emulsion"]["eboe"] == equipo.numero]
        valor = _media(de_emulsion["aceite_emulsion"])
        color, nota = color_de(valor, _rango(sesion, planta.id, "Aceite en emulsión", "Emulsión (licor)", fruta))
        return {"nombre": f"Emulsión {corto}", "valor": valor, "color": color, "destino": destino,
                "texto": _texto(valor, " %"), "claves_alertas": claves,
                "hover": f"<b>Emulsión {corto}</b> (rebalse, laboratorio del BOE)<br>Aceite en emulsión: "
                         f"{_texto(valor, ' %')} ({nota})<br>°Brix: {_texto(_media(de_emulsion['brix']), '', 1)} · pH: "
                         f"{_texto(_media(de_emulsion['ph']), '')}<br>N = {de_emulsion['aceite_emulsion'].notna().sum()}"}
    if tipo == "entrada_eboe":
        valor = _media(entradas["aceite_fruta"])
        frutos = _media(entradas["n_frutos"] * 10 / entradas["peso_fruta_kg"].where(entradas["peso_fruta_kg"] > 0))
        color, nota = color_de(valor, None)
        return {"nombre": f"Entrada {corto}", "valor": valor, "texto": _texto(valor, " kg/MT"), "color": color,
                "destino": destino, "claves_alertas": [],
                "hover": f"<b>Entrada {corto}</b><br>Aceite en fruta: {_texto(valor, ' kg/MT')}<br>Frutos en 10 kg: "
                         f"{_texto(frutos, '', 0)}<br>N = {len(entradas)} muestras · {nota}"}
    eficiencia = _media(entradas["eficiencia_par"] * 100)
    color, nota = color_de(eficiencia, _rango(sesion, planta.id, "Eficiencia eBOE", "BOE (todos)", fruta))
    programas = entradas["programa"].dropna()
    return {"nombre": corto, "valor": eficiencia, "color": color, "destino": destino, "claves_alertas": claves,
            "texto": "—" if eficiencia is None else f"ef. {_num(eficiencia, 1)} %",
            "hover": f"<b>{equipo.nombre}</b><br>Eficiencia: {_texto(eficiencia, ' %', 1)} ({nota})<br>Programa: "
                     f"{programas.mode().iloc[0] if len(programas) else '—'}<br>"
                     f"N = {entradas['eficiencia_par'].notna().sum()} pares"}


def _primaria(sesion, planta: Planta, m: dict, ctx: dict) -> dict:
    """Marcadores de una centrífuga primaria: estado (nombre; gris si no anduvo), caudal de emulsión, aceite en
    descarga, fase liviana (caudal del balance y % de aceite) y fase pesada (caudal del balance). Si la primaria no
    anduvo en el turno, sus datos de línea quedan en gris y vacíos."""
    tipo, fruta = m["tipo"], ctx["fruta"]
    equipo = ctx["equipos"].get(m["equipo"])
    nombre = equipo.nombre if equipo else m["equipo"]
    muestras = ctx["muestras"][ctx["muestras"]["codigo"] == m["equipo"]]
    balance = ctx["balance"][ctx["balance"]["codigo"] == m["equipo"]] if not ctx["balance"].empty else ctx["balance"]
    destino = {"pagina": "centrifugas"}
    claves = [(equipo.id, "centrifuga")] if equipo else []
    if muestras.empty:
        return {"nombre": nombre, "valor": None, "color": SIN_DATOS, "destino": destino, "claves_alertas": [],
                "texto": f"{nombre} · sin datos" if tipo == "primaria" else "", "vacio": True,
                "hover": f"<b>{nombre}</b><br>No anduvo en el turno (sin muestras)."}
    if tipo == "primaria":
        return {"nombre": nombre, "valor": None, "color": EN_MARCHA, "destino": destino, "claves_alertas": [],
                "texto": nombre, "hover": f"<b>{nombre}</b><br>En marcha en el turno · N = {len(muestras)} muestras"}
    if tipo == "primaria_caudal":
        valor = _mediana(muestras.loc[muestras["fase"] == "emulsion", "caudal_lh"])
        color, nota = color_de(valor, _rango(sesion, planta.id, "Caudal de emulsión", PRIMARIA, fruta))
        return {"nombre": f"Caudal {nombre}", "valor": valor, "color": color, "destino": destino, "claves_alertas": [],
                "texto": _texto(valor, " L/h", 0),
                "hover": f"<b>{nombre}: caudal de emulsión</b><br>{_texto(valor, ' L/h', 0)} (mediana del turno; {nota})"}
    if tipo == "primaria_descarga":
        descarga = muestras[muestras["fase"] == "descarga"]
        valor = _media(descarga["aceite_pct"])
        color, nota = color_de(valor, _rango(sesion, planta.id, "Aceite en descarga", PRIMARIA, fruta))
        return {"nombre": f"Descarga {nombre}", "valor": valor, "color": color, "destino": destino,
                "claves_alertas": claves, "texto": _texto(valor, " %"),
                "hover": f"<b>{nombre}: aceite en descarga</b><br>{_texto(valor, ' %')} ({nota})<br>Sólidos: "
                         f"{_texto(_mediana(muestras['solidos_pct']), ' %', 1)} · CP pesada: "
                         f"{_texto(_mediana(muestras['cp_pesada_bar']), ' bar', 1)}<br>N = "
                         f"{descarga['aceite_pct'].notna().sum()} muestras"}
    if tipo == "primaria_liviana":
        caudal = _mediana(balance["caudal_liviana"]) if not balance.empty else None
        aceite = _mediana(muestras.loc[muestras["fase"] == "fase_liviana", "aceite_pct"])
        color, nota = color_de(aceite, _rango(sesion, planta.id, "Aceite en fase liviana", PRIMARIA, fruta))
        return {"nombre": f"Fase liviana {nombre}", "valor": aceite, "color": color, "destino": destino,
                "claves_alertas": [], "texto": f"{_texto(caudal, ' L/h', 0)} · {_texto(aceite, ' %', 0)}",
                "hover": f"<b>{nombre}: fase liviana</b><br>Caudal: {_texto(caudal, ' L/h', 0)} (balance)<br>Aceite: "
                         f"{_texto(aceite, ' %', 1)} ({nota})"}
    caudal = _mediana(balance["caudal_pesada"]) if not balance.empty else None
    color, nota = color_de(caudal, None)
    return {"nombre": f"Fase pesada {nombre}", "valor": caudal, "color": color, "destino": destino,
            "claves_alertas": [], "texto": _texto(caudal, " L/h", 0),
            "hover": f"<b>{nombre}: fase pesada</b><br>Caudal: {_texto(caudal, ' L/h', 0)} (balance; {nota})"}


def _color_fp(planta: Planta, lab, calc) -> tuple[str, str]:
    """Semáforo del caudal de fase pesada que entra al eBOE: |lab − calc| / calc (%) contra las tolerancias de la
    planta (verde hasta tolerancia_fp_verde_pct, amarillo hasta tolerancia_fp_amarillo_pct, rojo más)."""
    if lab is None:
        return SIN_DATOS, "sin dato del laboratorio en el turno"
    if calc is None or calc <= 0:
        return NEUTRO, "sin caudal calculado (centrífugas)"
    verde, amarillo = planta.tolerancia_fp_verde_pct, planta.tolerancia_fp_amarillo_pct
    diferencia = abs(lab - calc) / calc * 100
    if verde is None or amarillo is None:
        return NEUTRO, f"diferencia {_num(diferencia, 0)} % (tolerancias de la planta a definir)"
    color = VERDE if diferencia <= verde else AMARILLO if diferencia <= amarillo else ROJO
    return color, f"diferencia {_num(diferencia, 0)} % (verde ≤ {_num(verde, 0)} %, amarillo ≤ {_num(amarillo, 0)} %)"


def _texto(valor, unidad: str, decimales: int = 2) -> str:
    return "—" if valor is None else f"{_num(valor, decimales)}{unidad}"


def _mediana(serie) -> float | None:
    valores = pd.to_numeric(pd.Series(serie), errors="coerce").dropna()
    return float(valores.median()) if len(valores) else None


def _agua(planta: Planta, analisis: pd.DataFrame, muestras: pd.DataFrame, balance: pd.DataFrame) -> float | None:
    """Agua por eBOE (m³/h) del turno, calculada desde las centrífugas (core.analisis_centrifugas.agua_por_eboe)."""
    if muestras[muestras["tipo"] == PRIMARIA].empty or planta.umbral_paralelo_lh is None or balance.empty:
        return None
    agua = agua_por_eboe(balance, primarias_por_turno(muestras, planta.umbral_paralelo_lh), analisis)
    return float(agua["agua_por_eboe_m3h"].iloc[0]) if len(agua) else None


def _alertas_por_marcador(sesion, planta_id: int, equipos: dict) -> dict:
    """Alertas abiertas o en análisis por clave de marcador: {clave: {"n", "ambitos"}}. Claves: (equipo_id, grupo)
    con el grupo de GRUPO_ALERTA (eboe, salida_eboe, emulsion_eboe) o "centrifuga"; ("planta", "emulsion_eboe") para
    el pH, que es de la planta. Las comunes a los eBOE (ámbito planta) cuentan en los tres. ambitos: lo que se filtra
    en Alertas (el nombre del equipo, o "Planta")."""
    por_id = {e.id: e for e in equipos.values()}
    eboes = [e for e in equipos.values() if e.tipo == "BOE"]
    resultado = {}
    for a in sesion.scalars(select(Alerta).where(Alerta.estado.in_(ABIERTAS))):
        if (a.equipo_id is not None and a.equipo_id not in por_id) or (a.equipo_id is None and a.planta_id != planta_id):
            continue
        grupo = GRUPO_ALERTA.get(a.variable, "centrifuga")
        if a.equipo_id is not None:
            claves = [(a.equipo_id, grupo)]
        elif a.variable == "ph":
            claves = [("planta", grupo)]
        else:
            claves = [(e.id, grupo) for e in eboes]
        for clave in claves:
            entrada = resultado.setdefault(clave, {"n": 0, "ambitos": set()})
            entrada["n"] += 1
            entrada["ambitos"].add(por_id[a.equipo_id].nombre if a.equipo_id is not None
                                   else "Planta (común eBOE)" if a.comun_eboe else "Planta")   # como en Alertas
    return resultado


# ---------------------------------------------------------------- alertas, pendientes y tendencia

def resumen_alertas(sesion, planta_id: int, cantidad: int = 5) -> dict:
    """{"n": alertas abiertas o en análisis, "recientes": las `cantidad` más recientes (listar_alertas)}."""
    lista = listar_alertas(sesion, planta_id, estados=ABIERTAS)
    return {"n": len(lista), "recientes": lista.head(cantidad)}


def rondas_esperadas(planta: Planta, dia: date, turno) -> list[datetime]:
    """Fechas y horas de las rondas de Scott del turno (parámetro horas_rondas), en orden."""
    inicio, fin = inicio_de_turno(planta, dia, turno), fin_de_turno(planta, dia, turno)
    esperadas = []
    for hora in leer_horas_rondas(planta.horas_rondas):
        for fecha in (inicio.date(), inicio.date() + timedelta(days=1)):
            momento = datetime.combine(fecha, hora)
            if inicio <= momento < fin:
                esperadas.append(momento)
    return sorted(esperadas)


def pendientes(sesion, planta: Planta, ahora: datetime, dia: date) -> dict:
    """Lo que falta cargar:
    - rondas: si `dia` es el de hoy, las rondas esperadas del turno en curso cuya hora ya pasó (con 60 min de
      tolerancia) y que no tienen análisis entre 15 min antes y 60 min después; {"turno", "faltan", "hechas",
      "esperadas"} (None si `dia` no es hoy o la planta no tiene horas_rondas).
    - sin_produccion: turnos cerrados de los DIAS_PRODUCCION días hasta `dia` con análisis Scott pero sin ningún
      registro de producción (ni borrador): [(fecha, turno)].
    - borradores: registros de producción en borrador.
    """
    resultado = {"rondas": None, "sin_produccion": [], "borradores": 0}
    equipos = _equipos(sesion, planta.id)
    if planta.horas_rondas and fecha_produccion(planta, ahora) == dia:
        turno = turno_de(planta, ahora)
        esperadas = rondas_esperadas(planta, dia, turno)
        momentos = list(sesion.scalars(select(AnalisisScott.fecha_hora).distinct().join(AnalisisScott.punto_muestreo)
                                       .where(PuntoMuestreo.equipo_id.in_(equipos),
                                              AnalisisScott.fecha_hora >= inicio_de_turno(planta, dia, turno) - ANTES_RONDA,
                                              AnalisisScott.fecha_hora < fin_de_turno(planta, dia, turno))))
        hechas = [e for e in esperadas if any(e - ANTES_RONDA <= m < e + DESPUES_RONDA for m in momentos)]
        faltan = [e for e in esperadas if e not in hechas and e + DESPUES_RONDA <= ahora]
        resultado["rondas"] = {"turno": turno.nombre, "faltan": faltan, "hechas": len(hechas),
                               "esperadas": len(esperadas)}
    desde, _ = _ventana(planta, dia - timedelta(days=DIAS_PRODUCCION - 1))
    _, hasta = _ventana(planta, dia)
    momentos = sesion.scalars(select(AnalisisScott.fecha_hora).distinct().join(AnalisisScott.punto_muestreo).where(
        PuntoMuestreo.equipo_id.in_(equipos), AnalisisScott.fecha_hora >= desde, AnalisisScott.fecha_hora < hasta))
    con_analisis = {}
    for m in momentos:
        t = turno_de(planta, m)
        con_analisis[(fecha_produccion(planta, m), t.nombre)] = t
    cargados = {(r.fecha_produccion, r.turno.nombre) for r in sesion.scalars(select(RegistroProduccion).where(
        RegistroProduccion.planta_id == planta.id,
        RegistroProduccion.fecha_produccion >= dia - timedelta(days=DIAS_PRODUCCION - 1),
        RegistroProduccion.fecha_produccion <= dia))}
    resultado["sin_produccion"] = sorted(
        [(d, n) for (d, n), t in con_analisis.items() if (d, n) not in cargados and fin_de_turno(planta, d, t) <= ahora],
        key=lambda par: inicio_de_turno(planta, par[0], con_analisis[par]))
    resultado["borradores"] = sesion.scalar(select(func.count(RegistroProduccion.id)).where(
        RegistroProduccion.planta_id == planta.id, RegistroProduccion.estado == "borrador"))
    return resultado


def tendencia_semanal(sesion, planta: Planta, dia: date, semanas: int = SEMANAS) -> pd.DataFrame:
    """Eficiencia eBOE (%) y aceite en salida (kg/MT) por eBOE y por semana, las `semanas` semanas que terminan en
    `dia` (semana 0 = los 7 días hasta `dia`), ponderados por las toneladas aprobadas del turno (promedio simple si
    no hay). Columnas: semana (0 = la más reciente), desde, hasta, etiqueta ("20/10–26/10"), eboe, variable
    ("eficiencia" o "aceite_salida"), valor, n y ponderada."""
    inicio, _ = _ventana(planta, dia - timedelta(days=7 * semanas - 1))
    _, fin = _ventana(planta, dia)
    fruta = tabla_analisis(sesion, planta.id, desde=inicio, hasta=fin, con_plc=False)
    columnas = ["semana", "desde", "hasta", "etiqueta", "eboe", "variable", "valor", "n", "ponderada"]
    if fruta.empty:
        return pd.DataFrame(columns=columnas)
    pares = pares_con_emulsion(fruta, SIN_EMULSION)
    toneladas = toneladas_por_turno(sesion, planta.id)
    pares = pares.assign(semana=(pd.Timestamp(dia) - pares["fecha_produccion"]).dt.days // 7)
    filas = []
    for (semana, eboe), sub in pares.groupby(["semana", "eboe"]):
        hasta = dia - timedelta(days=7 * int(semana))
        desde = hasta - timedelta(days=6)
        for variable, columna in (("eficiencia", "eficiencia_pct"), ("aceite_salida", "aceite_salida")):
            r = media_ponderada_por_turno(sub, columna, toneladas)
            filas.append({"semana": int(semana), "desde": desde, "hasta": hasta,
                          "etiqueta": f"{desde:%d/%m}–{hasta:%d/%m}", "eboe": eboe, "variable": variable,
                          "valor": r["valor"], "n": r["n"], "ponderada": r["ponderada"]})
    return pd.DataFrame(filas, columns=columnas).sort_values(["variable", "eboe", "semana"], ascending=[True, True, False],
                                                             ignore_index=True)


def objetivo_eficiencia(sesion, planta: Planta, dia: date, semanas: int = SEMANAS) -> dict | None:
    """Rango de la eficiencia eBOE (banda objetivo de la tendencia) para la fruta más frecuente de las últimas
    `semanas` semanas hasta `dia`."""
    _, fin = _ventana(planta, dia)
    frutas = sesion.scalars(select(AnalisisScott.fruta).join(AnalisisScott.punto_muestreo).where(
        PuntoMuestreo.equipo_id.in_(_equipos(sesion, planta.id)), AnalisisScott.fecha_hora < fin,
        AnalisisScott.fecha_hora >= fin - timedelta(days=7 * semanas), AnalisisScott.fruta.is_not(None))).all()
    fruta = pd.Series(frutas).mode().iloc[0] if frutas else None
    return _rango(sesion, planta.id, "Eficiencia eBOE", "BOE (todos)", fruta)
