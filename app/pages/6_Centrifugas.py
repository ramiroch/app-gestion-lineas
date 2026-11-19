"""Página Centrífugas: primarias, secundarias y pulidora de aceite.

Usa la barra de filtros global: a las centrífugas les aplican las fechas de producción, el turno y el proceso (el
eBOE y el programa no; sí a los datos del laboratorio del BOE de la pestaña Retorno de FP y BOE). Una pestaña por bloque:
resumen, retorno de FP y BOE, fases, operación vs referencias, pérdidas, condiciones de eficiencia de la primaria y
muestras para revisar. Solo se dibuja la pestaña
abierta; cada gráfico tiene botón PNG, cada tabla Excel y cada pestaña "Descargar todo".
Solo muestra: la lógica está en core/analisis_centrifugas.py y los rangos en RangoReferencia.
"""

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba

from core.analisis import filtrar
from core.analisis_centrifugas import (A_DEFINIR, CALCULADO, NO_APLICA, ORDEN_EQUIPOS, PRIMARIA, PULIDORA, RECIRCULADO,
                                       SIN_DATOS, VARIABLES, agua_por_eboe, con_motivo, filtrar_centrifugas, motivos,
                                       para_revisar, perdida_descarga_mensual, perdida_pesada_por_destino,
                                       primarias_por_turno, relacion_boe, resumen_por_temporada, resumen_temporadas,
                                       caudal_nominal_primaria, valores_por_variable)
from core.condiciones_primaria import (VARIABLES as VARIABLES_CONDICION, cp_liviana_por_muestreo, datos_primarias,
                                       frase_nominal, frase_pendiente, hasta_nominal, pendiente, por_cuantiles,
                                       por_rango_caudal)
from core.formato import con_signo, numero, texto_p
from core.proceso import filtrar_produccion
from descargas import Descargas
from filtros import (barra_filtros, centrifugas_planta, filtros_elegidos, mostrar_personas_planta, operadores_planta,
                     planta_elegida, produccion_planta, rangos_centrifugas_planta, tabla_emulsion_planta,
                     umbral_paralelo_planta)
from graficos import (GRIS, agua_en_el_tiempo, boxplot_por_equipo, color_equipo, columnas_agrupadas,
                      intervalos_agrupados, lineas_en_el_tiempo)
from presentacion import en_presentacion

FASES = ["aceite_emulsion", "aceite_liviana", "aceite_pesada", "aceite_descarga"]
OPERACION = ["solidos", "cp_pesada", "cp_liviana", "t_descarga", "caudal_emulsion"]
DECIMALES = {"aceite_emulsion": 2, "aceite_liviana": 0, "aceite_pesada": 3, "aceite_descarga": 2, "solidos": 1,
             "cp_pesada": 1, "cp_liviana": 1, "t_descarga": 0, "caudal_emulsion": 0, "eficiencia": 1,
             "perdida_descarga": 3, "perdida_pesada": 2}
ESTADOS_PESADA = {CALCULADO: "canal (pérdida)", RECIRCULADO: RECIRCULADO}     # cómo se muestra cada estado
APARTE = {"t_descarga": PULIDORA}    # la pulidora descarga cada 30–60 min: aplastaría a las demás en el mismo gráfico
COLORES_EQUIPOS = {equipo: color_equipo(equipo, ORDEN_EQUIPOS) for equipo in ORDEN_EQUIPOS}


def con_coma(df: pd.DataFrame, decimales: int = 1, **por_columna):
    estilo = df.style.format(precision=decimales, decimal=",", thousands=".", na_rep="—")
    for columna, d in por_columna.items():
        estilo = estilo.format(precision=d, decimal=",", thousands=".", na_rep="—", subset=[columna])
    return estilo


def texto_rango(rango: dict | None, decimales: int) -> str:
    if rango is None:
        return "sin rango"
    minimo, maximo = rango["minimo"], rango["maximo"]
    if minimo is None:
        return f"≤ {numero(maximo, decimales)} {rango['unidad']}"
    if maximo is None:
        return f"≥ {numero(minimo, decimales)} {rango['unidad']}"
    return f"{numero(minimo, decimales)}–{numero(maximo, decimales)} {rango['unidad']}"


