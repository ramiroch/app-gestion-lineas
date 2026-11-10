"""Página Datos: lista de análisis y lecturas de operación por ronda, con corrección, borrado, cambio de hora,
historial y restaurar (tarea 19).

Usa la barra de filtros global (fechas de producción, eBOE, turno) y filtros propios (punto, analista, con marca de
revisar, editados). Todo cambio pide motivo y queda en el historial (tabla Cambio); un borrado guarda el dato completo
y se puede restaurar. Después de cada cambio se recalcula y se reevalúan las alertas del turno.
Solo muestra: la lógica está en core/edicion.py (permisos: core.cambios.puede_editar).
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.cambios import historial
from core.db import crear_sesion
from core.edicion import (CAMPOS_EDITABLES, borrar_dato, borrar_ronda, cambiar_hora_ronda, datos_de_ronda,
                          editar_datos, lista_rondas, restaurar)
from core.formato import numero
from core.limites import VARIABLES_CONTROL
from core.models import Cambio, Planta, Proceso
from filtros import barra_filtros, filtros_elegidos, motor, planta_elegida

LUGARES = {"entrada": "Entrada", "salida": "Salida", "emulsion": "Emulsión", "operacion": "Operación"}
CONFIRMAR = "BORRAR"

st.set_page_config(page_title="Datos", layout="wide")
cartel_modo_prueba()
st.title("Datos de laboratorio y operación")


def usuario_actual() -> str:
    """"Usuario" en la barra lateral, el mismo que «Cargado por» de la carga (se recuerda en la sesión)."""
    st.session_state.setdefault("_cargado_por", "")
    valor = st.sidebar.text_input("Usuario", value=st.session_state["_cargado_por"], key="datos_usuario")
    st.session_state["_cargado_por"] = valor
    return valor.strip()


def resumen_alertas(cambios: list[dict]) -> list[dict]:
    """Las alertas nuevas o actualizadas como filas simples (se muestran después de recargar la página)."""
    return [{"Cambio": c["cambio"], "Variable": VARIABLES_CONTROL[c["alerta"].variable]["nombre"],
             "Ámbito": c["alerta"].equipo.nombre if c["alerta"].equipo else "Planta", "Regla": c["alerta"].regla,
             "Estado": c["alerta"].estado, "Comentario": c["alerta"].comentario or ""} for c in cambios]


def mostrar_alertas(filas: list[dict]) -> None:
    if not filas:
        st.caption("Sin alertas nuevas ni actualizadas.")
        return
    st.warning("Alertas nuevas o actualizadas (detalle en la página Alertas):")
    st.dataframe(pd.DataFrame(filas), hide_index=True)


def ejecutar(funcion, *argumentos, exito: str) -> None:
    """Corre un cambio de core.edicion y deja el resultado para mostrarlo después de recargar."""
    with crear_sesion(motor()) as sesion:
        try:
            resultado = funcion(sesion, sesion.get(Planta, planta_id), *argumentos)
        except (ValueError, PermissionError) as error:
            st.error(str(error))
            return
        st.session_state["_resultado_datos"] = {"exito": exito,
                                                "alertas": resumen_alertas(resultado.get("alertas", []))}
    st.cache_data.clear()
    st.rerun()


barra_filtros()
filtros = filtros_elegidos()
planta_id = planta_elegida()
usuario = usuario_actual()
if planta_id is None or filtros is None:
    st.info("No hay datos con esta planta.")
    st.stop()
if "_resultado_datos" in st.session_state:
    resultado = st.session_state.pop("_resultado_datos")
    st.success(resultado["exito"])
    mostrar_alertas(resultado["alertas"])

# ---------------------------------------------------------------- lista de rondas

uno, dos, tres, cuatro = st.columns(4)
lugares = uno.multiselect("Punto", list(LUGARES)[:3], format_func=LUGARES.get, key="datos_lugares")
with crear_sesion(motor()) as sesion:
    planta = sesion.get(Planta, planta_id)
    list(planta.turnos)
    sesion.expunge(planta)
    todas = lista_rondas(sesion, planta, filtros.desde, filtros.hasta, filtros.eboes, filtros.turnos)
analistas = dos.multiselect("Analista", sorted({a for texto in todas["analista"].dropna() for a in texto.split(", ")}),
                            key="datos_analistas")
solo_revisar = tres.checkbox("Solo con marca de revisar", key="datos_revisar")
solo_editados = cuatro.checkbox("Solo editados", key="datos_editados")
with crear_sesion(motor()) as sesion:
    rondas = lista_rondas(sesion, planta, filtros.desde, filtros.hasta, filtros.eboes, filtros.turnos, lugares,
                          analistas, solo_revisar, solo_editados)

st.caption(f"{numero(rondas['fecha_hora'].nunique(), 0)} rondas · {numero(len(rondas), 0)} filas (una por ronda y "
           "eBOE). Elegí una fila para ver y corregir su ronda.")
vista = pd.DataFrame({
    "Ronda": rondas["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"), "Turno": rondas["turno"], "Proceso": rondas["proceso"],
    "eBOE": rondas["eboe"], "Analista": rondas["analista"],
    "Entrada (kg/MT)": rondas["aceite_entrada"], "Salida (kg/MT)": rondas["aceite_salida"],
    "Eficiencia (%)": rondas["eficiencia"], "Emulsión (%)": rondas["aceite_emulsion"], "°Brix": rondas["brix"],
    "pH": rondas["ph"], "Programa": rondas["programa"], "MTPH": rondas["tph"],
    "Revisar": rondas["revisar"], "Editada": rondas["editado"].map({True: "✏", False: ""}),
    "Alertas": rondas["alertas"].map(lambda n: f"🔔 {n}" if n else "")})
eleccion = st.dataframe(vista.style.format(precision=2, decimal=",", na_rep="—"), hide_index=True,
                        on_select="rerun", selection_mode="single-row", key="datos_lista", height=320)
filas = eleccion.selection.rows
if not filas:
    st.info("Elegí una ronda de la lista.")
else:
    fecha_hora = rondas["fecha_hora"].iloc[filas[0]].to_pydatetime()
    st.subheader(f"Ronda del {fecha_hora:%d/%m/%Y %H:%M}")
    with crear_sesion(motor()) as sesion:
        datos = datos_de_ronda(sesion, planta, fecha_hora)
        procesos = {p.id: f"{p.temporada} · proceso {p.numero}" for p in sesion.query(Proceso).filter(
            Proceso.planta_id == planta_id)}
        pares = set(zip(datos["tabla"], datos["id"])) if not datos.empty else set()
        cambios_ronda = [c for c in historial(sesion, dato_ids=[i for _, i in pares]) if (c.tabla, c.dato_id) in pares]
        historia = [{"Cuándo": c.cambiado_en.strftime("%d/%m/%Y %H:%M"), "Quién": c.cambiado_por, "Acción": c.accion,
                     "Dato": f"{c.tabla} {c.dato_id}", "Campo": c.campo or "", "Antes": c.valor_anterior
                     if c.accion != "borrado" else "(dato completo)", "Después": c.valor_nuevo or "",
                     "Motivo": c.motivo} for c in cambios_ronda]

    # ---- corregir
    editables = [c for t in CAMPOS_EDITABLES.values() for c in t]
    tabla = datos.assign(dato=[f"eBOE {e} · {LUGARES[l]}" for e, l in zip(datos["eboe"], datos["lugar"])],
                         proceso=datos["proceso_id"].map(procesos))
    columnas = ["dato", "resultado", "revisar"] + [c for c in editables if c != "proceso_id"] + ["proceso"]
    editada = st.data_editor(
        tabla[columnas], hide_index=True, key=f"datos_editor_{fecha_hora:%Y%m%d%H%M}",
        disabled=["dato", "resultado", "revisar"],
        column_config={"dato": "Dato", "resultado": st.column_config.NumberColumn("Resultado", format="%.3f"),
                       "revisar": "Revisar",
                       "proceso": st.column_config.SelectboxColumn("Proceso", options=list(procesos.values()))}
        | {c: CAMPOS_EDITABLES["analisis_scott"].get(c) or CAMPOS_EDITABLES["lectura_operacion"].get(c)
           for c in editables if c != "proceso_id"})
    st.caption("Los campos de análisis no se aplican a la operación y al revés (se ignoran). El blanco es de la planta "
               "y no se edita. Para cambiar la hora, usá «Cambiar la hora de la ronda».")
    motivo = st.text_input("Motivo de la corrección (obligatorio)", key="datos_motivo")
    if st.button("Guardar correcciones", type="primary", disabled=not motivo.strip() or not usuario):
        nombres_proceso = {v: k for k, v in procesos.items()}
        pendientes = []
        for i, fila in editada.iterrows():
            original = tabla.loc[i]
            campos = CAMPOS_EDITABLES[original["tabla"]]
            cambios = {}
            for campo in campos:
                nuevo = nombres_proceso.get(fila["proceso"]) if campo == "proceso_id" else fila[campo]
                anterior = original[campo]
                if not (pd.isna(nuevo) and pd.isna(anterior)) and nuevo != anterior:
                    cambios[campo] = None if pd.isna(nuevo) else (nuevo.item() if hasattr(nuevo, "item") else nuevo)
            if cambios:
                pendientes.append((original["tabla"], int(original["id"]), cambios))
        if not pendientes:
            st.info("No hay cambios para guardar.")
        else:
            ejecutar(editar_datos, pendientes, motivo, usuario, datetime.now(),
                     exito=f"{len(pendientes)} dato(s) corregido(s).")
    if not usuario:
        st.info("Completá «Usuario» en la barra lateral para poder cambiar datos.")

    # ---- borrar, cambiar la hora, historial
    izquierda, centro, derecha = st.columns(3)
    with izquierda.container(border=True):
        st.markdown("**Borrar un dato**")
        opciones = {int(f["id"]): (f["tabla"], f"eBOE {f['eboe']} · {LUGARES[f['lugar']]}") for _, f in datos.iterrows()}
        elegido = st.selectbox("Dato", list(opciones), format_func=lambda i: opciones[i][1], key="datos_borrar_dato")
        motivo_b = st.text_input("Motivo", key="datos_motivo_borrar_dato")
        confirma = st.text_input(f"Escribí {CONFIRMAR} para confirmar", key="datos_confirma_dato")
        if st.button("Borrar dato", disabled=confirma != CONFIRMAR or not motivo_b.strip() or not usuario):
            ejecutar(borrar_dato, opciones[elegido][0], elegido, motivo_b, usuario, datetime.now(),
                     exito="Dato borrado (se puede restaurar desde «Borrados»).")
    with centro.container(border=True):
        st.markdown("**Borrar la ronda completa**")
        motivo_r = st.text_input("Motivo", key="datos_motivo_borrar_ronda")
        confirma_r = st.text_input(f"Escribí {CONFIRMAR} para confirmar", key="datos_confirma_ronda")
        if st.button("Borrar ronda", disabled=confirma_r != CONFIRMAR or not motivo_r.strip() or not usuario):
            ejecutar(borrar_ronda, fecha_hora, motivo_r, usuario, datetime.now(),
                     exito="Ronda borrada (se puede restaurar desde «Borrados»).")
    with derecha.container(border=True):
        st.markdown("**Cambiar la hora de la ronda**")
        a, b = st.columns(2)
        dia = a.date_input("Fecha nueva", value=fecha_hora.date(), format="DD/MM/YYYY", key="datos_dia_nuevo")
        hora = b.time_input("Hora nueva", value=fecha_hora.time(), step=timedelta(minutes=5), key="datos_hora_nueva")
        motivo_h = st.text_input("Motivo", key="datos_motivo_hora")
        if st.button("Cambiar hora", disabled=not motivo_h.strip() or not usuario):
            ejecutar(cambiar_hora_ronda, fecha_hora, datetime.combine(dia, hora), motivo_h, usuario, datetime.now(),
                     exito="Hora de la ronda cambiada.")
    with st.expander(f"Historial de la ronda ({len(historia)})"):
        if historia:
            st.dataframe(pd.DataFrame(historia), hide_index=True)
        else:
            st.caption("Sin cambios.")

# ---------------------------------------------------------------- borrados: restaurar

st.subheader("Borrados")
with crear_sesion(motor()) as sesion:
    restaurados = {c.valor_anterior for c in sesion.query(Cambio).filter(Cambio.accion == "restaurado")}
    borrados = [c for c in historial(sesion) if c.accion == "borrado" and str(c.id) not in restaurados]
    grupos = {}
    for c in borrados:
        grupos.setdefault(c.grupo, []).append(c)
    resumen = [{"grupo": g, "Cuándo": cs[0].cambiado_en.strftime("%d/%m/%Y %H:%M"), "Quién": cs[0].cambiado_por,
                "Ronda": cs[0].clave.split("|")[1][:16] if cs[0].clave else "", "Datos": len(cs),
                "Motivo": cs[0].motivo, "ids": [c.id for c in cs]} for g, cs in grupos.items()]
if not resumen:
    st.caption("No hay datos borrados para restaurar.")
else:
    tabla_borrados = pd.DataFrame(resumen)
    eleccion_b = st.dataframe(tabla_borrados.drop(columns=["grupo", "ids"]), hide_index=True, on_select="rerun",
                              selection_mode="single-row", key="datos_borrados")
    if eleccion_b.selection.rows:
        elegido = tabla_borrados.iloc[eleccion_b.selection.rows[0]]
        motivo_rest = st.text_input("Motivo para restaurar", key="datos_motivo_restaurar")
        if st.button(f"Restaurar {elegido['Datos']} dato(s)", disabled=not motivo_rest.strip() or not usuario):
            ejecutar(restaurar, [int(i) for i in elegido["ids"]], motivo_rest, usuario, datetime.now(),
                     exito="Datos restaurados con sus valores originales.")
