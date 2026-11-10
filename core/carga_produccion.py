"""Carga de producción por turno con aprobación (tarea 20). Sin Streamlit.

Un registro por planta + fecha de producción + turno + proceso (RegistroProduccion): fruta procesada (MT, de la
balanza de camiones), aceite envasado (kg), jugo concentrado 65 °Brix (kg, ya viene a 65 °Bx: sin conversión),
reproceso (kg) y comentario.
- turno_recien_terminado(), valores_por_defecto(): lo que propone el formulario (el turno que acaba de terminar).
- calcular(): rendimiento de aceite y de concentrado (kg/MT); promedio_ultimas_semanas(): el de los 14 días
  anteriores, solo aprobados y ponderado (Σ kg / Σ MT).
- validar(): errores (valores negativos, falta la fruta procesada, turno ya cargado) y avisos (rendimiento fuera del
  percentil 0,5–99,5 de los aprobados: "¿está bien tipeado?").
- guardar_borrador(), editar_registro() (un borrador se edita sin motivo ni historial; un aprobado, con motivo en
  Cambio, y sigue aprobado), aprobar(), devolver_a_borrador() (con motivo). Permisos: core.cambios.puede_editar y
  puede_aprobar (puntos únicos).
Solo los registros aprobados entran en los KPI y en la ponderación por toneladas (core.produccion).
"""

from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.cambios import exigir_aprobacion, exigir_permiso, nuevo_grupo, registrar
from core.models import AnalisisScott, Equipo, Linea, Planta, Proceso, PuntoMuestreo, RegistroProduccion, Turno
from core.turnos import fecha_produccion, fin_de_turno, inicio_de_turno, turno_de

BORRADOR, APROBADO, APP = "borrador", "aprobado", "app"
TABLA = "registro_produccion"
CAMPOS = {"fruta_t": "fruta procesada (MT)", "aceite_envasado_kg": "aceite envasado (kg)",
          "jugo_conc_kg": "jugo concentrado 65 °Bx (kg)", "reproceso_kg": "reproceso (kg)", "comentario": "comentario"}
NUMERICOS = ("fruta_t", "aceite_envasado_kg", "jugo_conc_kg", "reproceso_kg")
DIAS_PROMEDIO = 14
PERCENTILES = (0.5, 99.5)
N_MINIMO_PERCENTILES = 50
# Rendimiento → (nombre, kg del numerador)
RENDIMIENTOS = {"rendimiento_aceite": ("Rendimiento de aceite", "aceite_envasado_kg"),
                "rendimiento_concentrado": ("Rendimiento de concentrado 65 °Bx", "jugo_conc_kg")}


def _hay(valor) -> bool:
    return valor is not None and not (isinstance(valor, float) and np.isnan(valor))


# ---------------------------------------------------------------- valores iniciales

def turno_recien_terminado(planta: Planta, ahora: datetime) -> tuple:
    """(fecha de producción, turno) del turno anterior al que está en curso."""
    actual = turno_de(planta, ahora)
    inicio = inicio_de_turno(planta, fecha_produccion(planta, ahora), actual)
    antes = inicio - timedelta(minutes=1)
    return fecha_produccion(planta, antes), turno_de(planta, antes)


def valores_por_defecto(sesion, planta: Planta, ahora: datetime) -> dict:
    """{"dia", "turno" (nombre), "proceso_id", "procesos": [(id, texto)]}: el turno que acaba de terminar y el proceso
    más frecuente de sus análisis (si no hay, el último proceso de la planta)."""
    dia, turno = turno_recien_terminado(planta, ahora)
    procesos = sesion.scalars(select(Proceso).where(Proceso.planta_id == planta.id)
                              .order_by(Proceso.temporada.desc(), Proceso.numero.desc())).all()
    del_turno = sesion.scalars(select(AnalisisScott.proceso_id).join(AnalisisScott.punto_muestreo)
                               .join(PuntoMuestreo.equipo).join(Equipo.linea).where(
        Linea.planta_id == planta.id, AnalisisScott.fecha_hora >= inicio_de_turno(planta, dia, turno),
        AnalisisScott.fecha_hora < fin_de_turno(planta, dia, turno))).all()
    proceso_id = Counter(del_turno).most_common(1)[0][0] if del_turno else (procesos[0].id if procesos else None)
    return {"dia": dia, "turno": turno.nombre, "proceso_id": proceso_id,
            "procesos": [(p.id, f"{p.temporada} · proceso {p.numero}") for p in procesos]}


