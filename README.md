# Contabilidad para alumnos - versión web

Esta versión rehace el programa original como aplicación web para resolver dos problemas:

1. La navegación ya no queda abajo ni depende de la resolución de la pantalla.
2. Se puede publicar en la nube para que trabajen varios alumnos desde navegador.

## Qué hace

- Crear, editar y eliminar asientos
- Libro diario
- Libro mayor
- Cuenta de resultados
- Balance de situación
- Exportación CSV
- Filtro por alumno o grupo
- Carga de datos de ejemplo

## Cómo arrancar en local

```bash
python -m venv .venv
. .venv/bin/activate  # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Abrir en navegador:

```text
http://127.0.0.1:5000
```

## Despliegue recomendado

### Opción simple para pruebas
- Render o Railway
- Subir el proyecto a GitHub
- Crear servicio web Python
- Comando de arranque: `gunicorn app:app`

### Base de datos
- Para pruebas pequeñas puedes usar SQLite.
- Para una clase real es mejor PostgreSQL y configurar `DATABASE_URL`.

## Variables de entorno útiles

- `SECRET_KEY`
- `DATABASE_URL`
- `PORT`

## Nota importante

Si despliegas con SQLite en servicios con disco efímero, los datos pueden perderse al reiniciar o redeplegar. Para uso real con alumnos, mejor PostgreSQL.
