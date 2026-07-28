# ADR 0012: Ruta Herder canónica desde el índice editorial

- Estado: aceptado
- Fecha: 2026-07-22

## Contexto

Las 53 secciones `system_suggested` del manual fueron derivadas por regex y OCR.
Incluyen encabezados del propio índice, fragmentos de ejemplos y títulos dañados;
por ello no pueden actuar como estructura académica ni como numeración visible.
El usuario aportó un PDF verificado del índice y una transcripción estructurada
corregida de sus 51 temas y subapartados.

Los estados, sesiones, notas, dudas y vínculos de workbook ya viven en la base
principal. Son datos personales y no pueden perderse al reemplazar una proyección
editorial reconstruible.

## Decisión

El esquema 5 de la SQLite de biblioteca conserva importaciones versionadas del
índice de referencia, temas canónicos, outline, mappings legacy, variantes visuales
y auditoría. La transcripción corregida es la fuente estructurada y el PDF verificado
es la autoridad documental. Una importación solo se activa si contiene exactamente
Tema 1–51, sin huecos o duplicados, con páginas no decrecientes y todos los anchors
canónicos. Un candidato inválido nunca sustituye una ruta activa.

Cada tema usa una clave estable formada por fuente, versión y número editorial. El
título no es identidad. Los subapartados pertenecen al tema y no se promueven a temas
ni a estados personales obligatorios.

Se mantienen tres coordenadas independientes:

1. página del PDF de referencia del índice;
2. página impresa del libro;
3. página del PDF digital del manual, incluida disposición y región cuando estén
   verificadas.

Los rangos impresos derivados terminan antes del tema siguiente y se marcan
`calculated`. No se inventan páginas o regiones manuales. La ubicación confirmada
de Akkusativ conserva libro p. 162, manual PDF p. 89, `double_page` y región
`unknown`.

Las 53 secciones anteriores no se eliminan. La reconciliación combina origen y
versión, clave temática legacy, orden de páginas y título normalizado. Sus estados
son `exact`, `probable`, `ambiguous`, `unmatched` y `rejected`. Solo `exact` puede
servir como alias de lectura para presentar datos personales existentes bajo un
título canónico; nunca modifica las filas personales. Los demás estados requieren
revisión y conservan snapshots legacy.

Las revisiones de temas son idempotentes, auditables y reversibles. La importación
administrativa usa archivos locales, no se expone como path de filesystem en la API
pública y no requiere LM Studio al cargar `/study`.

## Consecuencias

- `/study` puede mostrar Tema 1–51 de forma continua y bilingüe.
- Reconstruir o versionar la ruta no elimina datos personales.
- La base principal y Alembic permanecen sin cambios.
- La biblioteca crece con un esquema editorial reconstruible y un backup previo de
  la versión 4.
- Los mappings probables no dan continuidad automática; requieren una decisión
  humana si afectan una sesión.
- Cambiar el índice crea otra versión y no reinterpreta silenciosamente revisiones
  ni evidencia histórica.

## Alternativas descartadas

- **Corregir las 53 secciones OCR in situ.** Confundiría hipótesis de extracción con
  la estructura editorial y rompería identidades históricas.
- **Guardar la ruta en la base principal.** Mezclaría datos reconstruibles del
  documento con datos personales y obligaría a una migración innecesaria.
- **Mapear únicamente por número o similitud.** Ambos producen falsos positivos con
  el índice y con fragmentos gramaticales parecidos.
- **Inferir toda la paginación manual desde una sola ancla.** Los escaneos dobles y
  regiones desconocidas hacen que esa proyección no sea defendible.

## Revisar cuando

- llegue una nueva versión física o digital del manual;
- existan suficientes anchors confirmados para calibrar rangos manuales;
- un mapping probable afecte datos personales que el usuario quiera reconciliar;
- se necesite edición jerárquica avanzada, división o unión de temas.
