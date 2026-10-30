"""Página Planta: datos de la planta y sus equipos por línea. Solo muestra; lee con core/consultas.py."""

import pandas as pd
import streamlit as st

from core.consultas import detalle_planta, filas_equipos, listar_clientes, listar_plantas
from core.db import crear_motor, crear_sesion

PENDIENTE = "Pendiente de instalación"
COLUMNAS = {"codigo": "Código", "nombre": "Nombre", "tipo": "Tipo", "numero": "N.º",
            "modelo": "Modelo", "numero_serie": "N.º de serie", "estado": "Estado"}
ESTILO_PENDIENTE = "background-color: rgba(255, 170, 0, 0.18)"
ESTILO_PUNTO = "color: gray; font-style: italic"


@st.cache_resource
def motor():
    """Conexión a la base, creada una sola vez."""
    return crear_motor()


def leer(consulta, *argumentos):
    """Ejecuta una consulta de core/consultas.py con una sesión nueva."""
    with crear_sesion(motor()) as sesion:
        return consulta(sesion, *argumentos)


def numero_es(valor, decimales: int) -> str:
    """Número con coma decimal: 0.2 → "0,20"."""
    return f"{valor:.{decimales}f}".replace(".", ",")


def mostrar_parametro(columna, titulo: str, valor) -> None:
    """Título chico y valor grande; si el valor falta, "a definir" en gris."""
    columna.caption(titulo)
    columna.markdown(f"### {valor}" if valor is not None else "### :gray[a definir]")


def tabla_equipos(equipos: list[dict]):
    """Tabla de equipos con sus puntos debajo; resalta los pendientes de instalación."""
    filas = filas_equipos(equipos)
    tabla = pd.DataFrame(filas)[list(COLUMNAS)].rename(columns=COLUMNAS)
    tabla["N.º"] = tabla["N.º"].map(lambda n: "" if pd.isna(n) else str(int(n)))   # 1, no 1.0
    tabla["Estado"] = tabla["Estado"].map(lambda e: f"🕓 {e}" if e == PENDIENTE else e)
    textos = ["Código", "Nombre", "Tipo", "Modelo", "N.º de serie", "Estado"]
    tabla[textos] = tabla[textos].fillna("")       # celdas vacías en blanco, no "None"

    def estilo(fila):
        if filas[fila.name]["es_punto"]:
            return [ESTILO_PUNTO] * len(fila)
        if filas[fila.name]["estado"] == PENDIENTE:
            return [ESTILO_PENDIENTE] * len(fila)
        return [""] * len(fila)

    return tabla.style.apply(estilo, axis=1), len(filas)


st.set_page_config(page_title="Planta", layout="wide")
st.title("Planta")

clientes = leer(listar_clientes)
if not clientes:
    st.info("La base no tiene datos. Corré `python -m core.importar_maestros`.")
    st.stop()

col_cliente, col_planta = st.columns(2)
id_cliente = {c["nombre"]: c["id"] for c in clientes}
nombre_cliente = col_cliente.selectbox("Cliente", list(id_cliente))
plantas = leer(listar_plantas, id_cliente[nombre_cliente])
if not plantas:
    col_planta.info("Este cliente no tiene plantas cargadas.")
    st.stop()
id_planta = {p["nombre"]: p["id"] for p in plantas}
nombre_planta = col_planta.selectbox("Planta", list(id_planta))

planta = leer(detalle_planta, id_planta[nombre_planta])

st.header(planta["nombre"])
st.caption(f"Cliente: {planta['cliente']} · País: {planta['pais']}")
blanco, fruta, emulsion, hora = (planta["blanco_ml"], planta["factor_aceite_fruta"],
                                 planta["factor_aceite_emulsion"], planta["hora_inicio_dia"])
c1, c2, c3, c4 = st.columns(4)
mostrar_parametro(c1, "Blanco", None if blanco is None else f"{numero_es(blanco, 2)} mL")
mostrar_parametro(c2, "Factor aceite en fruta", None if fruta is None else numero_es(fruta, 4))
mostrar_parametro(c3, "Factor aceite en emulsión", None if emulsion is None else numero_es(emulsion, 4))
mostrar_parametro(c4, "Inicio del día de producción", None if hora is None else hora.strftime("%H:%M"))
if None in (blanco, fruta, emulsion, hora):
    st.caption("Los parámetros «a definir» se completan en la hoja Planta de "
               "data/maestros_clientes.xlsx. Sin ellos no se pueden calcular análisis de esta planta.")

st.subheader("Equipos")
st.caption(f"🕓 = {PENDIENTE} · ↳ = punto de muestreo del equipo de arriba")
if not planta["lineas"]:
    st.info("Esta planta no tiene líneas cargadas.")

for linea in planta["lineas"]:
    equipos = linea["equipos"]
    pendientes = sum(1 for e in equipos if e["estado"] == PENDIENTE)
    st.markdown(f"#### {linea['nombre']}")
    st.caption(f"{len(equipos)} equipos" + (f" · 🕓 {pendientes} pendientes de instalación" if pendientes else ""))
    if not equipos:
        st.info("Línea sin equipos.")
        continue
    tabla, cantidad_filas = tabla_equipos(equipos)
    st.dataframe(tabla, hide_index=True, height=(cantidad_filas + 1) * 35 + 3)
