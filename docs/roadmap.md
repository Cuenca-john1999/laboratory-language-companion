# Roadmap

## Milestone 0 — Fundación

Perfil, persistencia migrada, dashboard real, proveedor Ollama reemplazable, chat local, estados de error, documentación y pruebas.

## Milestone 1 — Learning Engine

Núcleo funcional: currículo A0–A1 versionado, prerrequisitos, ledger de
evidencia idempotente con correcciones, dominio y repetición espaciada
deterministas, plan diario persistido y vistas de plan/habilidades. La entrega
automática de ejercicios y un diagnóstico explícito quedan para un milestone
posterior; no se simulan con datos inventados.

### Próximos pasos del diagnóstico textual

El contrato entre eje diagnóstico y habilidad curricular queda fijado por el
ADR 0008: el eje gobierna el recorrido, el mapping curricular es opcional y los
resultados no se proyectan todavía al progreso normal.

El orden de trabajo es:

1. `#006E4C`, completado: validación estática, escenarios y simulación
   reproducible de alcanzabilidad;
2. `#006E4D`, completado: banco `draft` reequilibrado con dos tareas por eje;
3. `#006E4E` y `#006E4E1`, completados: sesiones end-to-end y confianza
   conservadora para evidencia asistida;
4. `#006E4F1`, completado: infraestructura compatible para opciones con ID
   estable, manteniendo intacto el banco v1;
5. `#006E4F2`: migrar editorialmente las siete tareas cerradas del banco a IDs
   estables, con nuevas versiones y auditoría pedagógica;
6. realizar una auditoría final antes de `reviewed`;
7. iniciar el frontend para el alumno solo después de cerrar esos contratos.

Hasta entonces se usan la herramienta editorial, pruebas HTTP y el simulador de
alcanzabilidad; ningún banco `draft` se ofrece en producción.

## Milestone 2 — Biblioteca y memoria pedagógica

La fundación local ya incluye catálogo incremental, extractores, FTS5,
KnowledgeUnits revisables, roles core Herder, embeddings multilingües reales,
recuperación híbrida, consultas docentes fundamentadas, enrutamiento de modelos
y una experiencia **Pregunta a tu biblioteca** con progreso, continuaciones e
historial. También existen calidad y variantes por página, reextracción selectiva
y revisión visual local; no se ejecuta OCR visual masivo.

La memoria pedagógica verificable está implementada sobre el esquema 4 de la
biblioteca: conceptos y alias multilingües, páginas PDF/impresas, doble escaneo,
regiones, feedback independiente, correcciones reversibles e integración
prudente con Teacher. Las consultas deterministas de localización, su fallback
híbrido, caché invalidable y tarjeta de calibración ya reutilizan esa memoria sin
generación innecesaria. Quedan para después la revisión visual de regiones
personalizadas, calibración masiva y cualquier proyección al progreso normal,
que requeriría una decisión arquitectónica separada.

La consulta educativa y los futuros **Ejercicios rápidos** son experiencias
separadas: los ejercicios no forman parte del pipeline docente ni escriben
progreso. Quedan pendientes OCR convencional cuando exista una herramienta local,
transcripción local, revisión editorial de las secciones detectadas, streaming de
tokens validados y la experiencia final de lecciones. El perfeccionamiento
diagnóstico queda congelado temporalmente después de `#006E4F1`.

## Milestone 3 — Escucha y voz

Ejercicios auditivos, speech-to-text, text-to-speech y conversación de voz en vivo.

## Milestone 4 — Preparación avanzada

Simulaciones B1/B2, lenguaje profesional y clínico, recuperación de documentos.

## Milestone 5 — Portabilidad

Restauración asistida, exportaciones portables y políticas avanzadas de
retención. El backup local básico y consistente forma parte de la fundación.
