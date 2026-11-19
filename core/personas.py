"""Desempeño de equipos de trabajo (Sistema › Equipos de trabajo): eficiencia del eBOE y de la centrífuga primaria
por operador, químico (analista) y turno, y sus combinaciones.

Muestra desempeño de equipos y condiciones de trabajo, no evalúa personas. Privacidad:
- La sección solo aparece si la planta tiene mostrar_personas = sí, y nunca en el modo presentación.
- anonimizar() (activado por defecto en la pantalla): Operador A, B, C… y Químico A, B… por cantidad de datos.
- Nada de esto se exporta (ni PNG, ni Excel, ni "Descargar todo"), aunque mostrar_personas = sí.

Cálculo: medias ajustadas (como core.efectos) del modelo respuesta ~ grupo + condiciones de trabajo, con IC 95 % y N.
Condiciones: para el eBOE, eBOE, programa (grupos), MTPH (rangos), temporada y turno (si el grupo no es el turno);
para la primaria, el equipo, la temporada y el turno. Solo entran los grupos (o combinaciones) con N ≥ N_MINIMO.
Eficiencia de la primaria: la del balance de cada muestreo, sin copiados ni fuera de rango físico (dato inválido).
- turno_por_temporada(): el turno dentro de cada temporada (¿un turno sale abajo siempre o en una sola?).
- lectura_por_quimico(): ¿un químico lee distinto la entrada o la salida con la misma fruta? Es un posible sesgo de
  medición, no desempeño.
- Cruce químico × turno: quimico_por_turno() y participacion_quimicos() (% de los pares), brecha_turno_t() (la brecha
  del T sin y con el químico en el modelo) con conclusion_turno_t(), y temporada_con_y_sin_quimico() con
  diferencias_por_quimico() (cuánto de la diferencia entre temporadas podría venir de quién midió).
"""

import pandas as pd
import statsmodels.formula.api as smf
from sqlalchemy import select

from core.analisis_centrifugas import PRIMARIA
from core.efectos import _media_ajustada, _vector, contraste, orden_de_niveles
from core.models import Equipo, LecturaOperacion, Linea, MuestraCentrifuga

N_MINIMO = 20
ACLARACION = "Muestra desempeño de equipos y condiciones de trabajo, no evalúa personas."
PREFIJOS = {"operador": "Operador", "analista": "Químico", "operador_centrifugas": "Operador"}
NOMBRES_GRUPO = {"operador": "Operador de eBOE", "analista": "Químico", "turno": "Turno",
                 "operador_centrifugas": "Operador de centrífugas"}