def tabla_resumen(resumen: pd.DataFrame, decimales: int):
    """Tabla N, mediana, cuartiles y % fuera de rango de una variable (core.resumen_por_temporada)."""
    vista = pd.DataFrame({
        "Temporada": resumen["temporada"].astype(str), "Equipo": resumen["equipo"], "N": resumen["n"],
        "Q1": resumen["q1"], "Mediana": resumen["mediana"], "Q3": resumen["q3"],
        "Rango": [texto_rango(None if pd.isna(f) else {"minimo": a if pd.notna(a) else None,
                                                       "maximo": b if pd.notna(b) else None, "unidad": u}, decimales)
                  for f, a, b, u in zip(resumen["fuente"], resumen["minimo"], resumen["maximo"], resumen["unidad"])],
        "Fuente": resumen["fuente"], "Alerta": resumen["genera_alerta"].map({True: "sí", False: "no"}),
        "% fuera de rango": resumen["pct_fuera"], "N < 3": resumen["pocos_datos"].map({True: "⚠", False: ""})})
    return con_coma(vista, decimales, **{"N": 0, "% fuera de rango": 1})


def avisar_pocos(resumen: pd.DataFrame) -> None:
    pocos = resumen[resumen["pocos_datos"] & (resumen["n"] > 0)]
    if not pocos.empty:
        lista = ", ".join(f"{e} {t}" for e, t in zip(pocos["equipo"], pocos["temporada"]))
        st.warning(f"N < 3 en {lista}: poco representativo.")


def bandas(variable: str, equipos) -> tuple[dict, str | None]:
    """{equipo: (mínimo, máximo)} del rango de su tipo de equipo, y las fuentes que se usaron."""
    tipo_de = muestras.drop_duplicates("equipo").set_index("equipo")["tipo"]
    resultado, fuentes = {}, set()
    for equipo in equipos:
        rango = rangos.get((variable, tipo_de.get(equipo)))
        if rango is not None:
            resultado[equipo] = (rango["minimo"], rango["maximo"])
            fuentes.add(rango["fuente"])
    return resultado, " y ".join(sorted(fuentes)) or None


def boxplot_variable(registro: Descargas, valores: pd.DataFrame, variable: str, color_banda: str | None = None) -> None:
    """Boxplot por equipo y temporada de una variable, con su banda, su tabla y el aviso de N < 3."""
    nombre, unidad = VARIABLES[variable]
    datos = valores[(valores["variable"] == variable) & ~valores["no_medible"]]
    st.markdown(f"#### {nombre} ({unidad})")
    if datos.empty:
        st.info("Sin datos con estos filtros.")
        return
    aparte = datos["tipo"] == APARTE.get(variable)
    for parte, titulo in ((datos[~aparte], nombre), (datos[aparte], f"{nombre} · {APARTE.get(variable, '').lower()}")):
        if parte.empty:
            continue
        banda, fuente = bandas(variable, parte["equipo"].unique())
        opciones = {"bandas": banda, "nombre_banda": f"Referencia ({fuente})" if fuente else "Referencia"}
        if color_banda:
            opciones["color_banda"] = color_banda
        registro.grafico(boxplot_por_equipo(parte, f"{nombre} ({unidad})", unidad, ORDEN_EQUIPOS, DECIMALES[variable],
                                            **opciones), titulo)
    resumen = resumen_por_temporada(valores[valores["variable"] == variable], rangos)
    avisar_pocos(resumen)
    with st.expander(f"Tabla: {nombre}"):
        registro.tabla(tabla_resumen(resumen, DECIMALES[variable]), nombre)


# ---------------------------------------------------------------- pestañas

