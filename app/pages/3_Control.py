"""Página Gráficos de control: cartas de control y capacidad de proceso, en dos pestañas.

Usa la barra de filtros global.
- Cartas de control: X̄-R / X̄-S por turno o I-MR por muestra, con reglas de Nelson. Con "Fase I / fase II" los
  límites se calculan con la temporada base y se aplican a la nueva: se usan las dos temporadas completas (se
  ignoran las fechas y el proceso de la barra; se respetan turno y programa, como en la pestaña Temporadas de
  Análisis). Con "período filtrado", los límites salen de los datos filtrados.
- Capacidad: Cp/Cpk, Pp/Ppk, PPM, normalidad, índices por percentiles y estabilidad, con los límites de
  RangoReferencia.
Solo muestra: los subgrupos y la carta están en core/control.py, la estadística en core/spc.py y
core/capacidad.py, y los gráficos en app/graficos.py.
"""

import pandas as pd
import streamlit as st

from core.analisis import filtrar, filtros_sin_periodo
from core.capacidad import NO_APLICA, VARIABLES_CAPACIDAD, comparar_equipos, datos_capacidad, evaluar_capacidad
from core.control import POR_MUESTRA, POR_TURNO, VARIABLES, carta_de_control, frase_control, muestras_de
from core.formato import numero
from core.laboratorio import texto_p_ad
from core.operacion import especie
from core.spc import DESCRIPCION_REGLAS, REGLAS, resumen_reglas
from descargas import Descargas
from filtros import (barra_filtros, filtros_elegidos, limites_capacidad_planta, planta_elegida,
                     tabla_emulsion_planta, tabla_planta)
from graficos import carta_control, histograma_capacidad

TIPOS = {POR_TURNO: "X̄-R / X̄-S por turno", POR_MUESTRA: "I-MR por muestra"}
PERIODO_FILTRADO = "periodo"
SUBGRUPOS_RECOMENDADOS = 20     # Montgomery: la fase I necesita al menos 20–25 subgrupos
DECIMALES_CAPACIDAD = {"eficiencia": 1, "rendimiento": 2, "aceite_emulsion": 2, "brix": 1, "ph": 2}
NOTA_GAGE = ("La eficiencia sale de dos titulaciones Scott; parte de la variación es error de medición (pendiente: "
             "estudio Gage R&R).")
LADOS = {"ambos": "LIE y LSE", "inferior": "solo LIE (lado inferior)", "superior": "solo LSE (lado superior)"}


def elegir_opciones(fruta_planta: pd.DataFrame, filtros) -> dict:
    """Selectores de la página: variable, eBOE, tipo de carta, límites y reglas de Nelson."""
    variable_col, eboe_col, tipo_col, limites_col = st.columns([1.3, 0.7, 1, 1.6])
    variable = variable_col.selectbox(
        "Variable", list(VARIABLES), key="control_variable",
        format_func=lambda v: f"{VARIABLES[v]['nombre']} ({VARIABLES[v]['unidad']})")
    eboes = sorted(fruta_planta["eboe"].unique())
    if filtros.eboes:
        eboes = [e for e in eboes if e in filtros.eboes]
    eboe = eboe_col.selectbox("eBOE", eboes, key="control_eboe", format_func=lambda e: f"eBOE {e}",
                              index=eboes.index(2) if 2 in eboes else 0)
    por = tipo_col.radio("Tipo de carta", list(TIPOS), format_func=TIPOS.get, key="control_tipo",
                         help="Por turno: subgrupo = las muestras de ese eBOE en ese turno de producción. "
                              "X̄-R si todos los turnos tienen el mismo número de muestras; si no, X̄-S con "
                              "límites por subgrupo (como Minitab).")
    temporadas = sorted(int(t) for t in fruta_planta["temporada"].unique())
    opciones = {PERIODO_FILTRADO: "Calcular con el período filtrado"}
    if len(temporadas) >= 2:
        fases = (temporadas[-2], temporadas[-1])
        opciones = {fases: f"Fase I = temporada {fases[0]}, fase II = {fases[1]}", **opciones}
    limites = limites_col.radio("Límites", list(opciones), format_func=opciones.get, key="control_limites")

    st.caption("Reglas de Nelson")
    reglas = [regla for regla, columna in zip(REGLAS, st.columns(len(REGLAS)))
              if columna.checkbox(f"Regla {regla}", value=True, key=f"control_regla_{regla}",
                                  help=DESCRIPCION_REGLAS[regla])]
    return {"variable": variable, "eboe": eboe, "por": por, "fases": None if limites == PERIODO_FILTRADO else limites,
            "reglas": reglas}


