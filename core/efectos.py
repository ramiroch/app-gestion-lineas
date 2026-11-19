"""Efectos principales (estilo Minitab, medias ajustadas), interacción con MTPH y umbral de MTPH (Sistema).

Datos: los pares entrada/salida con las variables de fase pesada (core.fase_pesada.tabla_fase_pesada), o, para el
aceite captado por las primarias, un registro por turno (datos_por_turno).

- Factores (todos categóricos): programa (N0–N3, N4–N5, N6–N9), carga (MTPH en rangos ≤ 11, 11–12, 12–13, 13–14,
  > 14, o, con el selector, la fase pesada específica en rangos; nunca las dos juntas: dentro de 2026 son casi la
  misma variable), aceite en fruta de entrada (cuartiles), calibre (quintiles de frutos en 10 kg), mes (calendario:
  Jul, Ago…; no anidado en la temporada), turno, eBOE y temporada (solo si hay más de una). La fase pesada no entra
  por defecto.
- modelo_efectos(): OLS aditivo (statsmodels). ANOVA tipo II (F, p), R², N, medias ajustadas por nivel con IC 95 % y
  el VIF de cada factor (GVIF^(1/gl), Fox y Monette 1992; igual al VIF clásico con un gl). VIF > VIF_MAXIMO: el
  factor está confundido; confundido_con = el otro factor más asociado (V de Cramér).
- Media ajustada (como "Gráfica de efectos principales – medias ajustadas" de Minitab): la predicción del modelo para
  ese nivel con los demás factores repartidos en partes iguales entre sus niveles (no con la mezcla de los datos).
  Es lineal en los coeficientes: media = L·β, varianza = L·Σ·Lᵀ, IC con la t de los gl del residuo. Si L no es
  estimable (por ejemplo, una celda de la interacción sin datos), se marca y no se muestra.
- modelo_interaccion(): el mismo modelo más la interacción carga × temporada (o × régimen de agua: fase pesada
  específica ≥ REGIMEN_UMBRAL m³/t o menos), con el p de la interacción y las medias ajustadas de cada celda.
- quiebre(): regresión segmentada y = a + b₁·x + b₂·máx(0, x − q), probando cada MTPH entera entre 10 y 18 y
  eligiendo la de menor error; pendiente antes (b₁) y después (b₁ + b₂), p del cambio (b₂) y de la pendiente después.
- Aceite captado por las primarias, por turno. Aceite de la fase liviana de las primarias en marcha (kg/h, del
  balance de cada muestreo, sin los fuera de rango físico; los copiados sí entran, ver captado_por_turno; mediana
  por primaria en el turno, sumadas; un turno en paralelo con una sola primaria con balance queda afuera), dividido:
  · captado % = por el aceite removido por los eBOE (Σ de cada eBOE en marcha: MTPH × aceite en fruta de entrada ×
    eficiencia, kg/h) × 100. Del aceite que sacaron los rolos, cuánto llegó a la fase liviana.
  · captado kg/MT = por la MTPH total de los eBOE.
- segmentada_por_temporada(ajustado=True): la regresión segmentada con programa, aceite en fruta, calibre, mes, turno
  y eBOE dentro del mismo modelo (dentro de cada temporada), y el residuo parcial para dibujarla.
"""

import re

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats

from core.analisis_centrifugas import PARALELO, PRIMARIA
from core.centrifugas import aceite_perdido_kgh
from core.factores import (FRUTOS_MAXIMO, ORDENES, asignar_cuartil, asignar_quintil, cortes_cuartiles, cortes_quintiles,
                           etiquetas_cuartiles, etiquetas_quintiles, frutos_en_10kg, grupo_programa)
from core.fase_pesada import CLAVES_TURNO
from core.mensual import MESES
from core.programa import numero_programa
from core.volatilizacion import RANGOS_FP, asignar_rango, media_ic

ALFA = 0.05
VIF_MAXIMO = 5
ETIQUETAS_MTPH = ["≤ 11", "11–12", "12–13", "13–14", "> 14"]
CORTES_MTPH = [-np.inf, 11, 12, 13, 14, np.inf]          # cada rango incluye su límite superior (12 va en "11–12")
REGIMEN_UMBRAL = 0.8                                      # m³/t de fase pesada específica
REGIMENES = [f"FP ≥ {REGIMEN_UMBRAL:.1f} m³/t".replace(".", ","), f"FP < {REGIMEN_UMBRAL:.1f} m³/t".replace(".", ",")]
QUIEBRES = range(10, 19)                                  # MTPH enteras que se prueban como punto de quiebre
N_MINIMO_LADO = 10                                        # datos mínimos de cada lado del quiebre
RESPUESTAS = {
    "eficiencia_pct": {"nombre": "Eficiencia eBOE", "unidad": "%", "decimales": 1, "por_turno": False},
    "aceite_salida": {"nombre": "Aceite en fruta de salida", "unidad": "kg/MT", "decimales": 2, "por_turno": False},
    "captado_pct": {"nombre": "Aceite captado por las primarias", "unidad": "%", "decimales": 1, "por_turno": True},
    "captado_kg_mt": {"nombre": "Aceite captado por las primarias (por tonelada)", "unidad": "kg/MT", "decimales": 2,
                      "por_turno": True},
}
AYUDA_CAPTADO = ("Del aceite que sacaron los rolos, cuánto llegó a la fase liviana. Lo que falta se perdió entre el BOE "
                 "y las centrífugas (aire, esponja, purgas) o es error de medición.")
