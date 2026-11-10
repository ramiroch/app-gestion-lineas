"""Lista, edición, borrado, restauración y cambio de hora de los datos de laboratorio y operación (tarea 19).

Sin Streamlit. Datos: AnalisisScott ("analisis_scott") y LecturaOperacion ("lectura_operacion") de los eBOE.
- lista_rondas(): una fila por ronda (fecha y hora) y eBOE, con resultados, marcas de revisar, alertas y si se editó.
- datos_de_ronda(): los datos de una ronda con sus campos editables.
- editar_dato() / editar_datos(), borrar_dato(), borrar_ronda(), restaurar(), cambiar_hora_ronda(): motivo obligatorio y permiso
  (core.cambios.puede_editar, el único punto de control). Todo queda en Cambio; un borrado guarda la fila completa
  (nunca se pierde el dato original) y se puede restaurar.
- Después de cada cambio: se recalcula (los resultados salen siempre de los datos crudos), se actualiza la marca de
  entrada repetida y se reevalúan las alertas de los turnos tocados (reevaluar_turnos). Una alerta que se queda sin
  datos que la sostengan no se borra: se cierra sola con el comentario DATO_CORREGIDO.
"""

from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select

from core.alertas import CERRADA, HISTORICO
from core.calculos import aceite_en_emulsion, aceite_en_fruta
from core.cambios import (COLUMNA_CLAVE, SISTEMA, TABLAS, clave_de, desde_fila, exigir_permiso, fila_completa,
                          nuevo_grupo, registrar)
from core.carga_scott import (CALIDADES, DIAS_PREVIOS, PROGRAMAS, PUNTOS, RALLADOS, cambios_de_alertas,
                              chequeo_inmediato, estado_alertas, evaluar_turno, series_recientes)
from core.models import (AlertaDato, AnalisisScott, Cambio, Equipo, LecturaOperacion, Linea, MuestraCentrifuga,
                         Planta, Proceso, PuntoMuestreo, TurnoEvaluado)
from core.repetidas import marcar_entradas_repetidas
from core.turnos import fecha_produccion, fin_de_turno, inicio_de_turno, turno_de

DATO_CORREGIDO = "dato corregido"
LUGAR_DE_TIPO = {tipo: lugar for lugar, tipo in PUNTOS.items()}
# Campos que se pueden corregir, con su nombre para mostrar
CAMPOS_EDITABLES = {
    "analisis_scott": {"peso_muestra_g": "g", "vol_ml": "mL", "normalidad": "normalidad",
                       "peso_fruta_kg": "peso de fruta (kg)", "n_frutos": "n.º de frutos", "calidad": "calidad",
                       "rallado": "rallado", "brix": "°Brix", "ph": "pH", "analista": "analista", "fruta": "fruta",
                       "proceso_id": "proceso"},
    "lectura_operacion": {"programa": "programa", "tph": "MTPH", "caudal_fase_pesada": "caudal de fase pesada (m³/h)",
                          "operador": "operador"},
}


def _vacio(valor) -> bool:
    return valor is None or (isinstance(valor, float) and pd.isna(valor)) or valor == ""


def _motivo(motivo: str) -> str:
    if not (motivo or "").strip():
        raise ValueError("El motivo es obligatorio.")
    return motivo.strip()


def _dato(sesion, tabla: str, dato_id: int):
    if tabla not in TABLAS:
        raise ValueError(f'Tabla "{tabla}" no válida: {", ".join(TABLAS)}.')
    dato = sesion.get(TABLAS[tabla], dato_id)
    if dato is None:
        raise ValueError(f"No existe el dato {dato_id} de {tabla}.")
    return dato


# ---------------------------------------------------------------- lectura

def _ronda_objetos(sesion, planta: Planta, fecha_hora: datetime) -> list[tuple]:
    """[(tabla, objeto)] de los análisis y lecturas de los eBOE de la planta a esa fecha y hora."""
    analisis = sesion.scalars(select(AnalisisScott).join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo)
                              .join(Equipo.linea).where(Linea.planta_id == planta.id, Equipo.tipo == "BOE",
                                                        AnalisisScott.fecha_hora == fecha_hora)).all()
    lecturas = sesion.scalars(select(LecturaOperacion).join(LecturaOperacion.equipo).join(Equipo.linea).where(
        Linea.planta_id == planta.id, Equipo.tipo == "BOE", LecturaOperacion.fecha_hora == fecha_hora)).all()
    return [("analisis_scott", a) for a in analisis] + [("lectura_operacion", l) for l in lecturas]


