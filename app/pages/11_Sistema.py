"""Página Sistema: la mirada del sistema completo de recuperación de aceite (informe Brown 2023).

Usa la barra de filtros global. Una pestaña por análisis; solo se dibuja la abierta, cada gráfico tiene botón PNG,
cada tabla Excel y cada pestaña "Descargar todo".
- Volatilización: eficiencia eBOE (por turno y por día) y eficiencia de proceso (por día), en gráficos separados,
  contra la fase pesada específica o el rango de fase pesada de referencia (sin circularidad con la eficiencia).
- Efectos principales: modelo lineal con medias ajustadas (como Minitab), correlaciones y VIF de los factores, e
  interacción de la MTPH con la temporada o con el régimen de agua. Respuestas: eficiencia eBOE, aceite en salida o
  aceite captado por las primarias (por turno).
- Umbral de MTPH: respuesta por MTPH y regresión segmentada por temporada.
- Antes/después: un cambio de la bitácora (Carga diaria › Cambios operativos) y los KPI en los N días de producción
  antes y después, con su prueba.
- Fase pesada: caudal de fase pesada real (laboratorio) contra el requerido por el balance de aceite del licor;
  rango de fase pesada (gráfico Brown) por temporada, eBOE, día y turno; coherencia del muestreo del licor (indicador
  interno: no se usa en otros análisis y no se muestra en el modo presentación).
Solo muestra: la lógica está en core/fase_pesada.py, core/volatilizacion.py y core/efectos.py.
"""

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.analisis import filtrar, pares_con_emulsion
from core.analisis_centrifugas import filtrar_centrifugas
from core.cambios_operativos import DIAS_POR_DEFECTO, antes_despues, texto_cambio
from core.db import crear_sesion
from core.efectos import (AYUDA_CAPTADO, CARGAS, EXPLICACION_MEDIA_AJUSTADA, NOMBRES, RESPUESTAS, captado_por_turno,
                          correlaciones, datos_por_turno, frase_quiebre, modelo_efectos, modelo_interaccion,
                          orden_de_niveles, por_mtph, preparar_datos, segmentada_por_temporada)
from core.inicio import SIN_EMULSION
from core.estadistica import es_outlier_iqr
from core.fase_pesada import CLAVES_TURNO, mediana_por, tabla_fase_pesada
from core.formato import numero, texto_p
from core.parametros import ParametrosIncompletos
from core.proceso import filtrar_produccion, turnos_sin_aprobar
from core.repetidas import sin_entradas_repetidas
from core.volatilizacion import (HIPOTESIS, RANGOS_FP, eficiencia_eboe_por_turno, eficiencias_por_dia,
                                 medias_por_rango)
from descargas import Descargas
from filtros import (barra_filtros, cambios_planta, casilla_repetidas, centrifugas_planta, filtros_elegidos, motor,
                     parametros_fase_pesada_planta, planta_elegida, produccion_con_borradores, produccion_planta,
                     tabla_emulsion_planta, toneladas_planta)
from graficos import (boxplot_temporadas, curva_mtph, dispersion_por_temporada, efectos_principales,
                      grafico_interaccion, intervalos_agrupados, lineas_en_el_tiempo)
from presentacion import en_presentacion

st.set_page_config(page_title="Sistema", layout="wide")
cartel_modo_prueba()
st.title("Sistema de recuperación de aceite")
COHERENCIA = "Coherencia del muestreo (licor)"
AYUDA_COHERENCIA = ("Caudal de fase pesada que explicaría el aceite medido en el licor. Si queda por debajo del real, la "
                    "muestra de licor sale más rica que el licor promedio (punto o momento de muestreo, dilución hasta "
                    "el tanque). Indicador interno, no es una pérdida.")
CIRCULARIDAD = ("El rango de fase pesada usa la eficiencia del mismo análisis, así que compararlo con la eficiencia daría "
                "una relación matemática: se muestra solo contra el gráfico Brown. Para comparar con la eficiencia "
                "(Volatilización) se usan el rango de referencia (eficiencia fija de la planta) y la fase pesada "
                "específica (m³/t).")
CON_COHERENCIA = not en_presentacion()     # indicador interno: no en el modo presentación
st.caption("Mirada del sistema completo: eBOE, emulsión, fase pesada y centrífugas (informe Brown 2023).")

datos = barra_filtros()
if filtros_elegidos() is None:      # la planta no tiene análisis: la barra ya lo avisó
    st.stop()


def aviso_n(cantidad: int, que: str) -> None:
    if cantidad < 3:
        st.warning(f"N < 3 en {que}: los valores no son representativos.")