FACTORES_AJUSTE = ["programa_grupo", "aceite_rango", "calibre", "mes", "turno", "eboe"]   # el umbral, sin la MTPH
NOMBRES = {"programa_grupo": "Programa", "mtph_rango": "MTPH", "fp_rango": "Fase pesada específica",
           "aceite_rango": "Aceite en fruta de entrada", "calibre": "Calibre (frutos en 10 kg)", "mes": "Mes",
           "turno": "Turno", "eboe": "eBOE", "temporada": "Temporada", "regimen": "Régimen de agua"}
CARGAS = {"mtph": "mtph_rango", "fp": "fp_rango"}
NUMERICOS = {"tph": "MTPH", "fp_especifica": "FP específica (m³/t)", "rango_fp_ref": "Rango FP ref. (%)",
             "aceite_fruta": "Aceite en fruta (kg/MT)", "frutos_10kg": "Calibre (frutos/10 kg)"}
EXPLICACION_MEDIA_AJUSTADA = (
    "Una media ajustada es la media que tendría cada nivel si todos los niveles tuvieran la misma mezcla de los demás "
    "factores del modelo. Así se compara, por ejemplo, el turno N con el M a igual programa, MTPH, fruta y eBOE.")


# ---------------------------------------------------------------- preparación

def _texto(valores: pd.Series) -> pd.Series:
    """Categoría como texto (None si falta), para que patsy y las etiquetas coincidan."""
    return valores.map(lambda v: None if pd.isna(v) else str(v))


def rango_mtph(mtph: pd.Series) -> pd.Series:
    """Rango de MTPH (ETIQUETAS_MTPH); cada rango incluye su límite superior."""
    return pd.cut(pd.to_numeric(mtph, errors="coerce"), CORTES_MTPH, labels=ETIQUETAS_MTPH).astype(object) \
        .where(lambda s: s.notna(), None)


def regimen_agua(fp_especifica: pd.Series) -> pd.Series:
    """Régimen de agua: fase pesada específica ≥ REGIMEN_UMBRAL m³/t o menos (vacío si falta)."""
    valores = pd.to_numeric(fp_especifica, errors="coerce")
    return pd.Series([None if pd.isna(v) else REGIMENES[0] if v >= REGIMEN_UMBRAL else REGIMENES[1] for v in valores],
                     index=valores.index, dtype=object)


def _categorias(datos: pd.DataFrame) -> pd.DataFrame:
    """Agrega los factores categóricos a una tabla con tph, fp_especifica, aceite_fruta, frutos_10kg, programa,
    fecha_produccion, turno y temporada (y eboe, si es por par)."""
    datos = datos.copy()
    datos["programa_grupo"] = datos["programa"].map(lambda p: grupo_programa(numero_programa(p))
                                                    if isinstance(p, str) else None)
    datos["mtph_rango"] = rango_mtph(datos["tph"])
    datos["fp_rango"] = asignar_rango(datos["fp_especifica"], "fp_especifica")
    datos["regimen"] = regimen_agua(datos["fp_especifica"])
    # Con cortes repetidos (por ejemplo, un período corto con valores iguales) el factor no varía: queda vacío y no
    # entra al modelo.
    cortes = cortes_cuartiles(datos["aceite_fruta"]) if datos["aceite_fruta"].notna().sum() >= 4 else []
    if cortes and len(set(cortes)) == len(cortes):
        datos["aceite_rango"] = asignar_cuartil(datos["aceite_fruta"], cortes).map(
            etiquetas_cuartiles(cortes, "kg/MT", 1)).astype(object)
    else:
        datos["aceite_rango"] = None
    cortes = cortes_quintiles(datos["frutos_10kg"]) if datos["frutos_10kg"].notna().sum() >= 5 else []
    if cortes and len(set(cortes)) == len(cortes):
        datos["calibre"] = asignar_quintil(datos["frutos_10kg"], cortes).map(etiquetas_quintiles(cortes)).astype(object)
    else:
        datos["calibre"] = None
    datos["mes"] = pd.to_datetime(datos["fecha_produccion"]).dt.month.map(lambda m: MESES[m - 1])
    for columna in ("turno", "temporada") + (("eboe",) if "eboe" in datos else ()):
        datos[columna] = _texto(datos[columna])
    return datos


