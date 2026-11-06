# LEDESMA – Mapeo del archivo "eficiencia eBOE Naranja 25 2.xlsm" al formato único

**Versión:** 0.3 · 28/10/2026 · Ramiro (Tech Service) · Solo se usan las hojas **BOE** y **Resumen**.

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
| Rendimiento eBOE | 6,6–6,8 kg/MT |
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
| R | Ton/H | `LecturaOperacion.tph` | 6–23 MTPH |
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
5. **Eficiencia del proceso > 100 %** en algunos turnos (máx. 174 %) y rendimiento de aceite de hasta 17 kg/MT: el aceite envasado de un turno no corresponde a la fruta de ese turno (desfase del envasado). Mostrar ese KPI por día o por proceso, o marcarlo.
6. **Día de producción de 05:00 a 04:59 (confirmado por Ramiro):** coincide con los turnos M, T y N. Las muestras entre 00:00 y 04:59 (turno N) pertenecen al día de producción anterior. Regla: `fecha_produccion = fecha si hora ≥ 05:00, si no fecha − 1 día`. La fecha original de la muestra se guarda igual.
7. **Vínculos externos rotos:** finca, productor, rendimiento teórico, QF N°3, Brix Fritz y eficiencia de centrífuga vienen de otros archivos. Quedan fuera del primer import.

## 5. Diferencias con las fórmulas del doc 01

| Punto | LEDESMA | Doc 01 (Brown) | Cómo lo resuelve la app |
|---|---|---|---|
| Blanco | Fijo 0,2 mL | Se registra por análisis | Campo `blanco` con valor por defecto 0,2 por planta |
| Factor % emulsión | 3,45 | 3,442 | Factor configurable por planta (diferencia ≈ 0,2 %) |
| Factor kg/MT | 68,8416 | 68,841 | Ídem (diferencia despreciable) |
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

| Temporada | eBOE | Pares | Eficiencia media | Rend. eBOE (kg/MT) | Aceite en fruta entrada (kg/MT) | % de pares < 80 % |
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

## 9. Análisis a reproducir (presentación de septiembre 2026, limón 2024–2026)

La app tiene que generar para naranja (temporadas 2025 y 2026) los mismos análisis:

| # | Análisis | Detalle |
|---|---|---|
| 1 | Histograma de aceite en fruta | Entrada vs Salida superpuestos, con curva normal; leyenda con media, desvío y N |
| 2 | Boxplot de aceite en fruta | Por eBOE y lugar (Entrada / Salida), con outliers |
| 3 | Tabla de estadística | Aceite en fruta en Salida por eBOE: N, media, error estándar, desvío, mín, Q1, mediana, Q3, máx |
| 4 | Límites de outliers por IQR | Por eBOE: Q1, Q3, IQR, LI = Q1 − 1,5·IQR, LS = Q3 + 1,5·IQR |
| 5 | Vista de proceso (flowsheet) | Por temporada: por eBOE aceite entrada y salida (kg/MT), MTPH y eficiencia; totales: eficiencia de aceite, eficiencia de proceso, fruta procesada (t), aceite envasado (kg), rendimiento de aceite (kg/MT) |
| 6 | Burbujas | Eficiencia eBOE promedio vs MTPH; color = programa; tamaño = N; una vista por eBOE |
| 7 | Superficie y contorno | Eficiencia eBOE vs programa y MTPH (por eBOE) |
| 8 | ANOVA + Tukey por temporada | Aceite en fruta (F, p), comparaciones de Tukey y gráfico de intervalos al 95 % |
| 9 | Eficiencia por temporada | Boxplot e intervalos al 95 % |
| 10 | Emulsión | Intervalos al 95 % de aceite en emulsión (%) por temporada e histograma con curva normal |
| 11 | Comparación anual | Tabla con Tucumán, otras plantas y LEDESMA (fruta procesada, aceite en fruta, eficiencia eBOE, rendimiento, eficiencia de aceite, residuales, eficiencia de centrífugas). **Los datos de Tucumán y otras plantas y los de centrífugas no están en el archivo: se cargan aparte** |

Conclusiones de septiembre que conviene contrastar con naranja: diferencias significativas de aceite en fruta entre campañas (ANOVA); la pérdida de eficiencia viene de más eventos de bajo rendimiento, no de un menor máximo; la variación entre campañas es mayor que la variación dentro de cada campaña.

## 10. Centrífugas de aceite (archivo "CENTRIGUGAS Naranja 25 2.xlsx", hoja Datos)

Los cálculos están en `core/centrifugas.py`. Bloques de 4 filas por muestreo: Emulsión (alimentación, 3), fase liviana (4), fase pesada (5) y descarga (6). x = aceite en fracción; ρ(x) = 0,852·x + 0,997·(1 − x) kg/L.

