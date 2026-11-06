"""Pruebas de core/models.py y core/crear_base.py."""

from datetime import date, datetime, time

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from core.crear_base import agregar_columnas_nuevas, crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import (AnalisisScott, Cliente, Equipo, LecturaOperacion, LecturaPLC, Linea, MuestraCentrifuga,
                         Planta, Proceso, PuntoMuestreo, RangoReferencia, RegistroProduccion, Turno, texto_origen)

TIPOS_PUNTO = ["fruta entrada", "fruta salida", "emulsión"]


@pytest.fixture
def sesion():
    """Base vacía en memoria, con las tablas creadas. Se descarta al terminar."""
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


def crear_ledesma(sesion):
    """Carga LEDESMA: 1 línea, 3 eBOE y 3 puntos de muestreo por eBOE."""
    cliente = Cliente(nombre="LEDESMA", pais="Argentina")
    planta = Planta(nombre="LEDESMA", pais="Argentina", cliente=cliente, blanco_ml=0.2,
                    factor_aceite_fruta=68.8416, factor_aceite_emulsion=3.45,
                    hora_inicio_dia=time(5, 0))
    linea = Linea(nombre="Línea 1", planta=planta)
    for numero in [1, 2, 3]:
        equipo = Equipo(codigo=f"LED-BOE-0{numero}", nombre=f"eBOE {numero}", tipo="eBOE",
                        numero=numero, linea=linea)
        for tipo in TIPOS_PUNTO:
            PuntoMuestreo(tipo=tipo, nombre=f"eBOE {numero} – {tipo}", equipo=equipo)
    sesion.add(cliente)
    sesion.commit()
    return planta


def test_ledesma_tiene_9_puntos_de_muestreo(sesion):
    crear_ledesma(sesion)

    puntos = sesion.scalars(select(PuntoMuestreo)).all()

    assert len(puntos) == 9


def test_cada_punto_sabe_a_que_eboe_pertenece(sesion):
    crear_ledesma(sesion)

    for numero in [1, 2, 3]:
        equipo = sesion.scalars(select(Equipo).where(Equipo.numero == numero)).one()
        assert equipo.tipo == "eBOE"
        assert len(equipo.puntos_muestreo) == 3
        for punto in equipo.puntos_muestreo:
            assert punto.equipo.numero == numero
            assert punto.nombre.startswith(f"eBOE {numero}")
        assert sorted(p.tipo for p in equipo.puntos_muestreo) == sorted(TIPOS_PUNTO)


def test_planta_guarda_sus_parametros_de_calculo(sesion):
    planta = crear_ledesma(sesion)

    assert planta.blanco_ml == 0.2
    assert planta.factor_aceite_fruta == 68.8416
    assert planta.factor_aceite_emulsion == 3.45
    assert planta.hora_inicio_dia == time(5, 0)
    assert planta.pais == "Argentina"


def test_planta_sin_parametros_queda_vacia(sesion):
    planta = crear_ledesma(sesion)
    nueva = Planta(nombre="Otra", pais="Uruguay", cliente=planta.cliente)
    sesion.add(nueva)
    sesion.commit()

    assert (nueva.blanco_ml, nueva.factor_aceite_fruta, nueva.factor_aceite_emulsion,
            nueva.hora_inicio_dia) == (None, None, None, None)


def test_equipo_nuevo_queda_operativo_por_defecto(sesion):
    crear_ledesma(sesion)

    equipo = sesion.scalars(select(Equipo).where(Equipo.numero == 1)).one()

    assert equipo.estado == "Operativo"


def test_equipo_pendiente_de_instalacion(sesion):
    planta = crear_ledesma(sesion)
    desludger = Equipo(codigo="LED-DES-01", nombre="DESLUDGER N°1", tipo="desludger", numero=1,
                       estado="Pendiente de instalación", linea=planta.lineas[0])
    sesion.add(desludger)
    sesion.commit()

    guardado = sesion.scalars(select(Equipo).where(Equipo.tipo == "desludger")).one()

    assert guardado.estado == "Pendiente de instalación"
    assert guardado.nombre == "DESLUDGER N°1"
    assert guardado.linea.planta.nombre == "LEDESMA"


def test_volumen_por_descarga_opcional_y_mayor_que_cero():
    assert Equipo(codigo="LED-CSE-01", nombre="ESB 130", tipo="Centrífuga secundaria", numero=1).volumen_descarga_l \
        is None
    with pytest.raises(ValueError, match="mayor que 0"):
        Equipo(codigo="LED-CEN-01", nombre="ESE 230 (1)", tipo="Centrífuga primaria", numero=1, volumen_descarga_l=0)