def preparar_datos(tabla: pd.DataFrame) -> pd.DataFrame:
    """Un registro por par entrada/salida, con las respuestas por par y los factores. tabla:
    core.fase_pesada.tabla_fase_pesada() (pares con fp_especifica y rango_fp_ref). Agrega eficiencia_pct,
    frutos_10kg (vacío si pasa de FRUTOS_MAXIMO: error de carga) y los factores categóricos."""
    frutos = frutos_en_10kg(tabla)
    datos = tabla.assign(eficiencia_pct=tabla["eficiencia_par"] * 100,
                         frutos_10kg=frutos.where(frutos <= FRUTOS_MAXIMO))
    return _categorias(datos)


def captado_por_turno(balance: pd.DataFrame, modos: pd.DataFrame | None = None) -> pd.DataFrame:
    """Aceite captado por las primarias en cada turno (kg/h): aceite de la fase liviana (caudal × ρ × aceite,
    core.centrifugas.aceite_perdido_kgh) de cada muestreo con balance; mediana de cada primaria en el turno y suma de
    las primarias. Fuera de rango físico (caudal de fase liviana ≤ 0 o eficiencia imposible): no entran.
    Copiados: SÍ entran. Lo copiado es la fase pesada (o la descarga), que en el caudal de fase liviana pesa muy poco
    (~0,1 % de aceite); la fase liviana es propia de cada primaria. Si se sacaran, en 2025 (la fase pesada de la
    ESE 230 (2) es copia de la (1)) los turnos en paralelo sumarían una sola primaria y el captado daría la mitad.
    modos (core.analisis_centrifugas.primarias_por_turno): un turno con las primarias en paralelo y una sola con
    balance queda afuera (incompleto), en vez de sumar la mitad.
    Columnas: fecha_produccion, turno, captado_kgh, n_primarias, n_muestreos."""
    columnas = CLAVES_TURNO + ["captado_kgh", "n_primarias", "n_muestreos"]
    if balance.empty:
        return pd.DataFrame(columns=columnas)
    fuera = (balance["fuera_fisico"].fillna(False).astype(bool) if "fuera_fisico" in balance
             else pd.Series(False, index=balance.index))
    validos = balance[(balance["tipo"] == PRIMARIA) & ~fuera
                      & (pd.to_numeric(balance["caudal_liviana"], errors="coerce") > 0)
                      & balance["aceite_liviana"].notna()]
    if validos.empty:
        return pd.DataFrame(columns=columnas)
    validos = validos.assign(captado_kgh=[aceite_perdido_kgh(c, a / 100) for c, a in
                                          zip(validos["caudal_liviana"], validos["aceite_liviana"])])
    por_equipo = validos.groupby(CLAVES_TURNO + ["equipo"]).agg(captado_kgh=("captado_kgh", "median"),
                                                                n=("captado_kgh", "size"))
    turnos = por_equipo.groupby(CLAVES_TURNO).agg(captado_kgh=("captado_kgh", "sum"), n_primarias=("n", "size"),
                                                  n_muestreos=("n", "sum")).reset_index()
    if modos is not None and not modos.empty:
        en_paralelo = modos.loc[modos["modo"] == PARALELO, CLAVES_TURNO].assign(_paralelo=True)
        turnos = turnos.merge(en_paralelo, on=CLAVES_TURNO, how="left")
        turnos = turnos[~(turnos["_paralelo"].fillna(False).astype(bool) & (turnos["n_primarias"] < 2))]
    return turnos.reset_index(drop=True)[columnas]


def datos_por_turno(datos: pd.DataFrame, captado: pd.DataFrame) -> pd.DataFrame:
    """Un registro por turno con el aceite captado por las primarias y los factores del turno: MTPH (media de los
    eBOE), fase pesada específica (mediana), aceite en fruta y calibre (medias) y programa más usado. datos:
    preparar_datos() (con removido = entrada × eficiencia, kg/MT); captado: captado_por_turno(). De cada eBOE en
    marcha en el turno: su MTPH media y su removido medio. mtph_total = Σ MTPH; removido_kgh = Σ MTPH × removido;
    captado_pct = captado_kgh / removido_kgh × 100; captado_kg_mt = captado_kgh / mtph_total."""
    if datos.empty or captado.empty:
        return pd.DataFrame(columns=CLAVES_TURNO + ["captado_pct", "captado_kg_mt"])
    eboes = datos.groupby(CLAVES_TURNO + ["eboe"]).agg(tph=("tph", "mean"), removido=("removido", "mean"))
    eboes["removido_kgh"] = eboes["tph"] * eboes["removido"]
    por_eboe = eboes.groupby(CLAVES_TURNO).agg(mtph_total=("tph", "sum"),
                                               removido_kgh=("removido_kgh", lambda s: s.sum(min_count=1)))
    turnos = datos.groupby(CLAVES_TURNO).agg(
        temporada=("temporada", "first"), tph=("tph", "mean"), fp_especifica=("fp_especifica", "median"),
        aceite_fruta=("aceite_fruta", "mean"), frutos_10kg=("frutos_10kg", "mean"),
        programa=("programa", lambda p: p.dropna().mode().iloc[0] if p.notna().any() else None),
        rango_fp_ref=("rango_fp_ref", "median")).join(por_eboe).reset_index()
    captado = captado.assign(fecha_produccion=pd.to_datetime(captado["fecha_produccion"]))
    unidos = turnos.merge(captado, on=CLAVES_TURNO, how="inner")
    unidos["captado_kg_mt"] = unidos["captado_kgh"] / unidos["mtph_total"].where(unidos["mtph_total"] > 0)
    unidos["captado_pct"] = unidos["captado_kgh"] / unidos["removido_kgh"].where(unidos["removido_kgh"] > 0) * 100
    return _categorias(unidos)