# ---------------------------------------------------------------- cálculo

def calcular(datos: dict) -> dict:
    """Rendimiento de aceite (kg/MT) = aceite envasado / fruta procesada; rendimiento de concentrado 65 °Bx (kg/MT)
    = jugo concentrado 65 °Bx / fruta procesada. None si falta un dato o la fruta no es mayor que 0."""
    fruta = datos.get("fruta_t")
    resultado = {}
    for clave, (_, kg) in RENDIMIENTOS.items():
        valor = datos.get(kg)
        resultado[clave] = valor / fruta if _hay(fruta) and fruta > 0 and _hay(valor) else None
    return resultado


def _aprobados(sesion, planta_id: int, desde=None, hasta=None) -> pd.DataFrame:
    consulta = select(RegistroProduccion).where(RegistroProduccion.planta_id == planta_id,
                                                RegistroProduccion.estado == APROBADO)
    if desde is not None:
        consulta = consulta.where(RegistroProduccion.fecha_produccion >= desde)
    if hasta is not None:
        consulta = consulta.where(RegistroProduccion.fecha_produccion < hasta)
    return pd.DataFrame([{"fecha_produccion": r.fecha_produccion, "turno_id": r.turno_id, "fruta_t": r.fruta_t,
                          "aceite_envasado_kg": r.aceite_envasado_kg, "jugo_conc_kg": r.jugo_conc_kg}
                         for r in sesion.scalars(consulta)],
                        columns=["fecha_produccion", "turno_id", "fruta_t", "aceite_envasado_kg", "jugo_conc_kg"])


def promedio_ultimas_semanas(sesion, planta_id: int, dia, dias: int = DIAS_PROMEDIO) -> dict:
    """Rendimientos de los `dias` días de producción anteriores a `dia`, solo con registros aprobados, ponderados:
    Σ kg / Σ MT de los registros que tienen los dos datos. {rendimiento: valor o None, "n_<rendimiento>": turnos}."""
    tabla = _aprobados(sesion, planta_id, dia - timedelta(days=dias), dia)
    resultado = {}
    for clave, (_, kg) in RENDIMIENTOS.items():
        con_dato = tabla.dropna(subset=["fruta_t", kg])
        con_dato = con_dato[con_dato["fruta_t"] > 0]
        resultado[clave] = con_dato[kg].sum() / con_dato["fruta_t"].sum() if not con_dato.empty else None
        resultado[f"n_{clave}"] = len(con_dato.drop_duplicates(["fecha_produccion", "turno_id"]))
    return resultado


def percentiles_rendimientos(sesion, planta_id: int, minimo: int = N_MINIMO_PERCENTILES) -> dict:
    """{rendimiento: (p0,5, p99,5)} de los registros aprobados (uno por registro), si hay al menos `minimo`."""
    tabla = _aprobados(sesion, planta_id)
    resultado = {}
    for clave, (_, kg) in RENDIMIENTOS.items():
        con_dato = tabla.dropna(subset=["fruta_t", kg])
        valores = (con_dato[kg] / con_dato["fruta_t"])[con_dato["fruta_t"] > 0]
        if len(valores) >= minimo:
            resultado[clave] = tuple(np.percentile(valores, PERCENTILES))
    return resultado


# ---------------------------------------------------------------- validación

def buscar_registro(sesion, planta_id: int, dia, turno_id: int, proceso_id: int | None) -> RegistroProduccion | None:
    """El registro de planta + fecha de producción + turno + proceso, si existe."""
    consulta = select(RegistroProduccion).where(
        RegistroProduccion.planta_id == planta_id, RegistroProduccion.fecha_produccion == dia,
        RegistroProduccion.turno_id == turno_id)
    consulta = consulta.where(RegistroProduccion.proceso_id.is_(None) if proceso_id is None
                              else RegistroProduccion.proceso_id == proceso_id)
    return sesion.scalars(consulta).first()