def datos_de_la_carta(datos: pd.DataFrame, filtros, planta_id: int, fases) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Análisis de fruta y de emulsión que entran en la carta (con fases: temporadas completas)."""
    if fases is None:
        return datos, filtrar(tabla_emulsion_planta(planta_id), filtros)
    sin_periodo = filtros_sin_periodo(filtros)
    return (filtrar(tabla_planta(planta_id), sin_periodo),
            filtrar(tabla_emulsion_planta(planta_id), sin_periodo))


def tabla_reglas(resumen: pd.DataFrame, dispersion: str, fases, por: str) -> pd.DataFrame:
    """Cantidad y % de puntos fuera de control por regla (y por fase si hay fases)."""
    nombres = {"dispersion": f"{dispersion} fuera de límites", "alguna": "Alguna regla"}
    descripciones = {**DESCRIPCION_REGLAS, "dispersion": f"Panel de abajo: {dispersion} fuera de LCS / LCI",
                     "alguna": "Al menos una de las anteriores"}
    puntos = "Turnos" if por == POR_TURNO else "Muestras"
    tabla = pd.DataFrame({
        "Regla": [nombres.get(r, f"Regla {r}") for r in resumen["regla"]],
        "Descripción": [descripciones[r] for r in resumen["regla"]],
    })
    etiquetas = {"I": f"Fase I ({fases[0]})", "II": f"Fase II ({fases[1]})"} if fases else {"I": "Período"}
    for fase, etiqueta in etiquetas.items():
        tabla[f"{etiqueta}: {puntos.lower()} fuera"] = resumen[f"n_{fase}"]
        tabla[f"{etiqueta}: %"] = resumen[f"pct_{fase}"].astype(float).round(1)
    return tabla


def tabla_datos(tabla: pd.DataFrame, tipo: str, dispersion: str) -> pd.DataFrame:
    """Datos de la carta para ver o bajar a Excel: un renglón por punto."""
    simbolo = "I" if tipo == "I-MR" else "X̄"
    datos = tabla.rename(columns={
        "punto": "Punto", "fecha_produccion": "Fecha de producción", "turno": "Turno", "temporada": "Temporada",
        "proceso": "Proceso", "programa": "Programa", "mtph": "MTPH", "n": "n", "valor": simbolo, "lc": "LC",
        "lcs": "LCS", "lci": "LCI", "disp": dispersion, "disp_lc": f"LC {dispersion}", "disp_lcs": f"LCS {dispersion}",
        "disp_lci": f"LCI {dispersion}", "fase": "Fase"})
    datos["Reglas violadas"] = [", ".join(str(r) for r in reglas) + (f"{', ' if reglas else ''}{dispersion}" if fuera else "")
                                for reglas, fuera in zip(tabla["reglas"], tabla["fuera_disp"])]
    columnas = ["Punto", "Fecha de producción", "Turno", "Temporada", "Proceso", "Programa", "MTPH", "n", simbolo,
                "LC", "LCS", "LCI", dispersion, f"LC {dispersion}", f"LCS {dispersion}", f"LCI {dispersion}", "Fase",
                "Reglas violadas"]
    if tipo == "I-MR":
        datos.insert(2, "Hora", tabla["fecha_hora"].dt.strftime("%H:%M"))
        columnas.insert(2, "Hora")
        columnas.remove("n")
    datos["Fecha de producción"] = datos["Fecha de producción"].dt.date
    return datos[columnas]


def explicar_carta(carta: dict, opciones: dict) -> None:
    """Qué carta se usó, con qué subgrupos y con cuántos puntos se calcularon los límites."""
    tabla, fases = carta["tabla"], opciones["fases"]
    base = tabla[tabla["fase"] == "I"]
    variable = VARIABLES[opciones["variable"]]
    sigma = base["sigma"].iloc[0] * (base["n"].iloc[0] ** 0.5)       # σ del proceso (no de la media)
    origen = f"la temporada {fases[0]}" if fases else "el período filtrado"
    if carta["tipo"] == "X̄-S":
        tamanos = tabla["n"]
        detalle = (f"Carta X̄-S: los turnos tienen entre {tamanos.min()} y {tamanos.max()} muestras, así que los "
                   "límites cambian con el tamaño de cada turno (más anchos si hay menos muestras), como en Minitab.")
    elif carta["tipo"] == "X̄-R":
        detalle = f"Carta X̄-R: todos los turnos tienen {tabla['n'].iloc[0]} muestras."
    else:
        detalle = "Carta I-MR: un punto por muestra; σ sale del rango móvil promedio (MR̄ / d2)."
    st.caption(f"{detalle} Límites calculados con {origen}: {numero(len(base), 0)} puntos, línea central "
               f"{numero(base['lc'].iloc[0], variable['decimales'])} {variable['unidad']}, "
               f"σ {numero(sigma, variable['decimales'])} {variable['unidad']}.")
    if len(tabla) < 3:
        st.warning("Hay menos de 3 puntos: la carta no es representativa.")
    elif carta["tipo"] != "I-MR" and len(base) < SUBGRUPOS_RECOMENDADOS:
        st.warning(f"La fase I tiene {len(base)} turnos: se recomiendan al menos {SUBGRUPOS_RECOMENDADOS} "
                   "para que los límites sean confiables.")


# ---------------------------------------------------------------- capacidad

def indice(valor, decimales: int = 2) -> str:
    """Un índice para la tabla: número con coma, "no aplica" o "—"."""
    if valor == NO_APLICA:
        return NO_APLICA
    return "—" if valor is None or pd.isna(valor) else numero(valor, decimales)


def tabla_indices(r: dict, unidad: str, decimales: int) -> pd.DataFrame:
    """Índices con sigma dentro (Cp…), sigma total (Pp…) y, si los datos no son normales, por percentiles."""
    c, p = r["capacidad"], r["percentiles"]
    filas = [("Cp / Pp", c["cp"], c["pp"], p and p["pp"]), ("Cpk / Ppk", c["cpk"], c["ppk"], p and p["ppk"]),
             ("Cpl / Ppl", c["cpl"], c["ppl"], p and p["ppl"]), ("Cpu / Ppu", c["cpu"], c["ppu"], p and p["ppu"])]
    tabla = pd.DataFrame({"Índice": [f[0] for f in filas], "Dentro (Cp…)": [indice(f[1]) for f in filas],
                          "Total (Pp…)": [indice(f[2]) for f in filas]})
    if p:
        tabla["Percentiles (no normal)"] = [indice(f[3]) for f in filas]
    extra = [("Sigma", indice(c["sigma_dentro"], decimales + 1), indice(c["sigma_total"], decimales + 1), "—"),
             ("PPM esperado", indice(c["ppm_esperado_dentro"]["total"], 0), indice(c["ppm_esperado_total"]["total"], 0),
              "—")]
    for nombre, dentro, total, perc in extra:
        fila = {"Índice": f"{nombre} ({unidad})" if nombre == "Sigma" else nombre, "Dentro (Cp…)": dentro,
                "Total (Pp…)": total}
        if p:
            fila["Percentiles (no normal)"] = perc
        tabla.loc[len(tabla)] = fila
    return tabla


def tabla_datos_capacidad(r: dict, unidad: str, decimales: int) -> pd.DataFrame:
    """Media, PPM observados (abajo, arriba, total) y la prueba de normalidad."""
    c, prueba, p = r["capacidad"], r["normalidad"], r["percentiles"]
    filas = [("N", numero(c["n"], 0)), (f"Media ({unidad})", numero(c["media"], decimales)),
             ("PPM observado < LIE", indice(c["ppm_observado"]["lie"], 0)),
             ("PPM observado > LSE", indice(c["ppm_observado"]["lse"], 0)),
             ("PPM observado total", indice(c["ppm_observado"]["total"], 0)),
             ("Anderson-Darling (AD)", indice(prueba["ad"], 2)), ("Valor p", texto_p_ad(prueba["p"]).replace("p ", "", 1)),
             ("¿Normal? (p ≥ 0,05)", "sí" if prueba["normal"] else "no")]
    if p:
        filas += [(f"Percentil 0,135 ({unidad})", indice(p["p_bajo"], decimales)),
                  (f"Mediana ({unidad})", indice(p["mediana"], decimales)),
                  (f"Percentil 99,865 ({unidad})", indice(p["p_alto"], decimales))]
    return pd.DataFrame(filas, columns=["Dato", "Valor"])


def tabla_equipos(tabla: pd.DataFrame, decimales: int) -> pd.DataFrame:
    """Comparación por eBOE (core.capacidad.comparar_equipos), del peor al mejor."""
    return pd.DataFrame({
        "eBOE": tabla["eboe"].map(lambda e: f"eBOE {e}"), "N": tabla["n"],
        "Media": tabla["media"].map(lambda v: indice(v, decimales)), "Cpk": tabla["cpk"].map(indice),
        "Ppk": tabla["ppk"].map(indice), "Ppl": tabla["ppl"].map(indice), "Ppu": tabla["ppu"].map(indice),
        "Ppk percentiles": tabla["ppk_percentiles"].map(indice),
        "% fuera de especificación": tabla["pct_fuera"].map(lambda v: indice(v, 1)),
        "Normal": tabla["normal"].map({True: "sí", False: "no"}), "p (AD)": tabla["p_ad"].map(
            lambda v: texto_p_ad(v).replace("p ", "", 1)),
        "Estabilidad": tabla["estabilidad"], "% fuera de control": tabla["pct_fuera_control"].map(
            lambda v: indice(v, 1))})


def pestana_capacidad(registro: Descargas) -> None:
    st.caption("Capacidad del proceso contra la especificación de RangoReferencia, con los datos filtrados en la "
               "barra, en orden de tiempo. Sigma dentro: rango móvil (MR̄ / d2); sigma total: desvío de todos los "
               "datos. Si los datos no son normales (Anderson-Darling p < 0,05) se agregan los índices por "
               "percentiles (ISO 22514). Estabilidad: carta I-MR y regla 1 de Nelson.")
    izquierda, derecha = st.columns(2)
    variable = izquierda.selectbox("Variable", list(VARIABLES_CAPACIDAD), key="capacidad_variable",
                                   format_func=lambda v: f"{VARIABLES_CAPACIDAD[v][0]} ({VARIABLES_CAPACIDAD[v][2]})")
    nombre, _, unidad = VARIABLES_CAPACIDAD[variable]
    decimales = DECIMALES_CAPACIDAD[variable]
    todos = datos_capacidad(datos, filtrar(tabla_emulsion_planta(planta_id), filtros), variable)
    eboes = sorted(int(e) for e in todos["eboe"].dropna().unique())
    equipo = derecha.selectbox("eBOE", [None] + eboes, key="capacidad_eboe",
                               format_func=lambda e: "Todos juntos" if e is None else f"eBOE {e}")
    frutas = datos["fruta"].map(especie).dropna()
    fruta = frutas.mode().iloc[0] if not frutas.empty else None
    limites = limites_capacidad_planta(planta_id, variable, fruta)
    if limites is None:
        st.warning(f'La planta no tiene el rango "{nombre}" en RangoReferencia (hoja de los maestros).')
        return
    minimo, maximo = limites["banda"]
    st.caption(f"Especificación: {LADOS[limites['lado']]} — LIE {indice(limites['lie'], decimales)}, LSE "
               f"{indice(limites['lse'], decimales)} {unidad}. Banda objetivo {indice(minimo, decimales)}–"
               f"{indice(maximo, decimales)} {unidad}. Fuente: {limites['fuente'] or '—'}.")
    seleccion = todos if equipo is None else todos[todos["eboe"] == equipo]
    if equipo is None and len(eboes) > 1:
        st.caption("Todos juntos: los eBOE intercalados en el tiempo; el sigma dentro mezcla equipos. Para comparar, "
                   "mirá la tabla por eBOE.")
    if len(seleccion) < 3:
        st.warning("Hay menos de 3 datos: no se calcula la capacidad.")
        return
    r = evaluar_capacidad(seleccion["valor"], limites["lie"], limites["lse"])
    estable = r["estabilidad"]

    uno, dos, tres = st.columns(3)
    uno.metric("Fuera de especificación (observado)", f"{indice(r['pct_fuera'], 1)} %",
               help="Datos fuera de la especificación sobre el total, contados (no supone ninguna distribución).")
    dos.metric("Estabilidad (carta I-MR, regla 1)", estable["estado"] or "—",
               f"{indice(estable['pct_fuera'], 1)} % de puntos fuera de control", delta_color="off",
               help="Inestable: más puntos fuera de ±3σ de los que explica el azar (0,27 % en una normal estable).")
    tres.metric("N", numero(r["capacidad"]["n"], 0))
    for aviso in r["avisos"]:
        st.warning(aviso)
    if variable in ("eficiencia", "rendimiento"):
        st.info(NOTA_GAGE)

    titulo = f"{nombre} {'todos los eBOE' if equipo is None else f'eBOE {equipo}'}"
    registro.grafico(histograma_capacidad(seleccion["valor"], r["capacidad"], limites, f"{nombre} ({unidad})", unidad,
                                          decimales), f"Capacidad {titulo}")
    izquierda, derecha = st.columns([3, 2])
    with izquierda:
        st.markdown("**Índices**")
        registro.tabla(tabla_indices(r, unidad, decimales), f"Índices {titulo}", donde=izquierda)
    with derecha:
        st.markdown("**Datos y normalidad**")
        registro.tabla(tabla_datos_capacidad(r, unidad, decimales), f"Datos {titulo}", donde=derecha)
    st.caption("Cp/Cpk usan la variación de corto plazo (sigma dentro); Pp/Ppk, la de todo el período (sigma total). "
               "Capaz: Cpk y Ppk ≥ 1,33. Con un solo lado, Cp y Pp no aplican. Los PPM esperados suponen una normal: "
               "si los datos no lo son, mirá el % observado y los índices por percentiles.")

    if len(eboes) > 1:
        st.subheader("Comparación por eBOE")
        st.caption("Una fila por eBOE, del peor al mejor según Ppk (con un solo lado, es el índice de ese lado).")
        registro.tabla(tabla_equipos(comparar_equipos(todos, limites["lie"], limites["lse"]), decimales),
                       f"Capacidad por eBOE {nombre}")


# ---------------------------------------------------------------- cartas de control

def pestana_cartas(registro: Descargas) -> None:
    opciones = elegir_opciones(tabla_planta(planta_id), filtros)
    fases = opciones["fases"]
    if fases:
        st.caption(f"Con fases se usan las temporadas {fases[0]} y {fases[1]} completas: se ignoran las fechas y el "
                   "proceso de la barra; se respetan turno y programa.")
    if not opciones["reglas"]:
        st.info("No hay ninguna regla de Nelson elegida: solo se marcan los puntos de abajo fuera de límites.")

    fruta, emulsion = datos_de_la_carta(datos, filtros, planta_id, fases)
    muestras = muestras_de(fruta, emulsion, opciones["variable"])
    muestras = muestras[muestras["eboe"] == opciones["eboe"]]
    if muestras.empty:
        st.info("No hay muestras de este eBOE con estos filtros.")
        return
    try:
        carta = carta_de_control(muestras, opciones["por"], fases, opciones["reglas"])
    except ValueError as error:
        st.warning(str(error))
        return

    variable = VARIABLES[opciones["variable"]]
    tabla = carta["tabla"]
    resumen = resumen_reglas(tabla, opciones["reglas"])
    st.markdown(f"**{frase_control(resumen, opciones['eboe'], opciones['por'], fases)}**")
    explicar_carta(carta, opciones)

    nombre = f"Carta {carta['tipo']} {variable['nombre']} eBOE {opciones['eboe']}"
    registro.grafico(carta_control(tabla, carta["tipo"], carta["dispersion"], variable["nombre"], variable["unidad"],
                                   variable["decimales"], fases), nombre)

    st.subheader("Puntos fuera de control por regla")
    st.caption("% sobre los puntos de cada fase. Un punto puede violar varias reglas a la vez. Los puntos fuera de "
               "control se marcan, no se borran: también entran en el cálculo de los límites.")
    registro.tabla(tabla_reglas(resumen, carta["dispersion"], fases, opciones["por"])
                   .style.format(precision=1, decimal=",", na_rep="—"), "Puntos fuera de control por regla")
    with st.expander("Datos de la carta"):
        registro.tabla(tabla_datos(tabla, carta["tipo"], carta["dispersion"])
                       .style.format(precision=variable["decimales"], decimal=",", na_rep="—")
                       .format(precision=1, decimal=",", subset=["MTPH"], na_rep="—"), "Datos de la carta")


# ---------------------------------------------------------------- página

st.set_page_config(page_title="Gráficos de control", layout="wide")
st.title("Gráficos de control")

datos = barra_filtros()
filtros = filtros_elegidos()
if filtros is None:      # la planta no tiene análisis: la barra ya lo avisó
    st.stop()
planta_id = planta_elegida()

PESTANAS = {"Cartas de control": pestana_cartas, "Capacidad": pestana_capacidad}
for (nombre_pestana, dibujar), pestana in zip(PESTANAS.items(), st.tabs(list(PESTANAS), key="pestana_control",
                                                                            on_change="rerun", bind="query-params")):
    if pestana.open:
        with pestana:
            arriba = st.container()             # "Descargar todo" va arriba, pero se arma al final
            registro = Descargas(nombre_pestana)
            dibujar(registro)
            registro.boton_todo(arriba)