def correlaciones(datos: pd.DataFrame) -> pd.DataFrame:
    """r de Pearson entre los factores numéricos (NUMERICOS) por temporada, con su N (pares con los dos datos).
    Columnas: temporada, x, y (nombres), r, n."""
    filas = []
    columnas = [c for c in NUMERICOS if c in datos]
    for temporada, sub in datos.groupby("temporada", sort=True):
        for i, a in enumerate(columnas):
            for b in columnas[i + 1:]:
                par = sub[[a, b]].apply(pd.to_numeric, errors="coerce").dropna()
                r = float(par[a].corr(par[b])) if len(par) >= 3 and par[a].std() > 0 and par[b].std() > 0 else None
                filas.append({"temporada": temporada, "x": NUMERICOS[a], "y": NUMERICOS[b], "r": r, "n": len(par)})
    return pd.DataFrame(filas, columns=["temporada", "x", "y", "r", "n"])


# ---------------------------------------------------------------- modelo y medias ajustadas

def factores_del_modelo(datos: pd.DataFrame, carga: str = "mtph") -> list[str]:
    """Factores del modelo, en orden: programa, carga (MTPH o fase pesada específica en rangos), aceite en fruta,
    calibre, mes, turno, eBOE (si es por par) y temporada (si hay más de una)."""
    if carga not in CARGAS:
        raise ValueError(f'Carga desconocida: "{carga}". Las válidas son: {", ".join(CARGAS)}.')
    candidatos = ["programa_grupo", CARGAS[carga], "aceite_rango", "calibre", "mes", "turno"]
    if "eboe" in datos:
        candidatos.append("eboe")
    candidatos.append("temporada")
    return [c for c in candidatos if c in datos and datos[c].nunique() > 1]


def _datos_completos(datos: pd.DataFrame, respuesta: str, columnas: list[str]) -> pd.DataFrame:
    return datos.dropna(subset=[respuesta, *columnas]).copy()


def _formula(respuesta: str, factores: list[str], interaccion: tuple[str, str] | None = None) -> str:
    terminos = [f"C({f})" for f in factores]
    if interaccion:
        terminos.append(f"C({interaccion[0]}):C({interaccion[1]})")
    return f"{respuesta} ~ " + " + ".join(terminos)


PARTE = re.compile(r"C\((\w+)\)\[T\.(.*)\]")


def _vector(nombres: list[str], niveles: dict[str, list], fijos: dict[str, str]) -> np.ndarray:
    """Vector L de una media ajustada: 1 en el intercepto; en cada columna de un factor fijo, 1 si es su nivel (0 si
    no); en las de los demás factores, 1/(cantidad de niveles), es decir, repartidos en partes iguales."""
    vector = []
    for nombre in nombres:
        if nombre == "Intercept":
            vector.append(1.0)
            continue
        valor = 1.0
        for parte in nombre.split(":"):
            factor, nivel = PARTE.fullmatch(parte).groups()
            valor *= (1.0 if fijos[factor] == nivel else 0.0) if factor in fijos else 1.0 / len(niveles[factor])
        vector.append(valor)
    return np.array(vector)


def _media_ajustada(modelo, niveles: dict[str, list], fijos: dict[str, str], alfa: float) -> dict:
    """Media ajustada (L·β) con su IC; estimable=False (y sin valores) si L no está en el espacio de las filas de X."""
    vector = _vector(modelo.model.exog_names, niveles, fijos)
    proyector = modelo.model.pinv_wexog @ modelo.model.wexog
    if np.linalg.norm(vector - vector @ proyector) > 1e-6 * max(1.0, np.linalg.norm(vector)):
        return {"media": None, "ic_inf": None, "ic_sup": None, "estimable": False}
    media = float(vector @ modelo.params.values)
    error = float(np.sqrt(vector @ modelo.cov_params().values @ vector))
    margen = stats.t.ppf(1 - alfa / 2, modelo.df_resid) * error
    return {"media": media, "ic_inf": media - margen, "ic_sup": media + margen, "estimable": True}


def contraste(modelo, vector: np.ndarray) -> tuple[float, float | None] | None:
    """Valor (L·β) y p (t, dos colas) de un contraste del modelo; None si no es estimable (L fuera del espacio de las
    filas de X, por ejemplo cuando dos factores están confundidos)."""
    proyector = modelo.model.pinv_wexog @ modelo.model.wexog
    if np.linalg.norm(vector - vector @ proyector) > 1e-6 * max(1.0, np.linalg.norm(vector)):
        return None
    valor = float(vector @ modelo.params.values)
    error = float(np.sqrt(vector @ modelo.cov_params().values @ vector))
    return valor, (float(2 * stats.t.sf(abs(valor / error), modelo.df_resid)) if error > 0 else None)


