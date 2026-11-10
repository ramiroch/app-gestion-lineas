"""Página Carga Scott: una ronda de análisis por envío (tarea 18).

Para cada eBOE en marcha: entrada, salida, emulsión y operación. Cálculo en vivo con semáforo contra RangoReferencia,
errores que no dejan guardar y avisos que sí. Al guardar: chequeo inmediato del turno en curso y evaluación de los
turnos cerrados; se muestran las alertas nuevas o actualizadas. El orden de los campos permite cargar con Tab.
Solo muestra: la lógica está en core/carga_scott.py.
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba

from core.carga_scott import (CALIDADES, PROGRAMAS, RALLADOS, RESULTADOS, calcular_ronda, cambios_de_alertas,
                              contexto_validacion, estado_alertas, evaluar_turnos_cerrados, guardar_ronda,
                              percentiles_historicos, rangos_resultados, semaforo, validar_ronda, valores_por_defecto)
from core.db import crear_sesion
from core.formato import numero
from core.limites import VARIABLES_CONTROL
from core.models import Planta
from core.turnos import fecha_produccion, turno_de
from filtros import PLANTA_POR_DEFECTO, motor, plantas

MINUTOS_REVISION = 10            # cada cuánto se revisan los turnos cerrados al usar la página
COLORES = {"verde": "🟢", "rojo": "🔴", None: "⚪"}
# Campos de cada lugar: (clave, etiqueta, formato o lista de opciones)
CAMPOS = {
    "entrada": [("g", "g", "%.3f"), ("ml", "mL", "%.2f"), ("peso_fruta_kg", "Peso fruta (kg)", "%.2f"),
                ("n_frutos", "N.º frutos", "%d"), ("calidad", "Calidad", CALIDADES)],
    "salida": [("g", "g", "%.3f"), ("ml", "mL", "%.2f"), ("rallado", "Rallado", RALLADOS)],
    "emulsion": [("g", "g", "%.3f"), ("ml", "mL", "%.2f"), ("brix", "°Brix", "%.1f"), ("ph", "pH", "%.2f")],
    "operacion": [("programa", "Programa", PROGRAMAS), ("tph", "MTPH", "%.1f"),
                  ("caudal_fase_pesada", "Caudal fase pesada (m³/h)", "%.1f")],
}
TITULOS = {"entrada": "Entrada", "salida": "Salida", "emulsion": "Emulsión", "operacion": "Operación"}
ANCHO = 5                         # columnas por fila (la fila más larga: la entrada)

cartel_modo_prueba()
st.title("Carga Scott")


@st.cache_data(ttl=600, show_spinner="Leyendo el histórico de cada punto…")
def percentiles_planta(planta_id: int) -> dict:
    with crear_sesion(motor()) as sesion:
        return percentiles_historicos(sesion, planta_id)


def inicial(clave: str, valor) -> str:
    """Pone el valor inicial de un campo una sola vez (después manda lo que escribió el usuario)."""
    if clave not in st.session_state:
        st.session_state[clave] = valor
    return clave


def campo(donde, clave: str, etiqueta: str, formato):
    """Un campo numérico vacío o una lista de opciones (con "—" para vacío)."""
    if isinstance(formato, tuple):
        return donde.selectbox(etiqueta, (None,) + formato, key=clave, format_func=lambda v: "—" if v is None else v)
    paso = 1 if formato == "%d" else None
    return donde.number_input(etiqueta, value=None, min_value=0 if formato == "%d" else None, step=paso,
                              format=formato, key=clave)


def tabla_alertas(cambios: list[dict]) -> pd.DataFrame:
    filas = []
    for c in cambios:
        a = c["alerta"]
        filas.append({"Cambio": c["cambio"], "Variable": VARIABLES_CONTROL[a.variable]["nombre"],
                      "Ámbito": a.equipo.nombre if a.equipo else "Planta", "Regla": a.regla,
                      "Inmediata": "sí" if a.inmediata else "", "Turnos": a.turnos_afectados,
                      "Inicio": f"{a.fecha_hora:%d/%m/%Y %H:%M}", "Valor": numero(a.valor, 2),
                      "Comentario": a.comentario or ""})
    return pd.DataFrame(filas)


def revisar_turnos_cerrados(planta_id: int, quien: str) -> None:
    """Evaluación completa de los turnos cerrados sin evaluar, como mucho cada MINUTOS_REVISION por sesión."""
    clave = f"_turnos_revisados_{planta_id}"
    ultima = st.session_state.get(clave)
    if ultima and datetime.now() - ultima < timedelta(minutes=MINUTOS_REVISION):
        return
    with crear_sesion(motor()) as sesion:
        antes = estado_alertas(sesion)
        evaluados = evaluar_turnos_cerrados(sesion, sesion.get(Planta, planta_id), datetime.now(), quien)
        if evaluados:
            st.session_state["_resultado_turnos"] = {
                "turnos": evaluados, "alertas": tabla_alertas(cambios_de_alertas(sesion, antes))}
    st.session_state[clave] = datetime.now()


# ---------------------------------------------------------------- planta y quién carga

etiquetas = list(plantas())
if not etiquetas:
    st.warning("No hay plantas cargadas. Corré python -m core.importar_maestros.")
    st.stop()
etiqueta = st.sidebar.selectbox("Planta", etiquetas, key=inicial(
    "carga_planta", next((e for e in etiquetas if e == PLANTA_POR_DEFECTO), etiquetas[0])))
planta_id = plantas()[etiqueta]
# "Cargado por" se recuerda en la sesión (hasta la tarea 22 no hay usuarios).
st.session_state.setdefault("_cargado_por", "")
cargado_por = st.sidebar.text_input("Cargado por", value=st.session_state["_cargado_por"], key="carga_cargado_por")
st.session_state["_cargado_por"] = cargado_por

revisar_turnos_cerrados(planta_id, cargado_por.strip() or "carga Scott")
if "_resultado_turnos" in st.session_state:
    resultado = st.session_state.pop("_resultado_turnos")
    st.info("Turnos cerrados evaluados: " + ", ".join(f"{d:%d/%m} {t}" for d, t in resultado["turnos"]))
    if not resultado["alertas"].empty:
        st.dataframe(resultado["alertas"], hide_index=True)
if "_resultado_guardado" in st.session_state:
    resultado = st.session_state.pop("_resultado_guardado")
    st.success(resultado["mensaje"])
    if resultado["alertas"].empty:
        st.caption("Sin alertas nuevas ni actualizadas.")
    else:
        st.warning("Alertas nuevas o actualizadas:")
        st.dataframe(resultado["alertas"], hide_index=True)

with crear_sesion(motor()) as sesion:
    planta = sesion.get(Planta, planta_id)
    defecto = valores_por_defecto(sesion, planta, datetime.now())
    rangos = rangos_resultados(sesion, planta_id, defecto["fruta"])
    list(planta.turnos)           # se leen ahora: la planta se usa después, fuera de la sesión
    sesion.expunge(planta)

if not defecto["eboes"]:
    st.info("Esta planta no tiene eBOE cargados.")
    st.stop()

# ---------------------------------------------------------------- ronda

st.subheader("Ronda")
uno, dos, tres, cuatro, cinco = st.columns(5)
dia = uno.date_input("Fecha", key=inicial("carga_fecha", defecto["fecha_hora"].date()), format="DD/MM/YYYY")
hora = dos.time_input("Hora", key=inicial("carga_hora", defecto["fecha_hora"].time()), step=timedelta(minutes=5))
procesos = dict(defecto["procesos"])
proceso_id = tres.selectbox("Proceso", list(procesos), format_func=procesos.get,
                            key=inicial("carga_proceso", defecto["proceso_id"]))
analista = cuatro.text_input("Analista", key=inicial("carga_analista", defecto["analista"] or ""))
fruta = cinco.text_input("Fruta", key=inicial("carga_fruta", defecto["fruta"] or ""))
fecha_hora = datetime.combine(dia, hora)
try:
    st.caption(f"Día de producción {fecha_produccion(planta, fecha_hora):%d/%m/%Y} · turno "
               f"{turno_de(planta, fecha_hora).nombre}")
except ValueError as error:
    st.error(str(error))

uno, dos, tres, cuatro, _ = st.columns(5)
normalidad = {}
for donde, lugar, titulo in ((uno, "entrada", "N entrada"), (dos, "salida", "N salida"),
                             (tres, "emulsion", "N emulsión")):
    normalidad[lugar] = donde.number_input(titulo, format="%.4f", step=0.0001, min_value=0.0,
                                           key=inicial(f"carga_n_{lugar}", defecto["normalidad"].get(lugar)))
cuatro.metric("Blanco (de la planta)", "a definir" if planta.blanco_ml is None else f"{numero(planta.blanco_ml, 2)} mL")

columnas = st.columns(len(defecto["eboes"]))
en_marcha = [n for n, donde in zip(defecto["eboes"], columnas)
             if donde.checkbox(f"eBOE {n} en marcha", key=inicial(f"carga_marcha_{n}", n in defecto["en_marcha"]))]

# ---------------------------------------------------------------- un bloque por eBOE en marcha

eboes = {}
for numero_eboe in en_marcha:
    with st.container(border=True):
        st.markdown(f"**eBOE {numero_eboe}**")
        datos = {}
        for lugar, campos in CAMPOS.items():
            columnas = st.columns([1.2] + [1] * ANCHO)
            columnas[0].markdown(f"<div style='padding-top:2rem'>{TITULOS[lugar]}</div>", unsafe_allow_html=True)
            datos[lugar] = {clave: campo(columnas[i + 1], f"carga_{numero_eboe}_{lugar}_{clave}", etiqueta, formato)
                            for i, (clave, etiqueta, formato) in enumerate(campos)}
        eboes[numero_eboe] = datos

ronda = {"fecha_hora": fecha_hora, "proceso_id": proceso_id, "analista": analista, "fruta": fruta or None,
         "normalidad": normalidad, "eboes": eboes}

# ---------------------------------------------------------------- resultados en vivo

if eboes:
    st.subheader("Resultados")
    resultados = calcular_ronda(planta, ronda)
    filas = []
    for numero_eboe, r in resultados.items():
        fila = {"eBOE": f"eBOE {numero_eboe}"}
        for clave, (nombre, unidad, decimales, _) in RESULTADOS.items():
            valor = r[clave]
            texto = "—" if valor is None else f"{numero(valor, decimales)} {unidad}".strip()
            fila[nombre] = f"{COLORES[semaforo(valor, rangos.get(clave))]} {texto}" if clave in rangos else texto
        filas.append(fila)
    st.dataframe(pd.DataFrame(filas), hide_index=True)
    st.caption("🟢 dentro del rango de referencia · 🔴 fuera (respetando el lado: la eficiencia solo por debajo del "
               "mínimo, el °Brix solo por encima del máximo) · sin círculo: sin rango.")

with crear_sesion(motor()) as sesion:
    contexto = contexto_validacion(sesion, planta, ronda, datetime.now()) | {
        "percentiles": percentiles_planta(planta_id)}
errores, avisos = validar_ronda(planta, ronda, contexto)
for error in errores:
    st.error(error)
for aviso in avisos:
    st.warning(aviso)
if not cargado_por.strip():
    st.info("Completá «Cargado por» en la barra lateral para poder guardar.")

if st.button("Guardar ronda", type="primary", disabled=bool(errores) or not cargado_por.strip() or not eboes):
    with crear_sesion(motor()) as sesion:
        try:
            resultado = guardar_ronda(sesion, sesion.get(Planta, planta_id), ronda, cargado_por.strip(),
                                      datetime.now())
        except ValueError as error:
            st.error(str(error))
            st.stop()
        st.session_state["_resultado_guardado"] = {
            "mensaje": f"Ronda del {fecha_hora:%d/%m/%Y %H:%M} guardada: {len(resultado['analisis'])} análisis y "
                       f"{len(resultado['lecturas'])} lecturas de operación.",
            "alertas": tabla_alertas(resultado["alertas"])}
    st.cache_data.clear()                                    # para que la ronda aparezca en Análisis
    for clave in [c for c in st.session_state if str(c).startswith("carga_") and c not in (
            "carga_planta", "carga_cargado_por", "carga_proceso", "carga_analista", "carga_fruta")
            and not str(c).startswith("carga_marcha_")]:
        del st.session_state[clave]                          # campos vacíos y la hora de la ronda siguiente
    st.rerun()