| Cálculo | Planilla | App |
|---|---|---|
| Aceite % (col. P) | (mL − 0,1) × N × 3,45 / g | Igual, con blanco_centrifugas_ml y factor_aceite_emulsion de la planta |
| Caudal de fase liviana (col. M) | [M3·ρ3·(x3 − x5) − 16/t·3600·ρ6·(x6 − x5)] / [ρ4·(x4 − x5)] | Igual, con el volumen por descarga **del equipo** (Equipo.volumen_descarga_l), no 16 L fijo |
| Eficiencia (col. U) | M4·ρ4·x4 / (M3·ρ3·x3) | Igual, en %; menor que 0 o mayor que 100 → marcada "fuera de rango físico" |
| Caudal de fase pesada (col. M) | [M3·ρ3 − M4·ρ4 − 16/t·3600·ρ6·**(x6 − x5)**] / ρ5 | Balance de masa total **sin** (x6 − x5): ese factor solo va en el balance de aceite. Da ~0,6 % menos (mediana) |
| Aceite perdido en descarga (col. V) | x6 · ρ6 · **t** (t = segundos entre descargas, usado como caudal) | x6 · ρ6 · caudal de descarga, con caudal = volumen × 3600 / t. **La planilla sobreestima la pérdida t² / (V · 3600) veces: ~4,3 con t = 500 s y 16 L** (mediana 1,00 → 0,26 kg/h) |
| Aceite perdido en emulsión, liviana y pesada (col. V) | x · ρ · caudal | Igual (la pesada, con su caudal corregido) |

- Volumen por descarga = espacio de sólidos del tambor, sin porcentajes: ESE 230 (1) y (2) 16 L; MSD 50 2 L (fabricante 1,5–2 L, a confirmar si la descarga es parcial o total); ESB 130 y OSD 60 a definir. Si falta, el balance y la pérdida en descarga quedan "a definir": no hay valor por defecto.
- Verificación (`tests/test_centrifugas.py`): con las versiones de la planilla, las fórmulas dan igual que P, U y V en las filas 3, 2251, 4539, 5619 y 8831; en toda la hoja coinciden 1.861 de 1.867 muestreos. Los 6 distintos son errores de la planilla y se importan con `revisar`: fila 1683 (la descarga usa N 0,01), 3011 (aceite de emulsión escrito a mano), 6399 (valores pegados), 1787, 3527 y 5571 (falta la fórmula del caudal de fase liviana: Excel da eficiencia 0). 9 muestreos dan eficiencia negativa.
- Secundaria: se alimenta con la suma de las fases livianas de las primarias; aceite ponderado por caudal (col. Y y AB). Mismo balance, con el volumen de su equipo.

### Importación (`core/importar_centrifugas.py`)