def orden_de_niveles(factor: str, niveles) -> list[str]:
    """Niveles en el orden natural del factor (MTPH, fase pesada, meses, turnos y programas en su orden; el resto,
    alfabético o numérico)."""
    propios = {"mtph_rango": ETIQUETAS_MTPH, "fp_rango": RANGOS_FP["fp_especifica"]["etiquetas"], "mes": MESES,
               "turno": ORDENES["turno"], "programa_grupo": ORDENES["grupo_programa"], "regimen": REGIMENES}.get(factor)
    niveles = [str(n) for n in niveles]
    if propios:
        return [n for n in propios if n in niveles] + sorted(n for n in niveles if n not in propios)
    return sorted(niveles, key=lambda n: (float(n), n) if n.replace(".", "", 1).isdigit() else (np.inf, n))


def _cramer(a: pd.Series, b: pd.Series) -> float | None:
    """V de Cramér entre dos factores categóricos (0 = independientes, 1 = uno determina al otro)."""
    tabla = pd.crosstab(a, b)
    if min(tabla.shape) < 2:
        return None
    chi2 = stats.chi2_contingency(tabla, correction=False)[0]
    return float(np.sqrt(chi2 / (tabla.values.sum() * (min(tabla.shape) - 1))))


def vif_factores(modelo, datos: pd.DataFrame, factores: list[str]) -> pd.DataFrame:
    """VIF de cada factor: GVIF^(1/gl) (Fox y Monette), con GVIF = det(R₁₁)·det(R₂₂)/det(R) sobre la matriz de
    correlación de las columnas del modelo (sin el intercepto). Con un gl es el VIF clásico. confundido_con: el otro
    factor más asociado (V de Cramér) cuando el VIF pasa de VIF_MAXIMO. Columnas: factor, nombre, gl, vif,
    confundido, confundido_con, v_cramer."""
    nombres = modelo.model.exog_names
    columnas = [k for k, n in enumerate(nombres) if n != "Intercept" and modelo.model.exog[:, k].std() > 0]
    correlacion = np.corrcoef(modelo.model.exog[:, columnas], rowvar=False)
    signo_total, logdet_total = np.linalg.slogdet(correlacion)
    filas = []
    for factor in factores:
        propias = [i for i, k in enumerate(columnas) if nombres[k].startswith(f"C({factor})[")]
        resto = [i for i in range(len(columnas)) if i not in propias]
        if not propias:
            continue
        _, logdet_propias = np.linalg.slogdet(correlacion[np.ix_(propias, propias)])
        _, logdet_resto = np.linalg.slogdet(correlacion[np.ix_(resto, resto)]) if resto else (1, 0.0)
        if signo_total <= 0:                       # matriz singular: algún factor está totalmente confundido
            vif = np.inf
        else:
            vif = float(np.exp((logdet_propias + logdet_resto - logdet_total) / len(propias)))
        confundido = vif > VIF_MAXIMO
        otro, cramer = None, None
        if confundido:
            asociaciones = {f: _cramer(datos[factor], datos[f]) for f in factores if f != factor}
            asociaciones = {f: v for f, v in asociaciones.items() if v is not None}
            if asociaciones:
                otro = max(asociaciones, key=asociaciones.get)
                cramer = asociaciones[otro]
        filas.append({"factor": factor, "nombre": NOMBRES[factor], "gl": len(propias), "vif": vif,
                      "confundido": confundido, "confundido_con": NOMBRES.get(otro), "v_cramer": cramer})
    return pd.DataFrame(filas, columns=["factor", "nombre", "gl", "vif", "confundido", "confundido_con", "v_cramer"])