def test_destino_de_la_fase_pesada_y_rango_que_no_genera_alerta(sesion):
    assert Equipo(codigo="LED-CSE-02", nombre="MSD 50", tipo="Centrífuga secundaria", numero=2,
                  destino_fase_pesada="canal (pérdida)").destino_fase_pesada == "canal (pérdida)"
    with pytest.raises(ValueError, match="no válido"):
        Equipo(codigo="LED-CEN-01", nombre="ESE 230", tipo="Centrífuga primaria", numero=1, destino_fase_pesada="río")
    assert Equipo(codigo="LED-PUL-01", nombre="OSD 60", tipo="Pulidora", numero=1,
                  destino_fase_pesada="no aplica").destino_fase_pesada == "no aplica"     # la pulidora no tiene
    planta = crear_ledesma(sesion)
    sesion.add_all([RangoReferencia(planta=planta, variable="CP fase pesada", minimo=2, maximo=3, genera_alerta=False),
                    RangoReferencia(planta=planta, variable="Sólidos centrifugables", maximo=2)])
    sesion.commit()
    alertas = {r.variable: r.genera_alerta for r in sesion.scalars(select(RangoReferencia))}
    assert alertas == {"CP fase pesada": False, "Sólidos centrifugables": True}       # por defecto, sí


def test_estado_invalido_da_error():
    with pytest.raises(ValueError, match="no válido"):
        Equipo(codigo="LED-BOE-09", nombre="eBOE 9", tipo="eBOE", numero=9, estado="Roto")


def test_numero_activo_fijo_puede_repetirse(sesion):
    planta = crear_ledesma(sesion)
    for numero in [1, 2]:
        sesion.add(Equipo(codigo=f"LED-JEX-0{numero}", nombre=f"JEX N°{numero}", tipo="JEX",
                          numero=numero, numero_activo_fijo="AF-100", linea=planta.lineas[0]))
    sesion.commit()

    repetidos = sesion.scalars(select(Equipo).where(Equipo.numero_activo_fijo == "AF-100")).all()

    assert len(repetidos) == 2


def test_codigo_de_equipo_es_unico(sesion):
    planta = crear_ledesma(sesion)
    sesion.add(Equipo(codigo="LED-BOE-01", nombre="otro", tipo="eBOE", numero=9,
                      linea=planta.lineas[0]))

    with pytest.raises(IntegrityError):
        sesion.commit()


def test_codigo_de_equipo_nunca_cambia(sesion):
    crear_ledesma(sesion)
    equipo = sesion.scalars(select(Equipo).where(Equipo.codigo == "LED-BOE-01")).one()

    equipo.codigo = "LED-BOE-01"   # asignar el mismo código no es un cambio
    with pytest.raises(ValueError, match="nunca cambia"):
        equipo.codigo = "LED-BOE-99"


def test_codigo_vacio_da_error():
    with pytest.raises(ValueError, match="necesita un código"):
        Equipo(codigo="", nombre="eBOE 9", tipo="eBOE", numero=9)


# ---------------------------------------------------------------- turnos, procesos y datos de planta

@pytest.fixture
def motor():
    """Motor de una base en memoria compartida entre sesiones (para leer con una sesión nueva)."""
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    return motor


def cargar_ledesma_con_datos(motor):
    """LEDESMA con turnos, un proceso, un análisis, una lectura y un registro de producción."""
    with crear_sesion(motor) as sesion:
        planta = crear_ledesma(sesion)
        turno_n = Turno(nombre="N", hora_inicio=time(21, 0), hora_fin=time(4, 59), planta=planta)
        turno_m = Turno(nombre="M", hora_inicio=time(5, 0), hora_fin=time(12, 59), planta=planta)
        proceso = Proceso(numero=3, temporada=2025, planta=planta)
        # Con la planta ya guardada, los objetos nuevos hay que agregarlos a la sesión a mano.
        sesion.add_all([turno_n, turno_m, proceso])
        boe1 = sesion.scalars(select(Equipo).where(Equipo.codigo == "LED-BOE-01")).one()
        entrada = next(p for p in boe1.puntos_muestreo if p.tipo == "fruta entrada")
        sesion.add_all([
            AnalisisScott(punto_muestreo=entrada, fecha_hora=datetime(2025, 6, 25, 2, 0), proceso=proceso,
                          analista="CUESTA", fruta="N Val S/R", peso_muestra_g=25.0, vol_ml=7.4,
                          blanco_ml=0.2, normalidad=0.1, peso_fruta_kg=10.2, n_frutos=48,
                          calidad="Firme", ticket="T-1", licuadora="BERKEL", cargado_por="prueba"),
            LecturaOperacion(equipo=boe1, fecha_hora=datetime(2025, 6, 25, 2, 0), operador="PÉREZ",
                             programa="N3", flip_gate=6, tph=14.5, caudal_fase_pesada=3.2,
                             cargado_por="prueba"),
            RegistroProduccion(planta=planta, fecha_produccion=date(2025, 6, 24), turno=turno_n,
                               proceso=proceso, fruta_t=310.0, aceite_envasado_kg=1650.0,
                               jugo_conc_kg=22000.0, reproceso_kg=120.0, qf3=1.2, brix_fritz=65.1,
                               cargado_por="prueba"),
        ])
        sesion.commit()