def tabla_medianas(tabla: pd.DataFrame, claves: dict) -> pd.DataFrame:
    """Medianas de mediana_por() con nombres para la pantalla. claves: {columna: título}."""
    vista = tabla.rename(columns=claves)
    for columna in claves.values():
        if columna in ("Temporada", "eBOE"):
            vista[columna] = vista[columna].astype(str)
    return pd.DataFrame({**{t: vista[t] for t in claves.values()},
                         "Rango de fase pesada (%)": vista["rango_fp_pct"].map(lambda v: numero(v, 1)),
                         "Real (m³/h)": vista["real"].map(lambda v: numero(v, 2)),
                         "Requerido (m³/h)": vista["requerido"].map(lambda v: numero(v, 2)),
                         **({f"{COHERENCIA} (m³/h)": vista["coherente"].map(lambda v: numero(v, 2))}
                            if CON_COHERENCIA else {}),
                         "N análisis": vista["n"].map(lambda v: numero(v, 0) + (" ⚠️" if v < 3 else ""))})


def pestana_fase_pesada(registro: Descargas) -> None:
    try:
        parametros = parametros_fase_pesada_planta(planta_elegida())
    except ParametrosIncompletos as error:
        st.warning(str(error))
        return
    st.caption(
        "**Requerido**: caudal de fase pesada que lleva el licor al aceite objetivo "
        f"({numero(parametros['omega_licor_objetivo_pct'], 2)} %), con el balance de aceite del licor: aceite removido "
        f"de la fruta (MTPH × entrada × eficiencia), agua de los aspersores "
        f"({numero(parametros['caudal_aspersores_lh'], 0)} L/h) y aceite en la fase pesada que vuelve "
        f"({numero(parametros['omega_fp_pct'], 2)} %). **Real**: caudal que carga el laboratorio del BOE. **Rango de "
        "fase pesada** = (real − requerido) / requerido × 100: negativo, entra menos fase pesada que la requerida "
        "(gráfico Brown). Por turno y por día, mediana de los análisis.")
    st.caption(CIRCULARIDAD)
    emulsion = filtrar(tabla_emulsion_planta(planta_elegida()), filtros_elegidos())
    tabla = tabla_fase_pesada(pares_con_emulsion(datos, emulsion), parametros)
    validos = tabla.dropna(subset=["rango_fp_pct"])
    if validos.empty:
        st.info("No hay pares con caudal de fase pesada y MTPH con estos filtros.")
        return
    validos = validos.assign(outlier=es_outlier_iqr(validos["rango_fp_pct"]))

    columnas = st.columns(4)
    columnas[0].metric("Rango de fase pesada", f"{numero(validos['rango_fp_pct'].median(), 1)} %",
                       border=True, help=f"Mediana de los análisis. N = {len(validos)} análisis.")
    tarjetas = [(columnas[1], "caudal_fase_pesada", "Real (laboratorio)", ""),
                (columnas[2], "requerido", "Requerido (balance)", "")]
    if CON_COHERENCIA:
        tarjetas.append((columnas[3], "coherente", COHERENCIA, AYUDA_COHERENCIA + " "))
    for columna, clave, titulo, ayuda in tarjetas:
        valores = validos[clave].dropna()
        columna.metric(titulo, f"{numero(valores.median(), 2)} m³/h" if len(valores) else "—", border=True,
                       help=f"{ayuda}Mediana de los análisis. N = {len(valores)}.")
    aviso_n(len(validos), "el período")
    st.caption(f"{numero(int(validos['outlier'].sum()), 0)} análisis con el rango fuera de los límites IQR: se marcan "
               "en la tabla de análisis, no se excluyen.")

    st.markdown("##### Por temporada")
    por_temporada = mediana_por(validos, ["temporada"])
    registro.tabla(tabla_medianas(por_temporada, {"temporada": "Temporada"}), "Fase pesada por temporada")
    figura = boxplot_temporadas(validos, "rango_fp_pct", "Rango de fase pesada (%)", "%", 1)
    abajo, arriba_eje = validos["rango_fp_pct"].quantile([0.01, 0.99])
    margen = (arriba_eje - abajo) * 0.05
    figura.update_yaxes(range=[abajo - margen, arriba_eje + margen])
    registro.grafico(figura, "Rango de fase pesada por temporada")
    afuera = int(((validos["rango_fp_pct"] < abajo) | (validos["rango_fp_pct"] > arriba_eje)).sum())
    st.caption(f"Eje recortado al percentil 1–99 para ver la caja: {numero(afuera, 0)} análisis quedan fuera del eje "
               "(siguen contados en las medianas; los valores muy altos aparecen cuando el requerido es muy chico).")

    st.markdown("##### Por temporada y eBOE")
    incluir = casilla_repetidas("sistema_fp_repetidas")
    por_eboe = sin_entradas_repetidas(validos, incluir)
    if not incluir:
        st.caption("Sin las entradas repetidas en varios eBOE (posible copia), como en el resto de las comparaciones "
                   "entre eBOE.")
    registro.tabla(tabla_medianas(mediana_por(por_eboe, ["temporada", "eboe"]),
                                  {"temporada": "Temporada", "eboe": "eBOE"}), "Fase pesada por temporada y eBOE")

    st.markdown("##### Por día de producción")
    dias = mediana_por(validos, ["fecha_produccion"])
    series = {"real": ("Real (laboratorio)", "n"), "requerido": ("Requerido (balance)", "n")}
    if CON_COHERENCIA:
        series["coherente"] = (COHERENCIA, "n")
    registro.grafico(lineas_en_el_tiempo(dias, "fecha_produccion", series, "Caudal de fase pesada (m³/h)", "m³/h"),
                     "Caudal de fase pesada por día")
    st.caption("Mediana de los análisis de cada día. Informativo: estos caudales no generan alertas.")

    with st.expander("Por turno"):
        turnos = mediana_por(validos, CLAVES_TURNO).assign(
            fecha_produccion=lambda d: d["fecha_produccion"].dt.strftime("%d/%m/%Y"))
        registro.tabla(tabla_medianas(turnos, {"fecha_produccion": "Día", "turno": "Turno"}), "Fase pesada por turno")
    with st.expander("Análisis"):
        registro.tabla(pd.DataFrame({
            "Fecha y hora": validos["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"), "Turno": validos["turno"],
            "eBOE": validos["eboe"], "MTPH": validos["tph"].map(lambda v: numero(v, 1)),
            "Entrada (kg/MT)": validos["aceite_fruta"].map(numero),
            "Eficiencia (%)": (validos["eficiencia_par"] * 100).map(lambda v: numero(v, 1)),
            "Licor (%)": validos["aceite_emulsion"].map(numero),
            "Real (m³/h)": validos["caudal_fase_pesada"].map(numero),
            "Requerido (m³/h)": validos["requerido"].map(numero),
            **({f"{COHERENCIA} (m³/h)": validos["coherente"].map(numero)} if CON_COHERENCIA else {}),
            "Rango (%)": validos["rango_fp_pct"].map(lambda v: numero(v, 1)),
            "Outlier (IQR)": validos["outlier"].map({True: "sí", False: ""}),
            "Entrada repetida": validos["entrada_repetida"].map({True: "sí", False: ""})}),
            "Fase pesada por análisis")


