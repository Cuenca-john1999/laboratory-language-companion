# ADR 0019: Extracción cloud desacoplada y artefactos preservados

- Estado: aceptada
- Fecha: 2026-08-18

## Decisión

La extracción cloud se integra como un tipo de `document_processing_run` fijado a una
`source_version`. Los adaptadores implementan `CloudKnowledgeProvider`; el registro selecciona
únicamente proveedores configurados cuyas capacidades satisfacen el job. El core no importa ni
ramifica por Gemini.

La respuesta atraviesa fronteras explícitas `RAW → transporte compacto → canonical LLC → informe
de validación`. Cada frontera produce un artefacto local con hash. RAW se persiste antes de
interpretar JSON, de modo que un error de esquema no destruya una extracción recuperable. Los
errores nativos se normalizan sin ocultar un detalle diagnóstico sanitizado y sin modificar el
estado de la versión documental.

Gemini es el primer adaptador real, cargado como dependencia opcional. Las llamadas solo ocurren
al ejecutar explícitamente un run; el inicio de LLC y la ingestión normal permanecen locales.

## Consecuencias

Un segundo proveedor solo necesita adaptador, capabilities, normalización de errores y registro.
No cambia transporte, canonical, validadores, persistencia, retrieval ni tutor. La selección AUTO
actual es determinista con un único proveedor compatible y queda preparada para una política de
routing posterior, sin límites de cuota hardcodeados.