def validar(datos: dict, existente_id: int | None = None, editando_id: int | None = None,
            percentiles: dict | None = None) -> tuple[list[str], list[str]]:
    """(errores, avisos) de un registro. existente_id: el registro que ya hay para ese turno y proceso (si hay);
    editando_id: el que se está editando (no choca consigo mismo)."""
    errores, avisos = [], []
    for campo in NUMERICOS:
        if _hay(datos.get(campo)) and datos[campo] < 0:
            errores.append(f"{CAMPOS[campo].capitalize()} no puede ser negativo (dice {datos[campo]}).")
    if not _hay(datos.get("fruta_t")):
        errores.append("Falta la fruta procesada.")
    elif datos["fruta_t"] == 0:
        errores.append("La fruta procesada tiene que ser mayor que 0.")
    if existente_id is not None and existente_id != editando_id:
        errores.append("Ese turno y proceso ya están cargados: editá el registro existente.")
    for clave, valor in calcular(datos).items():
        limites = (percentiles or {}).get(clave)
        if valor is not None and limites and not limites[0] <= valor <= limites[1]:
            avisos.append(f"{RENDIMIENTOS[clave][0]} {valor:.2f} kg/MT fuera de lo habitual ({limites[0]:.2f}–"
                          f"{limites[1]:.2f}): ¿está bien tipeado?")
    return errores, avisos


def _valores(datos: dict) -> dict:
    """Solo los CAMPOS, con los vacíos como None y el comentario sin espacios de más."""
    valores = {campo: (datos.get(campo) if _hay(datos.get(campo)) else None) for campo in CAMPOS}
    valores["comentario"] = (valores["comentario"] or "").strip() or None
    return valores


# ---------------------------------------------------------------- guardar, editar, aprobar

def guardar_borrador(sesion, planta: Planta, datos: dict, usuario: str, ahora: datetime) -> RegistroProduccion:
    """Crea el registro en "borrador" (origen "app"). datos: dia, turno (nombre), proceso_id y los CAMPOS. Error si
    validar() encuentra errores o falta el usuario."""
    if not (usuario or "").strip():
        raise ValueError("Falta quién carga el registro.")
    turno = next((t for t in planta.turnos if t.nombre == datos.get("turno")), None)
    if turno is None or datos.get("dia") is None:
        raise ValueError("Faltan la fecha de producción o el turno.")
    existente = buscar_registro(sesion, planta.id, datos["dia"], turno.id, datos.get("proceso_id"))
    errores, _ = validar(datos, existente.id if existente else None)
    if errores:
        raise ValueError("No se puede guardar: " + " ".join(errores))
    registro = RegistroProduccion(planta_id=planta.id, fecha_produccion=datos["dia"], turno_id=turno.id,
                                  proceso_id=datos.get("proceso_id"), estado=BORRADOR, origen_carga=APP,
                                  cargado_por=usuario.strip(), cargado_en=ahora, **_valores(datos))
    sesion.add(registro)
    sesion.commit()
    return registro


def _registro(sesion, registro_id: int) -> RegistroProduccion:
    registro = sesion.get(RegistroProduccion, registro_id)
    if registro is None:
        raise ValueError(f"No existe el registro de producción {registro_id}.")
    return registro


def editar_registro(sesion, registro_id: int, cambios: dict, usuario: str, ahora: datetime,
                    motivo: str | None = None) -> int:
    """Corrige los CAMPOS de un registro. Borrador: sin motivo ni historial. Aprobado: motivo obligatorio, un
    registro de Cambio por campo y sigue aprobado. Devuelve cuántos campos cambiaron."""
    registro = _registro(sesion, registro_id)
    exigir_permiso(usuario, registro)
    ajenos = set(cambios) - set(CAMPOS)
    if ajenos:
        raise ValueError(f"No se pueden editar: {', '.join(sorted(ajenos))}.")
    nuevos = _valores({c: getattr(registro, c) for c in CAMPOS} | cambios)
    reales = {c: v for c, v in nuevos.items() if c in cambios and v != getattr(registro, c)}
    if not reales:
        return 0
    errores, _ = validar(nuevos)
    if errores:
        raise ValueError("No se puede guardar: " + " ".join(errores))
    if registro.estado == APROBADO:
        if not (motivo or "").strip():
            raise ValueError("Un registro aprobado se corrige con motivo.")
        grupo = nuevo_grupo()
        for campo, valor in reales.items():
            registrar(sesion, TABLA, registro.id, "edición", motivo, usuario, ahora, grupo, campo=campo,
                      anterior=getattr(registro, campo), nuevo=valor)
    for campo, valor in reales.items():
        setattr(registro, campo, valor)
    sesion.commit()
    return len(reales)


