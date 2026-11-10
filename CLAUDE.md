# CLAUDE.md – App de gestión de líneas de aceite y jugo (Brown)

## Quién soy y cómo trabajar conmigo
- Soy Ramiro, ingeniero de procesos (Tech Service) de Brown. Atiendo plantas citrícolas en varios países.
- **Estoy aprendiendo Python.** Explicá cada cambio en español, en pocas palabras, antes de hacerlo. Si usás algo nuevo (una librería, un patrón), decime qué es y para qué sirve.
- Trabajá en **pasos chicos**: un archivo o una función por vez. Después de cada paso, decime cómo probarlo.
- Antes de instalar paquetes, borrar o renombrar archivos, preguntame.
- Respondé siempre en español.

## Qué es el proyecto
Sistema de gestión para las líneas de aceite y jugo de los clientes de Brown. Reemplaza los Excel, PDF y fotos que cada planta manda en su propio formato por **un formato único** con cálculos y estadística automáticos.

**Objetivo actual (hasta el 30/11/2026):** dejar funcionando la primera planta, **LEDESMA**, con:
1. Datos maestros (planta, líneas, equipos, puntos de muestreo, rangos).
2. Carga de análisis de laboratorio (Scott) con cálculo automático y validación de rangos.
3. Importación del histórico del archivo `data/historico/eficiencia eBOE Naranja 25 2.xlsm` (hojas **BOE** y **Resumen**).
4. Cartas de control (I-MR, X̄-R, X̄-S) con reglas de Nelson, y capacidad (Cp, Cpk, Pp, Ppk).
5. Dashboard de la planta con semáforos y alertas.

### Planta LEDESMA (resumen; el detalle está en `docs/mapeo_ledesma.md`)
- Naranja Valencia · **3 eBOE** · turnos M 05:00–12:59, T 13:00–20:59, N 21:00–04:59.
- **Día de producción: de 05:00 a 04:59** (empieza con el turno M). Una muestra de 00:00–04:59 pertenece al día de producción anterior (`fecha_produccion`). La hora de inicio del día es un parámetro de la planta.
- Puntos de muestreo por eBOE: Fruta entrada, Fruta salida y Emulsión (licor).
- Muestras en pares Entrada/Salida por eBOE y hora. **Cada eBOE tiene su propia muestra de entrada** (fruta, peso, n.º de frutos, g y mL); no hay una entrada única para los 3. Entrada con los mismos g y mL que la de otro eBOE en la misma ronda: revisar = "entrada repetida en varios eBOE (posible copia)" (`core/repetidas.py`, se marca al importar); no se excluye del cálculo, pero sí de las comparaciones entre eBOE por defecto (casilla "Incluir entradas repetidas"). Eficiencia eBOE = 1 − salida/entrada; rendimiento eBOE = entrada − salida (kg/MT).
- Fórmulas de la planta: blanco fijo 0,2 mL; aceite en fruta = 68,8416 × (ml − blanco) × N / g; aceite en emulsión % = (ml − blanco) × 3,45 × N / g. Los factores y el blanco son **configurables por planta**.

