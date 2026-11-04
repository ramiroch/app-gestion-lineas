"""Barra de filtros global de las páginas de análisis, en la barra lateral.

Uso en una página de análisis:
    from filtros import barra_filtros
    datos = barra_filtros()        # DataFrame con los análisis ya filtrados

Los valores elegidos se guardan en st.session_state["filtros"], un diccionario propio y no
las claves de los widgets: Streamlit borra el valor de un widget cuando se pasa por una
página que no lo dibuja, y así los filtros se mantienen entre páginas.
La lógica (leer, filtrar, atajos de fechas) está en core/analisis.py; acá solo se muestra.

Para probarla sola: streamlit run app/filtros.py
"""

import streamlit as st

from core.analisis import ATAJO_PERSONALIZADO, Filtros, atajos, filtrar, opciones, rango_de_atajo, tabla_analisis
from core.consultas import listar_clientes, listar_plantas
from core.db import crear_motor, crear_sesion

PLANTA_POR_DEFECTO = "LEDESMA"
CLAVE = "filtros"
CAMPOS = ("atajo", "fechas", "eboes", "turnos", "procesos", "programas")   # todos menos la planta


@st.cache_resource
def motor():
    """Conexión a la base, creada una sola vez."""
    return crear_motor()


@st.cache_data(ttl=600, show_spinner="Leyendo los análisis de la planta…")
def tabla_planta(planta_id: int):
    """Tabla de análisis de la planta, guardada en caché 10 minutos (leerla tarda unos segundos)."""
    with crear_sesion(motor()) as sesion:
        return tabla_analisis(sesion, planta_id)


@st.cache_data(ttl=600)
def plantas() -> dict[str, int]:
    """{etiqueta: id} de todas las plantas; la etiqueta lleva el cliente si se llama distinto."""
    with crear_sesion(motor()) as sesion:
        return {(p["nombre"] if p["nombre"] == c["nombre"] else f'{p["nombre"]} ({c["nombre"]})'): p["id"]
                for c in listar_clientes(sesion) for p in listar_plantas(sesion, c["id"])}


def _guardado() -> dict:
    return st.session_state.setdefault(CLAVE, {})


def _limpiar() -> None:
    """Borra todos los filtros menos la planta (se llama al apretar el botón)."""
    guardado = _guardado()
    for campo in CAMPOS:
        guardado.pop(campo, None)
        st.session_state.pop(f"_filtro_{campo}", None)


def _recargar() -> None:
    """Borra el caché para leer de nuevo la base (por ejemplo, después de importar datos)."""
    tabla_planta.clear()
    plantas.clear()


def _preparar(campo: str, por_defecto, validos=None):
    """Carga en el widget el valor guardado (o el por defecto) antes de dibujarlo.

    Si se pasan los valores válidos, descarta los que ya no están (por ejemplo, al cambiar de planta).
    """
    clave = f"_filtro_{campo}"
    valor = st.session_state.get(clave, _guardado().get(campo, por_defecto))
    if validos is not None:
        valor = [v for v in valor if v in validos] if isinstance(valor, list) else (
            valor if valor in validos else por_defecto)
    st.session_state[clave] = valor
    return clave


def _multiselect(barra, titulo: str, campo: str, valores: list) -> list:
    clave = _preparar(campo, [], valores)
    _guardado()[campo] = barra.multiselect(titulo, valores, key=clave, placeholder="Todos")
    return _guardado()[campo]


def barra_filtros():
    """Dibuja la barra de filtros en la barra lateral y devuelve los análisis filtrados (DataFrame)."""
    barra = st.sidebar
    barra.header("Filtros")
    guardado = _guardado()

    # Planta: si cambia, los demás filtros vuelven a cero.
    etiquetas = list(plantas())
    if not etiquetas:
        barra.warning("No hay plantas cargadas. Corré python -m core.importar_maestros.")
        st.stop()
    por_defecto = next((e for e in etiquetas if e == PLANTA_POR_DEFECTO), etiquetas[0])
    planta = barra.selectbox("Planta", etiquetas, key=_preparar("planta", por_defecto, etiquetas))
    if guardado.get("planta") not in (None, planta):
        _limpiar()
    guardado["planta"] = planta

    tabla = tabla_planta(plantas()[planta])
    if tabla.empty:
        barra.info("Esta planta todavía no tiene análisis cargados.")
        return tabla
    op = opciones(tabla)

    # Fechas de producción: un atajo o un rango elegido a mano.
    lista_atajos = atajos(op)
    atajo = barra.selectbox("Fechas de producción", lista_atajos,
                            key=_preparar("atajo", lista_atajos[0], lista_atajos))
    guardado["atajo"] = atajo
    if atajo == ATAJO_PERSONALIZADO:
        primera = min(desde for desde, _ in op["temporadas"].values())
        clave = _preparar("fechas", (primera, op["ultima_fecha"]))
        elegidas = barra.date_input("Desde – hasta", key=clave, format="DD/MM/YYYY")
        desde, hasta = (elegidas[0], elegidas[-1]) if len(elegidas) == 2 else (elegidas[0] if elegidas else None, None)
    else:
        desde, hasta = rango_de_atajo(atajo, op)
        barra.caption(f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}")
    guardado["fechas"] = (desde, hasta) if desde and hasta else guardado.get("fechas")

    eboes = _multiselect(barra, "eBOE", "eboes", op["eboes"])
    turnos = _multiselect(barra, "Turno", "turnos", op["turnos"])
    procesos = _multiselect(barra, "Proceso", "procesos", list(op["procesos"]))
    programas = _multiselect(barra, "Programa", "programas", op["programas"])

    limpiar, recargar = barra.columns(2)
    limpiar.button("Limpiar filtros", on_click=_limpiar, width="stretch")
    recargar.button("Recargar datos", on_click=_recargar, width="stretch",
                    help="Vuelve a leer la base (por ejemplo, después de importar datos nuevos).")

    filtros = Filtros(desde=desde, hasta=hasta, eboes=eboes, turnos=turnos,
                      procesos=[op["procesos"][p] for p in procesos], programas=programas)
    datos = filtrar(tabla, filtros)

    pares = int(((datos["lugar"] == "Entrada") & datos["eficiencia_par"].notna()).sum())
    barra.markdown(f"**{len(datos):,} muestras** · {pares:,} pares entrada/salida".replace(",", "."))
    if len(datos) < 3:
        barra.warning("Quedan menos de 3 muestras: los promedios y gráficos no son representativos.")
    return datos


if __name__ == "__main__":      # streamlit run app/filtros.py: prueba de la barra sola
    st.title("Prueba de la barra de filtros")
    st.dataframe(barra_filtros(), hide_index=True)
