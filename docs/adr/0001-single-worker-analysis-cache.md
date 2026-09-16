# ADR 0001: Un worker mientras Analisis use cache local

## Estado

Aceptada.

## Contexto

La vista de Analisis mantiene resultados en una cache en memoria por proceso,
indexada por usuario y rango temporal. Las escrituras que cambian agregaciones
invalida las entradas del usuario afectado dentro de ese mismo proceso.

Con varios workers, cada proceso tendria su propia cache. Una importacion,
renombrado, borrado o mantenimiento podria invalidar un worker mientras otro
seguiría entregando una entrada anterior.

## Decision

El servidor se ejecuta con `uvicorn` y un unico worker. Los lanzadores del
repositorio conservan `--workers 1` y cualquier configuracion de despliegue
debe respetarlo mientras se mantenga esta cache.

## Consecuencias

- La coherencia de Analisis es predecible dentro del proceso.
- La aplicacion no escala horizontalmente ni aprovecha multiples workers ASGI
  sin cambiar la estrategia de cache.
- Para escalar se debe sustituir la cache por una compartida o por resultados
  persistidos, y definir una invalidacion entre procesos antes de aumentar
  workers.