def _resultado(planta: Planta, analisis: AnalisisScott, lugar: str):
    """Aceite en fruta (kg/MT) o en emulsión (%) desde los datos crudos; None si no se puede calcular."""
    funcion = aceite_en_emulsion if lugar == "emulsion" else aceite_en_fruta
    try:
        return funcion(planta, analisis.vol_ml, analisis.blanco_ml, analisis.normalidad, analisis.peso_muestra_g)
    except ValueError:
        return None


def datos_de_ronda(sesion, planta: Planta, fecha_hora: datetime) -> pd.DataFrame:
    """Una fila por dato de la ronda: tabla, id, eboe, lugar ("entrada", "salida", "emulsion" u "operacion"), los
    campos editables de su tabla, resultado (aceite) y revisar. Ordenada por eBOE y lugar."""
    orden = {"entrada": 0, "salida": 1, "emulsion": 2, "operacion": 3}
    filas = []
    for tabla, dato in _ronda_objetos(sesion, planta, fecha_hora):
        if tabla == "analisis_scott":
            lugar = LUGAR_DE_TIPO.get(dato.punto_muestreo.tipo)
            fila = {"eboe": dato.punto_muestreo.equipo.numero, "lugar": lugar,
                    "resultado": _resultado(planta, dato, lugar), "revisar": dato.revisar}
        else:
            fila = {"eboe": dato.equipo.numero, "lugar": "operacion", "resultado": None, "revisar": None}
        fila |= {"tabla": tabla, "id": dato.id} | {c: getattr(dato, c) for c in CAMPOS_EDITABLES[tabla]}
        filas.append(fila)
    tabla = pd.DataFrame(filas)
    if tabla.empty:
        return tabla
    return tabla.sort_values(["eboe", "lugar"], key=lambda c: c.map(orden) if c.name == "lugar" else c,
                             ignore_index=True)


