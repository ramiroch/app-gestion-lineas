# Resultados de Minitab para `tests/test_capacidad.py`

La prueba `test_igual_que_minitab` lee estos archivos; si no están, se saltea. Los CSV **no se suben a GitHub**
(`.gitignore`), porque pueden tener datos reales de la planta: quedan solo en tu computadora.

## `capacidad_resultados.csv` (uno por caso)

Separador `;` y coma decimal (como lo guarda Excel en español). Columnas:

| columna | qué va |
|---|---|
| caso | nombre corto, por ejemplo `eficiencia_eboe2_2026` |
| archivo_datos | el CSV con los datos de ese caso (en esta carpeta) |
| lie, lse | límites de especificación (vacío si no hay) |
| subgrupo | tamaño de subgrupo (vacío = datos individuales) |
| n, media, sigma_dentro, sigma_total | de la salida de Minitab ("StDev(Within)" y "StDev(Overall)") |
| cp, cpk, pp, ppk | índices de Minitab |
| ppm_esperado_total | "Expected Overall Performance, PPM Total" |
| ad, p | prueba de normalidad de Anderson-Darling ("AD" y "P-Value"); si dice "< 0,005", dejar p vacío |

Las columnas que se dejan vacías no se comparan.

## Archivo de datos de cada caso

Una columna `valor`, en el **orden de tiempo** en que se cargaron en Minitab (el sigma dentro de datos
individuales usa el rango móvil, así que el orden importa). Separador `;` y coma decimal.

## En Minitab

Stat › Quality Tools › Capability Analysis › Normal, con "Subgroup size" 1 para datos individuales. Dejar las
opciones por defecto de Estimate (rango móvil promedio de largo 2; con subgrupos, "Pooled standard deviation" con
"Use unbiasing constants"). Si Minitab usa la constante c4 también en el sigma total, se compara con
`capacidad(..., usar_c4_total=True)`.
