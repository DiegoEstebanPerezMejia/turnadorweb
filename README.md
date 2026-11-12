# Turnero Web (multiusuario)

Sistema de turnos hecho con **Flask**, base de datos **Neon (PostgreSQL)** y listo para **Render**.

- **Inicio de sesión** con usuario y contraseña.
- **Cada usuario tiene su propio turnero** (turnos, ajustes, jornadas e historial independientes). Varios usuarios pueden estar conectados al mismo tiempo sin mezclarse.
- **Ventana de administración** dentro de la página: crear usuarios, asignarles contraseña, dar o quitar acceso, cambiar rol, restablecer claves e inicializar el turnero de cualquiera.
- **Ventana de estadísticas** por usuario, con el detalle de su jornada actual y el acumulado histórico. Los administradores pueden ver las de todos.

## Estructura

```
turnero-web/
├── app.py              # Flask: login, API, administración, estadísticas
├── models.py           # Tablas (SQLAlchemy)
├── templates/index.html
├── static/css/style.css
├── static/js/app.js
├── requirements.txt
├── render.yaml         # Blueprint de Render
├── .env.example
└── .gitignore
```

> Las carpetas deben llamarse exactamente `templates` y `static`. Si falta `templates/index.html`, Render responde con `TemplateNotFound`.

## Variables de entorno

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Cadena de conexión de Neon. Vacía = SQLite local |
| `SECRET_KEY` | Firma las cookies de sesión. En Render se genera sola (`render.yaml`) |
| `ADMIN_USER` | Usuario del primer administrador (por defecto `admin`) |
| `ADMIN_PASSWORD` | Contraseña del primer administrador. Si falta, se crea con `admin123` y **se obliga a cambiarla** al entrar |
| `APP_TIMEZONE` | Zona horaria de fechas y horas (por defecto `America/Mexico_City`) |
| `SESSION_HOURS` | Duración de la sesión, se renueva con el uso (por defecto 12) |
| `COOKIE_SECURE` | `0` para pruebas por http local con `DATABASE_URL` definida |

`ADMIN_USER` y `ADMIN_PASSWORD` solo se usan cuando la base **no tiene usuarios**. Después, todo se gestiona desde la página.

## Despliegue (resumen)

1. **Neon**: crea el proyecto y copia la cadena con *Connection pooling* (`...-pooler...?sslmode=require`).
2. **GitHub**: sube la carpeta con `git init`, `git add .`, `git commit`, `git push`.
3. **Render**: *New → Blueprint*, elige el repositorio y rellena `DATABASE_URL` y `ADMIN_PASSWORD`.
   - Manual: Build `pip install -r requirements.txt`, Start `gunicorn app:app --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT`, y las variables de la tabla (con `SECRET_KEY` = una cadena larga aleatoria).

### Si ya tenías la versión anterior desplegada

Al arrancar, la app **migra sola** tu base de Neon sin borrar nada:
- Las cuentas antiguas pasan a ser usuarios: `admin` (administrador) y `sub_admin` (usuario normal), con las mismas contraseñas.
- Los turnos, ajustes e historial que ya existían quedan asignados al usuario `admin`.
- Los usuarios se escriben en minúsculas al iniciar sesión.
- Añade la variable `SECRET_KEY` en Render (si usas Blueprint, se crea sola).

## Uso

### Iniciar sesión
Entra con tu usuario y contraseña. Si el administrador te asignó una clave temporal, la página te pedirá cambiarla antes de continuar. La sesión dura 12 horas y se renueva mientras la uses. Puedes tener sesiones abiertas desde varios equipos a la vez.

### Administrador (botón 👥 Usuarios)
- **➕ Nuevo usuario**: usuario, nombre, contraseña inicial, si es administrador y si debe cambiar la clave al entrar. Su turnero se crea vacío.
- **⛔ Quitar acceso / ✅ Dar acceso**: quitar el acceso cierra su sesión al instante; sus datos se conservan y se pueden reactivar.
- **Hacer admin / Quitar admin**: un administrador puede gestionar usuarios.
- **🔑 Clave**: restablece la contraseña de otro usuario y cierra sus sesiones abiertas.
- **📊 Estadísticas**: abre las estadísticas de ese usuario.
- **♻️ Inicializar**: cierra la jornada del usuario (queda en su historial) y deja su turnero en blanco.
- No puedes quitarte el acceso, cambiar tu propio rol ni restablecer tu propia clave desde ahí (usa **🔑 Mi clave**). Así siempre queda al menos un administrador activo.
- No hay botón de borrar usuarios a propósito: así se conservan sus estadísticas. Quitarles el acceso los deja inutilizables.

### Mi turnero
Igual que antes: paneles de **espera** y **atención**, empleado obligatorio u opcional, cotización de 6 dígitos, regla de paridad al limpiar y reiniciar, historial y **🖥️ Modo Pantalla** (`/?modo=pantalla`, muestra el turnero del usuario que tiene la sesión abierta).

Los modos de empleado y cotización ya no piden credenciales: son ajustes de tu propio turnero.

**🏁 Cerrar Jornada** guarda el resumen en tu historial y empieza una jornada nueva con el contador en 0. Para salir de tu cuenta usa **🚪 Salir**.

### 📊 Mis estadísticas
- **Jornada actual**: turnos generados, atendidos, limpiados, cotizaciones, empleados distintos, tiempos promedio, mínimo, máximo y total, tabla por empleado y detalle turno por turno.
- **Acumulado**: lo mismo sobre todas tus jornadas, más desglose por empleado, por día, por hora del día y la lista de jornadas cerradas.

### Atajos de teclado
Los navegadores reservan `Ctrl+N`, `Ctrl+L`, etc., así que se usan combinaciones con **Alt**:

| Atajo | Acción |
|---|---|
| `Alt+N` | Nuevo turno |
| `Alt+S` | Siguiente |
| `Alt+A` o `Retroceso` | Devolver turno a espera |
| `Alt+L` | Limpiar espera |
| `Alt+K` | Limpiar atención |
| `Alt+C` | Registrar cotización |

## Uso local
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # opcional
python app.py                    # http://localhost:5000
```
Sin `.env`, entra con `admin` / `admin123` y la página te pedirá cambiar la clave.

## Seguridad
- Contraseñas guardadas con hash; cookie de sesión firmada, `HttpOnly`, `SameSite=Lax` y `Secure` en producción.
- Cada petición revalida al usuario: desactivarlo o cambiarle la clave cierra sus sesiones al momento.
- Un usuario solo puede ver y modificar sus propios turnos; las rutas de administración exigen rol de administrador.
- Bloqueo temporal tras 5 intentos fallidos de inicio de sesión desde la misma IP.
- Las escrituras de la API solo se aceptan desde el propio JavaScript de la página (protección extra contra CSRF).

## Notas del plan gratuito
- Render gratis duerme el servicio tras un rato sin visitas; la primera carga puede tardar ~1 minuto.
- Neon suspende la base sin uso y se reactiva sola. Si ves un error de conexión por `channel_binding`, quita ese parámetro de la cadena.