def aprobar(sesion, registro_ids: list[int], usuario: str, ahora: datetime) -> int:
    """Aprueba los borradores (aprobado_por, aprobado_en; queda en Cambio). Error si alguno no es borrador."""
    if not (usuario or "").strip():
        raise ValueError("Falta quién aprueba.")
    registros = [_registro(sesion, i) for i in registro_ids]
    grupo = nuevo_grupo()
    for registro in registros:
        exigir_aprobacion(usuario, registro)
        if registro.estado != BORRADOR:
            raise ValueError(f"El registro {registro.id} no está en borrador.")
        errores, _ = validar({c: getattr(registro, c) for c in CAMPOS})
        if errores:
            raise ValueError(f"No se puede aprobar el registro {registro.id}: " + " ".join(errores))
        registrar(sesion, TABLA, registro.id, "estado", "aprobado", usuario, ahora, grupo, campo="estado",
                  anterior=BORRADOR, nuevo=APROBADO)
        registro.estado, registro.aprobado_por, registro.aprobado_en = APROBADO, usuario, ahora
    sesion.commit()
    return len(registros)


def devolver_a_borrador(sesion, registro_id: int, motivo: str, usuario: str, ahora: datetime) -> RegistroProduccion:
    """Vuelve un aprobado a borrador (deja de contar en los KPI). Motivo obligatorio; queda en Cambio."""
    if not (motivo or "").strip():
        raise ValueError("El motivo es obligatorio.")
    registro = _registro(sesion, registro_id)
    exigir_aprobacion(usuario, registro)
    if registro.estado != APROBADO:
        raise ValueError(f"El registro {registro.id} no está aprobado.")
    registrar(sesion, TABLA, registro.id, "estado", motivo, usuario, ahora, nuevo_grupo(), campo="estado",
              anterior=APROBADO, nuevo=BORRADOR)
    registro.estado, registro.aprobado_por, registro.aprobado_en = BORRADOR, None, None
    sesion.commit()
    return registro


# ---------------------------------------------------------------- lista

def lista_registros(sesion, planta_id: int, estados=None, desde=None, hasta=None) -> pd.DataFrame:
    """Registros de producción de la planta, del más nuevo al más viejo, con sus rendimientos. Filtros: estados y
    días de producción desde/hasta (incluidos)."""
    consulta = (select(RegistroProduccion, Turno.nombre, Proceso).join(Turno, RegistroProduccion.turno_id == Turno.id)
                .outerjoin(Proceso, RegistroProduccion.proceso_id == Proceso.id)
                .where(RegistroProduccion.planta_id == planta_id))
    if estados:
        consulta = consulta.where(RegistroProduccion.estado.in_(list(estados)))
    if desde is not None:
        consulta = consulta.where(RegistroProduccion.fecha_produccion >= desde)
    if hasta is not None:
        consulta = consulta.where(RegistroProduccion.fecha_produccion <= hasta)
    planta = sesion.get(Planta, planta_id)
    filas = []
    for r, turno, proceso in sesion.execute(consulta).all():
        filas.append({"id": r.id, "fecha_produccion": r.fecha_produccion, "turno": turno,
                      "_inicio": inicio_de_turno(planta, r.fecha_produccion, r.turno),
                      "proceso": f"{proceso.temporada} · {proceso.numero}" if proceso else "",
                      **{c: getattr(r, c) for c in CAMPOS}, **calcular({c: getattr(r, c) for c in CAMPOS}),
                      "estado": r.estado, "origen_carga": r.origen_carga, "cargado_por": r.cargado_por,
                      "aprobado_por": r.aprobado_por, "aprobado_en": r.aprobado_en})
    columnas = ["id", "fecha_produccion", "turno", "proceso", *CAMPOS, *RENDIMIENTOS, "estado", "origen_carga",
                "cargado_por", "aprobado_por", "aprobado_en"]
    tabla = pd.DataFrame(filas, columns=columnas + ["_inicio"])
    return tabla.sort_values("_inicio", ascending=False, ignore_index=True)[columnas]
