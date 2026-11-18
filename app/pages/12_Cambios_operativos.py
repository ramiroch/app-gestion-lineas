"""Página Cambios operativos (Carga diaria): la bitácora de la planta.

Cargar, editar y borrar cambios de programa, agua/fase pesada, centrífugas, mantenimiento, fruta u otros, con su fecha
y hora y, si corresponde, el equipo. Editar y borrar piden motivo y quedan en el historial (tabla Cambio). Los cambios
se dibujan como líneas verticales en los gráficos de evolución y se comparan antes/después en Análisis › Sistema.
Usa la barra de filtros global (planta y fechas). Solo muestra: la lógica está en core/cambios_operativos.py
(permisos: core.cambios.puede_editar).
"""

from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.cambios import historial
from core.cambios_operativos import borrar_cambio, crear_cambio, editar_cambio, equipos_de_planta
from core.db import crear_sesion
from core.models import CATEGORIAS_CAMBIO_OPERATIVO
from filtros import barra_filtros, cambios_planta, filtros_elegidos, motor, planta_elegida

CONFIRMAR = "BORRAR"

st.set_page_config(page_title="Cambios operativos", layout="wide")
cartel_modo_prueba()
st.title("Bitácora de cambios operativos")
st.caption("Cambios de programa, agua o fase pesada, centrífugas, mantenimiento, fruta u otros. Aparecen como líneas "
           "verticales en los gráficos de evolución (Análisis por mes, cartas de control, tendencia del Inicio) y se "
           "comparan antes/después en Análisis › Sistema.")


def usuario_actual() -> str:
    """"Usuario" en la barra lateral, el mismo de la carga y de Datos (se recuerda en la sesión)."""
    st.session_state.setdefault("_cargado_por", "")
    valor = st.sidebar.text_input("Usuario", value=st.session_state["_cargado_por"], key="cambios_usuario")
    st.session_state["_cargado_por"] = valor
    return valor.strip()


def listo(mensaje: str) -> None:
    cambios_planta.clear()                    # las líneas de los gráficos se actualizan enseguida
    st.session_state["_resultado_cambios"] = mensaje
    st.rerun()


barra_filtros()
filtros = filtros_elegidos()
if filtros is None:
    st.stop()
planta_id = planta_elegida()
usuario = usuario_actual()
with crear_sesion(motor()) as sesion:
    equipos = equipos_de_planta(sesion, planta_id)
if "_resultado_cambios" in st.session_state:
    st.success(st.session_state.pop("_resultado_cambios"))


def nombre_equipo(equipo_id) -> str:
    return "Planta (general)" if equipo_id is None or pd.isna(equipo_id) else equipos.get(int(equipo_id), "?")


st.subheader("Cargar un cambio")
with st.form("cambios_nuevo", clear_on_submit=True):
    uno, dos, tres, cuatro = st.columns([1, 1, 1.4, 1.6])
    dia = uno.date_input("Fecha", value=date.today(), format="DD/MM/YYYY")
    hora = dos.time_input("Hora", value=time(8, 0), step=600)
    categoria = tres.selectbox("Categoría", CATEGORIAS_CAMBIO_OPERATIVO)
    equipo = cuatro.selectbox("Equipo (opcional)", [None, *equipos], format_func=nombre_equipo)
    descripcion = st.text_area("Descripción", placeholder="Qué cambió y por qué (por ejemplo: programa N5 → N7 en el "
                                                         "eBOE 2 por fruta blanda).")
    if st.form_submit_button("Guardar", type="primary", disabled=not usuario):
        try:
            with crear_sesion(motor()) as sesion:
                crear_cambio(sesion, planta_id, datetime.combine(dia, hora), categoria, descripcion, usuario,
                             datetime.now(), equipo_id=equipo)
            listo("Cambio guardado.")
        except (ValueError, PermissionError) as error:
            st.error(str(error))
if not usuario:
    st.info("Completá «Usuario» en la barra lateral para guardar cambios.")

st.subheader("Cambios del período")
lista = cambios_planta(planta_id)
en_periodo = lista[(lista["fecha_produccion"] >= pd.Timestamp(filtros.desde))
                   & (lista["fecha_produccion"] <= pd.Timestamp(filtros.hasta))].reset_index(drop=True)
