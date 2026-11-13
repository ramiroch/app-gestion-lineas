"""Página de inicio: cómo vienen los equipos y qué está pendiente, en 10 segundos (tarea 21).

Flowsheet de la planta con un semáforo por equipo sobre un período (desde–hasta, todos los turnos o uno; por
defecto, el último día con datos; clic → la página del equipo, o Alertas desde la 🔔), alertas abiertas, pendientes
(rondas, producción, borradores; siempre sobre hoy) y la tendencia semanal por eBOE hasta la fecha "hasta". Solo muestra: la lógica está en core/inicio.py.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.db import crear_sesion
from core.inicio import (cargar_flowsheet, datos_flowsheet, fecha_referencia, objetivo_eficiencia, pendientes,
                         resumen_alertas, tendencia_semanal, turnos_del_periodo)
from core.models import Planta
from core.turnos import fecha_produccion
from filtros import PLANTA_POR_DEFECTO, fijar_filtros, motor, plantas
from graficos import flowsheet, tendencia_semanal as grafico_tendencia

st.set_page_config(page_title="Inicio", layout="wide")
cartel_modo_prueba()
st.title("Inicio")

PAGINAS = {"analisis": "pages/2_Analisis.py", "centrifugas": "pages/6_Centrifugas.py",
           "alertas": "pages/9_Alertas.py"}

etiquetas = list(plantas())
if not etiquetas:
    st.warning("No hay plantas cargadas. Corré python -m core.importar_maestros.")
    st.stop()
if "inicio_planta" not in st.session_state:
    st.session_state["inicio_planta"] = next((e for e in etiquetas if e == PLANTA_POR_DEFECTO), etiquetas[0])
planta_id = plantas()[st.sidebar.selectbox("Planta", etiquetas, key="inicio_planta")]

with crear_sesion(motor()) as sesion:
    planta = sesion.get(Planta, planta_id)
    list(planta.turnos)
    referencia = fecha_referencia(sesion, planta, date.today())
    sesion.expunge(planta)
if referencia is None:
    st.info("Esta planta todavía no tiene análisis ni muestras de centrífugas.")
    st.stop()

# ---------------------------------------------------------------- período y turno

TODOS = "Todos"
uno, dos, _ = st.columns([1.4, 2, 2.6])
fechas = uno.date_input("Período (desde – hasta)", value=(referencia, referencia), format="DD/MM/YYYY",
                        key=f"inicio_fechas_{planta_id}",
                        help="Días de producción. Por defecto, hoy o el último día con análisis o muestras de "
                             "centrífugas.")
fechas = list(fechas) if isinstance(fechas, (list, tuple)) else [fechas]
desde, hasta = (fechas[0], fechas[-1]) if fechas else (referencia, referencia)   # mientras se elige el rango: un día
nombres = [t.nombre for t in sorted(planta.turnos, key=lambda t: (t.hora_inicio < planta.hora_inicio_dia,
                                                                  t.hora_inicio))]
turno = dos.segmented_control("Turno", [TODOS] + nombres, default=TODOS, key=f"inicio_turno_{planta_id}",
                              help="Con un período y un turno se toman todos los turnos de ese tipo (por ejemplo, "
                                   "todos los N de la semana).") or TODOS
elegido = None if turno == TODOS else turno
with crear_sesion(motor()) as sesion:
    turnos = turnos_del_periodo(sesion, planta, desde, hasta, elegido)
periodo = f"{desde:%d/%m/%Y}" if desde == hasta else f"{desde:%d/%m} al {hasta:%d/%m}"
st.caption(f"Período: {periodo} · turnos: {'todos' if elegido is None else elegido} · {len(turnos)} turno"
           f"{'' if len(turnos) == 1 else 's'} con datos")

# ---------------------------------------------------------------- flowsheet

try:
    config = cargar_flowsheet(planta.nombre)
except ValueError as error:
    config = None
    st.warning(f"El flowsheet de la planta está mal configurado: {error}")
if config is not None:
    with crear_sesion(motor()) as sesion:
        marcadores = datos_flowsheet(sesion, planta, config, desde, hasta, elegido)
    evento = st.plotly_chart(flowsheet(config, marcadores), on_select="rerun", selection_mode="points",
                             key=f"inicio_flowsheet_{planta_id}_{desde}_{hasta}_{turno}", width="content")
    st.caption("Valores del período: eficiencia y aceite en salida ponderados por las toneladas aprobadas; el resto, "
               "mediana de los turnos. Círculo: semáforo contra el rango de referencia (gris: sin datos; azul: sin "
               "rango o rango en revisión). 🔔: alertas cuyo episodio toca el período, en cualquier estado. Pasá el "
               "mouse para ver el detalle y el N; clic en un equipo para ir a su página, o en la 🔔 para ver sus "
               "alertas.")
    puntos = (evento or {}).get("selection", {}).get("points", [])     # clic en un punto del flowsheet
    if puntos and puntos[0].get("customdata"):
        indice, que = puntos[0]["customdata"]
        marcador = marcadores[int(indice)]
        destino = marcador.get("destino") or {}
        eboes = [destino["eboe"]] if destino.get("eboe") else []
        if que == "alerta":                            # Alertas de ese equipo en el período, en cualquier estado
            fijar_filtros(planta_id, eboes, desde, hasta)
            st.session_state.update({"alertas_ambitos": marcador["ambitos"], "alertas_historico": True,
                                     "alertas_cerradas": True, "alertas_superpuestas": True})
            st.switch_page(PAGINAS["alertas"])
        if destino.get("pagina") == "analisis":
            fijar_filtros(planta_id, eboes, desde, hasta, [] if elegido is None else [elegido])
            st.switch_page(PAGINAS["analisis"])
        elif destino.get("pagina") in PAGINAS:
            st.switch_page(PAGINAS[destino["pagina"]])

# ---------------------------------------------------------------- alertas y pendientes

izquierda, derecha = st.columns(2)
with crear_sesion(motor()) as sesion:
    alertas = resumen_alertas(sesion, planta_id)
    ahora = datetime.now()
    falta = pendientes(sesion, planta, ahora, fecha_produccion(planta, ahora))   # siempre sobre hoy
with izquierda.container(border=True):
    st.markdown(f"**Alertas abiertas o en análisis: {alertas['n']}**")
    if alertas["n"]:
        recientes = alertas["recientes"]
        st.dataframe(pd.DataFrame({"Inicio": recientes["inicio"].dt.strftime("%d/%m %H:%M"),
                                   "Variable": recientes["nombre"], "Ámbito": recientes["ambito"],
                                   "Regla": recientes["regla"], "Estado": recientes["estado"]}), hide_index=True)
    else:
        st.caption("No hay alertas abiertas.")
    st.page_link(PAGINAS["alertas"], label="Ir a Alertas", icon="🔔")
with derecha.container(border=True):
    st.markdown("**Pendientes**")
    rondas = falta["rondas"]
    if rondas is None:
        st.caption("Rondas de Scott: se controlan en el día de hoy (turno en curso).")
    elif rondas["faltan"]:
        st.warning(f"Turno {rondas['turno']}: faltan las rondas de las "
                   + ", ".join(f"{m:%H:%M}" for m in rondas["faltan"])
                   + f" ({rondas['hechas']} de {rondas['esperadas']} hechas).")
    else:
        st.caption(f"Turno {rondas['turno']}: rondas al día ({rondas['hechas']} de {rondas['esperadas']}).")
    st.page_link("pages/7_Carga_Scott.py", label="Cargar una ronda de Scott", icon="🧪")
    if falta["sin_produccion"]:
        st.warning("Turnos sin producción cargada: " + ", ".join(f"{d:%d/%m} {t}" for d, t in falta["sin_produccion"]))
    else:
        st.caption("Producción: no hay turnos de la última semana sin cargar.")
    if falta["borradores"]:
        st.warning(f"{falta['borradores']} registro(s) de producción en borrador sin aprobar.")
    st.page_link("pages/10_Produccion.py", label="Cargar o aprobar producción", icon="🏭")

# ---------------------------------------------------------------- tendencia semanal

st.subheader("Tendencia semanal por eBOE")
with crear_sesion(motor()) as sesion:
    tabla = tendencia_semanal(sesion, planta, hasta)
    objetivo = objetivo_eficiencia(sesion, planta, hasta)
if tabla.empty:
    st.info("No hay pares entrada/salida en las últimas 8 semanas.")
else:
    st.caption(f"Últimas 8 semanas hasta el {hasta:%d/%m/%Y}, ponderadas por las toneladas aprobadas del turno (promedio "
               "simple si no hay). N en el tooltip; las semanas con N < 3 se ven tenues.")
    uno, dos = st.columns(2)
    uno.plotly_chart(grafico_tendencia(tabla, "eficiencia", "Eficiencia eBOE (%)", "%", 1, objetivo),
                     width="stretch")
    dos.plotly_chart(grafico_tendencia(tabla, "aceite_salida", "Aceite en fruta de salida (kg/MT)", "kg/MT", 2),
                     width="stretch")
