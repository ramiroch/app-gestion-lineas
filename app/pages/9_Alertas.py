"""Página Alertas: lista con filtros, detalle con la carta del período y los datos, causa / acción / comentario y
cambio de estado (tarea 19).

Usa las fechas de producción de la barra de filtros global (inicio de la alerta). Por defecto muestra las abiertas y
en análisis; opciones para ver las histórico y las cerradas. Estados: abierta → en análisis → cerrada (una cerrada no
se reabre, pero acepta comentarios); las histórico no cambian de estado pero aceptan causa, acción y comentario.
Todo queda en el historial (Cambio). Solo muestra: la lógica está en core/gestion_alertas.py.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.cambios import historial
from core.db import crear_sesion
from core.formato import numero
from core.gestion_alertas import (SIGUIENTES, cambiar_estado, carta_de_alerta, datos_de_alerta, guardar_seguimiento,
                                  listar_alertas)
from core.limites import VARIABLES_CONTROL
from core.models import Alerta, Planta
from filtros import barra_filtros, filtros_elegidos, motor, planta_elegida
from graficos import carta_alerta

st.set_page_config(page_title="Alertas", layout="wide")
cartel_modo_prueba()
st.title("Alertas")

barra_filtros()
filtros = filtros_elegidos()
planta_id = planta_elegida()
st.session_state.setdefault("_cargado_por", "")
usuario = st.sidebar.text_input("Usuario", value=st.session_state["_cargado_por"], key="alertas_usuario").strip()
st.session_state["_cargado_por"] = usuario
if planta_id is None:
    st.info("Elegí una planta.")
    st.stop()
if "_resultado_alertas" in st.session_state:
    st.success(st.session_state.pop("_resultado_alertas"))

# ---------------------------------------------------------------- filtros y lista

uno, dos, tres, cuatro = st.columns(4)
estados = ["abierta", "en análisis"]
if uno.checkbox("Ver histórico", key="alertas_historico"):
    estados.append("histórico")
if uno.checkbox("Ver cerradas", key="alertas_cerradas"):
    estados.append("cerrada")
variables = dos.multiselect("Variable", list(VARIABLES_CONTROL), format_func=lambda v: VARIABLES_CONTROL[v]["nombre"],
                            key="alertas_variables")
reglas = tres.multiselect("Regla", ["1", "2", "3", "rango"], key="alertas_reglas")
causa = cuatro.radio("Causa", ["todas", "con causa", "sin causa"], horizontal=True, key="alertas_causa")
cinco, seis, siete, _ = st.columns([1, 1, 1.4, 0.6])
solo_inmediatas = cinco.checkbox("Solo inmediatas", key="alertas_inmediatas")
solo_comunes = seis.checkbox("Solo comunes a los eBOE", key="alertas_comunes")
superpuestas = siete.checkbox("Incluir las que empezaron antes del período", key="alertas_superpuestas",
                              help="También las alertas cuyo episodio empezó antes y siguió en el período (así llega "
                                   "desde la 🔔 del Inicio).")

desde, hasta = (filtros.desde, filtros.hasta) if filtros else (None, None)
with crear_sesion(motor()) as sesion:
    lista = listar_alertas(sesion, planta_id, estados, desde, hasta, variables or None, None, reglas or None,
                           solo_inmediatas, solo_comunes, {"todas": None, "con causa": True, "sin causa": False}[causa],
                           superpuestas)
ambitos = st.multiselect("Ámbito", sorted(lista["ambito"].unique()), key="alertas_ambitos")
if ambitos:
    lista = lista[lista["ambito"].isin(ambitos)].reset_index(drop=True)

st.caption(f"{numero(len(lista), 0)} alertas · fechas de producción de la barra ("
           f"{'algún turno del episodio' if superpuestas else 'inicio de la alerta'}). Elegí una para ver el detalle.")
marcas = [" ".join(m for m, si in (("⚡ inmediata", i), ("✖ no confirmada", c is False), ("🏭 común", co)) if si)
          for i, c, co in zip(lista["inmediata"], lista["confirmada"], lista["comun"])]
vista = pd.DataFrame({"Inicio": lista["inicio"].dt.strftime("%d/%m/%Y %H:%M"),
                      "Fin": lista["fin"].dt.strftime("%d/%m/%Y %H:%M"), "Turnos": lista["turnos"],
                      "Variable": lista["nombre"], "Ámbito": lista["ambito"], "Regla": lista["regla"],
                      "Valor": lista["valor"], "Límite": lista["limite"], "Estado": lista["estado"], "Marcas": marcas,
                      "Causa": lista["con_causa"].map({True: "✔", False: ""}), "Comentario": lista["comentario"]})
eleccion = st.dataframe(vista.style.format(precision=2, decimal=",", na_rep="—"), hide_index=True,
                        on_select="rerun", selection_mode="single-row", key="alertas_lista", height=320)
if not eleccion.selection.rows:
    st.stop()

# ---------------------------------------------------------------- detalle

alerta_id = int(lista["id"].iloc[eleccion.selection.rows[0]])
with crear_sesion(motor()) as sesion:
    planta = sesion.get(Planta, planta_id)
    alerta = sesion.get(Alerta, alerta_id)
    nombre = VARIABLES_CONTROL[alerta.variable]["nombre"]
    ambito = alerta.equipo.nombre if alerta.equipo else "Planta"
    st.subheader(f"{nombre} · {ambito} · regla {alerta.regla}")
    st.caption(f"Estado: {alerta.estado} · inicio {alerta.fecha_hora:%d/%m/%Y %H:%M}, fin "
               f"{(alerta.fin or alerta.fecha_hora):%d/%m/%Y %H:%M} · {alerta.turnos_afectados} turno(s) · valor "
               f"{numero(alerta.valor, 2)}" + ("" if alerta.limite is None else f", límite {numero(alerta.limite, 2)}")
               + (f" · cerrada por {alerta.cerrada_por} el {alerta.cerrada_en:%d/%m/%Y %H:%M}"
                  if alerta.cerrada_en else ""))
    if alerta.comentario:
        st.info(alerta.comentario)
    for carta in carta_de_alerta(sesion, planta, alerta):
        st.plotly_chart(carta_alerta(carta), width="stretch")
    datos = datos_de_alerta(sesion, planta, alerta)
    estado, causa_actual, accion_actual, comentario_actual = (alerta.estado, alerta.causa or "", alerta.accion or "",
                                                              alerta.comentario or "")
    cambios = [{"Cuándo": c.cambiado_en.strftime("%d/%m/%Y %H:%M"), "Quién": c.cambiado_por, "Campo": c.campo,
                "Antes": c.valor_anterior or "", "Después": c.valor_nuevo or "", "Motivo": c.motivo}
               for c in historial(sesion, "alerta", [alerta_id])]

st.markdown(f"**Datos de la alerta** (N = {len(datos)})")
if len(datos) < 3:
    st.caption("⚠ Menos de 3 datos.")
st.dataframe(datos.assign(fecha_hora=datos["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M")).rename(columns={
    "dato_id": "Id", "fecha_hora": "Fecha y hora", "turno": "Turno", "equipo": "Equipo", "lugar": "Lugar",
    "resultado": "Resultado", "brix": "°Brix", "ph": "pH", "analista": "Analista", "revisar": "Revisar"})
    .style.format(precision=3, decimal=",", na_rep="—"), hide_index=True)
st.caption("Para corregir un dato, buscá su ronda en la página Datos (la alerta se reevalúa sola).")

with st.form("alertas_seguimiento"):
    st.markdown("**Seguimiento**")
    causa_nueva = st.text_area("Causa", value=causa_actual)
    accion_nueva = st.text_area("Acción", value=accion_actual)
    comentario_nuevo = st.text_area("Comentario", value=comentario_actual)
    if st.form_submit_button("Guardar seguimiento", disabled=not usuario):
        with crear_sesion(motor()) as sesion:
            n = guardar_seguimiento(sesion, alerta_id, usuario, datetime.now(), causa_nueva, accion_nueva,
                                    comentario_nuevo)
        st.session_state["_resultado_alertas"] = f"Seguimiento guardado ({n} campo(s) cambiado(s))."
        st.rerun()

siguientes = SIGUIENTES.get(estado, ())
if siguientes:
    columnas = st.columns(len(siguientes) + 2)
    motivo = columnas[0].text_input("Motivo (opcional)", key=f"alertas_motivo_{alerta_id}")
    for donde, nuevo in zip(columnas[1:], siguientes):
        if donde.button("En análisis" if nuevo == "en análisis" else "Cerrar", key=f"alertas_{nuevo}_{alerta_id}",
                        disabled=not usuario, type="primary" if nuevo == "cerrada" else "secondary"):
            with crear_sesion(motor()) as sesion:
                cambiar_estado(sesion, alerta_id, nuevo, usuario, datetime.now(), motivo or None)
            st.session_state["_resultado_alertas"] = f"La alerta pasó a «{nuevo}»."
            st.rerun()
elif estado == "histórico":
    st.caption("Alerta histórico: no cambia de estado (sí acepta causa, acción y comentario).")
else:
    st.caption("Alerta cerrada: no se reabre (si vuelve a pasar, se crea otra). Sí acepta comentarios.")
if not usuario:
    st.info("Completá «Usuario» en la barra lateral para guardar cambios.")
with st.expander(f"Historial de la alerta ({len(cambios)})"):
    if cambios:
        st.dataframe(pd.DataFrame(cambios), hide_index=True)
    else:
        st.caption("Sin cambios.")