def tabla_por_rango(medias: pd.DataFrame, que: str, decimales: int = 1) -> pd.DataFrame:
    """Media, IC 95 % y N por temporada y rango (core.volatilizacion.medias_por_rango) para la pantalla."""
    return pd.DataFrame({
        "Temporada": medias["temporada"].astype(str), "Rango": medias["grupo"],
        f"N {que}": medias["n"].map(lambda v: numero(v, 0) + (" ⚠️" if v < 3 else "")),
        "Media (%)": medias["media"].map(lambda v: numero(v, decimales)),
        "IC 95 %": [f"{numero(a, decimales)} a {numero(b, decimales)}" if pd.notna(a) else "—"
                    for a, b in zip(medias["ic_inf"], medias["ic_sup"])]})


def sin_ic_chico(medias: pd.DataFrame) -> pd.DataFrame:
    """Para el gráfico: los rangos con N < 3 van como punto, sin intervalo (su IC es enorme y aplasta el resto)."""
    chicos = medias["n"] < 3
    return medias.assign(ic_inf=medias["ic_inf"].mask(chicos), ic_sup=medias["ic_sup"].mask(chicos))


def pestana_volatilizacion(registro: Descargas) -> None:
    try:
        parametros = parametros_fase_pesada_planta(planta_elegida())
    except ParametrosIncompletos as error:
        st.warning(str(error))
        return
    st.info(HIPOTESIS)
    st.caption("El rango de fase pesada de la pestaña Fase pesada usa la eficiencia del mismo análisis: compararlo con "
               "la eficiencia daría una relación matemática. Acá se usan dos variables sin esa circularidad: el rango "
               f"de referencia (con la eficiencia fija en {numero(parametros['eficiencia_ref_rango_pct'], 0)} %, "
               "parámetro de la planta) y la fase pesada específica (caudal real / MTPH).")
    variable = st.radio("Variable de fase pesada", list(RANGOS_FP), horizontal=True, key="sistema_vol_variable",
                        format_func=lambda v: f"{RANGOS_FP[v]['nombre']} ({RANGOS_FP[v]['unidad']})")
    rango = RANGOS_FP[variable]
    nombre, unidad, decimales = rango["nombre"], rango["unidad"], rango["decimales"]
    tabla = tabla_fase_pesada(pares_con_emulsion(datos, SIN_EMULSION), parametros)
    filtros, planta = filtros_elegidos(), planta_elegida()
    toneladas = toneladas_planta(planta)
    turnos = eficiencia_eboe_por_turno(tabla, variable)
    dias = eficiencias_por_dia(tabla, variable, toneladas, filtrar_produccion(produccion_planta(planta), filtros))
    if turnos.dropna(subset=["rango"]).empty:
        st.info("No hay pares con caudal de fase pesada y MTPH con estos filtros.")
        return
    st.caption(f"Rangos: {', '.join(rango['etiquetas'])} {unidad} (cada uno incluye su límite inferior). El valor de un "
               "turno o de un día es la mediana de sus análisis.")

    st.markdown("##### Eficiencia eBOE")
    izquierda, derecha = st.columns(2)
    registro.grafico(dispersion_por_temporada(dias, "valor", "eficiencia_eboe", nombre, "Eficiencia eBOE", unidad, "%",
                                              decimales, 1, n="n_pares"),
                     f"Eficiencia eBOE por día vs {nombre.lower()}", donde=izquierda)
    medias_eboe = medias_por_rango(turnos, "eficiencia", variable)
    if not medias_eboe.empty:
        registro.grafico(intervalos_agrupados(sin_ic_chico(medias_eboe), "Eficiencia eBOE (%)", "%", 1, f"{nombre} ({unidad})"),
                         f"Eficiencia eBOE por rango de {nombre.lower()}", donde=derecha)
    st.caption("Puntos: un día de producción (eficiencia de los eBOE juntos, ponderada por las toneladas aprobadas de "
               "cada turno; tenues con N < 3 pares). Intervalos: media e IC 95 % de los turnos de cada rango (cada "
               "turno, la media de sus pares). Los rangos con N < 3 van sin intervalo (⚠️ en la tabla).")
    registro.tabla(tabla_por_rango(medias_eboe, "turnos"), f"Eficiencia eBOE por rango de {nombre.lower()}")

    st.markdown("##### Eficiencia de proceso (rendimiento en tambor)")
    sin_aprobar = turnos_sin_aprobar(produccion_con_borradores(planta), filtros)
    if sin_aprobar:
        st.warning(f"{sin_aprobar} turno{'s' if sin_aprobar > 1 else ''} con producción en borrador: no entra"
                   f"{'n' if sin_aprobar > 1 else ''} hasta aprobarlo{'s' if sin_aprobar > 1 else ''}.")
    proceso = dias.dropna(subset=["eficiencia_proceso"])
    if proceso.empty:
        st.info("No hay días con producción aprobada (aceite envasado y al menos 60 t de fruta) con estos filtros.")
        return
    izquierda, derecha = st.columns(2)
    registro.grafico(dispersion_por_temporada(proceso, "valor", "eficiencia_proceso", nombre, "Eficiencia de proceso",
                                              unidad, "%", decimales, 1),
                     f"Eficiencia de proceso por día vs {nombre.lower()}", donde=izquierda)
    medias_proceso = medias_por_rango(proceso, "eficiencia_proceso", variable)
    if not medias_proceso.empty:
        registro.grafico(intervalos_agrupados(sin_ic_chico(medias_proceso), "Eficiencia de proceso (%)", "%", 1,
                                              f"{nombre} ({unidad})"),
                         f"Eficiencia de proceso por rango de {nombre.lower()}", donde=derecha)
    st.caption("Por día de producción, porque el envasado no coincide con el turno: Σ aceite envasado / Σ fruta de los "
               "turnos aprobados del día, sobre el aceite en fruta de entrada del día (como Análisis › Proceso). Los "
               "días con menos de 60 t no entran. Los rangos con N < 3 van sin intervalo (⚠️ en la tabla).")
    registro.tabla(tabla_por_rango(medias_proceso, "días"), f"Eficiencia de proceso por rango de {nombre.lower()}")
    with st.expander("Días"):
        marcados = proceso.assign(outlier=es_outlier_iqr(proceso["eficiencia_proceso"]))
        st.caption(f"{numero(int(marcados['outlier'].sum()), 0)} días fuera de los límites IQR: se marcan, no se "
                   "excluyen.")
        registro.tabla(pd.DataFrame({
            "Día": marcados["fecha_produccion"].dt.strftime("%d/%m/%Y"), "Temporada": marcados["temporada"].astype(str),
            f"{nombre} ({unidad})": marcados["valor"].map(lambda v: numero(v, decimales)), "Rango": marcados["rango"],
            "Fruta (t)": marcados["fruta_t"].map(lambda v: numero(v, 0)),
            "Eficiencia de proceso (%)": marcados["eficiencia_proceso"].map(lambda v: numero(v, 1)),
            "Outlier (IQR)": marcados["outlier"].map({True: "sí", False: ""})}), "Eficiencia de proceso por día")