def pestana_resumen(registro: Descargas) -> None:
    st.caption("Medianas por temporada con los filtros de la barra. Sin las muestras copiadas de la otra primaria. "
               "Los kg de pérdida en descarga son ESTIMADOS: kg/h × tiempo hasta el muestreo siguiente del mismo "
               "equipo (máximo 4 h); se muestran con las horas que cubren, sin extrapolar al resto de la temporada.")
    resumen = resumen_temporadas(muestras_f, balance_f, turnos, produccion_f)
    if resumen.empty:
        st.info("Sin datos con estos filtros.")
        return
    for fila in resumen.sort_values("temporada", ascending=False).to_dict("records"):
        st.markdown(f"#### Temporada {fila['temporada']}")
        uno, dos, tres, cuatro, cinco, seis = st.columns(6)
        uno.metric("Caudal de emulsión (mediana)", f"{numero(fila['caudal_mediana_lh'], 0)} L/h",
                   help=f"Por primaria. N = {fila['n_caudal']} muestras")
        dos.metric("Turnos con primarias", f"{fila['turnos_paralelo']} en paralelo",
                   f"{fila['turnos_una_sola']} una sola", delta_color="off",
                   help="En paralelo: observado (las dos muestreadas a la misma hora) o inferido con el umbral de la "
                        "planta.")
        tres.metric("Eficiencia de la primaria (mediana)", f"{numero(fila['eficiencia_mediana'], 1)} %",
                    help=f"N = {fila['n_eficiencia']} muestreos; {fila['n_fuera_fisico']} fuera de rango físico "
                         "(marcados, entran en la mediana)")
        cuatro.metric("Pérdida en descarga (mediana)", f"{numero(fila['descarga_mediana_kgh'], 3)} kg/h",
                      help=f"N = {fila['n_descarga']} muestreos (todos los equipos con volumen por descarga)")
        cinco.metric("Pérdida en descarga estimada", f"{numero(fila['kg_estimados'], 0)} kg",
                     f"en {numero(fila['horas_estimadas'], 0)} h cubiertas", delta_color="off",
                     help="Sin extrapolar: solo las horas cubiertas por los muestreos (máximo 4 h cada uno).")
        seis.metric("Pérdida por tonelada (mediana diaria)", f"{numero(fila['kg_mt_mediana'], 4)} kg/MT",
                    help=f"N = {fila['n_dias']} días con toneladas procesadas")
        chicos = [nombre for nombre, n in (("caudal", fila["n_caudal"]), ("eficiencia", fila["n_eficiencia"]),
                                           ("pérdida en descarga", fila["n_descarga"]), ("kg/MT", fila["n_dias"]))
                  if n < 3]
        if chicos:
            st.warning(f"N < 3 en {', '.join(chicos)}: poco representativo.")
    with st.expander("Tabla del resumen"):
        registro.tabla(con_coma(resumen.rename(columns={
            "temporada": "Temporada", "caudal_mediana_lh": "Caudal emulsión (L/h)", "n_caudal": "N caudal",
            "turnos_paralelo": "Turnos en paralelo", "turnos_una_sola": "Turnos una sola",
            "eficiencia_mediana": "Eficiencia primaria (%)", "n_eficiencia": "N eficiencia",
            "n_fuera_fisico": "Fuera de rango físico", "descarga_mediana_kgh": "Pérdida descarga (kg/h)",
            "n_descarga": "N descarga", "kg_estimados": "kg estimados", "horas_estimadas": "Horas cubiertas",
            "kg_mt_mediana": "kg/MT (mediana diaria)", "n_dias": "N días"}), 1,
            **{"Pérdida descarga (kg/h)": 3, "kg/MT (mediana diaria)": 4, "Temporada": 0}), "Resumen por temporada")


