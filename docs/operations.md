# Operaciones locales

## Principios

Los scripts calculan la raíz a partir de su propia ubicación y cambian a ella
antes de trabajar. No dependen del directorio desde el que se invoquen ni
contienen la ruta absoluta del SSD. Las rutas SQLite relativas se interpretan
respecto a la raíz del proyecto.

Un entorno virtual de Python sí contiene rutas generadas por Python. Si cambia
el nombre o punto de montaje del SSD, recrea `.venv` y vuelve a instalar las
dependencias; no copies el entorno virtual entre rutas.

## Diagnóstico

```bash
./scripts/doctor.sh
```

El diagnóstico comprueba:

- disponibilidad y espacio del volumen;
- escritura real en `data`;
- Node.js, npm, Python 3.12 y `.venv`;
- dependencias npm y Python;
- integridad SQLite y revisión Alembic;
- instalación, servidor e inventario de modelos de LM Studio;
- ocupación de los puertos 3000 y 8000.

Un símbolo `✗` produce código de salida 1. Los símbolos `!` son avisos: por
ejemplo, LM Studio apagado o una migración que `dev.sh` puede aplicar. El script
legado `check-environment.sh` es solo un wrapper y se conserva temporalmente.

## Desarrollo

```bash
./scripts/dev.sh
```

El script no instala dependencias y no inicia LM Studio. Valida primero los
requisitos y que ambos puertos estén libres. Si SQLite ya existe, ejecuta
`quick_check`. Antes de una migración pendiente crea un bundle consistente de
solo la base con la etiqueta `pre-migration`; después ejecuta Alembic y verifica
que la revisión final coincida con el único `head`.

FastAPI y Next.js escuchan solo en `127.0.0.1`. La salida de ambos queda en
primer plano para no ocultar fallos. Cuando uno termina o se pulsa `Ctrl-C`, el
script envía una señal de cierre a los dos y espera sus procesos descendientes.

La configuración pública de la web se lee de `.env` como datos, sin ejecutar el
archivo como shell. Esto permite mantener un único `.env` en la raíz aunque
Next.js se ejecute desde su workspace.

## Aplicación nativa de control para macOS

`./scripts/build-macos-app.sh` compila en release el paquete Swift de
`apps/macos-controller` y crea `dist/DeutschOS.app`. No requiere abrir Xcode,
no añade dependencias y firma el bundle localmente de forma ad hoc. Usa el SDK
15.4 incluido en las Command Line Tools cuando está presente porque algunas
CLT 26.6 distribuyen un compilador y un SDK 26.5 con revisiones Swift
incompatibles. `DEUTSCHOS_MACOS_SDK` permite seleccionar otro SDK.

La aplicación SwiftUI ejecuta directamente con `Process`, sin Terminal:

- `scripts/start.sh` al pulsar **Iniciar**;
- `scripts/stop.sh` al pulsar **Detener** o **Salir**;
- `scripts/status.sh --machine` al abrir y cada cuatro segundos.

Después de confirmar que API y web responden, **Iniciar** abre mediante
`NSWorkspace` el bundle exacto `~/Applications/DeutschOS.app`. Si su bundle
identifier ya está ejecutándose, lo activa sin crear otra instancia. Safari
solo recibe `http://127.0.0.1:3000` como respaldo cuando la web app falta o
macOS devuelve un error al abrirla.

El protocolo `deutschos-status-v1` informa SSD, modelos, disponibilidad de
LM Studio/API/web, disponibilidad y cantidad de fuentes de la biblioteca, y
ownership de cada PID. Los campos de biblioteca son aditivos para conservar la
compatibilidad del protocolo. Swift no reimplementa la detección de
puertos, la validación de procesos ni el cierre de árboles. Durante un arranque
o cierre conserva la fase transitoria y actualiza los servicios desde el estado
real; las acciones incompatibles quedan deshabilitadas.

La X roja y `⌘Q` se interceptan antes de cerrar la ventana. Si hay actividad,
una alerta ofrece **Detener y salir** o **Cancelar**. Confirmar cancela de forma
segura cualquier `start.sh` en curso, ejecuta `stop.sh`, verifica que no queden
PID gestionados ni listeners y solo entonces termina. **Salir** hace el mismo
cierre sin dejar la app oculta.

```bash
./scripts/test-macos-app.sh
./scripts/test-macos-app.sh --integration
./scripts/build-macos-app.sh
open dist/DeutschOS.app
```

Para añadirla al Dock, localiza `dist/DeutschOS.app` en Finder y arrástrala al
Dock. El bundle incorpora la raíz absoluta para poder copiarlo a
`~/Applications`; tras mover el proyecto o cambiar su punto de montaje hay que
recompilar. Si la app permanece en el SSD, macOS no puede ejecutarla mientras
la unidad está desconectada. **Abrir logs** abre `logs/`; `controller.log`
registra únicamente acciones y transiciones operativas, nunca conversaciones.

### Launcher AppleScript de respaldo

El launcher silencioso anterior se regenera con otro nombre y nunca sustituye
la app principal:

```bash
./scripts/create-macos-launcher.sh
open "dist/legacy/DeutschOS Launcher.app"
```

Ese respaldo ejecuta `start.sh` y abre la web directamente, sin ventana de
control. `dist/` sigue ignorado por Git porque contiene binarios locales.

### Gestión de servicios reutilizada

