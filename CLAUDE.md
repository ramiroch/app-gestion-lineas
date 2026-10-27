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
3. Importación del histórico desde los Excel de LEDESMA (laboratorio, operación y producción). Las frutas y los equipos de la planta salen de esos archivos y de `data/maestros_ledesma.xlsx`: no los inventes.
4. Cartas de control (I-MR, X̄-R, X̄-S) con reglas de Nelson, y capacidad (Cp, Cpk, Pp, Ppk).
5. Dashboard de la planta con semáforos y alertas.

## Stack (versión 0, local)
- Python 3.12 o superior (hoy se usa 3.13), entorno virtual en `.venv`.
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

## Fórmulas (fuente: técnica Scott y manual BOE de Brown)
| KPI | Fórmula | Referencia limón Argentina |
|---|---|---|
| Aceite en fruta (kg/t) | (mL − blanco) × N × 68,841 / g | Disponible 4–8 kg/t |
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
- Capacidad: Cp/Cpk con sigma dentro (MR̄/d2), Pp/Ppk con sigma total; Anderson-Darling; Box-Cox si no es normal.
- **Todo resultado estadístico se valida contra Minitab con una prueba automática.**

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
streamlit run app/app.py        # abrir la app
pytest                          # correr las pruebas
```

## Gráficos
Plotly, estilo sobrio: línea de datos oscura, límites de control punteados en gris, puntos fuera de control resaltados con forma y color (rojo para regla 1, naranja para el resto), tooltip con fecha, turno y analista. Nunca dos ejes Y en un mismo gráfico.
