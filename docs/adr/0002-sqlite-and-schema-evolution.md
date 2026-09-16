# ADR 0002: SQLite como base soportada y evolucion controlada del esquema

## Estado

Aceptada para SQLite. El soporte de otros dialectos queda pendiente.

## Contexto

La aplicacion se distribuye como herramienta local y guarda su estado en
`data/mendi.db`. SQLite permite una instalacion sin servicios adicionales y el
engine configura claves foraneas, WAL y tiempo de espera para escrituras.

El bootstrap crea tablas y contiene ajustes suaves limitados. SQLAlchemy
`create_all()` no altera una tabla existente; por tanto, no representa por si
solo una estrategia completa de migraciones.

Aunque `MENDI_DATABASE_URL` acepta una URL SQLAlchemy alternativa, el proyecto
no declara drivers, migraciones ni validacion de compatibilidad para PostgreSQL
o MariaDB.

## Decision

SQLite es la unica base de datos soportada actualmente. Los cambios de esquema
deben ser idempotentes, revisables y preservar los datos existentes. Los
ajustes ad hoc solo sirven como puente para cambios pequenos y controlados.

Antes de promover otro dialecto a soportado, se debe introducir una estrategia
de migraciones formal, declarar el driver correspondiente y validar los flujos
criticos en ese dialecto.

## Consecuencias

- El arranque local sigue siendo simple y sin configuracion adicional.
- Las caracteristicas nuevas no deben depender de SQL especifico de un motor
  alternativo sin una decision explicita.
- Los cambios de modelos requieren un plan de evolucion, no solo editar el ORM.
- La documentacion no debe prometer soporte de bases de datos no verificadas.