def lista_rondas(sesion, planta: Planta, desde=None, hasta=None, eboes=None, turnos=None, lugares=None,
                 analistas=None, solo_revisar: bool = False, solo_editados: bool = False) -> pd.DataFrame:
    """Una fila por ronda (fecha y hora) y eBOE, en orden de tiempo (la más nueva primero).

    Filtros: días de producción desde/hasta (incluidos), eBOE, turnos, lugares (la ronda tiene ese punto), analistas,
    solo con marca de revisar y solo editados (con historial en Cambio). Columnas: fecha_hora, fecha_produccion,
    turno, proceso, eboe, analista, g y mL de entrada, salida y emulsión, aceite_entrada, aceite_salida (kg/MT),
    eficiencia (%), aceite_emulsion (%), brix, ph, programa, tph, revisar (motivos), editado, alertas (cantidad).
    """
    inicio = datetime.combine(desde, datetime.min.time()) if desde else None
    fin = datetime.combine(hasta, datetime.min.time()) + timedelta(days=2) if hasta else None
    consulta = (select(AnalisisScott, Equipo.numero, PuntoMuestreo.tipo, Proceso)
                .join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).join(Equipo.linea)
                .join(AnalisisScott.proceso).where(Linea.planta_id == planta.id, Equipo.tipo == "BOE"))
    lecturas = (select(LecturaOperacion, Equipo.numero).join(LecturaOperacion.equipo).join(Equipo.linea)
                .where(Linea.planta_id == planta.id, Equipo.tipo == "BOE"))
    if inicio:
        consulta, lecturas = (consulta.where(AnalisisScott.fecha_hora >= inicio),
                              lecturas.where(LecturaOperacion.fecha_hora >= inicio))
    if fin:
        consulta, lecturas = (consulta.where(AnalisisScott.fecha_hora < fin),
                              lecturas.where(LecturaOperacion.fecha_hora < fin))
    editados = {(t, i) for t, i in sesion.execute(select(Cambio.tabla, Cambio.dato_id)).all()}
    con_alerta = {}
    for dato_id, alerta_id in sesion.execute(select(AlertaDato.dato_id, AlertaDato.alerta_id)
                                             .where(AlertaDato.tabla == "analisis_scott")).all():
        con_alerta.setdefault(dato_id, set()).add(alerta_id)

    rondas = {}
    for a, numero, tipo, proceso in sesion.execute(consulta).all():
        lugar = LUGAR_DE_TIPO[tipo]
        fila = rondas.setdefault((a.fecha_hora, numero), {
            "fecha_hora": a.fecha_hora, "eboe": numero, "proceso": f"{proceso.temporada} · {proceso.numero}",
            "analistas": set(), "lugares": set(), "revisar": [], "editado": False, "alertas": set()})
        fila["analistas"].add(a.analista)
        fila["lugares"].add(lugar)
        fila[f"{lugar}_g"], fila[f"{lugar}_ml"] = a.peso_muestra_g, a.vol_ml
        fila[f"aceite_{lugar}"] = _resultado(planta, a, lugar)
        if lugar == "emulsion":
            fila["brix"], fila["ph"] = a.brix, a.ph
        if a.revisar:
            fila["revisar"].append(a.revisar)
        fila["editado"] |= ("analisis_scott", a.id) in editados
        fila["alertas"] |= con_alerta.get(a.id, set())
    for lectura, numero in sesion.execute(lecturas).all():
        fila = rondas.get((lectura.fecha_hora, numero))
        if fila is not None:
            fila["programa"], fila["tph"] = lectura.programa, lectura.tph
            fila["editado"] |= ("lectura_operacion", lectura.id) in editados
    columnas = ["fecha_hora", "fecha_produccion", "turno", "proceso", "eboe", "analista", "entrada_g", "entrada_ml",
                "aceite_entrada", "salida_g", "salida_ml", "aceite_salida", "eficiencia", "emulsion_g", "emulsion_ml",
                "aceite_emulsion", "brix", "ph", "programa", "tph", "revisar", "editado", "alertas"]
    filas = []
    for fila in rondas.values():
        dia, turno = fecha_produccion(planta, fila["fecha_hora"]), turno_de(planta, fila["fecha_hora"]).nombre
        if (desde and dia < desde) or (hasta and dia > hasta) or (eboes and fila["eboe"] not in eboes) \
                or (turnos and turno not in turnos) or (lugares and not fila["lugares"] & set(lugares)) \
                or (analistas and not fila["analistas"] & set(analistas)) or (solo_revisar and not fila["revisar"]) \
                or (solo_editados and not fila["editado"]):
            continue
        entrada, salida = fila.get("aceite_entrada"), fila.get("aceite_salida")
        filas.append(fila | {"fecha_produccion": dia, "turno": turno,
                             "analista": ", ".join(sorted(fila["analistas"])),
                             "eficiencia": 100 * (1 - salida / entrada) if entrada and salida is not None else None,
                             "revisar": "; ".join(sorted(set(fila["revisar"]))), "alertas": len(fila["alertas"])})
    tabla = pd.DataFrame(filas).reindex(columns=columnas)
    return tabla.sort_values(["fecha_hora", "eboe"], ascending=[False, True], ignore_index=True)


# ---------------------------------------------------------------- validación de valores

