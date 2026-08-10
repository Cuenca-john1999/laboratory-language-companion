# Seguridad y privacidad local

## Límite de red

LLC no requiere cuentas, claves API ni servicios cloud. `dev.sh` enlaza
FastAPI y Next.js a `127.0.0.1` y rechaza URLs de API/LM Studio que no sean
loopback. La telemetría de Next.js se desactiva durante el desarrollo. LM Studio es
opcional y su indisponibilidad no bloquea el resto de la aplicación.

## Datos privados

El perfil, las sesiones y el futuro historial pedagógico son datos privados.
SQLite, materiales educativos, índices, embeddings, audio, transcripciones,
configuración local y backups están excluidos de
Git. Los scripts no escriben logs persistentes ni muestran el contenido de
`.env`, conversaciones o copias; los errores de los servidores sí permanecen
visibles en la terminal para poder diagnosticarlos.

La exclusión de Git no cifra el SSD. Se recomienda cifrado de disco, una sesión
de macOS protegida y backups en una unidad local cifrada. En volúmenes montados
con `noowners`, macOS puede no aplicar los bits POSIX como en el disco interno;
la seguridad física y el cifrado son especialmente importantes.

## Backups

`backup.sh` establece una máscara privada, crea SQLite mediante una snapshot
consistente y no sigue enlaces simbólicos. No incluye modelos ni sus pesos. Si
existe `.env`, genera una copia saneada con una lista cerrada de opciones
locales no secretas; las claves desconocidas y las URLs con credenciales
embebidas se omiten, y solo sus nombres se anotan en el manifiesto. No copia
`config/local` de forma genérica porque no se puede demostrar que archivos
futuros carezcan de credenciales.

Un destino bajo `./backups` está en el mismo SSD y sirve para rollback, no para
recuperación ante pérdida física; usa `LLC_BACKUP_DIR` para otra unidad.

El manifiesto contiene nombres de archivos, tamaños y hashes, pero no vuelca el
contenido. Aun sin secretos de `.env`, todo el bundle debe tratarse como privado
porque la base contiene datos personales y pedagógicos.

## Git

Antes del primer commit se debe inspeccionar `git status --ignored`, revisar el
staging archivo por archivo y confirmar que no aparecen bases, `.env`, backups,
audio, transcripciones ni pesos. Si un secreto llega a Git, eliminar el archivo
en un commit posterior no basta: hay que retirarlo del historial y rotar el
secreto.

Los directorios de código llamados `models` no se ignoran globalmente. Solo se
anclan los almacenes locales en la raíz para conservar rastreados los modelos
SQLAlchemy de la API.

La biblioteca no sigue symlinks, no ejecuta archivos, no extrae archivos
comprimidos genéricos y rechaza rutas fuera de la raíz. Los temporales de PDF se
crean en el runtime, nunca junto al original. Los límites de tamaño, páginas,
texto y miembros ZIP reducen consumo accidental; los errores públicos omiten
trazas y texto completo. La búsqueda local excluye soluciones por defecto y la
generación solo puede citar IDs recuperados.

La reextracción visual es selectiva: renderiza una sola página con resolución y
tiempo limitados, envía únicamente esa imagen al modelo local, elimina el temporal
y conserva la salida como variante con provenance. Nunca sobrescribe el PDF ni la
extracción anterior. Si OCR o visión no están disponibles, la API responde con un
fallo de capacidad saneado y no intenta descargar herramientas o modelos.

Las consultas docentes persisten localmente su pregunta, respuesta y provenance
en la base reconstruible de biblioteca. Los DTO públicos no contienen rutas
absolutas, prompts, claims privados, chunk IDs ni reglas de ranking. El contexto
pedagógico enviado a Qwen se limita a idioma de explicación, nivel orientativo,
preferencias, objetivos y categorías de error; no incluye respuestas ni textos
personales completos. El servicio valida y repara citas una vez y responde con
insuficiencia si no puede demostrar el soporte.

## Dependencias web auditadas

La revisión en vivo del 12 de julio de 2026 encontró dos avisos moderados en la misma
cadena: Next.js estable 16.2.10 incluye PostCSS 8.4.31, afectado por
[`GHSA-qx2v-qp2m-jg93`](https://github.com/advisories/GHSA-qx2v-qp2m-jg93)
hasta PostCSS 8.5.9 inclusive (la primera versión corregida es 8.5.10). El ataque requiere procesar CSS no
confiable y volver a insertarlo en una etiqueta `style`. LLC solo compila
CSS mantenido en el repositorio, no admite subida o transformación de CSS del
usuario y escucha en loopback; esa ruta no es explotable en el uso normal.

`npm view` confirma que 16.2.10 sigue siendo el `latest` estable y todavía fija
PostCSS 8.4.31; no existe una actualización estable compatible que elimine el
aviso. Aunque npm clasifica ambos nodos dentro de dependencias de producción,
la ruta relevante de PostCSS se ejecuta al compilar CSS. El [mantenedor de
Next](https://github.com/vercel/next.js/issues/93234) indicó además que PostCSS
se usa en build y que el aviso no afecta al uso normal de Next. Se acepta temporalmente y
se revisará al publicarse el siguiente estable. No debe ejecutarse
`npm audit fix --force`: npm propone degradar a Next 9.3.3, un cambio destructivo.
Tampoco se adopta una versión canary para silenciar el informe.

React 19.1.1 aparece dentro de una familia mencionada por el [aviso oficial de
React Server
Components](https://react.dev/blog/2025/12/03/critical-security-vulnerability-in-react-server-components),
pero los paquetes afectados son `react-server-dom-*` y la
integración la proporciona Next. LLC usa Next 16.2.10, posterior a las
líneas corregidas, y `npm audit` no detectó esa vulnerabilidad crítica en el
árbol instalado. Se mantendrá la vigilancia y se actualizará el conjunto
Next/React de forma conjunta, con build y pruebas, cuando exista una versión
estable compatible.