def pestana_agua(registro: Descargas) -> None:
    if umbral is None:
        st.warning("La planta no tiene definido el umbral de primarias en paralelo (umbral_paralelo_lh, hoja Planta "
                   "de los maestros): no se puede calcular el retorno de FP a eBOE.")
        return
    st.caption(f"Retorno de FP a eBOE = caudal de fase pesada de las primarias en marcha (balance de cada "
               f"muestreo) / eBOE con análisis de Entrada en el turno (3 si no hay). Primarias en paralelo: "
               f"observado si las dos se muestrearon a la misma hora; si no, inferido (las dos en el turno y el caudal "
               f"de cada una ≤ {numero(umbral, 0)} L/h, parámetro de la planta). Laboratorio: columna Caudal de la "
               "hoja BOE (m³/h por eBOE).")
    agua = agua_por_eboe(balance_f, turnos, datos)
    if agua.empty:
        st.info("Sin turnos con balance de las primarias con estos filtros.")
        return
    temporadas = sorted(agua["temporada"].unique(), reverse=True)
    temporada = st.selectbox("Temporada", temporadas, key="centrifugas_temporada_agua")
    de_temporada = agua[agua["temporada"] == temporada]
    calc, lab = de_temporada["agua_por_eboe_m3h"].median(), de_temporada["agua_lab_m3h"].median()
    uno, dos, tres = st.columns(3)
    uno.metric("Retorno de FP a eBOE calculado (mediana)", f"{numero(calc, 2)} m³/h", help=f"N = {len(de_temporada)} turnos")
    dos.metric("Caudal del laboratorio (mediana)", f"{numero(lab, 2)} m³/h",
               help=f"N = {int(de_temporada['agua_lab_m3h'].notna().sum())} turnos con dato")
    tres.metric("Turnos en paralelo", f"{int((de_temporada['modo'] == 'en paralelo').sum())} de {len(de_temporada)}")
    registro.grafico(agua_en_el_tiempo(de_temporada), f"Retorno de FP a eBOE {temporada}")
    por_temporada = agua.groupby(["temporada", "modo"], as_index=False).agg(
        turnos=("agua_por_eboe_m3h", "size"), calculada=("agua_por_eboe_m3h", "median"),
        laboratorio=("agua_lab_m3h", "median"))
    with st.expander("Mediana por temporada y modo"):
        registro.tabla(con_coma(por_temporada.rename(columns={
            "temporada": "Temporada", "modo": "Primarias", "turnos": "Turnos", "calculada": "Calculada (m³/h)",
            "laboratorio": "Laboratorio (m³/h)"}), 2, **{"Turnos": 0, "Temporada": 0}), "Retorno de FP a eBOE")

    st.markdown("#### Aceite en emulsión: centrífugas vs laboratorio del BOE")
    st.caption("Mediana por día de producción. En el tooltip, el programa medio del día (de las Entradas del "
               "laboratorio): el retorno de FP está confundido con el programa.")
    relacion = relacion_boe(muestras_f, emulsion_f, datos)
    relacion = relacion[relacion["temporada"] == temporada]
    if relacion.empty:
        st.info("Sin días con datos de las dos fuentes.")
        return
    registro.grafico(lineas_en_el_tiempo(
        relacion, "fecha_produccion", {"aceite_emulsion_cen": ("Centrífugas (emulsión de la primaria)", "n_cen"),
                                       "aceite_emulsion_boe": ("Laboratorio del BOE (emulsión)", "n_boe")},
        "Aceite en emulsión (%)", "%", 2, extra=("programa_medio", "Programa medio")),
        f"Aceite en emulsión {temporada}")
    registro.grafico(lineas_en_el_tiempo(
        relacion.rename(columns={"aceite_salida": "Salida"}), "fecha_produccion",
        {"Salida": ("Aceite residual a la salida del eBOE", "n_salida")}, "Aceite en fruta (kg/MT)", "kg/MT", 2,
        extra=("programa_medio", "Programa medio")), f"Aceite residual en fruta {temporada}")


def pestana_fases(registro: Descargas) -> None:
    incluir = st.checkbox("Incluir copiadas", value=False, key="centrifugas_copiadas",
                          help="Muestras de la fase pesada o la descarga copiadas de la otra primaria (no se midieron). "
                               "Apagado: no entran en la comparación entre equipos.")
    st.caption("Aceite por fase, por temporada y equipo. Banda verde: rango de la planta (hoja Tablas) o, si no hay, "
               "el de GEA. Los outliers se marcan (círculos abiertos), no se borran. La fase liviana se anota directo "
               "(no se titula).")
    valores = valores_por_variable(muestras_f, balance_f, incluir_copiadas=incluir)
    for variable in FASES:
        boxplot_variable(registro, valores, variable)