st.caption(f"Del {filtros.desde:%d/%m/%Y} al {filtros.hasta:%d/%m/%Y} (fechas de la barra): "
           f"{len(en_periodo)} cambio{'' if len(en_periodo) == 1 else 's'} de {len(lista)} cargados. Elegí uno para "
           "editarlo o borrarlo.")
if en_periodo.empty:
    st.info("No hay cambios cargados en el período.")
    st.stop()
eleccion = st.dataframe(pd.DataFrame({
    "Fecha y hora": en_periodo["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"),
    "Día de producción": en_periodo["fecha_produccion"].dt.strftime("%d/%m/%Y"), "Turno": en_periodo["turno"],
    "Equipo": en_periodo["equipo"].fillna("Planta (general)"), "Categoría": en_periodo["categoria"],
    "Descripción": en_periodo["descripcion"], "Cargado por": en_periodo["cargado_por"]}),
    hide_index=True, on_select="rerun", selection_mode="single-row", key="cambios_lista")
filas = eleccion.selection.rows
if not filas:
    st.stop()
fila = en_periodo.iloc[filas[0]]
cambio_id = int(fila["id"])

izquierda, derecha = st.columns(2)
with izquierda.form(f"cambios_editar_{cambio_id}"):
    st.markdown("**Editar**")
    a, b = st.columns(2)
    nuevo_dia = a.date_input("Fecha", value=fila["fecha_hora"].date(), format="DD/MM/YYYY")
    nueva_hora = b.time_input("Hora", value=fila["fecha_hora"].time(), step=600)
    nueva_categoria = st.selectbox("Categoría", CATEGORIAS_CAMBIO_OPERATIVO,
                                   index=CATEGORIAS_CAMBIO_OPERATIVO.index(fila["categoria"]))
    opciones = [None, *equipos]
    actual = None if pd.isna(fila["equipo_id"]) else int(fila["equipo_id"])
    nuevo_equipo = st.selectbox("Equipo (opcional)", opciones, index=opciones.index(actual), format_func=nombre_equipo)
    nueva_descripcion = st.text_area("Descripción", value=fila["descripcion"])
    motivo = st.text_input("Motivo de la corrección")
    if st.form_submit_button("Guardar corrección", disabled=not usuario):
        try:
            with crear_sesion(motor()) as sesion:
                n = editar_cambio(sesion, cambio_id, {"fecha_hora": datetime.combine(nuevo_dia, nueva_hora),
                                                      "categoria": nueva_categoria, "equipo_id": nuevo_equipo,
                                                      "descripcion": nueva_descripcion}, motivo, usuario, datetime.now())
            listo(f"Cambio corregido ({n} campo{'' if n == 1 else 's'}).")
        except (ValueError, PermissionError) as error:
            st.error(str(error))
with derecha.form(f"cambios_borrar_{cambio_id}"):
    st.markdown("**Borrar**")
    motivo_borrar = st.text_input("Motivo del borrado")
    confirmar = st.text_input(f"Escribí {CONFIRMAR} para confirmar")
    if st.form_submit_button("Borrar el cambio", disabled=not usuario):
        if confirmar.strip().upper() != CONFIRMAR:
            st.error(f"Para borrar, escribí {CONFIRMAR}.")
        else:
            try:
                with crear_sesion(motor()) as sesion:
                    borrar_cambio(sesion, cambio_id, motivo_borrar, usuario, datetime.now())
                listo("Cambio borrado (queda en el historial).")
            except (ValueError, PermissionError) as error:
                st.error(str(error))
with crear_sesion(motor()) as sesion:
    registros = historial(sesion, "cambio_operativo", [cambio_id])
    filas_historial = [{"Cuándo": r.cambiado_en.strftime("%d/%m/%Y %H:%M"), "Quién": r.cambiado_por,
                        "Acción": r.accion, "Campo": r.campo, "Antes": r.valor_anterior, "Después": r.valor_nuevo,
                        "Motivo": r.motivo} for r in registros]
with st.expander(f"Historial del cambio ({len(filas_historial)})"):
    if filas_historial:
        st.dataframe(pd.DataFrame(filas_historial), hide_index=True)
    else:
        st.caption("Sin correcciones.")