def datos_para_modelo(parametros: dict, clave: str, respuesta: str) -> pd.DataFrame:
    """Pares (core.efectos.preparar_datos; sin las entradas repetidas salvo que se marque la casilla, porque el modelo
    compara eBOE) o, para el aceite captado, un registro por turno (las centrífugas, con la barra de filtros)."""
    pares = pares_con_emulsion(datos, SIN_EMULSION)
    if RESPUESTAS[respuesta]["por_turno"]:
        _, balance = centrifugas_planta(planta_elegida())
        balance = filtrar_centrifugas(balance, filtros_elegidos()) if not balance.empty else balance
        st.caption("Aceite captado = aceite de la fase liviana de las primarias en marcha (kg/h, balance de cada "
                   "muestreo sin los copiados; mediana de cada primaria en el turno, sumadas). En %: dividido por el "
                   "aceite removido por los eBOE (Σ MTPH × aceite en fruta de entrada × eficiencia, kg/h). En kg/MT: "
                   "dividido por la MTPH total de los eBOE. Un registro por turno.")
        turnos = datos_por_turno(preparar_datos(tabla_fase_pesada(pares, parametros)), captado_por_turno(balance))
        if respuesta == "captado_pct" and not turnos.empty:
            mas_de_100 = int((turnos["captado_pct"] > 100).sum())
            st.caption(f"{numero(mas_de_100, 0)} de {numero(int(turnos['captado_pct'].notna().sum()), 0)} turnos con "
                       "más de 100 % captado (error de medición): se marcan, no se excluyen.")
        return turnos
    incluir = casilla_repetidas(f"{clave}_repetidas")
    if not incluir:
        pares = sin_entradas_repetidas(pares)
        st.caption("Sin las entradas repetidas en varios eBOE (posible copia): el modelo compara eBOE.")
    return preparar_datos(tabla_fase_pesada(pares, parametros))