`start.sh` mantiene logs separados en `logs/` y PID files en `run/`. Un bloqueo
atómico evita dos arranques simultáneos. Cada PID file incluye el PID y la hora
de inicio del proceso; antes de reutilizarlo o detenerlo también se comprueba el
comando esperado. Si FastAPI o Next.js ya responden sin PID file, se reutilizan
sin adquirir su propiedad y nunca reciben señales sin una identidad demostrada.
Un puerto ocupado por una respuesta que no corresponde al servicio esperado se
trata como error y nunca se mata ese proceso.

LM Studio se inicia únicamente cuando no responde ya en loopback, con:

```bash
LM_STUDIO_MODELS=/Volumes/Juegos/DeutschOS/LM Studio/models
LM_STUDIO_HOST=127.0.0.1:1234
LM_STUDIO_NO_CLOUD=1
LM_STUDIO_NOHISTORY=1
```

El launcher no descarga modelos y exige al menos un manifiesto local. Para la
preparación de la base reutiliza `dev.sh --prepare-only`, que realiza las mismas
comprobaciones y migraciones seguras sin iniciar servidores. FastAPI y Next.js
se ejecutan sin `--reload` de Python y sin telemetría, con salida persistida en
`logs/api.log` y `logs/web.log`.

Comandos operativos:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/stop.sh
```

`stop.sh` envía primero SIGTERM y reserva SIGKILL para el último recurso sobre
FastAPI y Next.js. Solo actúa sobre procesos cuyo PID file, hora de inicio y
comando coinciden; limpia archivos obsoletos sin señalar procesos ajenos. El
servidor LM Studio recibe primero `lms server stop`; el controlador cierra
después `ai.elementlabs.lmstudio` mediante `NSRunningApplication.terminate()` y
solo escala a `forceTerminate()` sobre esa instancia exacta tras el timeout.
La web app se cierra del mismo modo por su bundle exacto, sin señalar Safari.
`status.sh` devuelve cero cuando
LM Studio, API y web responden y no hay PID files huérfanos. Los artefactos
`dist/`, `.build/`, `logs/`, `run/` y el almacén `LM Studio/` son locales y están
ignorados por Git. No hay LaunchAgent, permisos de administrador, telemetría ni
inicio al iniciar sesión.

## Biblioteca educativa

La biblioteca se administra con `scripts/educational-library.sh`; los comandos
y estados se documentan en [Biblioteca educativa](educational-library.md). Su
SQLite y sus informes viven en `var/educational-library/`, fuera de Git y de los
backups normales del progreso. La ausencia de la carpeta de materiales no impide
arrancar la API: el estado queda disponible y el escaneo informa el error local.
Eliminar el runtime obliga a reindexar, pero nunca elimina originales.

## Backup

```bash
./scripts/backup.sh
./scripts/backup.sh "/ruta/local/con espacios"
```

La precedencia del destino es: argumento, `DEUTSCHOS_BACKUP_DIR` y
`./backups`. Una ruta relativa se resuelve desde el proyecto, no desde la shell
del usuario. Dentro del repositorio solo se admite `./backups`, que está
ignorado por Git; cualquier otro destino debe quedar fuera del repositorio. El
destino tampoco puede estar dentro de `data`.

La copia se construye en un directorio parcial con permisos restrictivos y se
renombra al final. SQLite usa su API de backup en lugar de copiar el archivo en
caliente; se ejecuta `quick_check` antes y después. El bundle contiene:

- `database/`: snapshot SQLite coherente;
- `configuration/`: copia saneada de las opciones locales no secretas de `.env`;
- `data/`: futuros archivos pedagógicos regulares;
- `manifest.json`: fecha UTC, revisión Alembic, tamaños y SHA-256.

No se siguen enlaces simbólicos. Se excluyen otras bases no gestionadas,
temporales, cachés, directorios de LM Studio/Hugging Face y extensiones habituales
de pesos (`.gguf`, `.safetensors`, `.onnx`, entre otras). Los modelos deben
recuperarse desde su instalación local de LM Studio, no desde estos bundles.
Las claves `.env` que no estén en la lista pública del script se omiten. Las
URLs públicas con credenciales embebidas o que no apunten a loopback también se
rechazan. Sus nombres, nunca sus valores, quedan en
`excluded_configuration_keys`. Archivos arbitrarios de `config/local` tampoco
se copian porque podrían contener credenciales futuras.

## Restauración

La restauración permanece deliberadamente manual para evitar sobrescribir datos
por accidente:

1. Detén `dev.sh` y conserva la base actual con otro nombre o en otra unidad.
2. Revisa `manifest.json` y verifica los hashes de los archivos que restaurarás.
3. Copia el snapshot de `database/` a la ruta indicada por
   `DEUTSCHOS_DATABASE_URL` solo cuando hayas confirmado el bundle correcto.
4. Restaura la configuración saneada o archivos pedagógicos de forma selectiva.
5. Ejecuta `./scripts/doctor.sh` antes de volver a arrancar.

El script nunca aplica retención ni elimina backups antiguos.

## Downgrades destructivos

Los downgrades no forman parte del flujo normal. La revisión inicial rechaza
eliminar tablas y la revisión del Learning Engine rechaza borrar evidencia. Solo
una restauración o reset deliberado, después de verificar un backup, puede usar
temporalmente `DEUTSCHOS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`. `dev.sh` nunca define
esa variable ni ejecuta downgrades.