- Programas del PLC: N0–N9 son los de naranja (por lo general menos agresivos); los numéricos ("0" a "12") son otros programas, de limón y pomelo, que en naranja aparecen solo en pruebas. Nunca se mezclan ni se convierten a N.
- Centrífugas (planilla `CENTRIGUGAS Naranja 25 2.xlsx`, hoja Datos; los datos siguen debajo de la tabla de Excel): primarias ESE 230 (1) y (2) = LED-CEN-01/02; secundarias ESB 130 = LED-CSE-01 y MSD 50 = LED-CSE-02 (col. X; "130" → ESB 130); pulidora OSD 60 = LED-PUL-01. Sólidos con texto: siempre el texto original en `solidos_texto`; "<1" → 1 con marca "menor que"; ">7 y <8" → 7,5 (punto medio) con marca "rango". Se importan tal cual, con el motivo en `revisar`: ESB 130 anotada como primaria, eficiencia negativa ("fuera de rango físico") y los muestreos con errores de planilla (filas 1683, 3011, 6399, 1787, 3527, 5571; ver `docs/mapeo_ledesma.md` §10).
- Secundaria no indicada ("0" o vacío en la col. X = el operario no eligió el equipo): si la fila tiene datos (g, mL, t o % de fase liviana) se asume la única secundaria usada ese mes, con revisar = "secundaria no indicada, asumida …" (jul–ago 2026: MSD 50); si ese mes se usó más de una (o ninguna), no se asume y se salta con su motivo. Sin datos, no hay secundaria.
- Destino de la fase pesada (`Equipo.destino_fase_pesada`, hoja Equipo): primarias ESE 230 → "recircula al BOE"; secundarias MSD 50 y ESB 130 → "canal (pérdida)". La pérdida en fase pesada solo cuenta como pérdida del proceso si el destino es "canal". Secundaria sin muestras de fase pesada: "sin datos (no se muestrea)".
- Descarga con aceite > 20 % o muestra < 1,0 g: revisar = "valor extremo: muestra muy chica o titulación fuera de rango del método" (se marca, no se excluye de ningún cálculo; desde sep-2026 la MSD 50 viene con ~1 g y 20–60 mL).
- Pulidora OSD 60: destino_fase_pesada = "no aplica" (sus salidas son aceite pulido y descarga); nunca tiene fase pesada ni eficiencia por balance.
- Análisis de centrífugas: `core/analisis_centrifugas.py` (sin Streamlit). Rangos con `rangos_centrifugas()` (punto o equipo = tipo de equipo; si hay uno de la planta con fruta y uno general como GEA, gana el de la planta). Primarias en marcha por turno: "en paralelo (observado)" si las dos ESE 230 se muestrearon a la misma hora (prioridad); si no, regla inferida por turno con el parámetro de planta `umbral_paralelo_lh` (17.000 L/h en LEDESMA; nunca escrito en el código). La página va a ser `app/pages/6_Centrifugas.py`.
- CP liviana de la OSD 60 en 0: `cp_liviana_marca` = "no medible (valor cercano a 0 o manómetro a revisar)"; queda fuera de estadísticas y gráficos.
- `RangoReferencia.genera_alerta` (columna genera_alerta de la hoja, vacío = sí): "no" = solo referencia, se dibuja pero no alerta. CP de fase pesada de la primaria: GEA 2–3 bar es referencia sin alerta; sus alertas salen de la carta de control con límites propios de la planta (Fase I = 2025).
- Paso 4 (centrífugas): las muestras marcadas como copiadas no se usan en las comparaciones entre equipos (hay una opción para incluirlas). En 2025, la fase pesada de la ESE 230 (2) es copia de la (1) en 444 de 498 muestreos.
- Volumen por descarga: es de cada **equipo** (`Equipo.volumen_descarga_l`, hoja Equipo), igual al espacio de sólidos del tambor. Vacío = "a definir": sin él no se calcula el balance ni la pérdida en descarga; nunca un valor por defecto. Cálculos en `core/centrifugas.py` (versión física corregida; las diferencias con la planilla, en `docs/mapeo_ledesma.md` §10).
- Objetivos: eficiencia eBOE 80–90 %; rendimiento eBOE 6,6–6,8 kg/MT; aceite en emulsión 0,80–1,10 %; °Brix emulsión ≤ 5; pH 3–7.
- Producción por turno (carga manual): fruta procesada t, aceite envasado kg, jugo concentrado kg, reproceso kg.
- Nunca importes los promedios de la hoja Resumen: se recalculan desde la hoja BOE.
- En la hoja BOE, la columna **Fecha es la fecha de producción**: una muestra de 00:00–04:59 se tomó el día siguiente. `core/importar_ledesma.py` arma la fecha_hora real comparando con la fila anterior del mismo proceso, día y temporada (los procesos se renumeran cada temporada).

### Datos maestros
- Única fuente: `data/maestros_clientes.xlsx`. Se cargan con `python -m core.importar_maestros` (se puede correr muchas veces; las filas con error van a `data/reporte_maestros.csv`).
- Cada equipo se identifica por su `codigo` (SIGLA_PLANTA-TIPO-NN, por ejemplo LED-BOE-01), único y que **nunca cambia**.
- **Parámetros de cálculo de la planta** (blanco_ml, factor_aceite_fruta, factor_aceite_emulsion, hora_inicio_dia, blanco_centrifugas_ml, umbral_paralelo_lh, turnos_fin_episodio, turnos_para_alertar, sigma_alerta_inmediata): opcionales y **sin valor por defecto**. Lo que está vacío en la planilla queda vacío en la base y la pantalla lo muestra como "a definir". Los cálculos los leen **siempre** con `core.parametros.parametros_planta()` (o `validar_parametros()` si ya tienen la planta), pidiendo solo los que necesitan; dan un error claro si falta alguno. Nunca uses los valores de LEDESMA para otra planta.
- **Turnos** (hoja Turno): turno y día de producción se calculan con `core.turnos.turno_de()` y `fecha_produccion()`, nunca se guardan. **Excepciones:** las tablas `Alerta` y `TurnoEvaluado` guardan fecha de producción y turno, porque son eventos y los necesitan para no duplicarse. El turno N cruza la medianoche.