def elegir_respuesta(clave: str) -> str:
    """Eficiencia eBOE, aceite en salida o aceite captado por las primarias (en %, por defecto, o en kg/MT)."""
    opcion = st.radio("Respuesta", ["eficiencia_pct", "aceite_salida", "captado"], horizontal=True, key=clave,
                      format_func=lambda r: "Aceite captado por las primarias" if r == "captado" else
                      f"{RESPUESTAS[r]['nombre']} ({RESPUESTAS[r]['unidad']})")
    if opcion != "captado":
        return opcion
    medida = st.radio("Aceite captado", ["captado_pct", "captado_kg_mt"], horizontal=True, key=f"{clave}_captado",
                      format_func=lambda r: {"captado_pct": "% del aceite removido por los eBOE",
                                             "captado_kg_mt": "kg por tonelada de fruta"}[r])
    if medida == "captado_pct":
        st.caption(AYUDA_CAPTADO + " Más de 100 % no es físico: indica error de medición (se cuentan abajo y no se "
                   "borran).")
    return medida


def tabla_correlaciones(tabla: pd.DataFrame) -> pd.DataFrame:
    """Una fila por par de variables y una columna por temporada: "r (N)"."""
    vista = tabla.assign(par=tabla["x"] + " – " + tabla["y"],
                         valor=[("—" if pd.isna(r) else numero(r, 2)) + f" (N {numero(n, 0)})"
                                for r, n in zip(tabla["r"], tabla["n"])])
    return vista.pivot(index="par", columns="temporada", values="valor").reset_index().rename(columns={"par": "Variables"})


