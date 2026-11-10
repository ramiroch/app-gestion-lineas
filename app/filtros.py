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

from core.analisis import (ATAJO_PERSONALIZADO, Filtros, atajos, filtrar, opciones, rango_de_atajo, tabla_analisis,
                           tabla_emulsion)
from core.consultas import listar_clientes, listar_plantas, rango_referencia
from core.db import crear_motor, crear_sesion
from core.produccion import tabla_produccion, toneladas_por_turno
from core.models import Planta
from core.operacion import lecturas_plc, preparar_plc
from core.parametros import ParametrosIncompletos, validar_parametros
from core.analisis_centrifugas import balance_por_muestreo, por_muestreo, rangos_centrifugas, tabla_muestras
from core.capacidad import limites_capacidad

PLANTA_POR_DEFECTO = "LEDESMA"
CLAVE = "filtros"
CLAVE_ACTUALES = "_filtros_actuales"   # Filtros armados en el último dibujo de la barra
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


@st.cache_data(ttl=600, show_spinner="Leyendo los análisis de emulsión…")
def tabla_emulsion_planta(planta_id: int):
    """Tabla de análisis de emulsión de la planta, en caché 10 minutos."""
    with crear_sesion(motor()) as sesion:
        return tabla_emulsion(sesion, planta_id)


def casilla_repetidas(clave: str) -> bool:
    """Casilla "Incluir entradas repetidas" (por defecto no) para las comparaciones entre eBOE."""
    return st.checkbox("Incluir entradas repetidas", value=False, key=clave,
                       help="Entradas con los mismos g y mL que la de otro eBOE en la misma ronda (posible copia). Por "
                            "defecto quedan afuera de las comparaciones entre eBOE; en los demás cálculos están.")


def filtros_elegidos() -> Filtros | None:
    """Filtros elegidos en la barra (llamar después de barra_filtros()); None si no hay datos."""
    return st.session_state.get(CLAVE_ACTUALES)


@st.cache_data(ttl=600)
def toneladas_planta(planta_id: int):
    """Toneladas de fruta por turno de la planta (para ponderar), en caché 10 minutos."""
    with crear_sesion(motor()) as sesion:
        return toneladas_por_turno(sesion, planta_id)


@st.cache_data(ttl=600)
def produccion_planta(planta_id: int):
    """Registros de producción APROBADOS de la planta (por turno y proceso), en caché 10 minutos."""
    with crear_sesion(motor()) as sesion:
        return tabla_produccion(sesion, planta_id)


@st.cache_data(ttl=600)
def produccion_con_borradores(planta_id: int):
    """Registros de producción de la planta, también los borradores (para contar los turnos sin aprobar)."""
    with crear_sesion(motor()) as sesion:
        return tabla_produccion(sesion, planta_id, solo_aprobados=False)


@st.cache_data(ttl=600, show_spinner="Leyendo los datos del PLC…")
def plc_planta(planta_id: int):
    """Lecturas del PLC de los eBOE de la planta, con horómetro, duración, día de producción y turno
    (core.operacion.preparar_plc), en caché 10 minutos. Vacía si no hay datos del PLC."""
    with crear_sesion(motor()) as sesion:
        plc = lecturas_plc(sesion, planta_id)
        if plc.empty:
            return plc
        return preparar_plc(plc, sesion.get(Planta, planta_id))


@st.cache_data(ttl=600)
def hora_inicio_planta(planta_id: int):
    """Hora de inicio del día de producción de la planta (parámetro hora_inicio_dia)."""
    with crear_sesion(motor()) as sesion:
        return validar_parametros(sesion.get(Planta, planta_id), ["hora_inicio_dia"])["hora_inicio_dia"]


@st.cache_data(ttl=600)
def rango_planta(planta_id: int, variable: str) -> dict | None:
    """Rango de referencia (objetivo) de una variable de la planta, en caché 10 minutos."""
    with crear_sesion(motor()) as sesion:
        return rango_referencia(sesion, planta_id, variable)


@st.cache_data(ttl=600, show_spinner="Leyendo las muestras de centrífugas…")
def centrifugas_planta(planta_id: int):
    """(muestras, balance por muestreo) de las centrífugas de la planta (core.analisis_centrifugas), en caché
    10 minutos. Vacías si la planta no tiene muestras."""
    with crear_sesion(motor()) as sesion:
        muestras = tabla_muestras(sesion, planta_id)
    if muestras.empty:
        return muestras, muestras
    return muestras, balance_por_muestreo(por_muestreo(muestras))


@st.cache_data(ttl=600)
def rangos_centrifugas_planta(planta_id: int) -> dict:
    """Rangos de referencia de las centrífugas de la planta (core.analisis_centrifugas.rangos_centrifugas)."""
    with crear_sesion(motor()) as sesion:
        return rangos_centrifugas(sesion, planta_id)


@st.cache_data(ttl=600)
def umbral_paralelo_planta(planta_id: int) -> float | None:
    """Parámetro umbral_paralelo_lh de la planta (L/h); None si no está definido."""
    with crear_sesion(motor()) as sesion:
        try:
            return validar_parametros(sesion.get(Planta, planta_id), ["umbral_paralelo_lh"])["umbral_paralelo_lh"]
        except ParametrosIncompletos:
            return None


@st.cache_data(ttl=600)
def limites_capacidad_planta(planta_id: int, variable: str, fruta: str | None) -> dict | None:
    """Límites de especificación de RangoReferencia para la capacidad (core.capacidad.limites_capacidad)."""
    with crear_sesion(motor()) as sesion:
        return limites_capacidad(sesion, planta_id, variable, fruta)


def planta_elegida() -> int | None:
    """Id de la planta elegida en la barra (None si todavía no se dibujó)."""
    etiqueta = _guardado().get("planta")
    return plantas().get(etiqueta) if etiqueta else None


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
    tabla_emulsion_planta.clear()
    toneladas_planta.clear()
    plc_planta.clear()
    produccion_planta.clear()
    produccion_con_borradores.clear()
    rango_planta.clear()
    plantas.clear()
    centrifugas_planta.clear()
    limites_capacidad_planta.clear()
    rangos_centrifugas_planta.clear()
    umbral_paralelo_planta.clear()


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

    st.session_state[CLAVE_ACTUALES] = None
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
    st.session_state[CLAVE_ACTUALES] = filtros
    datos = filtrar(tabla, filtros)

    pares = int(((datos["lugar"] == "Entrada") & datos["eficiencia_par"].notna()).sum())
    barra.markdown(f"**{len(datos):,} muestras** · {pares:,} pares entrada/salida".replace(",", "."))
    if len(datos) < 3:
        barra.warning("Quedan menos de 3 muestras: los promedios y gráficos no son representativos.")
    return datos


if __name__ == "__main__":      # streamlit run app/filtros.py: prueba de la barra sola
    st.title("Prueba de la barra de filtros")
    st.dataframe(barra_filtros(), hide_index=True)