def modelo_efectos(datos: pd.DataFrame, respuesta: str, carga: str = "mtph", alfa: float = ALFA) -> dict:
    """Modelo aditivo de la respuesta con los factores de factores_del_modelo(): n, r2, r2_ajustado, factores,
    anova (factor, nombre, gl, sc, f, p), medias (factor, nombre, nivel, n, media, ic_inf, ic_sup, estimable; los
    niveles en su orden) y vif (vif_factores). Entran los registros con todos los datos. Sin datos suficientes,
    n y factores con anova, medias y vif en None."""
    if respuesta not in RESPUESTAS:
        raise ValueError(f'Respuesta desconocida: "{respuesta}". Las válidas son: {", ".join(RESPUESTAS)}.')
    factores = factores_del_modelo(datos, carga)
    completos = _datos_completos(datos, respuesta, factores)
    factores = [f for f in factores if completos[f].nunique() > 1]
    resultado = {"n": len(completos), "factores": factores, "r2": None, "r2_ajustado": None, "anova": None,
                 "medias": None, "vif": None}
    gl_modelo = sum(completos[f].nunique() - 1 for f in factores)
    if not factores or len(completos) <= gl_modelo + 2:
        return resultado
    modelo = smf.ols(_formula(respuesta, factores), data=completos).fit()
    anova = sm.stats.anova_lm(modelo, typ=2)
    niveles = {f: sorted(completos[f].unique()) for f in factores}
    medias = []
    for factor in factores:
        conteo = completos[factor].value_counts()
        for nivel in orden_de_niveles(factor, niveles[factor]):
            medias.append({"factor": factor, "nombre": NOMBRES[factor], "nivel": nivel, "n": int(conteo[nivel]),
                           **_media_ajustada(modelo, niveles, {factor: nivel}, alfa)})
    resultado.update(
        r2=float(modelo.rsquared), r2_ajustado=float(modelo.rsquared_adj),
        anova=pd.DataFrame([{"factor": f, "nombre": NOMBRES[f], "gl": anova.loc[f"C({f})", "df"],
                             "sc": anova.loc[f"C({f})", "sum_sq"], "f": anova.loc[f"C({f})", "F"],
                             "p": anova.loc[f"C({f})", "PR(>F)"]} for f in factores]),
        medias=pd.DataFrame(medias), vif=vif_factores(modelo, completos, factores))
    return resultado


def modelo_interaccion(datos: pd.DataFrame, respuesta: str, carga: str = "mtph", con: str = "temporada",
                       alfa: float = ALFA) -> dict:
    """El modelo de modelo_efectos() más la interacción carga × con (con = "temporada" o "regimen"; el régimen no se
    combina con la fase pesada como carga). Devuelve n, r2, p (de la interacción, ANOVA tipo II), a y b (factores) y
    celdas (a, b, n, media, ic_inf, ic_sup, estimable): la media ajustada de cada combinación con los demás factores
    repartidos en partes iguales. Celdas sin datos: n = 0 y no estimables."""
    if con not in ("temporada", "regimen"):
        raise ValueError('La interacción es con "temporada" o con "regimen".')
    if con == "regimen" and carga == "fp":
        raise ValueError("El régimen de agua sale de la fase pesada: no se combina con la fase pesada como carga.")
    a = CARGAS[carga]
    factores = factores_del_modelo(datos, carga)
    if con not in factores:
        factores = factores + ([con] if con in datos and datos[con].nunique() > 1 else [])
    completos = _datos_completos(datos, respuesta, factores)
    resultado = {"n": len(completos), "r2": None, "p": None, "a": a, "b": con, "celdas": None}
    if a not in factores or con not in factores or completos[a].nunique() < 2 or completos[con].nunique() < 2:
        return resultado
    factores = [f for f in factores if completos[f].nunique() > 1]
    modelo = smf.ols(_formula(respuesta, factores, (a, con)), data=completos).fit()
    anova = sm.stats.anova_lm(modelo, typ=2)
    niveles = {f: sorted(completos[f].unique()) for f in factores}
    conteo = completos.groupby([a, con]).size()
    celdas = []
    for nivel_b in orden_de_niveles(con, niveles[con]):
        for nivel_a in orden_de_niveles(a, niveles[a]):
            n = int(conteo.get((nivel_a, nivel_b), 0))
            media = (_media_ajustada(modelo, niveles, {a: nivel_a, con: nivel_b}, alfa) if n else
                     {"media": None, "ic_inf": None, "ic_sup": None, "estimable": False})
            celdas.append({"a": nivel_a, "b": nivel_b, "n": n, **media})
    resultado.update(r2=float(modelo.rsquared), p=float(anova.loc[f"C({a}):C({con})", "PR(>F)"]),
                     celdas=pd.DataFrame(celdas))
    return resultado


# ---------------------------------------------------------------- umbral de MTPH

def por_mtph(datos: pd.DataFrame, respuesta: str, por: str | None = "eboe") -> pd.DataFrame:
    """Media, IC 95 % y N de la respuesta por MTPH redondeada a la unidad (y por eBOE, si por = "eboe").
    Columnas: temporada, grupo (eBOE o "Planta"), mtph, n, media, ic_inf, ic_sup."""
    validos = datos.dropna(subset=["tph", respuesta]).assign(mtph=lambda d: d["tph"].round().astype(int))
    claves = ["temporada", por, "mtph"] if por else ["temporada", "mtph"]
    filas = [{"temporada": clave[0], "grupo": clave[1] if por else "Planta", "mtph": clave[-1],
              **media_ic(sub[respuesta])} for clave, sub in validos.groupby(claves, sort=True)]
    return pd.DataFrame(filas, columns=["temporada", "grupo", "mtph", "n", "media", "ic_inf", "ic_sup"])