def test_guardar_y_leer_turnos_y_proceso(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        planta = sesion.scalars(select(Planta).where(Planta.nombre == "LEDESMA")).one()
        turnos = {t.nombre: (t.hora_inicio, t.hora_fin) for t in planta.turnos}
        assert turnos == {"N": (time(21, 0), time(4, 59)), "M": (time(5, 0), time(12, 59))}
        assert [(p.numero, p.temporada) for p in planta.procesos] == [(3, 2025)]


def test_guardar_y_leer_analisis_scott(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        analisis = sesion.scalars(select(AnalisisScott)).one()
        assert analisis.punto_muestreo.equipo.codigo == "LED-BOE-01"
        assert analisis.punto_muestreo.tipo == "fruta entrada"
        assert analisis.proceso.numero == 3
        assert (analisis.peso_muestra_g, analisis.vol_ml, analisis.blanco_ml,
                analisis.normalidad) == (25.0, 7.4, 0.2, 0.1)
        assert (analisis.n_frutos, analisis.calidad, analisis.licuadora) == (48, "Firme", "BERKEL")
        assert analisis.ph is None and analisis.observaciones is None
        assert analisis.cargado_por == "prueba"
        assert isinstance(analisis.cargado_en, datetime)    # se completa solo


def test_guardar_y_leer_lectura_operacion(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        lectura = sesion.scalars(select(LecturaOperacion)).one()
        assert lectura.equipo.codigo == "LED-BOE-01"
        assert (lectura.programa, lectura.flip_gate, lectura.tph, lectura.caudal_fase_pesada) == (
            "N3", 6, 14.5, 3.2)
        assert lectura.equipo.lecturas == [lectura]


def test_guardar_y_leer_registro_produccion(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        registro = sesion.scalars(select(RegistroProduccion)).one()
        assert (registro.planta.nombre, registro.fecha_produccion, registro.turno.nombre) == (
            "LEDESMA", date(2025, 6, 24), "N")
        assert registro.proceso.numero == 3
        assert (registro.fruta_t, registro.aceite_envasado_kg, registro.jugo_conc_kg,
                registro.reproceso_kg, registro.qf3, registro.brix_fritz) == (
            310.0, 1650.0, 22000.0, 120.0, 1.2, 65.1)


def test_registro_produccion_unico_por_planta_fecha_turno_y_proceso(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        planta = sesion.scalars(select(Planta).where(Planta.nombre == "LEDESMA")).one()
        turno_n = next(t for t in planta.turnos if t.nombre == "N")
        sesion.add(RegistroProduccion(planta=planta, fecha_produccion=date(2025, 6, 24), turno=turno_n,
                                      proceso=planta.procesos[0], cargado_por="prueba"))
        with pytest.raises(IntegrityError):
            sesion.commit()


def test_turno_compartido_por_dos_procesos_admite_dos_registros(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        planta = sesion.scalars(select(Planta).where(Planta.nombre == "LEDESMA")).one()
        turno_n = next(t for t in planta.turnos if t.nombre == "N")
        proceso_4 = Proceso(numero=4, temporada=2025, planta=planta)
        sesion.add_all([proceso_4, RegistroProduccion(
            planta=planta, fecha_produccion=date(2025, 6, 24), turno=turno_n, proceso=proceso_4,
            fruta_t=50.0, cargado_por="prueba")])
        sesion.commit()

        assert len(sesion.scalars(select(RegistroProduccion)).all()) == 2


def test_turno_y_proceso_unicos(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        planta = sesion.scalars(select(Planta).where(Planta.nombre == "LEDESMA")).one()
        sesion.add(Turno(nombre="M", hora_inicio=time(6, 0), hora_fin=time(13, 59), planta=planta))
        with pytest.raises(IntegrityError):
            sesion.commit()
        sesion.rollback()
        sesion.add(Proceso(numero=3, temporada=2025, planta=planta))
        with pytest.raises(IntegrityError):
            sesion.commit()


@pytest.mark.parametrize("campo, valor, mensaje", [
    ("peso_muestra_g", 0, "mayor que 0"),
    ("normalidad", -0.1, "mayor que 0"),
    ("vol_ml", -1, "no puede ser negativo"),
    ("blanco_ml", -0.2, "no puede ser negativo"),
    ("ph", 15, "entre 0 y 14"),
])
def test_analisis_scott_valida_datos_crudos(campo, valor, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        AnalisisScott(**{campo: valor})


def test_lectura_y_produccion_validan_valores():
    with pytest.raises(ValueError, match="entre 1 y 12"):
        LecturaOperacion(flip_gate=13)
    with pytest.raises(ValueError, match="no puede ser negativo"):
        LecturaOperacion(tph=-5)
    with pytest.raises(ValueError, match="no puede ser negativo"):
        RegistroProduccion(fruta_t=-1)


def test_analisis_unico_por_punto_y_fecha_hora(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        original = sesion.scalars(select(AnalisisScott)).one()
        sesion.add(AnalisisScott(punto_muestreo=original.punto_muestreo, fecha_hora=original.fecha_hora,
                                 proceso=original.proceso, analista="OTRO", peso_muestra_g=20.0,
                                 vol_ml=5.0, blanco_ml=0.2, normalidad=0.1, cargado_por="prueba"))
        with pytest.raises(IntegrityError):
            sesion.commit()


def test_analisis_en_otro_punto_o_en_otra_hora_no_choca(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        original = sesion.scalars(select(AnalisisScott)).one()
        salida = next(p for p in original.punto_muestreo.equipo.puntos_muestreo if p.tipo == "fruta salida")
        for punto, momento in ((salida, original.fecha_hora),
                               (original.punto_muestreo, datetime(2025, 6, 25, 3, 0))):
            sesion.add(AnalisisScott(punto_muestreo=punto, fecha_hora=momento, proceso=original.proceso,
                                     analista="CUESTA", peso_muestra_g=25.0, vol_ml=1.0,
                                     blanco_ml=0.2, normalidad=0.01, cargado_por="prueba"))
        sesion.commit()

        assert len(sesion.scalars(select(AnalisisScott)).all()) == 3


def test_lectura_unica_por_equipo_y_fecha_hora(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        original = sesion.scalars(select(LecturaOperacion)).one()
        sesion.add(LecturaOperacion(equipo=original.equipo, fecha_hora=original.fecha_hora, tph=10.0,
                                    cargado_por="prueba"))
        with pytest.raises(IntegrityError):
            sesion.commit()


def test_origen_del_dato_importado_y_vacio_si_es_manual(motor):
    cargar_ledesma_con_datos(motor)

    with crear_sesion(motor) as sesion:
        lectura = sesion.scalars(select(LecturaOperacion)).one()
        lectura.origen = texto_origen("eficiencia eBOE Naranja 25 2.xlsm", "BOE", 125)
        sesion.commit()

    with crear_sesion(motor) as sesion:
        assert sesion.scalars(select(LecturaOperacion)).one().origen == (
            "eficiencia eBOE Naranja 25 2.xlsm › BOE › fila 125")
        assert sesion.scalars(select(AnalisisScott)).one().origen is None        # cargado a mano
        assert sesion.scalars(select(RegistroProduccion)).one().origen is None


def test_lectura_plc_unica_por_equipo_y_hora(sesion):
    planta = crear_ledesma(sesion)
    equipo = planta.lineas[0].equipos[0]
    momento = datetime(2026, 7, 1, 10, 5)
    sesion.add(LecturaPLC(equipo=equipo, fecha_hora=momento, horas_maquina=31549, minutos=29, elevador=1,
                          programa="N3", ton_seteadas=12, ton_calculadas=10.8, cargado_por="prueba",
                          origen="Ledesma712026.csv › fila 3"))
    sesion.commit()
    assert sesion.scalars(select(LecturaPLC)).one().programa == "N3"

    sesion.add(LecturaPLC(equipo=equipo, fecha_hora=momento, cargado_por="prueba"))
    with pytest.raises(IntegrityError):
        sesion.commit()


def test_lectura_plc_valida_elevador_y_negativos():
    with pytest.raises(ValueError, match="0 o 1"):
        LecturaPLC(elevador=2)
    with pytest.raises(ValueError, match="no puede ser negativo"):
        LecturaPLC(ton_calculadas=-1.0)


def _primaria(sesion):
    planta = crear_ledesma(sesion)
    equipo = Equipo(codigo="LED-CEN-01", nombre="ESE 230 (1)", tipo="Centrífuga primaria", numero=1,
                    linea=planta.lineas[0])
    sesion.add(equipo)
    return equipo


def test_muestra_centrifuga_guarda_datos_crudos_y_es_unica_por_equipo_hora_y_fase(sesion):
    equipo = _primaria(sesion)
    momento = datetime(2025, 6, 24, 14, 30)
    sesion.add_all([
        MuestraCentrifuga(equipo=equipo, fecha_hora=momento, fase="emulsion", caudal_lh=13000, gramos=3.504,
                          ml=6.54, normalidad=0.0996, blanco_ml=0.1, solidos_pct=1, analista="Bulegio",
                          operador="Escudero", fruta="N Val S/R", cargado_por="prueba", solidos_texto="<1",
                          solidos_marca="menor que", revisar="ESB 130 anotada como 1° centrífuga",
                          origen=texto_origen("CENTRIGUGAS Naranja 25 2.xlsx", "Datos", 3)),
        MuestraCentrifuga(equipo=equipo, fecha_hora=momento, fase="fase_liviana", aceite_pct_medido=72,
                          cp_liviana_bar=0.8, cargado_por="prueba"),
    ])
    sesion.commit()
    emulsion = sesion.scalars(select(MuestraCentrifuga).where(MuestraCentrifuga.fase == "emulsion")).one()
    assert (emulsion.gramos, emulsion.ml, emulsion.blanco_ml, emulsion.equipo.codigo) == (3.504, 6.54, 0.1,
                                                                                         "LED-CEN-01")
    assert emulsion.origen == "CENTRIGUGAS Naranja 25 2.xlsx › Datos › fila 3"
    assert (emulsion.solidos_pct, emulsion.solidos_texto, emulsion.solidos_marca) == (1, "<1", "menor que")
    assert emulsion.revisar == "ESB 130 anotada como 1° centrífuga"
    liviana = sesion.scalars(select(MuestraCentrifuga).where(MuestraCentrifuga.fase == "fase_liviana")).one()
    assert (liviana.aceite_pct_medido, liviana.caudal_lh, liviana.proceso, liviana.revisar) == (72, None, None,
                                                                                                None)

    sesion.add(MuestraCentrifuga(equipo=equipo, fecha_hora=momento, fase="emulsion", cargado_por="prueba"))
    with pytest.raises(IntegrityError):
        sesion.commit()


@pytest.mark.parametrize("campo, valor, mensaje", [
    ("fase", "Emulsion", "no válida"),                 # la fase llega ya normalizada
    ("gramos", 0, "mayor que 0"),
    ("normalidad", -0.1, "mayor que 0"),
    ("ml", -1, "no puede ser negativo"),
    ("caudal_lh", -5, "no puede ser negativo"),
    ("t_descarga_s", -1, "no puede ser negativo"),
    ("solidos_pct", 101, "entre 0 y 100"),
    ("aceite_pct_medido", -2, "entre 0 y 100"),
    ("solidos_marca", "aprox", "no válida"),
    ("cp_liviana_marca", "dudoso", "no válida"),
])
def test_muestra_centrifuga_valida_valores(campo, valor, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        MuestraCentrifuga(**{campo: valor})


def test_agregar_columnas_nuevas_a_una_base_vieja():
    """Una base creada antes de blanco_centrifugas_ml recibe la columna sin perder sus datos."""
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with motor.begin() as conexion:
        conexion.execute(text("INSERT INTO cliente (id, nombre, pais) VALUES (1, 'X', 'Argentina')"))
        conexion.execute(text("INSERT INTO planta (id, cliente_id, nombre, pais, blanco_ml) "
                              "VALUES (1, 1, 'Vieja', 'Argentina', 0.2)"))
        conexion.execute(text("ALTER TABLE planta DROP COLUMN blanco_centrifugas_ml"))
    assert "blanco_centrifugas_ml" not in {c["name"] for c in inspect(motor).get_columns("planta")}

    assert agregar_columnas_nuevas(motor) == ["planta.blanco_centrifugas_ml"]
    assert agregar_columnas_nuevas(motor) == []                    # la segunda vez no hay nada
    with crear_sesion(motor) as sesion:
        planta = sesion.get(Planta, 1)
        assert (planta.blanco_ml, planta.blanco_centrifugas_ml) == (0.2, None)