def pestana_operacion(registro: Descargas) -> None:
    st.caption("Variables de operación por temporada y equipo, con la banda de referencia en gris y el % de muestras "
               "fuera de rango en la tabla.")
    valores = valores_por_variable(muestras_f, balance_f)
    no_medible = valores[(valores["variable"] == "cp_liviana") & valores["no_medible"]]
    for variable in OPERACION:
        boxplot_variable(registro, valores, variable, color_banda=GRIS)
        if variable == "cp_pesada":
            rango = rangos.get(("cp_pesada", PRIMARIA))
            banda = f" La banda ({texto_rango(rango, 1)}, {rango['fuente']}) es solo referencia." if rango else ""
            st.info(f"CP fase pesada: práctica de planta; alerta por carta de control (Fase I = 2025).{banda}")
        if variable == "cp_liviana" and not no_medible.empty:
            por_equipo = no_medible.groupby(["equipo", "temporada"]).size()
            st.caption("CP liviana \"no medible\" (valor cercano a 0 o manómetro a revisar), contada aparte y sin "
                       "graficar: " + ", ".join(f"{e} {t}: {numero(n, 0)}" for (e, t), n in por_equipo.items()) + ".")


def pestana_perdidas(registro: Descargas) -> None:
    st.caption("Pérdida en descarga = aceite de la descarga × densidad × volumen por descarga del equipo × 3600 / t. "
               "Los kg son ESTIMADOS (kg/h × tiempo hasta el muestreo siguiente del mismo equipo, máximo 4 h). Sin "
               "volumen por descarga, la pérdida queda a definir. Sin las descargas copiadas.")
    mensual = perdida_descarga_mensual(balance_f)
    if mensual.empty:
        st.info("Sin pérdidas en descarga calculadas con estos filtros.")
    else:
        vista = mensual.assign(mes=mensual["mes"].dt.strftime("%m/%Y"))
        izquierda, derecha = st.columns(2)
        registro.grafico(columnas_agrupadas(vista, "mes", "equipo", "mediana_kgh", "Pérdida en descarga (kg/h)",
                                            "kg/h", 3, titulo_x="Mes", etiquetas=False, colores=COLORES_EQUIPOS),
                         "Pérdida en descarga por mes (kg/h)", donde=izquierda)
        registro.grafico(columnas_agrupadas(vista, "mes", "equipo", "kg", "Pérdida en descarga estimada (kg)", "kg", 0,
                                            titulo_x="Mes", etiquetas=False, colores=COLORES_EQUIPOS),
                         "Pérdida en descarga por mes (kg)", donde=derecha)
        with st.expander("Tabla por mes y equipo"):
            registro.tabla(con_coma(vista.rename(columns={"mes": "Mes", "equipo": "Equipo", "n": "N",
                                                          "mediana_kgh": "Mediana (kg/h)", "kg": "kg estimados",
                                                          "horas": "Horas cubiertas"}), 1,
                                    **{"N": 0, "Mediana (kg/h)": 3, "kg estimados": 1}), "Pérdida en descarga por mes")
    sin_volumen = balance_f[balance_f["estado_descarga"] == A_DEFINIR]["equipo"].unique()
    if len(sin_volumen):
        st.caption(f"Pérdida en descarga a definir (sin volumen por descarga): {', '.join(sorted(sin_volumen))}.")

    st.markdown("#### Aceite en la fase pesada, según su destino")
    st.caption("Solo es pérdida del proceso si la fase pesada va al canal. La de las primarias recircula al BOE. La "
               "pulidora no tiene fase pesada.")
    destino = perdida_pesada_por_destino(balance_f)
    destino = destino[destino["estado"] != NO_APLICA]
    if destino.empty:
        st.info("Sin datos de fase pesada con estos filtros.")
        return
    vista = destino.assign(estado=destino["estado"].map(lambda e: ESTADOS_PESADA.get(e, e)))
    con_valor = vista[vista["n"] > 0]
    if not con_valor.empty:
        registro.grafico(columnas_agrupadas(con_valor.assign(x=con_valor["equipo"] + " " + con_valor["temporada"]
                                                             .astype(str)), "x", "estado", "mediana_kgh",
                                            "Aceite en fase pesada (kg/h, mediana)", "kg/h", 2),
                         "Aceite en fase pesada por destino")
    registro.tabla(con_coma(pd.DataFrame({
        "Temporada": vista["temporada"].astype(str), "Equipo": vista["equipo"], "Destino": vista["destino"],
        "Estado": vista["estado"], "Muestreos": vista["n_muestreos"], "N con kg/h": vista["n"],
        "Mediana (kg/h)": vista["mediana_kgh"]}), 2, **{"Muestreos": 0, "N con kg/h": 0}), "Fase pesada por destino")
    if (vista["estado"] == SIN_DATOS).any():
        st.caption(f"\"{SIN_DATOS}\": la fase pesada de la secundaria casi nunca se muestrea.")