def quiebre(x, y, candidatos=QUIEBRES, minimo_lado: int = N_MINIMO_LADO,
            covariables: pd.DataFrame | None = None) -> dict | None:
    """Regresión segmentada y = a + b₁·x + b₂·máx(0, x − q) [+ covariables]: prueba cada q de `candidatos` (con al
    menos minimo_lado datos de cada lado) y elige el de menor suma de cuadrados del error. covariables: columnas
    numéricas (por ejemplo, variables indicadoras de los factores) que entran en el mismo modelo, así las pendientes
    quedan ajustadas por ellas. Devuelve quiebre, pendiente_antes (b₁), pendiente_despues (b₁ + b₂), p_cambio (de b₂:
    ¿cambia la pendiente?), p_despues (¿la pendiente después es ≠ 0?), r2, n, n_despues (datos con x > q) y ajustada
    (y sin el efecto de las covariables más su media, con el índice de x: el residuo parcial para dibujar contra x).
    None si ningún candidato tiene datos suficientes de los dos lados."""
    x, y = pd.Series(x), pd.Series(y)
    indice = x.index
    tabla = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce").values, "y": pd.to_numeric(y, errors="coerce").values})
    extra = (covariables.reset_index(drop=True).astype(float) if covariables is not None
             else pd.DataFrame(index=tabla.index))
    nombres = list(extra.columns)
    tabla = pd.concat([tabla, extra], axis=1).dropna()
    mejor = None
    for q in candidatos:
        despues = int((tabla["x"] > q).sum())
        if despues < minimo_lado or len(tabla) - despues < minimo_lado:
            continue
        matriz = sm.add_constant(pd.concat([tabla[["x"]], (tabla["x"] - q).clip(lower=0).rename("bisagra"),
                                            tabla[nombres]], axis=1), has_constant="add")
        modelo = sm.OLS(tabla["y"], matriz).fit()
        if mejor is None or modelo.ssr < mejor[1].ssr:
            mejor = (q, modelo, despues)
    if mejor is None:
        return None
    q, modelo, despues = mejor
    efecto = (tabla[nombres] @ modelo.params[nombres]) if nombres else pd.Series(0.0, index=tabla.index)
    ajustada = pd.Series((tabla["y"] - efecto + efecto.mean()).values, index=indice[tabla.index])
    pendiente_despues = modelo.t_test("x + bisagra = 0")
    return {"quiebre": q, "pendiente_antes": float(modelo.params["x"]),
            "pendiente_despues": float(modelo.params["x"] + modelo.params["bisagra"]),
            "p_cambio": float(modelo.pvalues["bisagra"]), "p_despues": float(pendiente_despues.pvalue),
            "r2": float(modelo.rsquared), "n": len(tabla), "n_despues": despues, "ajustada": ajustada}


def _indicadoras(datos: pd.DataFrame, factores: list[str]) -> pd.DataFrame:
    """Variables indicadoras (0/1) de los factores, sin el primer nivel de cada uno."""
    if not factores:
        return pd.DataFrame(index=datos.index)
    return pd.get_dummies(datos[factores].astype(str), drop_first=True, dtype=float)


def segmentada_por_temporada(datos: pd.DataFrame, respuesta: str, ajustado: bool = True
                             ) -> tuple[pd.DataFrame, pd.Series]:
    """quiebre() de la respuesta contra la MTPH en cada temporada. ajustado=True: los FACTORES_AJUSTE (programa,
    aceite en fruta, calibre, mes, turno y eBOE, los que varían en la temporada) entran en el mismo modelo
    segmentado, así las pendientes no se confunden con ellos. (Ajustar solo la respuesta, con los residuos de un
    modelo sin MTPH, achica la pendiente cuando la MTPH está correlacionada con esos factores.) Devuelve la tabla
    (temporada y las columnas de quiebre(), vacías si no hay datos suficientes) y la respuesta ajustada de cada
    registro (residuo parcial; sin ajustar, la respuesta tal cual), para dibujarla por MTPH."""
    columnas = ["temporada", "quiebre", "pendiente_antes", "pendiente_despues", "p_cambio", "p_despues", "r2", "n",
                "n_despues"]
    filas, ajustada = [], pd.Series(np.nan, index=datos.index, dtype=float)
    for temporada, sub in datos.dropna(subset=["tph", respuesta]).groupby("temporada", sort=True):
        factores = [f for f in FACTORES_AJUSTE if ajustado and f in sub and sub[f].nunique() > 1]
        sub = sub.dropna(subset=factores)
        resultado = quiebre(sub["tph"], sub[respuesta], covariables=_indicadoras(sub, factores))
        if resultado is None:
            filas.append({"temporada": temporada, "n": len(sub)})
            continue
        serie = resultado.pop("ajustada")
        ajustada.loc[serie.index] = serie.values
        filas.append({"temporada": temporada, **resultado})
    return pd.DataFrame(filas, columns=columnas), ajustada


def quiebres_por_temporada(datos: pd.DataFrame, respuesta: str, ajustado: bool = False) -> pd.DataFrame:
    """La tabla de segmentada_por_temporada() (sin ajustar por defecto)."""
    return segmentada_por_temporada(datos, respuesta, ajustado)[0]