AJUSTE_EBOE = ["eboe", "programa_grupo", "mtph_rango", "temporada", "turno"]
AJUSTE_PRIMARIA = ["equipo", "temporada", "turno"]
LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _nombre(valor) -> str | None:
    """Nombre limpio (sin espacios de más); vacío → None."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = " ".join(str(valor).split())
    return texto or None


def operadores_eboe(sesion, planta_id: int) -> pd.DataFrame:
    """Operador de cada lectura de operación de los eBOE de la planta: eboe (número), fecha_hora, operador."""
    filas = sesion.execute(select(Equipo.numero, LecturaOperacion.fecha_hora, LecturaOperacion.operador)
                           .join(Equipo, LecturaOperacion.equipo_id == Equipo.id).join(Linea)
                           .where(Linea.planta_id == planta_id, LecturaOperacion.operador.is_not(None))).all()
    return pd.DataFrame(filas, columns=["eboe", "fecha_hora", "operador"])


def personas_centrifugas(sesion, planta_id: int) -> pd.DataFrame:
    """Químico y operador de cada muestreo de centrífuga (código + fecha_hora; el más frecuente entre sus muestras):
    codigo, fecha_hora, analista, operador_centrifugas."""
    filas = sesion.execute(select(Equipo.codigo, MuestraCentrifuga.fecha_hora, MuestraCentrifuga.analista,
                                  MuestraCentrifuga.operador)
                           .join(Equipo, MuestraCentrifuga.equipo_id == Equipo.id).join(Linea)
                           .where(Linea.planta_id == planta_id)).all()
    tabla = pd.DataFrame(filas, columns=["codigo", "fecha_hora", "analista", "operador_centrifugas"])
    if tabla.empty:
        return tabla

    def moda(serie):
        valores = serie.map(_nombre).dropna()
        return valores.mode().iloc[0] if len(valores) else None

    return tabla.groupby(["codigo", "fecha_hora"], as_index=False).agg(
        analista=("analista", moda), operador_centrifugas=("operador_centrifugas", moda))


def datos_eboe(datos: pd.DataFrame, operadores: pd.DataFrame) -> pd.DataFrame:
    """Los pares (core.efectos.preparar_datos, con analista) con el operador del eBOE de la misma hora. eboe y
    temporada como texto (como en preparar_datos)."""
    operadores = operadores.assign(eboe=operadores["eboe"].astype(str), operador=operadores["operador"].map(_nombre))
    unidos = datos.drop(columns=["operador"], errors="ignore").merge(
        operadores.drop_duplicates(["eboe", "fecha_hora"]), on=["eboe", "fecha_hora"], how="left")
    return unidos.assign(analista=unidos["analista"].map(_nombre))


def datos_primaria(balance: pd.DataFrame, personas: pd.DataFrame) -> pd.DataFrame:
    """Los balances de las primarias con eficiencia válida (sin copiados ni fuera de rango físico), con el químico y el
    operador de centrífugas del muestreo. Columnas del balance más eficiencia_pct, analista, operador_centrifugas;
    equipo, temporada y turno como texto."""
    validos = balance[(balance["tipo"] == PRIMARIA) & ~balance["copiada"].fillna(False).astype(bool)
                      & ~balance["fuera_fisico"].fillna(False).astype(bool) & balance["eficiencia_pct"].notna()]
    unidos = validos.drop(columns=["analista"], errors="ignore").merge(personas, on=["codigo", "fecha_hora"], how="left")
    for columna in ("equipo", "temporada", "turno"):
        unidos[columna] = unidos[columna].map(lambda v: None if pd.isna(v) else str(v))
    return unidos


def anonimizar(datos: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    """Cambia los nombres de cada columna por "Operador A", "Operador B"… o "Químico A"… (PREFIJOS), en orden de
    cantidad de datos (A = el que tiene más). La misma persona tiene la misma letra en toda la tabla."""
    datos = datos.copy()
    for columna in columnas:
        orden = datos[columna].dropna().value_counts()
        prefijo = PREFIJOS.get(columna, "Persona")
        codigos = {nombre: f"{prefijo} {LETRAS[k] if k < len(LETRAS) else k + 1}" for k, nombre in enumerate(orden.index)}
        datos[columna] = datos[columna].map(codigos)
    return datos


def medias_por_grupo(datos: pd.DataFrame, respuesta: str, grupo: list[str], ajuste: list[str],
                     n_minimo: int = N_MINIMO, alfa: float = 0.05) -> dict:
    """Medias ajustadas de la respuesta por grupo (una columna, o una combinación "A × B" de dos), con las
    condiciones de trabajo `ajuste` (las que varían y no son parte del grupo) en el modelo. Solo los niveles con N ≥
    n_minimo. Devuelve n, r2, excluidos (niveles con pocos datos) y medias (grupo, n, media, ic_inf, ic_sup,
    estimable), de mayor a menor N."""
    datos = datos.dropna(subset=[respuesta, *grupo]).copy()
    datos["_grupo"] = datos[grupo].astype(str).agg(" × ".join, axis=1)
    conteo = datos["_grupo"].value_counts()
    validos = conteo[conteo >= n_minimo].index
    resultado = {"n": 0, "r2": None, "excluidos": int((conteo < n_minimo).sum()),
                 "medias": pd.DataFrame(columns=["grupo", "n", "media", "ic_inf", "ic_sup", "estimable"])}
    datos = datos[datos["_grupo"].isin(validos)]
    factores = [f for f in ajuste if f not in grupo and f in datos and datos[f].nunique() > 1]
    datos = datos.dropna(subset=factores)
    if datos["_grupo"].nunique() < 2 or len(datos) <= sum(datos[f].nunique() for f in factores) + datos["_grupo"].nunique():
        return resultado
    modelo = smf.ols(f"{respuesta} ~ C(_grupo)" + "".join(f" + C({f})" for f in factores), data=datos).fit()
    niveles = {f: sorted(datos[f].unique()) for f in ["_grupo", *factores]}
    conteo = datos["_grupo"].value_counts()
    filas = [{"grupo": nivel, "n": int(conteo[nivel]), **_media_ajustada(modelo, niveles, {"_grupo": nivel}, alfa)}
             for nivel in conteo.index]
    resultado.update(n=len(datos), r2=float(modelo.rsquared), medias=pd.DataFrame(filas))
    return resultado


def orden_turnos(niveles) -> list[str]:
    """Los turnos en el orden del día (M, T, N)."""
    return orden_de_niveles("turno", niveles)


def turno_por_temporada(datos: pd.DataFrame, respuesta: str, ajuste: list[str], n_minimo: int = N_MINIMO
                        ) -> pd.DataFrame:
    """Media ajustada de la respuesta por turno dentro de cada temporada (el modelo de medias_por_grupo en cada
    temporada, con las condiciones de trabajo menos la temporada). Columnas: temporada, turno, n, media, ic_inf,
    ic_sup, estimable."""
    filas = []
    for temporada, sub in datos.dropna(subset=["temporada"]).groupby("temporada", sort=True):
        resultado = medias_por_grupo(sub, respuesta, ["turno"], [f for f in ajuste if f != "temporada"], n_minimo)
        for fila in resultado["medias"].to_dict("records"):
            filas.append({"temporada": temporada, "turno": fila["grupo"], **{k: fila[k] for k in (
                "n", "media", "ic_inf", "ic_sup", "estimable")}})
    tabla = pd.DataFrame(filas, columns=["temporada", "turno", "n", "media", "ic_inf", "ic_sup", "estimable"])
    orden = {t: k for k, t in enumerate(orden_turnos(tabla["turno"].unique()))}
    return tabla.sort_values(["temporada", "turno"], key=lambda c: c.map(orden) if c.name == "turno" else c,
                             ignore_index=True)


AJUSTE_LECTURA = ["eboe", "programa_grupo", "mtph_rango", "semana"]     # la semana fija la temporada y la fruta
LECTURAS = {"aceite_fruta": "Entrada", "aceite_salida": "Salida"}
UMBRAL_SESGO_PCT = 2.0      # diferencia mínima (% de la lectura mediana) para marcar un posible sesgo (a confirmar)


def lectura_por_quimico(datos: pd.DataFrame, n_minimo: int = N_MINIMO, alfa: float = 0.05) -> pd.DataFrame:
    """¿Algún químico lee distinto el aceite en fruta de entrada o de salida con la misma fruta? Para cada lectura
    (LECTURAS), un modelo aceite ~ químico + eBOE + programa + MTPH + semana de producción, y para cada químico el
    contraste "su media ajustada − la lectura mediana de los químicos" (kg/MT) con su p (t). La referencia es el químico
    del medio (o el promedio de los dos del medio): así uno que lee distinto no corre la referencia de los demás.
    Una diferencia significativa (p < alfa) y de al menos UMBRAL_SESGO_PCT % de la lectura mediana (con miles de datos,
    diferencias chiquitas dan p < 0,05) en la entrada o en la salida es un posible sesgo de medición, no desempeño:
    la fruta y la operación son las mismas. datos: pares (core.efectos.preparar_datos, con analista).
    Columnas: quimico, n y, por lectura, media_<l>, dif_<l>, pct_<l> (% de la lectura mediana) y p_<l> (l = entrada o
    salida), y lectura (texto)."""
    datos = datos.assign(semana=pd.to_datetime(datos["fecha_produccion"]).dt.strftime("%G-%V"))
    salida = {}
    for respuesta, lugar in LECTURAS.items():
        sub = datos.dropna(subset=[respuesta, "analista"])
        conteo = sub["analista"].value_counts()
        sub = sub[sub["analista"].isin(conteo[conteo >= n_minimo].index)].assign(_grupo=lambda d: d["analista"])
        factores = [f for f in AJUSTE_LECTURA if f in sub and sub[f].nunique() > 1]
        sub = sub.dropna(subset=factores)
        if sub["_grupo"].nunique() < 2:
            continue
        modelo = smf.ols(f"{respuesta} ~ C(_grupo)" + "".join(f" + C({f})" for f in factores), data=sub).fit()
        niveles = {f: sorted(sub[f].unique()) for f in ["_grupo", *factores]}
        vectores = {q: _vector(modelo.model.exog_names, niveles, {"_grupo": q}) for q in niveles["_grupo"]}
        ordenados = sorted(vectores, key=lambda q: float(vectores[q] @ modelo.params.values))
        mitad = len(ordenados) // 2
        del_medio = ordenados[mitad - 1:mitad + 1] if len(ordenados) % 2 == 0 else [ordenados[mitad]]
        referencia = sum(vectores[q] for q in del_medio) / len(del_medio)
        for quimico, vector in vectores.items():
            fila = salida.setdefault(quimico, {"quimico": quimico, "n": 0})
            fila["n"] = max(fila["n"], int((sub["_grupo"] == quimico).sum()))
            resultado = contraste(modelo, vector - referencia)
            if resultado is None:
                continue
            dif, p = resultado
            clave = lugar.lower()
            fila[f"media_{clave}"] = float(vector @ modelo.params.values)
            fila[f"dif_{clave}"] = dif
            mediana = float(referencia @ modelo.params.values)
            fila[f"pct_{clave}"] = dif / mediana * 100 if mediana else None
            fila[f"p_{clave}"] = p
    columnas = ["quimico", "n", "media_entrada", "dif_entrada", "pct_entrada", "p_entrada", "media_salida",
                "dif_salida", "pct_salida", "p_salida", "lectura"]
    tabla = pd.DataFrame(list(salida.values())).reindex(columns=columnas)

    def texto(fila) -> str:
        distintas = [lugar.lower() for lugar in LECTURAS.values()
                     if pd.notna(fila[f"p_{lugar.lower()}"]) and fila[f"p_{lugar.lower()}"] < alfa
                     and abs(fila[f"pct_{lugar.lower()}"]) >= UMBRAL_SESGO_PCT]
        if not distintas:
            return "sin diferencias de lectura"
        return "posible sesgo de medición (" + " y ".join(distintas) + ")"

    tabla["lectura"] = tabla.apply(texto, axis=1) if len(tabla) else []
    return tabla.sort_values("n", ascending=False, ignore_index=True)


# ── Cruce químico × turno: ¿la brecha del turno T (o la diferencia entre temporadas) viene de quién midió? ──────────

def con_quimico(datos: pd.DataFrame, respuesta: str, n_minimo: int = N_MINIMO) -> pd.DataFrame:
    """Las filas con respuesta y químico, solo de los químicos con N ≥ n_minimo en su temporada: la misma muestra
    para el modelo con y sin el factor químico (así la comparación es justa)."""
    datos = datos.dropna(subset=[respuesta, "analista", "temporada"])
    conteo = datos.groupby(["temporada", "analista"])[respuesta].transform("size")
    return datos[conteo >= n_minimo]


def quimico_por_turno(datos: pd.DataFrame) -> pd.DataFrame:
    """% de los pares de cada turno medidos por cada químico, por temporada. Columnas: temporada, turno, analista, n,
    pct (sobre los pares con químico de ese turno y temporada)."""
    datos = datos.dropna(subset=["temporada", "turno", "analista"])
    if datos.empty:
        return pd.DataFrame(columns=["temporada", "turno", "analista", "n", "pct"])
    conteo = datos.groupby(["temporada", "turno", "analista"]).size().rename("n").reset_index()
    return conteo.assign(pct=conteo["n"] / conteo.groupby(["temporada", "turno"])["n"].transform("sum") * 100)


def participacion_quimicos(datos: pd.DataFrame) -> pd.DataFrame:
    """% de los pares de cada temporada medidos por cada químico. Columnas: temporada, analista, n, pct."""
    datos = datos.dropna(subset=["temporada", "analista"])
    if datos.empty:
        return pd.DataFrame(columns=["temporada", "analista", "n", "pct"])
    conteo = datos.groupby(["temporada", "analista"]).size().rename("n").reset_index()
    return conteo.assign(pct=conteo["n"] / conteo.groupby("temporada")["n"].transform("sum") * 100)


def brecha_turno_t(datos: pd.DataFrame, respuesta: str, ajuste: list[str], n_minimo: int = N_MINIMO
                   ) -> pd.DataFrame:
    """Brecha del turno T en cada temporada, sin y con el químico en el modelo.

    Brecha = media ajustada del T − promedio de las medias ajustadas de los otros turnos (puntos de la respuesta), con
    su p. Modelo: respuesta ~ turno + condiciones de trabajo (`ajuste` sin la temporada ni el turno) [+ químico], en la
    muestra de con_quimico() (la misma para los dos). Columnas: temporada, n, brecha_sin, p_sin, brecha_con, p_con
    (brecha_con vacía si no es estimable: el químico y el turno están confundidos)."""
    filas = []
    for temporada, sub in con_quimico(datos, respuesta, n_minimo).groupby("temporada", sort=True):
        if "T" not in set(sub["turno"]) or sub["turno"].nunique() < 2:
            continue
        fila = {"temporada": temporada, "n": len(sub)}
        sin_quimico = [f for f in ajuste if f != "analista"]      # por si el ajuste ya trae el químico
        for sufijo, extra in (("sin", []), ("con", ["analista"])):
            factores = [f for f in [*sin_quimico, *extra] if f not in ("temporada", "turno") and f in sub
                        and sub[f].nunique() > 1]
            sub_modelo = sub.dropna(subset=["turno", *factores])
            modelo = smf.ols(f"{respuesta} ~ C(turno)" + "".join(f" + C({f})" for f in factores),
                             data=sub_modelo).fit()
            niveles = {f: sorted(sub_modelo[f].unique()) for f in ["turno", *factores]}
            otros = [t for t in niveles["turno"] if t != "T"]
            nombres = modelo.model.exog_names
            vector = (_vector(nombres, niveles, {"turno": "T"})
                      - sum(_vector(nombres, niveles, {"turno": t}) for t in otros) / len(otros))
            resultado = contraste(modelo, vector)
            fila[f"brecha_{sufijo}"], fila[f"p_{sufijo}"] = resultado if resultado else (None, None)
        filas.append(fila)
    return pd.DataFrame(filas, columns=["temporada", "n", "brecha_sin", "p_sin", "brecha_con", "p_con"])


def conclusion_turno_t(brechas: pd.DataFrame, alfa: float = 0.05) -> str:
    """Frase de conclusión sobre la brecha del turno T. Solo cuentan las temporadas con el T abajo y significativo sin
    el químico; ahí la brecha "se explica por el químico" si con el químico deja de ser significativa o se reduce a
    menos de la mitad. Todas → se explica; ninguna → no se explica; algunas → en parte."""
    abajo = brechas[(brechas["brecha_sin"] < 0) & (brechas["p_sin"] < alfa)]
    if abajo.empty:
        return "Sin el químico en el modelo, el turno T no sale significativamente abajo en ninguna temporada."
    if abajo["brecha_con"].isna().any():
        return ("No se puede separar el turno del químico en alguna temporada (cada químico trabaja siempre en el "
                "mismo turno): no se sabe si la brecha del turno T viene de quién midió.")
    explicada = (abajo["p_con"] >= alfa) | (abajo["brecha_con"].abs() < abajo["brecha_sin"].abs() / 2)
    if explicada.all():
        return ("La brecha del turno T se explica por el químico: con el químico en el modelo, desaparece o se reduce "
                "a menos de la mitad.")
    if not explicada.any():
        return "La brecha del turno T no se explica por el químico: con el químico en el modelo, se mantiene."
    temporadas = ", ".join(str(t) for t in abajo.loc[explicada, "temporada"])
    return ("La brecha del turno T se explica en parte por el químico: desaparece o se reduce a menos de la mitad "
            f"solo en {temporadas}.")


def temporada_con_y_sin_quimico(datos: pd.DataFrame, respuesta: str, ajuste: list[str], n_minimo: int = N_MINIMO
                                ) -> pd.DataFrame:
    """Media ajustada de la respuesta por temporada sin y con el químico en el modelo (condiciones `ajuste`), en la
    misma muestra (con_quimico). Columnas: temporada, n, media_sin, media_con (vacía si no es estimable)."""
    sin_quimico = [f for f in ajuste if f != "analista"]
    muestra = con_quimico(datos, respuesta, n_minimo)
    sin = medias_por_grupo(muestra, respuesta, ["temporada"], sin_quimico, n_minimo)["medias"]
    con = medias_por_grupo(muestra, respuesta, ["temporada"], [*sin_quimico, "analista"], n_minimo)["medias"]
    if sin.empty:
        return pd.DataFrame(columns=["temporada", "n", "media_sin", "media_con"])
    con = con if not con.empty else pd.DataFrame(columns=["grupo", "media"])
    tabla = sin[["grupo", "n", "media"]].rename(columns={"grupo": "temporada", "media": "media_sin"}).merge(
        con[["grupo", "media"]].rename(columns={"grupo": "temporada", "media": "media_con"}), on="temporada",
        how="left")
    return tabla.sort_values("temporada", ignore_index=True)


def diferencias_por_quimico(tabla: pd.DataFrame) -> pd.DataFrame:
    """Diferencia entre temporadas seguidas, sin y con el químico en el modelo, y la parte que podría venir de quién
    midió (por_quimico = dif_sin − dif_con). tabla: temporada_con_y_sin_quimico. Columnas: base, nueva, dif_sin,
    dif_con, por_quimico."""
    filas = []
    for k in range(1, len(tabla)):
        a, b = tabla.iloc[k - 1], tabla.iloc[k]
        dif_sin = b["media_sin"] - a["media_sin"]
        dif_con = None if pd.isna(a["media_con"]) or pd.isna(b["media_con"]) else b["media_con"] - a["media_con"]
        filas.append({"base": a["temporada"], "nueva": b["temporada"], "dif_sin": dif_sin, "dif_con": dif_con,
                      "por_quimico": None if dif_con is None else dif_sin - dif_con})
    return pd.DataFrame(filas, columns=["base", "nueva", "dif_sin", "dif_con", "por_quimico"])