def avisar_n(tabla: pd.DataFrame, columna: str) -> None:
    """Aviso de los grupos con N < 3 (temporada y grupo)."""
    pocos = tabla[tabla["n"] < 3]
    if not pocos.empty:
        st.warning("N < 3 (poco representativo) en " + ", ".join(
            f"{t} {g}" for t, g in zip(pocos["temporada"], pocos[columna])) + ".")


def bloque_variable(registro: Descargas, datos: pd.DataFrame, variable: str, por: float, unidad_por: str) -> str:
    """Eficiencia por quintil de la variable (IC 95 % y N por temporada) y la pendiente ajustada. Devuelve la
    tendencia (frase_pendiente)."""
    nombre, unidad, _ = VARIABLES_CONDICION[variable]
    cuantiles = por_cuantiles(datos, variable)
    if cuantiles.empty:
        st.info(f"Sin datos de {nombre.lower()} con estos filtros.")
        return "sin datos"
    registro.grafico(intervalos_agrupados(cuantiles.sort_values(["orden", "temporada"]), "Eficiencia de la primaria (%)",
                                          "%", 1, f"{nombre} ({unidad}), quintiles"), f"Eficiencia por {nombre.lower()}")
    avisar_n(cuantiles, "grupo")
    tabla = pendiente(datos, variable, por)
    if not tabla.empty:
        registro.tabla(pd.DataFrame({
            "Temporada": tabla["temporada"].astype(str), "N": tabla["n"],
            f"Cambio de eficiencia cada {unidad_por} (puntos)": tabla["pendiente"].map(con_signo),
            "IC 95 %": [f"{con_signo(a)} a {con_signo(b)}" for a, b in zip(tabla["ic_inf"], tabla["ic_sup"])],
            "p": tabla["p"].map(texto_p)}), f"Pendiente: {nombre.lower()}")
    return frase_pendiente(tabla)


TENDENCIAS = {"sube": "sube", "baja": "baja", "no cambia": "no cambia de forma significativa",
              "en parte": "cambia solo en algunas temporadas", "sin datos": "sin datos"}