def pestana_efectos(registro: Descargas) -> None:
    try:
        parametros = parametros_fase_pesada_planta(planta_elegida())
    except ParametrosIncompletos as error:
        st.warning(str(error))
        return
    respuesta = elegir_respuesta("sistema_efectos_respuesta")
    carga = st.radio("Factor de carga", list(CARGAS), horizontal=True, key="sistema_efectos_carga",
                     format_func=lambda c: {"mtph": "MTPH en rangos (por defecto)",
                                            "fp": "Fase pesada específica en rangos"}[c],
                     help="Nunca los dos juntos: dentro de una temporada pueden ser casi la misma variable (en 2026 "
                          "el caudal de fase pesada es casi constante, así que la fase pesada específica es casi 1/MTPH).")
    info = RESPUESTAS[respuesta]
    nombre, unidad, decimales = info["nombre"], info["unidad"], info["decimales"]
    base = datos_para_modelo(parametros, "sistema_efectos", respuesta)
    if base.empty:
        st.info("No hay datos para el modelo con estos filtros.")
        return

    st.markdown("##### Antes del modelo: correlaciones entre los factores numéricos")
    registro.tabla(tabla_correlaciones(correlaciones(base)), "Correlaciones entre factores")
    st.caption("r de Pearson dentro de cada temporada. Con |r| ≥ 0,7 dos variables son casi la misma: no entran juntas "
               "al modelo (por eso usa MTPH o fase pesada específica, nunca las dos).")

    resultado = modelo_efectos(base, respuesta, carga)
    if resultado["medias"] is None:
        st.info(f"No hay datos suficientes para el modelo (N = {resultado['n']}).")
        return
    vif = resultado["vif"]
    for fila in vif[vif["confundido"]].to_dict("records"):
        st.warning(f"**{fila['nombre']}** está confundido" + (
            f" con **{fila['confundido_con']}** (V de Cramér {numero(fila['v_cramer'], 2)})" if fila["confundido_con"]
            else "") + f": VIF {numero(fila['vif'], 1)} > 5. Sus efectos no se pueden separar bien.")
    with st.expander("VIF de cada factor"):
        registro.tabla(pd.DataFrame({"Factor": vif["nombre"], "gl": vif["gl"].map(lambda v: numero(v, 0)),
                                     "VIF": vif["vif"].map(lambda v: numero(v, 2)),
                                     "Confundido con": vif["confundido_con"].fillna("—")}), "VIF de los factores")
        st.caption("VIF = GVIF^(1/gl) (Fox y Monette): con un gl es el VIF de siempre. Más de 5: el factor está "
                   "confundido con otros.")

    st.markdown(f"##### Efectos principales · {nombre}")
    columnas = st.columns(3)
    columnas[0].metric("R²", f"{numero(resultado['r2'] * 100, 1)} %", border=True,
                       help="Parte de la variación de la respuesta que explica el modelo.")
    columnas[1].metric("R² ajustado", f"{numero(resultado['r2_ajustado'] * 100, 1)} %", border=True)
    columnas[2].metric("N", numero(resultado["n"], 0), border=True,
                       help="Registros con todos los datos del modelo (" +
                            ("turnos" if info["por_turno"] else "pares entrada/salida") + ").")
    st.caption(EXPLICACION_MEDIA_AJUSTADA)
    registro.grafico(efectos_principales(resultado["medias"], f"{nombre} ({unidad})", unidad, decimales),
                     f"Efectos principales - {nombre}")
    anova = resultado["anova"]
    registro.tabla(pd.DataFrame({"Factor": anova["nombre"], "gl": anova["gl"].map(lambda v: numero(v, 0)),
                                 "F": anova["f"].map(lambda v: numero(v, 1)), "p": anova["p"].map(texto_p)}),
                   f"ANOVA - {nombre}")
    st.caption(f"Modelo lineal con {', '.join(NOMBRES[f].lower() for f in resultado['factores'])}. ANOVA tipo II: el "
               "efecto de cada factor una vez que están los demás. La fase pesada " +
               ("no entra." if carga == "mtph" else "entra en lugar de la MTPH."))
    with st.expander("Medias ajustadas"):
        medias = resultado["medias"]
        registro.tabla(pd.DataFrame({
            "Factor": medias["nombre"], "Nivel": medias["nivel"],
            "N": medias["n"].map(lambda v: numero(v, 0) + (" ⚠️" if v < 3 else "")),
            f"Media ajustada ({unidad})": [numero(m, decimales) if e else "no estimable"
                                          for m, e in zip(medias["media"], medias["estimable"])],
            "IC 95 %": [f"{numero(a, decimales)} a {numero(b, decimales)}" if e else "—"
                        for a, b, e in zip(medias["ic_inf"], medias["ic_sup"], medias["estimable"])]}),
            f"Medias ajustadas - {nombre}")

    st.markdown("##### Interacción con la carga")
    opciones = ["temporada"] if carga == "fp" else ["temporada", "regimen"]
    por_defecto = 0 if base["temporada"].nunique() > 1 else len(opciones) - 1
    con = st.radio("Interacción con", opciones, index=por_defecto, horizontal=True, key="sistema_efectos_con",
                   format_func=lambda c: {"temporada": "Temporada",
                                          "regimen": "Régimen de agua (FP ≥ 0,8 m³/t o menos)"}[c])
    if base["temporada"].nunique() == 1:
        st.caption("Hay una sola temporada en el período. Para comparar temporadas (por ejemplo, si con agua suficiente "
                   "la eficiencia no cae con la MTPH), elegí en la barra un período que las incluya: «Personalizado» con "
                   "todas las fechas.")
    interaccion = modelo_interaccion(base, respuesta, carga, con)
    if interaccion["celdas"] is None:
        st.info("Hace falta más de un nivel de la carga y de " + ("temporada." if con == "temporada" else
                                                                  "régimen de agua."))
        return
    a = interaccion["a"]
    st.markdown(f"**{NOMBRES[a]} × {NOMBRES[con].lower()}: {texto_p(interaccion['p'])}** · "
                f"{'el efecto de la carga cambia' if interaccion['p'] < 0.05 else 'no hay evidencia de que cambie'} "
                f"según {NOMBRES[con].lower()} (N = {numero(interaccion['n'], 0)}).")
    celdas = interaccion["celdas"]
    registro.grafico(grafico_interaccion(celdas, NOMBRES[a], nombre, unidad, decimales,
                                         orden_de_niveles(a, celdas["a"].unique())),
                     f"Interacción {NOMBRES[a]} x {NOMBRES[con].lower()} - {nombre}")
    st.caption("Media ajustada de cada combinación, con los demás factores repartidos en partes iguales, e IC 95 %. "
               "Las combinaciones sin datos no se dibujan; las de N < 3 se ven tenues.")
    registro.tabla(pd.DataFrame({
        NOMBRES[con]: celdas["b"], NOMBRES[a]: celdas["a"],
        "N": celdas["n"].map(lambda v: numero(v, 0) + (" ⚠️" if 0 < v < 3 else "")),
        f"Media ajustada ({unidad})": [numero(m, decimales) if e else ("sin datos" if n == 0 else "no estimable")
                                      for m, e, n in zip(celdas["media"], celdas["estimable"], celdas["n"])],
        "IC 95 %": [f"{numero(x, decimales)} a {numero(y, decimales)}" if e else "—"
                    for x, y, e in zip(celdas["ic_inf"], celdas["ic_sup"], celdas["estimable"])]}),
        f"Interacción - {nombre}")