- Se lee la hoja Datos entera. Bloques de 4 filas (emulsión, fase liviana, fase pesada, descarga); las fases se normalizan ("F Livina", "Fase livina", "FASE PESADA"…). Una fila que no encaja en un bloque o un bloque de plantilla sin datos se salta con su motivo.
- Primaria: L a W. Secundaria: X a AG, una vez por muestreo ("130" → ESB 130; "0" o vacío → sin secundaria). De la secundaria se guardan fase liviana (AB, % directo), fase pesada (Z/AA) y descarga (Z/AA/AC); su alimentación se calcula.
- Normalidad: emulsión y descarga N 0,1 (I); fase pesada N 0,01 (J), en primaria y secundaria.
- Fecha: misma convención que el BOE (comprobado: después de las 23:10 viene la 01:10 con la misma Fecha en 126 casos; la Fecha cambia a las 05:00 en 122).
- Se marcan para revisar (se importan igual): ESB 130 como primaria, eficiencia fuera de rango físico, valor pegado sin g y mL, fase pesada copiada de la otra primaria, normalidad dudosa en la descarga (fila 1683) o fase pesada calculada con N 0,1 en la planilla (secundaria 2025), aceite % de la planilla distinto del recalculado (fila 3011), planilla sin caudal de fase liviana (filas 1787, 3527, 5571) y fecha fuera de secuencia (el Día cambia y la Fecha no, o la Fecha va para atrás).
- Misma clave (equipo + fecha_hora + fase) con datos distintos: no se carga ninguna (regla general).
- Fecha corregida por el Día: si el Día cambia y la Fecha no (o la Fecha va para atrás, como el 12/06/2026 por 12/08), la Fecha pasa a ser la del Día anterior + 1; `fecha_original` guarda la de la planilla y `revisar` dice "fecha corregida por el Día". Si la Fecha avanza y el Día no (proceso 11, 30/08/2026 anotado como Día 3), el que está mal es el Día: la Fecha se deja y se marca "Día fuera de secuencia".
- Fase pesada y descarga copiadas: si una primaria tiene los mismos g y mL que la otra del mismo muestreo, se marca "copiada de la otra primaria". En 2025, la fase pesada de la ESE 230 (2) es copia de la (1) en 444 de 498 muestreos.
- Pulidora OSD 60 (AH y AI, fila de emulsión): dos muestras por muestreo, "descarga" con t_descarga_s = AH × 60 y "fase_liviana" con cp_liviana_bar = AI, sin titulación. **La CP liviana vale 0 en todo 2026 y en 508 de 542 muestreos de 2025**: se guarda como viene, con `cp_liviana_marca` = "no medible (valor cercano a 0 o manómetro a revisar)" (confirmado por la planta; no va a `revisar` y queda fuera de estadísticas y gráficos). Si AI está vacío, queda vacío.
- Secundaria no indicada: en 2026 la col. X dice "0" cuando el operario no eligió el equipo (confirmado por la planta). Si hay datos se asume la única secundaria del mes: 46 muestreos de agosto de 2026 pasan a MSD 50, marcados "secundaria no indicada, asumida MSD 50".
- Destino de la fase pesada: la de las primarias ESE 230 recircula al BOE (no es pérdida); la de las secundarias MSD 50 y ESB 130 va al canal (pérdida). La secundaria casi no tiene muestras de fase pesada (59 en 2025, ninguna en 2026).
- Claves repetidas que no se resuelven con la fecha: cinco pares en 2025 (filas 691/695, 4299/4303, 4315/4319, 4627/4631, 5323/5327; misma primaria y hora, uno sin caudal: probablemente uno era la otra primaria) y el 14/08/2026 03:10 (filas 6475 y 6503, procesos 6 y 7).

### Rangos de referencia de centrífugas (RangoReferencia, hoja de maestros)

| Variable | Equipo | Rango | Fuente | Alerta |
|---|---|---|---|---|
| Sólidos centrifugables | Primaria | ≤ 2 % | GEA 2019 | sí |
| CP fase pesada | Primaria | 2–3 bar | GEA 2019 | **no** (alertas por carta de control, Fase I = 2025) |
| CP fase liviana | Primaria | 0,5–1,5 bar | GEA 2019 | sí |
| Aceite en fase liviana | Primaria | 75–85 % | GEA 2019 | sí (se usa el de la planta) |
| Intervalo de descarga | Primaria | 180–240 s | GEA 2019 | sí |
| Caudal de emulsión | Primaria (ESE 230) | 12.000–16.000 L/h | GEA 2019 | sí |
| Intervalo de descarga | Pulidora (3 etapas) | 30–60 min | GEA 2019 | sí |
| Aceite en emulsión | Primaria | 0,70–1,10 % | Planilla LEDESMA (hoja Tablas) | sí |
| Aceite en fase pesada | Primaria | 0,05–0,20 % | Planilla LEDESMA | sí |
| Aceite en fase liviana | Primaria | 70–80 % | Planilla LEDESMA | sí |
| Aceite en descarga | Primaria | ≤ 0,5 % | Planilla LEDESMA | sí |
| Aceite en fase liviana | Secundaria | 90–95 % | Planilla LEDESMA | sí |
| Aceite en descarga | Secundaria | ≤ 0,75 % | Planilla LEDESMA | sí |
| Aceite en descarga | Pulidora | ≤ 0,75 % | Planilla LEDESMA | **no** (la descarga de la pulidora no se titula) |

Los GEA van sin fruta (son del fabricante); los de la planilla, con fruta Naranja. Si hay los dos para la misma variable y equipo, se usa el de la planta. La hoja Tablas también tiene categorías que no son rangos (fase liviana 61–69 % "Atención", 40–60 % "Emulsión estable (mayonesa)") y "Temperatura 35" sin unidad ni punto: no se cargaron.

Primarias en paralelo: en 2025 las dos primarias se muestreaban a la misma hora (510 de 695 muestreos), a ~18.000 L/h cada una → "en paralelo (observado)", con prioridad. En 2026, una por ronda, alternando → regla por turno "inferida": las dos aparecen en el turno y la mediana del caudal de cada una es ≤ umbral_paralelo_lh (parámetro de la planta, 17.000 L/h en LEDESMA). La evaluación es por turno, no por día.

**Validación:** el agua por eBOE calculada desde las centrífugas coincide con el caudal del laboratorio del BOE (2025: 11,80 vs 11,81; 2026: 6,21 vs 6,29). La capacidad de centrifugado fija el agua de los eBOE.