def validar_valores(sesion, planta: Planta, tabla: str, valores: dict) -> list[str]:
    """Errores de los valores de un dato (todos sus campos, ya con los cambios)."""
    errores = []
    if tabla == "analisis_scott":
        g, ml, n = valores.get("peso_muestra_g"), valores.get("vol_ml"), valores.get("normalidad")
        if _vacio(g) or g <= 0:
            errores.append(f"Los g tienen que ser mayores que 0 (dice {g}).")
        if _vacio(n) or n <= 0:
            errores.append(f"La normalidad tiene que ser mayor que 0 (dice {n}).")
        if _vacio(ml) or ml < (valores.get("blanco_ml") or 0):
            errores.append(f"Los mL ({ml}) no pueden faltar ni ser menores que el blanco ({valores.get('blanco_ml')}).")
        if not _vacio(valores.get("ph")) and not 0 <= valores["ph"] <= 14:
            errores.append(f"El pH tiene que estar entre 0 y 14 (dice {valores['ph']}).")
        for campo in ("brix", "peso_fruta_kg", "n_frutos"):
            if not _vacio(valores.get(campo)) and valores[campo] < 0:
                errores.append(f"{CAMPOS_EDITABLES[tabla][campo]} no puede ser negativo.")
        if not _vacio(valores.get("calidad")) and valores["calidad"] not in CALIDADES:
            errores.append(f"Calidad no válida: usá {', '.join(CALIDADES)}.")
        if not _vacio(valores.get("rallado")) and valores["rallado"] not in RALLADOS:
            errores.append(f"Rallado no válido: usá {', '.join(RALLADOS)}.")
        if not (valores.get("analista") or "").strip():
            errores.append("Falta el analista.")
        proceso = sesion.get(Proceso, valores.get("proceso_id")) if valores.get("proceso_id") else None
        if proceso is None or proceso.planta_id != planta.id:
            errores.append("El proceso no es de esta planta.")
    else:
        if not _vacio(valores.get("programa")) and valores["programa"] not in PROGRAMAS:
            errores.append(f"Programa no válido: {valores['programa']}.")
        for campo in ("tph", "caudal_fase_pesada"):
            if not _vacio(valores.get(campo)) and valores[campo] < 0:
                errores.append(f"{CAMPOS_EDITABLES[tabla][campo]} no puede ser negativo.")
    return errores


# ---------------------------------------------------------------- cambios

def editar_dato(sesion, planta: Planta, tabla: str, dato_id: int, cambios: dict, motivo: str, usuario: str,
                ahora: datetime) -> dict:
    """Corrige campos de un dato (solo CAMPOS_EDITABLES): editar_datos() con un solo dato."""
    return editar_datos(sesion, planta, [(tabla, dato_id, cambios)], motivo, usuario, ahora)


def editar_datos(sesion, planta: Planta, correcciones: list[tuple], motivo: str, usuario: str,
                 ahora: datetime) -> dict:
    """Corrige varios datos de una vez: correcciones = [(tabla, dato_id, {campo: valor nuevo})], solo
    CAMPOS_EDITABLES. Un registro de Cambio por campo cambiado (valor anterior y nuevo), todos en un mismo grupo.
    Primero valida todo (si algo falla, no cambia nada); después recalcula, marca entradas repetidas y reevalúa una
    sola vez los turnos tocados. Devuelve {"cambios": n.º de campos, "alertas": alertas nuevas o actualizadas,
    "grupo"}."""
    motivo = _motivo(motivo)
    pendientes = []
    for tabla, dato_id, cambios in correcciones:
        dato = _dato(sesion, tabla, dato_id)
        exigir_permiso(usuario, dato)
        ajenos = set(cambios) - set(CAMPOS_EDITABLES[tabla])
        if ajenos:
            raise ValueError(f"No se pueden editar: {', '.join(sorted(ajenos))}.")
        reales = {campo: None if _vacio(valor) else valor for campo, valor in cambios.items()
                  if (None if _vacio(valor) else valor) != getattr(dato, campo)}
        if not reales:
            continue
        valores = {c.key: getattr(dato, c.key) for c in dato.__table__.columns} | reales
        errores = validar_valores(sesion, planta, tabla, valores)
        if errores:
            raise ValueError(f"No se puede guardar el dato {dato_id}: " + " ".join(errores))
        pendientes.append((tabla, dato, reales))
    if not pendientes:
        return {"cambios": 0, "alertas": [], "grupo": None}
    grupo = nuevo_grupo()
    for tabla, dato, reales in pendientes:
        clave = clave_de(tabla, dato)
        for campo, valor in reales.items():
            registrar(sesion, tabla, dato.id, "edición", motivo, usuario, ahora, grupo, clave, campo,
                      getattr(dato, campo), valor)
            setattr(dato, campo, valor)
    momentos = sorted({dato.fecha_hora for _, dato, _ in pendientes})
    alertas = _despues(sesion, planta, momentos, set(), usuario, ahora)
    return {"cambios": sum(len(r) for _, _, r in pendientes), "alertas": alertas, "grupo": grupo}