def mostrar_quiebres(registro: Descargas, quiebres: pd.DataFrame, respuesta: str, titulo: str) -> None:
    """Frases y tabla de los quiebres por temporada (core.efectos.segmentada_por_temporada)."""
    for fila in quiebres.to_dict("records"):
        st.markdown("- " + frase_quiebre(fila, respuesta))
    unidad_pendiente = "puntos" if RESPUESTAS[respuesta]["unidad"] == "%" else RESPUESTAS[respuesta]["unidad"]
    registro.tabla(pd.DataFrame({
        "Temporada": quiebres["temporada"].astype(str), "Quiebre (MTPH)": quiebres["quiebre"].map(lambda v: numero(v, 0)),
        f"Pendiente antes ({unidad_pendiente}/MTPH)": quiebres["pendiente_antes"].map(lambda v: numero(v, 3)),
        f"Pendiente después ({unidad_pendiente}/MTPH)": quiebres["pendiente_despues"].map(lambda v: numero(v, 3)),
        "p del cambio": quiebres["p_cambio"].map(lambda v: "—" if pd.isna(v) else texto_p(v)),
        "p después": quiebres["p_despues"].map(lambda v: "—" if pd.isna(v) else texto_p(v)),
        "N": quiebres["n"].map(lambda v: numero(v, 0)), "N después": quiebres["n_despues"].map(lambda v: numero(v, 0))}),
        titulo)


def pestana_umbral(registro: Descargas) -> None:
    try:
        parametros = parametros_fase_pesada_planta(planta_elegida())
    except ParametrosIncompletos as error:
        st.warning(str(error))
        return
    respuesta = elegir_respuesta("sistema_umbral_respuesta")
    info = RESPUESTAS[respuesta]
    nombre, unidad, decimales = info["nombre"], info["unidad"], info["decimales"]
    base = datos_para_modelo(parametros, "sistema_umbral", respuesta)
    if base.dropna(subset=["tph", respuesta]).empty:
        st.info("No hay datos con MTPH con estos filtros.")
        return
    ajustados, ajustada = segmentada_por_temporada(base, respuesta, ajustado=True)
    st.markdown("##### Ajustado por programa, aceite en fruta, calibre, mes, turno" +
                (" y eBOE" if not info["por_turno"] else ""))
    registro.grafico(curva_mtph(por_mtph(base.assign(_ajustada=ajustada), "_ajustada", por=None), ajustados,
                                f"{nombre} ajustada", unidad, decimales), f"{nombre} ajustada por MTPH")
    st.caption("Respuesta ajustada (residuo parcial): sin el efecto de los demás factores, estimado en el mismo modelo "
               "segmentado. Media e IC 95 % de cada MTPH (redondeada a la unidad), los eBOE juntos; tenues con N < 3. "
               "Línea punteada: el punto de quiebre de cada temporada.")
    mostrar_quiebres(registro, ajustados, respuesta, f"Quiebre de MTPH ajustado - {nombre}")
    st.caption("Regresión segmentada: se prueba cada MTPH entera de 10 a 18 como quiebre (con al menos 10 datos de "
               "cada lado) y se elige la de menor error. Los demás factores entran en el mismo modelo, así la pendiente "
               "no se confunde con ellos (por ejemplo, con programas más agresivos a más MTPH). p del cambio: ¿la "
               "pendiente cambia en el quiebre? p después: ¿por encima del quiebre la respuesta sube o baja?")
    with st.expander("Sin ajustar"):
        crudos, _ = segmentada_por_temporada(base, respuesta, ajustado=False)
        registro.grafico(curva_mtph(por_mtph(base, respuesta, por=None), crudos, nombre, unidad, decimales),
                         f"{nombre} por MTPH sin ajustar")
        mostrar_quiebres(registro, crudos, respuesta, f"Quiebre de MTPH sin ajustar - {nombre}")
    if not info["por_turno"]:
        with st.expander("Por eBOE"):
            tabla = por_mtph(base, respuesta, por="eboe")
            registro.tabla(pd.DataFrame({
                "Temporada": tabla["temporada"].astype(str), "eBOE": tabla["grupo"].astype(str),
                "MTPH": tabla["mtph"].map(lambda v: numero(v, 0)),
                "N": tabla["n"].map(lambda v: numero(v, 0) + (" ⚠️" if v < 3 else "")),
                f"Media ({unidad})": tabla["media"].map(lambda v: numero(v, decimales)),
                "IC 95 %": [f"{numero(x, decimales)} a {numero(y, decimales)}" if pd.notna(x) else "—"
                            for x, y in zip(tabla["ic_inf"], tabla["ic_sup"])]}), f"{nombre} por MTPH y eBOE")


