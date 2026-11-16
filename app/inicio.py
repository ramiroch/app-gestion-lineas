"""Página de inicio: cómo vienen los equipos y qué está pendiente, en 10 segundos (tarea 21).

Encabezado con el logo de la planta y el de Brown; período (por defecto, la última semana con datos; un botón por
temporada) y turno; tarjetas de la planta en el período, las mismas de Análisis › Período y › Proceso (proceso: fruta,
aceite en entrada, eficiencia, aceite en salida; producción: rendimientos de aceite y de jugo concentrado, alertas
abiertas); flowsheet de la planta con un semáforo por equipo (clic → la página del equipo, o Alertas desde la 🔔),
alertas abiertas, pendientes (rondas y producción solo con la temporada en marcha; siempre sobre hoy) y la tendencia
semanal por eBOE hasta la fecha "hasta". Solo muestra: la lógica está en core/inicio.py.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.cartas_periodo import parametros_control
from core.db import crear_sesion
from core.formato import numero
from core.inicio import (cargar_flowsheet, datos_flowsheet, logo_planta, objetivo_eficiencia, pendientes,
                         periodo_por_defecto, resumen_alertas, tarjetas_periodo, temporadas_con_datos,
                         tendencia_semanal, turnos_del_periodo)
from core.models import Planta
from core.turnos import fecha_produccion
from estilo import encabezado_cobrand, titulo_planta
from filtros import PLANTA_POR_DEFECTO, fijar_filtros, motor, plantas
from graficos import flowsheet, tendencia_semanal as grafico_tendencia

st.set_page_config(page_title="Inicio", layout="wide")
cartel_modo_prueba()

PAGINAS = {"analisis": "pages/2_Analisis.py", "centrifugas": "pages/6_Centrifugas.py",
           "alertas": "pages/9_Alertas.py", "control": "pages/3_Control.py"}
TITULO = "Gestión de líneas de aceite y jugo"

etiquetas = list(plantas())
if not etiquetas:
    st.title(TITULO)
    st.warning("No hay plantas cargadas. Corré python -m core.importar_maestros.")
    st.stop()
if "inicio_planta" not in st.session_state:
    st.session_state["inicio_planta"] = next((e for e in etiquetas if e == PLANTA_POR_DEFECTO), etiquetas[0])
planta_id = plantas()[st.sidebar.selectbox("Planta", etiquetas, key="inicio_planta")]

with crear_sesion(motor()) as sesion:
    planta = sesion.get(Planta, planta_id)
    list(planta.turnos)
    por_defecto = periodo_por_defecto(sesion, planta, date.today())
    temporadas = temporadas_con_datos(sesion, planta)
    sesion.expunge(planta)
encabezado = st.container()          # se completa cuando se sabe el período y el turno
if por_defecto is None:
    encabezado.html(encabezado_cobrand(logo_planta(planta.nombre), planta.nombre, TITULO, ""))
    st.info("Esta planta todavía no tiene análisis ni muestras de centrífugas.")
    st.stop()

# ---------------------------------------------------------------- período y turno

TODOS = "Todos"
CLAVE_FECHAS = f"inicio_fechas_{planta_id}"
if CLAVE_FECHAS not in st.session_state:
    st.session_state[CLAVE_FECHAS] = por_defecto


def _poner_periodo(desde_, hasta_) -> None:
    st.session_state[CLAVE_FECHAS] = (desde_, hasta_)


uno, dos, tres = st.columns([1.5, 2.3, 1.7], vertical_alignment="bottom")
fechas = uno.date_input("Período (desde – hasta)", key=CLAVE_FECHAS, format="DD/MM/YYYY",
                        help="Días de producción. Por defecto, la última semana con datos: los 7 días que terminan en "
                             "el último día con análisis Scott o muestras de centrífugas.")
with dos.container(horizontal=True, gap="small"):
    st.button("Última semana", on_click=_poner_periodo, args=por_defecto, key=f"inicio_semana_{planta_id}",
              help=f"Del {por_defecto[0]:%d/%m/%Y} al {por_defecto[1]:%d/%m/%Y}")
    for temporada, (primero, ultimo) in temporadas.items():
        st.button(str(temporada), on_click=_poner_periodo, args=(primero, ultimo),
                  key=f"inicio_temporada_{planta_id}_{temporada}",
                  help=f"Temporada {temporada}: del {primero:%d/%m/%Y} al {ultimo:%d/%m/%Y} (primer y último día con "
                       "datos)")
fechas = list(fechas) if isinstance(fechas, (list, tuple)) else [fechas]
desde, hasta = (fechas[0], fechas[-1]) if fechas else por_defecto     # mientras se elige el rango: un día
nombres = [t.nombre for t in sorted(planta.turnos, key=lambda t: (t.hora_inicio < planta.hora_inicio_dia,
                                                                  t.hora_inicio))]
turno = tres.segmented_control("Turno", [TODOS] + nombres, default=TODOS, key=f"inicio_turno_{planta_id}",
                               help="Con un período y un turno se toman todos los turnos de ese tipo (por ejemplo, "
                                    "todos los N de la semana).") or TODOS
elegido = None if turno == TODOS else turno
with crear_sesion(motor()) as sesion:
    turnos = turnos_del_periodo(sesion, planta, desde, hasta, elegido)
    tarjetas = tarjetas_periodo(sesion, planta, desde, hasta, elegido)
periodo = f"{desde:%d/%m/%Y}" if desde == hasta else f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"
encabezado.html(encabezado_cobrand(
    logo_planta(planta.nombre), planta.nombre, TITULO,
    f"Período: {periodo} · turnos: {'todos' if elegido is None else elegido} · {len(turnos)} turno"
    f"{'' if len(turnos) == 1 else 's'} con datos"))

# ---------------------------------------------------------------- tarjetas de la planta en el período


def _n(cantidad: int, que: str) -> str:
    """"N = 15 turnos", con el aviso si N < 3."""
    return f"N = {numero(cantidad, 0)} {que}" + (" · ⚠️ N < 3: poco confiable" if cantidad < 3 else "")


def _ponderada(media: dict) -> str:
    """Cómo se calculó una media de Análisis › Período, con su N."""
    if media["ponderada"]:
        como = (f"media de cada turno ponderada por sus toneladas aprobadas ({numero(media['n_ponderados'], 0)} de "
                f"{numero(media['n'], 0)} muestras en turnos con producción)")
    else:
        como = "promedio simple: el período no tiene toneladas aprobadas"
    return f"{como}. {_n(media['n'], 'muestra' if media['n'] == 1 else 'muestras')}."


def _valor(valor, unidad: str, decimales: int) -> str:
    return "—" if valor is None else f"{numero(valor, decimales)} {unidad}"


borradores = tarjetas["borradores"]
aviso_borradores = (f" ⚠️ {borradores} turno{'s' if borradores != 1 else ''} del período en borrador: no "
                    f"entra{'n' if borradores != 1 else ''} hasta aprobarlo{'s' if borradores != 1 else ''}."
                    if borradores else "")
st.markdown("##### Proceso")
columnas = st.columns(4)
fruta = tarjetas["fruta"]
columnas[0].metric("Fruta procesada", _valor(fruta["valor"], "t", 0), border=True,
                   help=f"Σ fruta procesada de los turnos aprobados del período (como Análisis › Proceso). "
                        f"{_n(fruta['n'], 'turnos')}.{aviso_borradores}")
for columna, clave, titulo, unidad, decimales, detalle in (
        (columnas[1], "entrada", "Aceite en fruta de entrada", "kg/MT", 2,
         "Aceite en fruta de entrada de los eBOE juntos"),
        (columnas[2], "eficiencia", "Eficiencia eBOE", "%", 1,
         "Eficiencia de los pares entrada/salida de los eBOE juntos"),
        (columnas[3], "salida", "Aceite en salida", "kg/MT", 2, "Aceite en fruta de salida de los eBOE juntos")):
    columna.metric(titulo, _valor(tarjetas[clave]["valor"], unidad, decimales), border=True,
                   help=f"{detalle}, como Análisis › Período: {_ponderada(tarjetas[clave])}")

st.markdown("##### Producción")
columnas = st.columns(4)
aceite, jugo = tarjetas["rendimiento_aceite"], tarjetas["rendimiento_concentrado"]
columnas[0].metric("Rendimiento de aceite", _valor(aceite["valor"], "kg/MT", 2), border=True,
                   help="Σ aceite envasado / Σ fruta procesada de los turnos aprobados con los dos datos (como "
                        f"Análisis › Proceso). {_n(aceite['n'], 'turnos')}.{aviso_borradores}")
columnas[1].metric("Rendimiento de jugo concentrado 65 °Bx", _valor(jugo["valor"], "kg/MT", 1), border=True,
                   delta=jugo["nota"] or None, delta_color="off", delta_arrow="off",
                   help="Σ jugo concentrado 65 °Bx / Σ fruta procesada de los días con concentrado (se envasa por "
                        "tanda: por día o período, nunca por turno; como Análisis › Proceso). Solo turnos aprobados. "
                        f"{_n(jugo['n'], 'días')}.{aviso_borradores}"
                        + (f" {jugo['nota']}: el concentrado de esa temporada no tiene el °Bx verificado."
                           if jugo["nota"] else ""))
columnas[2].metric("Alertas abiertas", numero(tarjetas["alertas"], 0), border=True,
                   help="Alertas abiertas o en análisis de la planta (no dependen del período).")
if borradores:
    st.caption(aviso_borradores.strip() + " Se aprueban en la página Producción.")

# ---------------------------------------------------------------- flowsheet

try:
    config = cargar_flowsheet(planta.nombre)
except ValueError as error:
    config = None
    st.warning(f"El flowsheet de la planta está mal configurado: {error}")
if config is not None:
    st.html(titulo_planta(f"Flowsheet · {planta.nombre}"))
    with crear_sesion(motor()) as sesion:
        marcadores = datos_flowsheet(sesion, planta, config, desde, hasta, elegido)
    evento = st.plotly_chart(flowsheet(config, marcadores), on_select="rerun", selection_mode="points",
                             key=f"inicio_flowsheet_{planta_id}_{desde}_{hasta}_{turno}", width="content")
    st.caption("Valores del período: eficiencia y aceite en salida ponderados por las toneladas aprobadas; el resto, "
               "mediana de los turnos. Círculo: semáforo contra el rango de referencia (gris: sin datos; azul: sin "
               "rango o rango en revisión). 🔔: alertas cuyo episodio toca el período, en cualquier estado. Pasá el "
               "mouse para ver el detalle y el N; clic en un valor para abrir su carta de control con este período, en el "
               "nombre de un eBOE para ir a Análisis, o en la 🔔 para ver sus alertas.")
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
        if destino.get("pagina") == "control":         # Control › Cartas de control con la variable y el período
            st.switch_page(PAGINAS["control"], query_params=parametros_control(
                destino["variable"], destino["equipo"], desde, hasta, elegido, planta_id))
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
with izquierda.container(border=True, key="tarjeta_alertas"):
    st.markdown(f"#### Alertas abiertas o en análisis: {alertas['n']}")
    if alertas["n"]:
        recientes = alertas["recientes"]
        st.dataframe(pd.DataFrame({"Inicio": recientes["inicio"].dt.strftime("%d/%m %H:%M"),
                                   "Variable": recientes["nombre"], "Ámbito": recientes["ambito"],
                                   "Regla": recientes["regla"], "Estado": recientes["estado"]}), hide_index=True)
    else:
        st.caption("No hay alertas abiertas.")
    st.page_link(PAGINAS["alertas"], label="Ir a Alertas", icon="🔔")
with derecha.container(border=True, key="tarjeta_pendientes"):
    st.markdown("#### Pendientes")
    rondas = falta["rondas"]
    if not falta["en_temporada"]:
        ultima = falta["ultima_ronda"]
        st.info("Fuera de temporada: sin carga de laboratorio " + (
            f"desde el {fecha_produccion(planta, ultima):%d/%m/%Y}." if ultima else "todavía."))
    elif rondas is None:
        st.caption("Rondas de Scott: se controlan en el día de hoy (turno en curso).")
    elif rondas["faltan"]:
        st.warning(f"Turno {rondas['turno']}: faltan las rondas de las "
                   + ", ".join(f"{m:%H:%M}" for m in rondas["faltan"])
                   + f" ({rondas['hechas']} de {rondas['esperadas']} hechas).")
    else:
        st.caption(f"Turno {rondas['turno']}: rondas al día ({rondas['hechas']} de {rondas['esperadas']}).")
    st.page_link("pages/7_Carga_Scott.py", label="Cargar una ronda de Scott", icon="🧪")
    if not falta["en_temporada"]:
        pass                                          # fuera de temporada no se busca producción faltante
    elif falta["sin_produccion"]:
        st.warning("Turnos sin producción cargada: " + ", ".join(f"{d:%d/%m} {t}" for d, t in falta["sin_produccion"]))
    else:
        st.caption("Producción: no hay turnos de la última semana sin cargar.")
    if falta["borradores"]:
        st.warning(f"{falta['borradores']} registro(s) de producción en borrador sin aprobar.")
    st.page_link("pages/10_Produccion.py", label="Cargar o aprobar producción", icon="🏭")

# ---------------------------------------------------------------- tendencia semanal

with st.container(border=True, key="tarjeta_tendencia"):
    st.markdown("#### Tendencia semanal por eBOE")
    with crear_sesion(motor()) as sesion:
        tabla = tendencia_semanal(sesion, planta, hasta)
        objetivo = objetivo_eficiencia(sesion, planta, hasta)
    if tabla.empty:
        st.info("No hay pares entrada/salida en las últimas 8 semanas.")
    else:
        st.caption(f"Últimas 8 semanas hasta el {hasta:%d/%m/%Y}, ponderadas por las toneladas aprobadas del turno "
                   "(promedio simple si no hay). N en el tooltip; las semanas con N < 3 se ven tenues.")
        uno, dos = st.columns(2)
        uno.plotly_chart(grafico_tendencia(tabla, "eficiencia", "Eficiencia eBOE (%)", "%", 1, objetivo),
                         width="stretch")
        dos.plotly_chart(grafico_tendencia(tabla, "aceite_salida", "Aceite en fruta de salida (kg/MT)", "kg/MT", 2),
                         width="stretch")