def borrar_dato(sesion, planta: Planta, tabla: str, dato_id: int, motivo: str, usuario: str,
                ahora: datetime) -> dict:
    """Borra un dato y guarda la fila completa en Cambio (se puede restaurar). Después reevalúa el turno."""
    motivo = _motivo(motivo)
    dato = _dato(sesion, tabla, dato_id)
    exigir_permiso(usuario, dato)
    grupo = nuevo_grupo()
    momento = dato.fecha_hora
    registrar(sesion, tabla, dato.id, "borrado", motivo, usuario, ahora, grupo, clave_de(tabla, dato),
              anterior=fila_completa(dato))
    sesion.delete(dato)
    return {"alertas": _despues(sesion, planta, [momento], {(tabla, dato_id)}, usuario, ahora), "grupo": grupo}


def borrar_ronda(sesion, planta: Planta, fecha_hora: datetime, motivo: str, usuario: str, ahora: datetime) -> dict:
    """Borra todos los análisis y lecturas de la ronda (un mismo grupo en Cambio, cada uno con su fila completa)."""
    motivo = _motivo(motivo)
    datos = _ronda_objetos(sesion, planta, fecha_hora)
    if not datos:
        raise ValueError(f"No hay datos en la ronda del {fecha_hora:%d/%m/%Y %H:%M}.")
    for _, dato in datos:
        exigir_permiso(usuario, dato)
    grupo = nuevo_grupo()
    for tabla, dato in datos:
        registrar(sesion, tabla, dato.id, "borrado", motivo, usuario, ahora, grupo, clave_de(tabla, dato),
                  anterior=fila_completa(dato))
        sesion.delete(dato)
    quitados = {(tabla, dato.id) for tabla, dato in datos}
    return {"borrados": len(datos), "alertas": _despues(sesion, planta, [fecha_hora], quitados, usuario, ahora),
            "grupo": grupo}


def restaurar(sesion, planta: Planta, cambio_ids: list[int], motivo: str, usuario: str, ahora: datetime) -> dict:
    """Vuelve a crear los datos borrados (registros de Cambio "borrado") con sus valores originales, si no chocan con
    otro dato en ese punto (o equipo) y hora y no se restauraron antes. Mantiene el id original si está libre. Se
    registra en Cambio ("restaurado", con el registro de borrado en valor_anterior) y se reevalúa el turno."""
    motivo = _motivo(motivo)
    grupo, nuevos = nuevo_grupo(), []
    for cambio_id in cambio_ids:
        cambio = sesion.get(Cambio, cambio_id)
        if cambio is None or cambio.accion != "borrado" or cambio.tabla not in TABLAS:
            raise ValueError(f"El registro {cambio_id} no es un borrado que se pueda restaurar.")
        ya = sesion.scalars(select(Cambio).where(Cambio.accion == "restaurado", Cambio.campo == "cambio",
                                                 Cambio.valor_anterior == str(cambio_id))).first()
        if ya is not None:
            raise ValueError(f"El dato del registro {cambio_id} ya se restauró el {ya.cambiado_en:%d/%m/%Y %H:%M}.")
        modelo = TABLAS[cambio.tabla]
        valores = desde_fila(modelo, cambio.valor_anterior)
        columna = getattr(modelo, COLUMNA_CLAVE[cambio.tabla])
        choca = sesion.scalars(select(modelo).where(columna == valores[COLUMNA_CLAVE[cambio.tabla]],
                                                    modelo.fecha_hora == valores["fecha_hora"])).first()
        if choca is not None:
            raise ValueError(f"No se puede restaurar: ya hay otro dato en ese punto a las "
                             f"{valores['fecha_hora']:%d/%m/%Y %H:%M}.")
        if sesion.get(modelo, valores["id"]) is not None:
            valores.pop("id")
        nuevo = modelo(**valores)
        exigir_permiso(usuario, nuevo)
        sesion.add(nuevo)
        sesion.flush()
        registrar(sesion, cambio.tabla, nuevo.id, "restaurado", motivo, usuario, ahora, grupo,
                  clave_de(cambio.tabla, nuevo), "cambio", cambio_id, None)
        nuevos.append(nuevo)
    momentos = sorted({n.fecha_hora for n in nuevos})
    return {"restaurados": len(nuevos), "alertas": _despues(sesion, planta, momentos, set(), usuario, ahora),
            "grupo": grupo}