def pestana_antes_despues(registro: Descargas) -> None:
    cambios = cambios_planta(planta_elegida())
    if cambios.empty:
        st.info("No hay cambios en la bitácora de la planta (se cargan en Carga diaria › Cambios operativos).")
        return
    cambios = cambios.sort_values("fecha_hora", ascending=False)
    textos = {int(i): texto_cambio(fila) for i, fila in zip(cambios["id"], cambios.to_dict("records"))}
    uno, dos = st.columns([3, 1])
    elegido = uno.selectbox("Cambio", list(textos), format_func=textos.get, key="sistema_cambio")
    dias = dos.number_input("Días antes y después", min_value=1, max_value=60, value=DIAS_POR_DEFECTO, step=1,
                            key="sistema_cambio_dias")
    fila = cambios[cambios["id"] == elegido].iloc[0]
    dia = fila["fecha_produccion"]
    with crear_sesion(motor()) as sesion:
        tabla = antes_despues(sesion, elegido, int(dias))
    st.caption(f"Antes: {dia - pd.Timedelta(days=dias):%d/%m/%Y} al {dia - pd.Timedelta(days=1):%d/%m/%Y}. Después: "
               f"{dia + pd.Timedelta(days=1):%d/%m/%Y} al {dia + pd.Timedelta(days=dias):%d/%m/%Y}. El día del cambio "
               f"({dia:%d/%m/%Y}) no entra en ninguno de los dos lados. Prueba: Welch si los dos lados son normales "
               "(Anderson-Darling), si no Mann-Whitney.")
    st.info("Una comparación antes/después no prueba la causa: en esos días pudieron cambiar otras cosas (fruta, "
            "programa, MTPH). Miralo junto con la bitácora y los gráficos de evolución.")

    def con(v, d, signo=False):
        if v is None or pd.isna(v):
            return "—"
        return ("+" if signo and v > 0 else "") + numero(v, d)

    registro.tabla(pd.DataFrame({
        "KPI": tabla["kpi"] + " (" + tabla["unidad"] + ")", "Ámbito": tabla["ambito"], "Por": tabla["por"],
        "Antes": [con(v, d) for v, d in zip(tabla["antes"], tabla["decimales"])],
        "Después": [con(v, d) for v, d in zip(tabla["despues"], tabla["decimales"])],
        "Diferencia": [con(v, d, True) for v, d in zip(tabla["diferencia"], tabla["decimales"])],
        "%": [con(v, 1, True) for v in tabla["pct"]],
        "N antes": [numero(n, 0) + (" ⚠️" if n < 3 else "") for n in tabla["n_antes"]],
        "N desp.": [numero(n, 0) + (" ⚠️" if n < 3 else "") for n in tabla["n_despues"]],
        "Prueba": tabla["prueba"].fillna("—"), "p": tabla["p"].map(lambda v: "—" if pd.isna(v) else texto_p(v))}),
        f"Antes y después del cambio del {fila['fecha_hora']:%d-%m-%Y}")
    st.caption("Por: qué se cuenta en el N (cada análisis, cada par entrada/salida, cada día de producción o cada "
               "turno). Si el cambio es de un eBOE, los KPI de los eBOE son solo de ese eBOE; los de la planta, de la "
               "planta. Rendimiento en tambor y eficiencia de proceso: por día, con la producción aprobada (días con al "
               "menos 60 t). Retorno de FP y aceite captado: por turno, desde las centrífugas.")


PESTANAS = {"Volatilización": pestana_volatilizacion, "Efectos principales": pestana_efectos,
            "Umbral de MTPH": pestana_umbral, "Antes/después": pestana_antes_despues,
            "Fase pesada": pestana_fase_pesada}
# Con key y on_change, cada pestaña sabe si está abierta (.open): solo se dibuja la abierta.
# bind="query-params": la pestaña queda en la URL (?pestana_sistema=Fase+pesada) y se puede compartir el enlace.
for (nombre, dibujar), pestana in zip(PESTANAS.items(), st.tabs(list(PESTANAS), key="pestana_sistema",
                                                                    on_change="rerun", bind="query-params")):
    if pestana.open:
        with pestana:
            arriba = st.container()
            registro = Descargas(nombre)
            dibujar(registro)
            registro.boton_todo(arriba)
