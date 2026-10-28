# LEDESMA – Mapeo del archivo "eficiencia eBOE Naranja 25 2.xlsm" al formato único

**Versión:** 0.2 · 28/10/2026 · Ramiro (Tech Service) · Solo se usan las hojas **BOE** y **Resumen**.

## 1. Qué hay en el archivo

| Dato | Valor |
|---|---|
| Planta | LEDESMA |
| Fruta | Naranja Valencia ("N Val S/R" 96 %, "N Val C/R" 4 %) |
| Equipos | **3 eBOE** (1, 2 y 3) |
| Turnos | M 05:00–12:59 · T 13:00–20:59 · N 21:00–04:59 (hoja Tablas) |
| Período | Temporada 2025 (24/06 a oct-2025) y temporada 2026 (jul a 26/10/2026) |
| Procesos | 29 |
| Hoja BOE | 9.349 muestras (≈ 4.670 pares entrada/salida de fruta + 4.559 análisis de emulsión) |
| Hoja Resumen | 542 turnos con datos de producción |

### Objetivos de la planta (hoja Tablas)

| Variable | Objetivo |
|---|---|
| Eficiencia eBOE | 80–90 % |
| Rendimiento eBOE | 6,6–6,8 kg/t |
| Rendimiento de jugo | ≤ 14 |
| Aceite en emulsión (licor) | Normal 0,80–1,10 % (bajo < 0,80; alto > 1,10) |
| °Brix de la emulsión | ≤ 5 |
| pH de la emulsión | 3–7 |

## 2. Hoja BOE (tabla "Tabla4", rango A3:AJ)

Una fila por muestra. Por cada eBOE y hora hay **dos filas**: la de **Entrada** (que también trae emulsión y operación) y la de **Salida**.

| Col | Encabezado | Destino en la app | Notas |
|---|---|---|---|
| A | PROCESO | `Proceso.numero` | Se repite hacia abajo con fórmula |
| B | Día | `Proceso.dia` | Día dentro del proceso |
| C | Especie | `Analisis.fruta` | Normalizar "N Val S7R" → "N Val S/R" |
| D | Fecha | `fecha_hora` (fecha) | |
| E | Hora | `fecha_hora` (hora) | 16 horas cargadas como texto ("15.10") → convertir |
| F | Químico | `Analisis.analista` | Normalizar mayúsculas ("cuesta") |
| G | Operador | `LecturaOperacion.operador` | |
| H | Turno | **se recalcula** | Fórmula según hora |
| I | eBOE | `Equipo` (eBOE 1, 2, 3) | |
| J | pH | `AnalisisEmulsion.ph` | Solo en filas de Entrada |
| K | °Brix | `AnalisisEmulsion.brix` | |
| L | Muestra [g] | `AnalisisEmulsion.peso_g` | Dato crudo |
| M | Vol [ml] | `AnalisisEmulsion.vol_ml` | Dato crudo |
| N | Aceite [%] | **se recalcula** | = (ml − 0,2) × 3,45 × N / g |
| O | Sólidos centrifugables [%] | `AnalisisEmulsion.solidos_centrif` | |
| P | Programa | `LecturaOperacion.programa` | N0–N9; normalizar "0" → "N0" |
| Q | Flip Gate | `LecturaOperacion.flip_gate` | 1–12 |
| R | Ton/H | `LecturaOperacion.tph` | 6–23 t/h |
| S | Caudal fase pesada [m³/h] | `LecturaOperacion.caudal_fase_pesada` | Medido |
| T | Caudal fase pesada calc. | **se recalcula** | Balance de la planta; ver sección 5 |
| U | Lugar de muestreo | `PuntoMuestreo` | Entrada / Salida |
| V | Calidad de fruta | `AnalisisFruta.calidad` | Firme, Blanda, Podrido, Partida; normalizar mayúsculas |
| W | Pinchado | `AnalisisFruta.rallado` | Solo en Salida: Ok, Poco rallada, Sobre rallada; normalizar "OK" |
| X | Peso fruta [kg] | `AnalisisFruta.peso_fruta_kg` | |
| Y | Número de frutos | `AnalisisFruta.n_frutos` | |
| Z | Frutos en 10 kg | **se recalcula** | = Y × 10 / X |
| AA | Peso muestra [g] | `Analisis.peso_g` | Dato crudo |
| AB | Vol [ml]2 | `Analisis.vol_ml` | Dato crudo |
| AC | Aceite en fruta [kg/MT] | **se recalcula** | = 68,8416 × (ml − 0,2) × N / g |
| AD | Eficiencia eBOE | **se recalcula** | En la fila de Entrada: 1 − salida / entrada |
| AE | Rend. eBOE [kg/MT] | **se recalcula** | entrada − salida |
| AF | N BrO3 | `Analisis.normalidad` | ≈ 0,100 en Entrada y ≈ 0,010 en Salida |
| AG | N° de Ticket | `Analisis.ticket` | 1.213 filas con dato |
| AH–AI | Finca, Productor | — (no se importan) | Vienen de un archivo externo (NARANJA25) |
| AJ | Licuadora | `Analisis.licuadora` | BERKEL u otra |

## 3. Hoja Resumen

Tiene tres tablas: **por turno** (Tabla1, A:V), **por día** (Tabla8, X:AR) y **por proceso** (Tabla9, AT:BM). **Solo se importa la tabla por turno.** Día y proceso los calcula la app, ponderando por toneladas como hace el Excel.