## Stack (versión 0, local)
- Python 3.13, entorno virtual en `.venv`.
- **Streamlit** para las pantallas, **Plotly** para los gráficos.
- **SQLAlchemy** sobre **SQLite** (`data/app.db`). Más adelante se migra a PostgreSQL en Azure: no uses nada específico de SQLite.
- pandas, NumPy, SciPy, statsmodels para cálculos y estadística.
- **pytest** para las pruebas.

## Estructura
```
app/            # Streamlit: app.py (inicio) y pages/ (una página por pantalla)
core/           # lógica sin interfaz: models.py, db.py, calculos.py, spc.py, capacidad.py, importadores
tests/          # pruebas pytest, un archivo por módulo de core/
data/           # base SQLite y archivos de clientes  →  NO se sube a GitHub (.gitignore)
docs/           # documentos del proyecto (definición, arquitectura, mapeos)
```
**Regla clave:** toda la lógica (cálculos, estadística, acceso a datos) va en `core/`, nunca dentro de las páginas de Streamlit. Así se reutiliza cuando pasemos a FastAPI + Next.js.

## Modelo de datos (núcleo)
```
Cliente ─< Planta ─< Linea ─< Equipo ─< PuntoMuestreo ─< Analisis
Equipo ─< LecturaOperacion (fecha, programa, MTPH setpoint, MTPH real)
RangoReferencia (variable, fruta, país, mínimo, máximo, fuente)
LimitesControl (variable, punto, período base, LC, LCS, LCI)  ·  Alerta
```
- Tipos de equipo: eBOE 6107E, mBOE 6108, bBOE 6107B, JEX 620/720, centrífuga primaria, desludger, pulidora, finisher.
- **Guardar siempre los datos crudos** (mL, blanco, normalidad, gramos, factor) y calcular el resultado. Nunca guardar solo el resultado.
- Cada registro guarda quién lo cargó y cuándo.
- **Datos importados:** AnalisisScott es único por punto de muestreo + fecha_hora; LecturaOperacion por equipo + fecha_hora; RegistroProduccion por planta + fecha de producción + turno + proceso (un turno puede tener dos procesos; las toneladas del turno se suman con `core.produccion.toneladas_turno()`). El campo `origen` guarda archivo › hoja › fila (`texto_origen()`); queda vacío en la carga manual. Si el Excel trae filas con la misma clave y datos distintos, no se carga ninguna y van al reporte (`core.duplicados.separar_repetidas()`).

## Fórmulas (fuente: técnica Scott y manual BOE de Brown)
| KPI | Fórmula | Referencia limón Argentina |
|---|---|---|
| Aceite en fruta (kg/MT) | (mL − blanco) × N × 68,841 / g | Disponible 4–8 kg/MT |
| Aceite en licor o fase (%) | (mL − blanco) × N × 3,442 / g | según el punto |
| Remoción del BOE (%) | 100 × (entrada − salida) / entrada | 85–90 % |
| Eficiencia simple del desludger (%) | (alimentación − fase media) / alimentación × 100 | 94–98 % |
| Eficiencia real del desludger (%) | [(F×f) − (M×m) − (S×s)] / (F×f) × 100 | — |
| Eficiencia de la pulidora (%) | (E − S) / [E − (S×E/100)] × 100 | 99 % |
| Alimentación al desludger | — | 0,70–0,90 % |
| Fase liviana del primario | — | 75–85 % |
| Fase media | — | 0,05–0,10 % |
| Barros del desludger | — | 0,30–0,50 % |
| °Brix de la fase media | — | < 4,0 (3,0 indica vaciar el BOE) |

CAF = kg de aceite por tonelada de fruta a la salida del BOE (menor es mejor).