def frase_quiebre(fila: dict, respuesta: str, alfa: float = ALFA) -> str:
    """Una frase por temporada con el resultado de quiebre(): dónde está el quiebre y qué pasa por encima (sube, baja
    o no cambia de forma significativa, con su p). Avisa si el quiebre quedó en el borde de los valores probados."""
    from core.formato import numero, texto_p
    unidad, decimales = RESPUESTAS[respuesta]["unidad"], RESPUESTAS[respuesta]["decimales"]
    unidad = "puntos" if unidad == "%" else unidad
    temporada = fila["temporada"]
    if pd.isna(fila.get("quiebre")):
        return f"{temporada}: no hay datos suficientes de los dos lados de ningún quiebre (N = {numero(fila['n'], 0)})."
    q = int(fila["quiebre"])
    antes, despues = fila["pendiente_antes"], fila["pendiente_despues"]
    if fila["p_despues"] < alfa:
        tendencia = (f"{'sube' if despues > 0 else 'baja'} {numero(abs(despues), decimales + 1)} {unidad} por MTPH "
                     f"({texto_p(fila['p_despues'])})")
    else:
        tendencia = f"no cambia de forma significativa ({texto_p(fila['p_despues'])})"
    borde = " Quedó en el borde de los valores probados (10–18): el cambio puede empezar antes o después." \
        if q in (min(QUIEBRES), max(QUIEBRES)) else ""
    cambio = "la pendiente cambia" if fila["p_cambio"] < alfa else "la pendiente no cambia de forma significativa"
    return (f"{temporada}: quiebre en {q} MTPH ({cambio}, {texto_p(fila['p_cambio'])}). Hasta ahí, "
            f"{numero(antes, decimales + 1)} {unidad} por MTPH. Por encima ({numero(fila['n_despues'], 0)} datos): "
            f"{tendencia}.{borde}")


def descomponer_por_mezcla(interaccion: dict, datos: pd.DataFrame, base: str, nueva: str) -> dict | None:
    """Separa la diferencia de la media ajustada entre dos temporadas (nueva − base) en la parte que se debe a operar
    con otra mezcla de la carga (por ejemplo, más MTPH) y la parte a igual carga.

    interaccion: modelo_interaccion(con="temporada") con todas las temporadas (sus celdas: media ajustada de cada
    rango de carga en cada temporada). datos: los registros del modelo (para la proporción de cada rango en cada
    temporada). Con μ_t(m) la media ajustada del rango m en la temporada t y p_t(m) la proporción de registros:
    media_t = Σ p_t(m)·μ_t(m); nueva_con_mezcla_base = Σ p_base(m)·μ_nueva(m);
    por_mezcla = media_nueva − nueva_con_mezcla_base; a_igual_carga = nueva_con_mezcla_base − media_base (suman la
    diferencia). Solo con los rangos estimables en las dos temporadas (proporciones reescaladas a 1). Devuelve
    media_base, media_nueva, nueva_con_mezcla_base, diferencia, por_mezcla, a_igual_carga, rangos (tabla por rango:
    rango, p_base, p_nueva, media_base, media_nueva, n_base, n_nueva) y n_base, n_nueva. None si no hay celdas."""
    celdas = interaccion.get("celdas")
    if celdas is None or interaccion.get("b") != "temporada":
        return None
    a = interaccion["a"]
    cel = celdas[celdas["estimable"]].pivot(index="a", columns="b", values="media")
    if base not in cel or nueva not in cel:
        return None
    rangos = [r for r in orden_de_niveles(a, cel.index) if pd.notna(cel.loc[r, base]) and pd.notna(cel.loc[r, nueva])]
    if not rangos:
        return None
    conteo = datos.dropna(subset=[a]).groupby(["temporada", a]).size()
    n_base = pd.Series({r: conteo.get((base, r), 0) for r in rangos}, dtype=float)
    n_nueva = pd.Series({r: conteo.get((nueva, r), 0) for r in rangos}, dtype=float)
    if n_base.sum() == 0 or n_nueva.sum() == 0:
        return None
    p_base, p_nueva = n_base / n_base.sum(), n_nueva / n_nueva.sum()
    mu_base, mu_nueva = cel.loc[rangos, base], cel.loc[rangos, nueva]
    media_base, media_nueva = float((p_base * mu_base).sum()), float((p_nueva * mu_nueva).sum())
    con_mezcla_base = float((p_base * mu_nueva).sum())
    return {"media_base": media_base, "media_nueva": media_nueva, "nueva_con_mezcla_base": con_mezcla_base,
            "diferencia": media_nueva - media_base, "por_mezcla": media_nueva - con_mezcla_base,
            "a_igual_carga": con_mezcla_base - media_base, "n_base": int(n_base.sum()), "n_nueva": int(n_nueva.sum()),
            "rangos": pd.DataFrame({"rango": rangos, "p_base": p_base.values, "p_nueva": p_nueva.values,
                                    "media_base": mu_base.values, "media_nueva": mu_nueva.values,
                                    "n_base": n_base.astype(int).values, "n_nueva": n_nueva.astype(int).values})}