| Col | Encabezado | Destino en la app | Tipo |
|---|---|---|---|
| A–D | Proceso, Día, Fecha, Turno | `RegistroProduccion` (clave) | |
| E–H | Aceite en fruta, residual, eficiencia, rend eBOE | **se recalculan desde la hoja BOE** | 289 de 542 turnos tienen el valor pegado en vez de la fórmula |
| I | Fruta procesada [t] | `RegistroProduccion.fruta_t` | **Carga manual** |
| J | Aceite envasado [kg] | `RegistroProduccion.aceite_envasado_kg` | **Carga manual** |
| K | Rend de aceite turno [kg/t] | **se recalcula** | = J / I |
| L | Eficiencia del proceso | **se recalcula** | = K / E (aceite en fruta) |
| M | Jugo conc [kg] | `RegistroProduccion.jugo_conc_kg` | **Carga manual** |
| N | Rend jugo conc | **se recalcula** | = I × 1000 / M (kg de fruta por kg de concentrado) |
| O | Rend teórico | — | Archivo externo; solo 71 turnos |
| P | Reproc [kg] | `RegistroProduccion.reproceso_kg` | **Carga manual** |
| Q | QF N°3 | `RegistroProduccion.qf3` | Archivo externo (RML 04); 113 turnos |
| R | Brix Fritz | `RegistroProduccion.brix_fritz` | Archivo externo; 108 turnos |
| S–U | BOE1/2/3 T/H | **se recalcula** | Promedio de la hoja BOE |
| V | Eficiencia centrífuga 1° | — | Vacía |

## 4. Problemas de calidad de datos (el importador los tiene que reportar)

1. **Promedios pegados** en Resumen (289 turnos): no confiar en E–H; recalcular desde BOE.
2. **7 Entradas sin su Salida** inmediata del mismo eBOE: no se puede calcular eficiencia; van al reporte.
3. **Horas como texto** (16 filas, por ejemplo "15.10").
4. **Texto inconsistente:** firme/Firme, OK/Ok, poco rallada/Poco rallada, cuesta/Cuesta, "N Val S7R", programa 0 / N0.
5. **Eficiencia del proceso > 100 %** en algunos turnos (máx. 174 %) y rendimiento de aceite de hasta 17 kg/t: el aceite envasado de un turno no corresponde a la fruta de ese turno (desfase del envasado). Mostrar ese KPI por día o por proceso, o marcarlo.
6. **Día de producción de 05:00 a 04:59 (confirmado por Ramiro):** coincide con los turnos M, T y N. Las muestras entre 00:00 y 04:59 (turno N) pertenecen al día de producción anterior. Regla: `fecha_produccion = fecha si hora ≥ 05:00, si no fecha − 1 día`. La fecha original de la muestra se guarda igual.
7. **Vínculos externos rotos:** finca, productor, rendimiento teórico, QF N°3, Brix Fritz y eficiencia de centrífuga vienen de otros archivos. Quedan fuera del primer import.

## 5. Diferencias con las fórmulas del doc 01

| Punto | LEDESMA | Doc 01 (Brown) | Cómo lo resuelve la app |
|---|---|---|---|
| Blanco | Fijo 0,2 mL | Se registra por análisis | Campo `blanco` con valor por defecto 0,2 por planta |
| Factor % emulsión | 3,45 | 3,442 | Factor configurable por planta (diferencia ≈ 0,2 %) |
| Factor kg/t | 68,8416 | 68,841 | Ídem (diferencia despreciable) |
| Eficiencia eBOE | 1 − salida/entrada | Remoción del BOE = 100 × (entrada − salida)/entrada | Es la misma fórmula |
| Caudal fase pesada calc. | (AC × R × AD − 350 × 0,997 × 0,9 %) / ((0,9 % − 0,1 %) × (0,852 × 0,15 % + 0,997 × (1 − 0,15 %)) × 1000) | — | Balance propio de LEDESMA; se implementa después, con sus constantes documentadas |

## 6. Datos maestros que salen de este archivo

- **Cliente/Planta:** LEDESMA · fruta: naranja Valencia.
- **Línea:** 1 (a confirmar).
- **Equipos:** eBOE 1, eBOE 2, eBOE 3.
- **Puntos de muestreo (9):** por cada eBOE, Fruta entrada · Fruta salida · Emulsión (licor).
- **Rangos:** los de la sección 1.

## 7. Primeros números (para validar el importador)

Eficiencia promedio y rendimiento por eBOE, calculados desde la hoja BOE:

| Temporada | eBOE | Pares | Eficiencia media | Rend. eBOE (kg/t) | Aceite en fruta entrada (kg/t) | % de pares < 80 % |
|---|---|---|---|---|---|---|
| 2025 | 1 | 732 | 85,3 % | 7,01 | 8,21 | 12 % |
| 2025 | 2 | 727 | 81,3 % | 6,70 | 8,25 | 35 % |
| 2025 | 3 | 733 | 86,4 % | 7,02 | 8,13 | 8 % |
| 2026 | 1 | 817 | 82,7 % | 6,96 | 8,43 | 26 % |
| 2026 | 2 | 810 | 78,2 % | 6,53 | 8,35 | 51 % |
| 2026 | 3 | 796 | 83,9 % | 7,06 | 8,42 | 19 % |

Cuando el importador termine, la app tiene que dar estos mismos valores.

## 8. Preguntas abiertas

1. ¿Qué significan **S/R** y **C/R** en la especie?
2. ¿Qué es un **PROCESO** (corrida, lote, semana)?
3. ¿El **programa N0–N9** es el programa del eBOE?
4. ¿Hay **una sola línea**?