def cambiar_hora_ronda(sesion, planta: Planta, fecha_hora: datetime, nueva: datetime, motivo: str, usuario: str,
                       ahora: datetime) -> dict:
    """Cambia la fecha y hora de todos los datos de la ronda, si no choca con otra ronda (ningún dato de los eBOE
    de la planta a esa hora). Un registro de Cambio por dato; se reevalúan el turno viejo y el nuevo."""
    motivo = _motivo(motivo)
    if nueva == fecha_hora:
        raise ValueError("La hora nueva es igual a la de la ronda.")
    datos = _ronda_objetos(sesion, planta, fecha_hora)
    if not datos:
        raise ValueError(f"No hay datos en la ronda del {fecha_hora:%d/%m/%Y %H:%M}.")
    if _ronda_objetos(sesion, planta, nueva):
        raise ValueError(f"Ya hay una ronda el {nueva:%d/%m/%Y %H:%M}: elegí otra hora.")
    for _, dato in datos:
        exigir_permiso(usuario, dato)
    grupo = nuevo_grupo()
    for tabla, dato in datos:
        registrar(sesion, tabla, dato.id, "hora", motivo, usuario, ahora, grupo, clave_de(tabla, dato), "fecha_hora",
                  fecha_hora, nueva)
        dato.fecha_hora = nueva
    return {"datos": len(datos), "alertas": _despues(sesion, planta, [fecha_hora, nueva], set(), usuario, ahora),
            "grupo": grupo}


# ---------------------------------------------------------------- después de cambiar: alertas

def _despues(sesion, planta: Planta, momentos: list[datetime], quitados: set, usuario: str,
             ahora: datetime) -> list[dict]:
    """Marca de entradas repetidas y reevaluación de los turnos tocados; guarda todo."""
    sesion.flush()
    marcar_entradas_repetidas(sesion, planta.id, guardar=False)
    alertas = reevaluar_turnos(sesion, planta, momentos, quitados, usuario, ahora)
    sesion.commit()
    return alertas