def pestana_condiciones(registro: Descargas) -> None:
    st.caption("Eficiencia de la centrífuga primaria (balance de cada muestreo, sin copiados ni fuera de rango físico) "
               "según las condiciones de operación, por temporada, con IC 95 % y N. Las diferencias y pendientes se "
               "ajustan por equipo, mes, turno, químico y aceite en la emulsión (cuartiles): así un químico que lee "
               "distinto o un mes con otra fruta no se confunden con el caudal.")
    _, personas = operadores_planta(planta_id)
    quimicos = personas[["codigo", "fecha_hora", "analista"]] if not personas.empty else None
    datos = datos_primarias(balance_f, cp_liviana_por_muestreo(muestras_f), quimicos) if not balance_f.empty else []
    if len(datos) == 0:
        st.info("No hay balances de las primarias con estos filtros.")
        return
    try:
        nominal = caudal_nominal_primaria(rangos)
    except ValueError as error:
        st.warning(str(error))
        return
    rango = rangos.get(("caudal_emulsion", PRIMARIA)) or {}

    st.markdown("#### Caudal de alimentación")
    rangos_caudal = por_rango_caudal(datos).sort_values(["desde", "temporada"])
    registro.grafico(intervalos_agrupados(rangos_caudal.rename(columns={"rango": "grupo"}),
                                          "Eficiencia de la primaria (%)", "%", 1, "Caudal de emulsión (L/h)"),
                     "Eficiencia por caudal de alimentación")
    avisar_n(rangos_caudal, "rango")
    if rango.get("minimo") is not None and (datos["caudal_emulsion"] < rango["minimo"]).any():
        bajos = int((datos["caudal_emulsion"] < rango["minimo"]).sum())
        st.caption(f"Marcados, no excluidos: {numero(bajos, 0)} muestreos con caudal menor que "
                   f"{numero(rango['minimo'], 0)} L/h (mínimo del rango de referencia; posible error de carga).")
    tabla = hasta_nominal(datos, nominal)
    registro.tabla(pd.DataFrame({
        "Temporada": tabla["temporada"].astype(str),
        f"Hasta {numero(nominal, 0)} L/h": [f"{numero(m, 1)} % (N {numero(n, 0)})" if n else "—"
                                            for m, n in zip(tabla["media_hasta"], tabla["n_hasta"])],
        f"Más de {numero(nominal, 0)} L/h": [f"{numero(m, 1)} % (N {numero(n, 0)})" if n else "—"
                                             for m, n in zip(tabla["media_sobre"], tabla["n_sobre"])],
        "Diferencia simple (puntos)": (tabla["media_sobre"] - tabla["media_hasta"]).map(con_signo),
        "Diferencia ajustada (puntos)": [f"{con_signo(d)} ({texto_p(p)})" if pd.notna(d) else "pocos datos de un lado"
                                         for d, p in zip(tabla["dif"], tabla["p"])]}),
        "Hasta el caudal nominal y por encima")
    st.markdown(f"**«Hasta {numero(nominal, 0)} L/h, buena performance»: {frase_nominal(tabla, nominal)}**")
    st.caption(f"{numero(nominal, 0)} L/h = máximo del rango «Caudal de emulsión» de la primaria en RangoReferencia "
               f"({rango.get('fuente') or 'sin fuente'}). Diferencia = más del nominal − hasta el nominal. La simple "
               "compara promedios; la ajustada, a igual equipo, mes, turno, químico y aceite en la emulsión (cuando el "
               "caudal cambia con el mes, la simple mezcla las dos cosas).")

    st.markdown("#### Caudal de fase liviana")
    tendencia = bloque_variable(registro, datos, "caudal_liviana", 100, "100 L/h")
    respuesta = {"sube": "se repite", "baja": "no se repite (baja)", "no cambia": "no se repite",
                 "en parte": "se repite solo en algunas temporadas"}.get(tendencia, tendencia)
    st.markdown(f"**«Más caudal de fase liviana → más eficiencia»: {respuesta}.**")
    st.warning("Ojo: se cumple por construcción. La eficiencia sale del balance con el caudal de fase liviana "
               "(eficiencia = caudal de fase liviana × aceite en la fase liviana / aceite que entra), así que a igual "
               "alimentación más fase liviana es, por definición, más eficiencia. No dice qué mover en la operación: "
               "para eso sirve la CP de la fase liviana (abajo), que se mide aparte.")

    st.markdown("#### Aceite en la emulsión de alimentación")
    tendencia = bloque_variable(registro, datos, "aceite_emulsion", 0.1, "0,1 % de aceite")
    st.caption(f"La eficiencia {TENDENCIAS[tendencia]} con el aceite en la emulsión (en el ajuste no entra el aceite "
               "en la emulsión: es la variable).")

    st.markdown("#### CP de la fase liviana")
    tendencia = bloque_variable(registro, datos, "cp_liviana", 0.1, "0,1 bar")
    st.caption(f"La eficiencia {TENDENCIAS[tendencia]} con la CP de la fase liviana. Es la lectura del manómetro, "
               "independiente del balance (las CP «no medible» no entran).")
    if mostrar_personas_planta(planta_id) and not en_presentacion():
        st.caption("Por operador de centrífugas y turno (ajustado también por químico): Análisis › Sistema › Equipos "
                   "de trabajo.")


