# ADR 0005: Repetición espaciada y correcciones append-only

- Estado: aceptado
- Fecha: 2026-07-11

## Contexto

Las puntuaciones libres y las evaluaciones opacas dificultan explicar por qué
cambia el progreso. También debe ser posible corregir una evaluación equivocada
sin modificar ni borrar el historial original.

## Decisión

Una evaluación acepta uno de cuatro resultados categóricos:

- `failure`;
- `partial`;
- `correct_with_help`;
- `correct_without_help`.

`learning-engine-v2` asigna respectivamente `0.0`, `0.4`, `0.7` y `1.0`. La
primera evidencia establece el dominio en ese valor; a partir de la segunda:

```text
mastery'   = 0.65 × mastery + 0.35 × outcome_score
confidence = min(0.95, effective_evidence_count / 10)
```

Ambos valores se redondean y se acotan entre 0 y 1. `correct_without_help`
incrementa la racha sin ayuda; cualquier otro resultado la reinicia y
`failure` incrementa además el contador de lapsos. El criterio predeterminado
de dominio exige dominio ≥ 0.80, confianza ≥ 0.60, seis evidencias y racha sin
ayuda ≥ 2; cada habilidad conserva sus propios umbrales versionados.

Los intervalos son: fallo 1 día, parcial 2 días, correcto con ayuda 3 días y
correcto sin ayuda 3/7/14/30/60 días para rachas 1/2/3/4/5+. Ningún LLM
establece estos valores.

Cada `SkillEvidence` guarda resultado, valor derivado, versión del motor y una
instantánea antes/después del estado agregado. Triggers SQLite impiden editar o
borrar el ledger. Los UUID hacen idempotente el registro: repetir el mismo UUID
y contenido devuelve la misma evidencia; reutilizarlo con otro contenido
produce conflicto.

Para corregir una evaluación se envía otra evidencia con un UUID nuevo y una
referencia `corrects_submission_id`. La nueva fila sustituye lógicamente a la
anterior; no la modifica. El motor reconstruye `StudentSkill` usando solo las
evidencias efectivas. Una evidencia solo puede ser sustituida una vez, pero una
corrección puede ser corregida de nuevo formando una cadena auditable.

## Consecuencias

El estado agregado se puede reconstruir y una corrección no infla el contador
de evidencias. Las instantáneas históricas describen lo que decidió la versión
del motor en aquel momento; la proyección actual describe el ledger efectivo.
Cambiar valores, intervalos o fórmula requiere una versión nueva y una política
explícita de reconstrucción.