def reevaluar_turnos(sesion, planta: Planta, momentos: list[datetime], quitados: set, usuario: str,
                     ahora: datetime) -> list[dict]:
    """Reevalúa las alertas de los turnos de producción de esos momentos, después de corregir o borrar datos.

    En cada turno: se sacan de sus alertas los datos de ese turno (y los borrados, `quitados`); el turno se vuelve a
    evaluar (completo si ya estaba evaluado, con TurnoEvaluado; si no, solo el chequeo inmediato), lo que vuelve a
    sumar los datos a las alertas que se siguen violando; después se ajusta cada alerta tocada (inicio, fin y
    turnos con los datos que le quedan) o, si quedó sin datos, se cierra sola con DATO_CORREGIDO. Un turno con todos
    sus datos importados se reevalúa como "histórico". Devuelve las alertas nuevas o actualizadas.
    """
    antes = estado_alertas(sesion)
    nombres = {t.nombre: t for t in planta.turnos}
    turnos = sorted({(fecha_produccion(planta, m), turno_de(planta, m).nombre) for m in momentos},
                    key=lambda par: inicio_de_turno(planta, par[0], nombres[par[1]]))
    for dia, nombre in turnos:
        turno = nombres[nombre]
        inicio, fin = inicio_de_turno(planta, dia, turno), fin_de_turno(planta, dia, turno)
        del_turno = sesion.scalars(select(AnalisisScott).join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo)
                                   .join(Equipo.linea).where(Linea.planta_id == planta.id, Equipo.tipo == "BOE",
                                                             AnalisisScott.fecha_hora >= inicio,
                                                             AnalisisScott.fecha_hora < fin)).all()
        ids = {a.id for a in del_turno} | {i for t, i in quitados if t == "analisis_scott"}
        enlaces = sesion.scalars(select(AlertaDato).where(AlertaDato.tabla == "analisis_scott",
                                                          AlertaDato.dato_id.in_(ids))).all() if ids else []
        afectadas = {e.alerta for e in enlaces}
        originales = {a.id: (a.fecha_hora, a.fin) for a in afectadas}
        for enlace in enlaces:
            enlace.alerta.datos.remove(enlace)
            sesion.delete(enlace)
        for alerta in afectadas:          # el turno queda dentro del episodio: si se sigue violando, se vuelve a sumar
            alerta.fecha_hora = min(alerta.fecha_hora, inicio)
            alerta.fin = max(alerta.fin or alerta.fecha_hora, fin - timedelta(minutes=1))
        sesion.flush()
        evaluado = sesion.scalars(select(TurnoEvaluado).where(
            TurnoEvaluado.planta_id == planta.id, TurnoEvaluado.fecha_produccion == dia,
            TurnoEvaluado.turno_id == turno.id)).first() is not None
        if del_turno and evaluado:
            historico = all(a.origen for a in del_turno)
            series = series_recientes(sesion, planta, inicio - timedelta(days=DIAS_PREVIOS))
            evaluar_turno(sesion, planta, series, dia, nombre, usuario, historico)
        elif del_turno:
            chequeo_inmediato(sesion, planta, del_turno[0].fecha_hora, usuario)
        for alerta in afectadas:
            _ajustar_episodio(sesion, planta, alerta, dia, nombre, originales[alerta.id], usuario, ahora)
        sesion.flush()
    return cambios_de_alertas(sesion, antes)


def _agregar_comentario(alerta, texto: str) -> None:
    if texto not in (alerta.comentario or ""):
        alerta.comentario = texto if not alerta.comentario else f"{alerta.comentario}; {texto}"


def _ajustar_episodio(sesion, planta: Planta, alerta, dia, turno: str, original: tuple, usuario: str,
                      ahora: datetime) -> None:
    """Inicio, fin y turnos de la alerta según los datos que le quedan; sin datos, se cierra sola."""
    momentos = []
    for enlace in alerta.datos:
        modelo = AnalisisScott if enlace.tabla == "analisis_scott" else MuestraCentrifuga
        dato = sesion.get(modelo, enlace.dato_id)
        if dato is not None:
            momentos.append(dato.fecha_hora)
    if not momentos:
        alerta.fecha_hora, alerta.fin = original
        alerta.episodio_abierto = False
        anterior = alerta.comentario
        _agregar_comentario(alerta, DATO_CORREGIDO)
        grupo = nuevo_grupo()
        if alerta.estado not in (CERRADA, HISTORICO):
            registrar(sesion, "alerta", alerta.id, "estado", DATO_CORREGIDO, SISTEMA, ahora, grupo, campo="estado",
                      anterior=alerta.estado, nuevo=CERRADA)
            alerta.estado, alerta.cerrada_por, alerta.cerrada_en = CERRADA, SISTEMA, ahora
        if alerta.comentario != anterior:
            registrar(sesion, "alerta", alerta.id, "seguimiento", DATO_CORREGIDO, SISTEMA, ahora, grupo,
                      campo="comentario", anterior=anterior, nuevo=alerta.comentario)
        return
    por_turno = {}
    for momento in momentos:
        clave = (fecha_produccion(planta, momento), turno_de(planta, momento))
        por_turno[clave] = min(por_turno.get(clave, momento), momento)
    ordenados = sorted(por_turno.items(), key=lambda par: par[1])
    (primer_dia, primer_turno), alerta.fecha_hora = ordenados[0]
    alerta.fecha_produccion, alerta.turno_id = primer_dia, primer_turno.id
    alerta.fin, alerta.turnos_afectados = ordenados[-1][1], len(ordenados)
    if not any(d == dia and t.nombre == turno for d, t in por_turno):
        _agregar_comentario(alerta, f"{DATO_CORREGIDO} en el turno {dia:%d/%m} {turno}")