def pestana_revisar(registro: Descargas) -> None:
    st.caption("Muestras importadas tal cual pero marcadas para revisar, con su motivo (una muestra puede tener "
               "varios). Respeta las fechas, el turno y el proceso de la barra.")
    revisar = para_revisar(muestras_f)
    if revisar.empty:
        st.info("No hay muestras para revisar con estos filtros.")
        return
    izquierda, derecha = st.columns(2)
    elegidos = izquierda.multiselect("Motivo", motivos(revisar), key="centrifugas_motivos")
    equipos = derecha.multiselect("Equipo", sorted(revisar["equipo"].unique(), key=ORDEN_EQUIPOS.index),
                                  key="centrifugas_equipos_revisar")
    vista = con_motivo(revisar, elegidos)
    if equipos:
        vista = vista[vista["equipo"].isin(equipos)]
    st.markdown(f"**{numero(len(vista), 0)} muestras** de {numero(len(revisar), 0)} marcadas.")
    registro.tabla(pd.DataFrame({
        "Fecha y hora": vista["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"),
        "Día de producción": vista["fecha_produccion"].dt.strftime("%d/%m/%Y"), "Turno": vista["turno"],
        "Proceso": vista["proceso"], "Equipo": vista["equipo"], "Fase": vista["fase"], "Motivo": vista["motivo"],
        "Fecha original": pd.to_datetime(vista["fecha_original"]).dt.strftime("%d/%m/%Y").fillna(""),
        "Origen": vista["origen"]}), "Muestras para revisar")


# ---------------------------------------------------------------- página

st.set_page_config(page_title="Centrífugas", layout="wide")
cartel_modo_prueba()
st.title("Centrífugas de aceite")

datos = barra_filtros()
filtros = filtros_elegidos()
if filtros is None:             # la planta no tiene análisis: la barra ya lo avisó
    st.stop()
planta_id = planta_elegida()
muestras, balance = centrifugas_planta(planta_id)
if muestras.empty:
    st.info("No hay muestras de centrífugas de esta planta. Cargalas con: python -m core.importar_centrifugas")
    st.stop()
muestras_f, balance_f = filtrar_centrifugas(muestras, filtros), filtrar_centrifugas(balance, filtros)
rangos = rangos_centrifugas_planta(planta_id)
umbral = umbral_paralelo_planta(planta_id)
turnos = primarias_por_turno(muestras_f, umbral) if umbral else primarias_por_turno(muestras_f.iloc[0:0], 0)
produccion_f = filtrar_produccion(produccion_planta(planta_id), filtros)
emulsion_f = filtrar(tabla_emulsion_planta(planta_id), filtros)
st.caption(f"{numero(len(muestras_f), 0)} muestras de {numero(len(muestras), 0)} con los filtros (fechas de "
           "producción, turno y proceso; el eBOE y el programa no aplican a las centrífugas).")
if len(muestras_f) < 3:
    st.warning("Quedan menos de 3 muestras: los gráficos no son representativos.")

PESTANAS = {
    "Resumen": pestana_resumen,
    "Retorno de FP y BOE": pestana_agua,
    "Fases": pestana_fases,
    "Operación vs referencias": pestana_operacion,
    "Pérdidas": pestana_perdidas,
    "Condiciones de eficiencia": pestana_condiciones,
    "Para revisar": pestana_revisar,
}
for (nombre, dibujar), pestana in zip(PESTANAS.items(), st.tabs(list(PESTANAS), key="pestana_centrifugas",
                                                                    on_change="rerun", bind="query-params")):
    if pestana.open:
        with pestana:
            arriba = st.container()
            registro = Descargas(nombre)
            dibujar(registro)
            registro.boton_todo(arriba)
