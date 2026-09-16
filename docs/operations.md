# Operacion de mendi.log

## Arranque local

Los lanzadores preparan el entorno virtual, instalan las dependencias y
arrancan la aplicacion en `http://127.0.0.1:8000`.

```powershell
.\run.bat
```

```bash
./run.sh
```

El servidor debe ejecutarse con un unico worker. La cache de Analisis vive en
memoria del proceso y varios workers pueden servir resultados obsoletos despues
de una escritura.

## Primer usuario

La base de datos no incluye usuarios inicialmente. Crea el primero desde la
raiz del repositorio:

```powershell
.\.venv\Scripts\python.exe scripts\create_user.py --email admin@ejemplo.local --role admin
```

El script solicita la contrasena sin mostrarla. El primer usuario activo se
promueve a administrador cuando la aplicacion arranca.

## Configuracion

Usa `.env.example` como inventario de variables. La aplicacion no carga un
archivo `.env` automaticamente: el proceso o el gestor de despliegue debe
proveer las variables de entorno.

| Variable | Uso |
| --- | --- |
| `MENDI_SECRET_KEY` | Clave HMAC para tokens CSRF. Obligatoria fuera de desarrollo local. |
| `MENDI_REQUIRE_HTTPS` | Usa `1` para marcar la cookie de sesion como `Secure`. |
| `MENDI_DATABASE_URL` | Sobrescribe la URL SQLite por defecto. |

Antes de exponer la aplicacion fuera de localhost, define una clave secreta
aleatoria y termina TLS en el punto de acceso. No expongas el servicio con la
clave por defecto de desarrollo.

## Datos locales y copias de seguridad

Todo el estado de usuario esta bajo `data/`, directorio ignorado por Git:

- `data/mendi.db`: SQLite con usuarios, sesiones, rutas y caches.
- `data/gpx/<user_id>/`: GPX originales de cada usuario.
- Ficheros WAL y SHM de SQLite, mientras existan.

Para una copia local coherente, detiene el servidor y archiva el directorio
`data/` completo. Para restaurar, manten la aplicacion detenida, sustituye el
directorio completo y vuelve a iniciarla. No restaures solo la base de datos si
necesitas conservar los originales GPX asociados.

No borres manualmente una carpeta `data/gpx/<user_id>/` o ficheros GPX desde el
sistema: se perderia la posibilidad de reprocesar esas rutas. Usa la aplicacion
para eliminar rutas y usuarios.

## Mantenimiento funcional

Los endpoints de mantenimiento exigen una sesion con rol de escritura y CSRF:

| Accion | Endpoint | Efecto |
| --- | --- | --- |
| Reprocesar | `POST /api/reprocesar` | Recalcula rutas desde los GPX guardados. |
| Completar regiones | `POST /api/backfill-regions` | Consulta localizaciones faltantes. |
| Limpiar duplicados | `POST /api/limpiar-duplicados` | Detecta y puede eliminar GPX duplicados por usuario. |

Estas acciones responden en NDJSON. No las ejecutes contra datos de usuario sin
entender su efecto y sin una copia de seguridad reciente.

## Dependencias externas

- Nominatim se usa para geocodificacion inversa y limita la frecuencia a una
  solicitud aproximada por segundo. Un fallo remoto no debe impedir importar.
- Open-Meteo devuelve clima historico o reciente y se cachea en SQLite.
- Overpass aporta cimas cuando no hay waypoints GPX adecuados; el fallback es
  el punto de maxima altitud del track.

La aplicacion sigue funcionando si estos servicios fallan, aunque pueden faltar
ubicacion, clima o nombres de cimas.

## Base de datos y despliegue

SQLite es la opcion soportada y validada. `MENDI_DATABASE_URL` permite
configurar otra URL SQLAlchemy, pero no convierte automaticamente PostgreSQL o
MariaDB en alternativas soportadas: faltan drivers declarados, migraciones
formales y validacion por dialecto.

Consulta [`architecture.md`](architecture.md) para la estructura interna y los
ADRs para las decisiones operativas vigentes.