## Estadística
- Cartas: I-MR, X̄-R, X̄-S con constantes estándar (A2, A3, D3, D4, B3, B4, d2, c4).
- Fase I: límites calculados con un período base y luego **congelados**. Fase II: cada dato nuevo se evalúa contra esos límites.
- Reglas de Nelson 1–8, configurables por variable.
- Las alertas ABIERTAS se evalúan por turno con la carta entre/dentro (I-MR-R/S de Minitab, tipo_carta "I-MR-S"): subgrupo = turno de producción; los límites del promedio del turno salen de la variación ENTRE turnos (MR̄ de los promedios / d2, iguales para todo n) y la carta S de la variación DENTRO (S barra); reglas_alerta "1,2,3,rango" (crean alertas; el pH solo "rango", con "1,2,3,5,S" de marca) y reglas_marca "5,S" (se calculan y se marcan en la carta, no alertan; "S" = regla 1 de la S); turnos de 1 muestra: promedio sí, S no. Nelson solo del lado malo (`lado_alerta` en VARIABLES_CONTROL): eficiencia → abajo; aceite en fruta salida, aceite en descarga y °Brix → arriba; aceite en emulsión, pH y CP pesada → ambos. Reglas 1 y 2: lado del punto respecto de la LC; regla 3: sentido de la tendencia. La X̄-S y la I-MR quedan como historial. Persistencia: un episodio se crea recién cuando la misma regla se viola en `turnos_para_alertar` turnos seguidos del mismo ámbito (parámetro de la planta, 2 en LEDESMA) y empieza en el primero; excepción: regla 1 más allá de ±`LimitesControl.sigma_alerta_inmediata` σ entre del lado malo → alerta inmediata (`Alerta.inmediata`); al congelar se copia el parámetro de la planta (4 en LEDESMA); vacío = sin alerta inmediata (aceite en emulsión). Alertas "histórico" de 2026 cargadas el 09/11/2026: 189 episodios. Episodios: los turnos que vuelven a violar la misma regla se suman a la alerta en curso (turnos_afectados, fin); se cierra sola tras `turnos_fin_episodio` turnos sin violarla (parámetro de la planta, 3 en LEDESMA); una alerta cerrada a mano no se reabre. El rango también con el promedio del turno. La I-MR queda solo para analizar en la pantalla de cartas. pH de la emulsión: una sola serie por PLANTA (ámbito planta). Los motivos de dato inválido (copiada, fuera de rango físico, valor pegado, verificar normalidad) excluyen solo variables de g y mL (aceite %, pérdidas), nunca la CP, el caudal ni el tiempo de descarga. MSD 50: sin límites de control por ahora, solo rango. LCI negativo → "sin límite inferior".
- Límites congelados (`core/limites.py`, tabla `LimitesControl`): uno vigente por variable + ámbito (equipo o planta) + fruta; los anteriores quedan como historial. Fase I sin exclusiones automáticas salvo los datos inválidos de centrífugas (copiada, fuera de rango físico, valor pegado, normalidad dudosa), con motivo "dato inválido: …" en `LimitesExclusion`; el usuario puede excluir otros, siempre con motivo. Campos `reglas_alerta` (por defecto "1,2,3,rango") y `reglas_marca` (por defecto "5,S" por turno, "5,MR" en I-MR).
- Alertas (`core/alertas.py`, tablas `Alerta` y `AlertaDato`): `evaluar_nuevos()` contra los límites vigentes (mirando los puntos anteriores de la fase II) y contra los rangos con genera_alerta = sí, respetando lado_capacidad. Una alerta por origen + regla + variable + equipo + turno de producción. Datos importados del histórico → estado "histórico". Misma regla y variable en los 3 eBOE en el mismo turno → UNA alerta de ámbito planta (`comun_eboe`, comentario "posible causa común a los 3 eBOE: fruta del día, laboratorio o proceso"), no tres de equipo; las de equipo solo cuando no es común. Rango de aceite en emulsión (licor) 0,8–1,1 %: genera_alerta = no hasta revisarlo con la planta (se dibuja como banda); la descarga de la MSD 50 sigue con alerta. `alertas_de(tabla, dato_id)` da las alertas de un análisis.
- Carga Scott (`core/carga_scott.py`, página `app/pages/7_Carga_Scott.py`): una ronda por envío, con entrada, salida, emulsión y operación de cada eBOE en marcha; normalidad por tipo de punto (aviso si cambia en un factor ≥ 5); avisos de entrada copiada, g < 1 y valor fuera del percentil 0,5–99,5 del punto. Al guardar: chequeo inmediato (regla 1 más allá de sigma_alerta_inmediata) del turno en curso si tiene 2 rondas o más; los turnos cerrados sin `TurnoEvaluado` se evalúan completos (una inmediata que el promedio completo ya no sostiene queda con `confirmada` = no y el comentario "no confirmada al cierre del turno"; no se borra) (al guardar y al abrir la página, como mucho cada 10 min) y se marcan.
- Edición (`core/edicion.py`, página `app/pages/8_Datos.py`): lista por ronda; corregir (`editar_datos`), borrar un dato o la ronda, cambiar la hora de la ronda y restaurar, siempre con motivo. Historial en la tabla `Cambio` (campo, antes, después, quién, cuándo, motivo; un borrado guarda la fila completa). Permiso en un único punto: `core.cambios.puede_editar(usuario, dato)` (hoy siempre sí; tarea 22). Después de cada cambio: marca de entrada repetida y reevaluación del turno (`reevaluar_turnos`); una alerta que queda sin datos no se borra: se cierra sola ("cerrada" por "sistema", comentario "dato corregido"; una histórico conserva su estado). Los importadores no pisan ni recrean datos con historial en `Cambio` (reporte: "corregido en la app").
- Gestión de alertas (`core/gestion_alertas.py`, página `app/pages/9_Alertas.py`): abierta → en análisis → cerrada (no se reabre; acepta comentarios); histórico no cambia de estado pero acepta causa, acción y comentario; todo en `Cambio`.
- Capacidad (`core/capacidad.py`, pestaña Capacidad de Gráficos de control): Cp/Cpk con sigma dentro (MR̄/d2), Pp/Ppk con sigma total; Anderson-Darling; si no es normal (p < 0,05), índices por percentiles (ISO 22514) y Box-Cox. Los límites salen de RangoReferencia con `lado_capacidad` ("ambos", "inferior", "superior"): eficiencia eBOE → solo LIE 80 (el 80–90 sigue siendo la banda objetivo); °Brix → solo LSE. Estabilidad con la carta I-MR y la regla 1 de Nelson: si es inestable, los índices son orientativos y se mira el % observado. Primero se muestra el % observado fuera de especificación.
- **Todo resultado estadístico se valida contra Minitab con una prueba automática.**

