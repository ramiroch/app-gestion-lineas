"""Página Producción: carga por turno (borrador) y aprobación (tarea 20).

Cargar: planta, fecha de producción, turno y proceso (por defecto, el turno que acaba de terminar); fruta procesada
(MT), aceite envasado (kg), jugo concentrado 65 °Bx (kg), reproceso (kg) y comentario. Rendimientos en vivo contra
el promedio de las últimas 2 semanas (aprobados). Si el turno y proceso ya están cargados, se edita ese registro (un
aprobado se corrige con motivo y sigue aprobado). Aprobar: borradores → aprobados; un aprobado vuelve a borrador con
motivo. Solo los aprobados entran en los KPI. Solo muestra: la lógica está en core/carga_produccion.py.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.carga_produccion import (CAMPOS, RENDIMIENTOS, aprobar, buscar_registro, calcular, devolver_a_borrador,
                                   editar_registro, guardar_borrador, lista_registros, percentiles_rendimientos,
                                   promedio_ultimas_semanas, validar, valores_por_defecto)
from core.db import crear_sesion
from core.formato import numero
from core.models import Planta
from filtros import PLANTA_POR_DEFECTO, motor, plantas

ETIQUETAS = {"fruta_t": "Fruta procesada (MT)", "aceite_envasado_kg": "Aceite envasado (kg)",
             "jugo_conc_kg": "Jugo concentrado 65 °Bx (kg)", "reproceso_kg": "Reproceso (kg)"}

st.set_page_config(page_title="Producción", layout="wide")
cartel_modo_prueba()
st.title("Producción por turno")


@st.cache_data(ttl=600)
def percentiles_planta(planta_id: int) -> dict:
    with crear_sesion(motor()) as sesion:
        return percentiles_rendimientos(sesion, planta_id)


def terminar(mensaje: str) -> None:
    st.session_state["_resultado_produccion"] = mensaje
    st.cache_data.clear()                                   # para que los KPI lean lo nuevo
    st.rerun()


# ---------------------------------------------------------------- planta y usuario

etiquetas = list(plantas())
if not etiquetas:
    st.warning("No hay plantas cargadas. Corré python -m core.importar_maestros.")
    st.stop()
if "produccion_planta" not in st.session_state:
    st.session_state["produccion_planta"] = next((e for e in etiquetas if e == PLANTA_POR_DEFECTO), etiquetas[0])
planta_id = plantas()[st.sidebar.selectbox("Planta", etiquetas, key="produccion_planta")]
st.session_state.setdefault("_cargado_por", "")
usuario = st.sidebar.text_input("Usuario", value=st.session_state["_cargado_por"], key="produccion_usuario").strip()
st.session_state["_cargado_por"] = usuario
if "_resultado_produccion" in st.session_state:
    st.success(st.session_state.pop("_resultado_produccion"))
if not usuario:
    st.info("Completá «Usuario» en la barra lateral para poder guardar o aprobar.")

with crear_sesion(motor()) as sesion:
    planta = sesion.get(Planta, planta_id)
    list(planta.turnos)
    defecto = valores_por_defecto(sesion, planta, datetime.now())
    sesion.expunge(planta)

cargar, aprobacion = st.tabs(["Cargar", "Aprobar"])

# ---------------------------------------------------------------- cargar

with cargar:
    uno, dos, tres = st.columns(3)
    if "produccion_dia" not in st.session_state:
        st.session_state["produccion_dia"] = defecto["dia"]
        st.session_state["produccion_turno"] = defecto["turno"]
        st.session_state["produccion_proceso"] = defecto["proceso_id"]
    dia = uno.date_input("Fecha de producción", format="DD/MM/YYYY", key="produccion_dia")
    nombres = [t.nombre for t in planta.turnos]
    turno = dos.selectbox("Turno", nombres, key="produccion_turno")
    procesos = dict(defecto["procesos"])
    proceso_id = tres.selectbox("Proceso", list(procesos), format_func=procesos.get, key="produccion_proceso")
    turno_id = next(t.id for t in planta.turnos if t.nombre == turno)

    with crear_sesion(motor()) as sesion:
        existente = buscar_registro(sesion, planta_id, dia, turno_id, proceso_id)
        actual = {c: getattr(existente, c) for c in CAMPOS} if existente else {}
        estado = existente.estado if existente else None
        existente_id = existente.id if existente else None
        promedio = promedio_ultimas_semanas(sesion, planta_id, dia)
    if existente_id:
        st.info(f"Ese turno y proceso ya están cargados (estado: {estado}). Estás editando ese registro"
                + (": un aprobado se corrige con motivo y sigue aprobado." if estado == "aprobado" else "."))

    sufijo = f"{dia:%Y%m%d}_{turno}_{proceso_id}"           # campos nuevos al cambiar de turno o de proceso
    columnas = st.columns(4)
    datos = {"dia": dia, "turno": turno, "proceso_id": proceso_id}
    for donde, campo in zip(columnas, ETIQUETAS):
        datos[campo] = donde.number_input(ETIQUETAS[campo], value=actual.get(campo), min_value=None,
                                          format="%.1f" if campo == "fruta_t" else "%.0f",
                                          key=f"produccion_{campo}_{sufijo}")
    datos["comentario"] = st.text_input("Comentario", value=actual.get("comentario") or "",
                                        key=f"produccion_comentario_{sufijo}")

    resultados = calcular(datos)
    izquierda, derecha = st.columns(2)
    for donde, clave in zip((izquierda, derecha), RENDIMIENTOS):
        valor, referencia, n = resultados[clave], promedio[clave], promedio[f"n_{clave}"]
        donde.metric(f"{RENDIMIENTOS[clave][0]} (kg/MT)", "—" if valor is None else numero(valor, 2),
                     None if valor is None or referencia is None else f"{numero(valor - referencia, 2)} contra 2 semanas")
        donde.caption("Últimas 2 semanas (aprobados): " + ("sin datos" if referencia is None else
                                                          f"{numero(referencia, 2)} kg/MT, N = {n} turnos")
                      + (" · ⚠ N < 3" if referencia is not None and n < 3 else ""))

    errores, avisos = validar(datos, existente_id, existente_id, percentiles_planta(planta_id))
    for error in errores:
        st.error(error)
    for aviso in avisos:
        st.warning(aviso)
    motivo = st.text_input("Motivo de la corrección (obligatorio)", key=f"produccion_motivo_{sufijo}") \
        if estado == "aprobado" else None
    texto = {None: "Guardar borrador", "borrador": "Guardar cambios", "aprobado": "Guardar corrección"}[estado]
    bloqueado = bool(errores) or not usuario or (estado == "aprobado" and not (motivo or "").strip())
    if st.button(texto, type="primary", disabled=bloqueado):
        with crear_sesion(motor()) as sesion:
            try:
                if existente_id is None:
                    guardar_borrador(sesion, sesion.get(Planta, planta_id), datos, usuario, datetime.now())
                    mensaje = f"Turno {turno} del {dia:%d/%m/%Y} guardado como borrador."
                else:
                    n = editar_registro(sesion, existente_id, {c: datos[c] for c in CAMPOS}, usuario,
                                        datetime.now(), motivo)
                    mensaje = f"{n} campo(s) corregido(s)."
            except (ValueError, PermissionError) as error:
                st.error(str(error))
                st.stop()
        terminar(mensaje)

# ---------------------------------------------------------------- aprobar

with aprobacion:
    with crear_sesion(motor()) as sesion:
        borradores = lista_registros(sesion, planta_id, estados=["borrador"])
        aprobados = lista_registros(sesion, planta_id, estados=["aprobado"]).head(60)
    percentiles = percentiles_planta(planta_id)

    def vista(tabla: pd.DataFrame) -> pd.DataFrame:
        avisos = [" ".join(validar(fila.to_dict(), percentiles=percentiles)[1]) for _, fila in tabla.iterrows()]
        return pd.DataFrame({
            "Fecha": pd.to_datetime(tabla["fecha_produccion"]).dt.strftime("%d/%m/%Y"), "Turno": tabla["turno"],
            "Proceso": tabla["proceso"], **{ETIQUETAS[c]: tabla[c] for c in ETIQUETAS},
            "Rend. aceite (kg/MT)": tabla["rendimiento_aceite"],
            "Rend. conc. 65 °Bx (kg/MT)": tabla["rendimiento_concentrado"], "Comentario": tabla["comentario"],
            "Cargado por": tabla["cargado_por"], "Avisos": avisos})

    st.subheader(f"Borradores ({len(borradores)})")
    if borradores.empty:
        st.caption("No hay borradores para aprobar.")
    else:
        eleccion = st.dataframe(vista(borradores).style.format(precision=2, decimal=",", thousands=".", na_rep="—"),
                                hide_index=True, on_select="rerun", selection_mode="multi-row",
                                key="produccion_borradores")
        elegidos = [int(borradores["id"].iloc[i]) for i in eleccion.selection.rows]
        if st.button(f"Aprobar ({len(elegidos)})", type="primary", disabled=not elegidos or not usuario):
            with crear_sesion(motor()) as sesion:
                try:
                    n = aprobar(sesion, elegidos, usuario, datetime.now())
                except (ValueError, PermissionError) as error:
                    st.error(str(error))
                    st.stop()
            terminar(f"{n} registro(s) aprobado(s): ya cuentan en los KPI.")
        st.caption("Elegí uno o más borradores (casilla de la izquierda) y apretá «Aprobar». Para corregir uno, "
                   "abrilo en la pestaña Cargar con su fecha, turno y proceso.")

    st.subheader("Aprobados recientes")
    if aprobados.empty:
        st.caption("No hay registros aprobados.")
    else:
        eleccion_a = st.dataframe(vista(aprobados).style.format(precision=2, decimal=",", thousands=".", na_rep="—"),
                                  hide_index=True, on_select="rerun", selection_mode="single-row",
                                  key="produccion_aprobados", height=300)
        if eleccion_a.selection.rows:
            registro_id = int(aprobados["id"].iloc[eleccion_a.selection.rows[0]])
            motivo_d = st.text_input("Motivo para devolver a borrador", key="produccion_motivo_devolver")
            if st.button("Devolver a borrador", disabled=not motivo_d.strip() or not usuario):
                with crear_sesion(motor()) as sesion:
                    try:
                        devolver_a_borrador(sesion, registro_id, motivo_d, usuario, datetime.now())
                    except (ValueError, PermissionError) as error:
                        st.error(str(error))
                        st.stop()
                terminar("Registro devuelto a borrador: deja de contar en los KPI hasta que se apruebe.")
