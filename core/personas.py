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
"""

import pandas as pd
import statsmodels.formula.api as smf
from sqlalchemy import select

from core.analisis_centrifugas import PRIMARIA
from core.efectos import _media_ajustada, orden_de_niveles
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