## Reglas de análisis
- Los promedios de eficiencia, rendimiento y aceite por día, turno, proceso o temporada se **ponderan por toneladas procesadas** cuando hay dato de producción; si no hay, se usa el promedio simple y se aclara en pantalla.
- Todo gráfico, tarjeta o tabla muestra el **N** con el que se calculó y avisa cuando **N < 3**.
- Los outliers se **marcan, nunca se borran**.
- Todas las páginas de análisis usan la **barra de filtros global** (fechas de producción, eBOE, turno, proceso, programa).

## Reglas de código
- Funciones puras en `core/` con docstring: qué calcula, fórmula, unidades y fuente.
- Validar entradas (por ejemplo, g > 0) y dar mensajes de error claros en español.
- Nombres en español para el dominio (`remocion_boe`, `punto_muestreo`); código simple y legible antes que ingenioso.
- Cada función nueva de `core/` lleva su prueba en `tests/`.
- Nunca poner datos reales de clientes ni contraseñas en el código ni en GitHub.

## Comandos
```
.venv\Scripts\activate          # activar el entorno (Windows)
pip install -r requirements.txt
pip install -e .                # instala el proyecto (core/) en modo editable; una sola vez
python -m core.crear_base         # crea las tablas nuevas y agrega las columnas nuevas (antes, copiá data/app.db a data/respaldos/)
python -m core.importar_maestros  # carga los datos maestros desde data/maestros_clientes.xlsx
python -m core.importar_ledesma   # carga el histórico de LEDESMA (hojas BOE y Resumen)
python -m core.importar_plc       # carga los CSV del PLC de los eBOE (data/plc/ o la carpeta que se pase)
python -m core.importar_centrifugas  # carga la planilla de centrífugas (--simular: solo arma el reporte)
streamlit run app/app.py        # abrir la app
python -m core.copia_prueba      # copia data/app.db a data/app_prueba.db (modo prueba)
$env:APP_DB = "datapp_prueba.db"; streamlit run app/app.py   # app en modo prueba (PowerShell); cartel en todas las páginas
pytest                          # correr las pruebas
```

## Gráficos
Plotly, estilo sobrio: línea de datos oscura, límites de control punteados en gris, puntos fuera de control resaltados con forma y color (rojo para regla 1, naranja para el resto), tooltip con fecha, turno y analista. Nunca dos ejes Y en un mismo gráfico.
Unidades: kg/MT para aceite en fruta y MTPH para caudal de fruta.
