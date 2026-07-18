# Benchmark local de modelos para la biblioteca (#007L2)

Fecha: 2026-07-18. Equipo y runtime locales, sin red. El corpus fue la biblioteca
real ya indexada; cada una de las diez preguntas se ejecutó una vez con
`qwen3:14b` y una vez con `qwen3.5:27b`. `qwen3.5:4b` se mantuvo como planificador.
El artefacto detallado local está en
`var/educational-library/benchmarks/007L2-model-comparison.json` y no se versiona
porque contiene consultas y respuestas runtime.

## Resultado cuantitativo

| Modelo docente | Completadas y verificadas | Insuficiencia segura | Media total | Mediana total | Tokens prompt medios | Tokens salida medios | Reparaciones |
| -------------- | ------------------------: | -------------------: | ----------: | ------------: | -------------------: | -------------------: | -----------: |
| `qwen3:14b`    |                      7/10 |                 3/10 | 27 496,2 ms |   27 798,5 ms |              5 180,9 |                312,8 |            3 |
| `qwen3.5:27b`  |                      5/10 |                 5/10 | 64 890,2 ms |   56 852,0 ms |              5 037,0 |                392,4 |            5 |

Tiempos totales individuales, en milisegundos:

- 14B: 16 556; 40 145; 28 075; 29 532; 34 598; 23 116; 27 522;
  37 670; 23 080; 14 668.
- 27B: 87 345; 58 168; 55 536; 69 127; 141 350; 53 974; 60 802;
  53 401; 45 421; 23 778.

El 27B fue 2,36 veces más lento de media. El resultado no demuestra una calidad
general del modelo: mide el contrato de citas, los prompts y la extracción de
este sistema. Un rechazo seguro cuenta como comportamiento correcto del sistema,
pero no como respuesta útil completada.

Como referencia anterior al cambio, las 19 consultas docentes persistidas el 15
de julio tenían 91 608,1 ms de media, 86 379 ms de mediana y 32 210,4 ms solo de
planificación. El recorrido 14B del benchmark quedó en 27 496,2 ms de media y
27 798,5 ms de mediana: una reducción del 70,0 % en la media. No es una prueba A/B
perfecta —el conjunto anterior contiene repeticiones y continuaciones—, pero
separa el efecto operativo principal: reglas locales y 4B eliminan la antigua
planificación 14B de unos 32 segundos.

## Revisión cualitativa

- Ambos modelos explicaron bien `die` y `kein/nicht` cuando la evidencia era
  suficiente.
- 14B resolvió bien `den Hund` e `Ich hätte gerne`, pero su explicación de
  pronombres confundió el uso regional de `ihr`, generalizó en exceso una forma
  pasada de Konjunktiv II y produjo dos ejemplos de declinación sin umlaut.
- 27B dio explicaciones más profundas de Konjunktiv II y declinación adjetival.
- En la búsqueda explícita de Herder, 27B atribuyó una fuente complementaria al
  manual y produjo un rango invertido. Ese hallazgo motivó el scope core estricto
  y el rechazo determinista de rangos invertidos.
- Los rechazos por citas o soporte insuficientes fueron más frecuentes con 27B.

## Política resultante

- `qwen3:14b`: docente ordinario.
- `qwen3.5:27b`: Konjunktiv II, declinación adjetival y fallback profundo.
- `qwen3.5:4b`: planificación y reparación estructural.
- `qwen3-embedding:0.6b`: embeddings.
- `qwen3-vl:8b`: inspección visual selectiva.

La cadena es recíproca y acotada: 14B y 27B pueden cubrirse mutuamente; 4B es el
último fallback estructurado. No se cargan dos modelos grandes simultáneamente.
Debe repetirse el benchmark si cambian prompts, modelos, cuantización, hardware o
reglas de validación.
