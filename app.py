"""Turnero web multiusuario: Flask + PostgreSQL (Neon), listo para Render.

- Cada usuario inicia sesión y trabaja con SU PROPIO turnero (turnos, ajustes,
  sesiones e historial independientes); varios usuarios pueden estar conectados
  al mismo tiempo.
- Los administradores crean usuarios, les dan o quitan acceso y restablecen claves.
- Cada usuario tiene su ventana de estadísticas (los administradores ven las de todos).
"""
import os
import re
import secrets
import threading
import time
from collections import Counter
from datetime import timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, g, jsonify, render_template, request, session
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from models import Ajustes, Sesion, Turno, Usuario, ahora_utc, db

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv es opcional
    pass

# ============================================================
# CONFIGURACIÓN
# ============================================================
TZ_LOCAL = ZoneInfo(os.environ.get("APP_TIMEZONE", "America/Mexico_City"))
MIN_PASSWORD = 6
MAX_PASSWORD = 128
MAX_FALLOS = 5  # intentos de login fallidos permitidos...
VENTANA_FALLOS = 300  # ...dentro de esta ventana (segundos)
USUARIO_RE = re.compile(r"[a-z0-9._-]{3,40}")


def _database_uri():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///turnero_local.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.json.sort_keys = False

_uri = _database_uri()
_opciones = {"pool_pre_ping": True, "pool_recycle": 280}  # Neon suspende conexiones
if not _uri.startswith("sqlite"):
    _opciones.update(pool_size=5, max_overflow=5)
app.config["SQLALCHEMY_DATABASE_URI"] = _uri
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = _opciones
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# --- Cookie de sesión (firmada) ---
_secret = os.environ.get("SECRET_KEY", "").strip()
if not _secret:
    _secret = secrets.token_hex(32)
    app.logger.warning(
        "SECRET_KEY no está definida: se usa una clave temporal y las sesiones "
        "se cerrarán en cada reinicio. Defínela en las variables de entorno."
    )
_cookie_segura = os.environ.get("COOKIE_SECURE")
if _cookie_segura is None:
    _cookie_segura = "1" if os.environ.get("DATABASE_URL", "").strip() else "0"
app.config.update(
    SECRET_KEY=_secret,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_cookie_segura == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=int(os.environ.get("SESSION_HOURS", "12"))),
)
db.init_app(app)


# ============================================================
# UTILIDADES
# ============================================================
class ApiError(Exception):
    def __init__(self, mensaje, status=400):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status = status


@app.errorhandler(ApiError)
def _manejar_api_error(e):
    db.session.rollback()
    return jsonify({"error": e.mensaje}), e.status


@app.errorhandler(Exception)
def _manejar_error(e):
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description}), e.code
        return e
    app.logger.exception("Error no controlado")
    db.session.rollback()
    return jsonify({"error": "Error interno del servidor."}), 500


@app.before_request
def _proteger_csrf():
    # Defensa extra contra CSRF: la API solo acepta escrituras hechas por nuestro
    # propio JavaScript (un formulario de otro sitio no puede añadir esta cabecera).
    if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get("X-Requested-With") != "turnero":
            raise ApiError("Solicitud no válida.", 400)


@app.after_request
def _cabeceras(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


def _aware(dt):
    if dt is None:
        return None
    from datetime import timezone

    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local(dt, fmt="%d/%m/%Y %H:%M:%S"):
    return _aware(dt).astimezone(TZ_LOCAL).strftime(fmt) if dt else None


def formatear_duracion(segundos):
    h, r = divmod(int(segundos), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _nuevo_token(ahora):
    return ahora.strftime("%Y%m%d%H%M%S%f")


def _json():
    return request.get_json(silent=True) or {}


def _normalizar_usuario(valor):
    return str(valor or "").strip().lower()


def _usuario_json(u):
    return {
        "id": u.id,
        "usuario": u.usuario,
        "nombre": u.nombre,
        "es_admin": bool(u.es_admin),
        "activo": bool(u.activo),
        "debe_cambiar": bool(u.debe_cambiar),
        "creado": _local(u.creado, "%d/%m/%Y %H:%M"),
        "ultimo_acceso": _local(u.ultimo_acceso, "%d/%m/%Y %H:%M"),
    }


def _commit_tolerante():
    try:
        db.session.commit()
    except IntegrityError:  # otro proceso lo creó primero
        db.session.rollback()


# ============================================================
# AUTENTICACIÓN (sesión por cookie) con límite de intentos
# ============================================================
_FALLOS = {}
_FALLOS_LOCK = threading.Lock()
# Hash de relleno: comprobar contraseñas de usuarios inexistentes tarda lo mismo.
_HASH_FALSO = generate_password_hash("usuario-inexistente")


def _verificar_bloqueo(ip):
    ahora = time.time()
    with _FALLOS_LOCK:
        intentos = [t for t in _FALLOS.get(ip, []) if ahora - t < VENTANA_FALLOS]
        if intentos:
            _FALLOS[ip] = intentos
        else:
            _FALLOS.pop(ip, None)
        if len(intentos) >= MAX_FALLOS:
            espera = int(VENTANA_FALLOS - (ahora - intentos[0])) + 1
            raise ApiError(
                f"Demasiados intentos fallidos. Intenta de nuevo en {espera} segundos.",
                429,
            )


def _registrar_fallo(ip):
    with _FALLOS_LOCK:
        _FALLOS.setdefault(ip, []).append(time.time())


def _limpiar_fallos(ip):
    with _FALLOS_LOCK:
        _FALLOS.pop(ip, None)


def _cargar_usuario():
    """Usuario de la sesión actual, o None. Se revalida en CADA petición, así que
    desactivar a alguien o cambiarle la clave cierra sus sesiones al instante."""
    if "usuario" in g:
        return g.usuario
    g.usuario = None
    uid = session.get("uid")
    if uid:
        u = db.session.get(Usuario, uid)
        if u is not None and u.activo and session.get("v") == u.version_sesion:
            g.usuario = u
        else:
            session.clear()
    return g.usuario


def _exigir_usuario(permitir_cambio=False):
    u = _cargar_usuario()
    if u is None:
        raise ApiError("Tu sesión terminó. Inicia sesión de nuevo.", 401)
    if u.debe_cambiar and not permitir_cambio:
        raise ApiError("Debes cambiar tu contraseña antes de continuar.", 403)
    return u


def login_requerido(f):
    @wraps(f)
    def envoltura(*a, **k):
        _exigir_usuario()
        return f(*a, **k)

    return envoltura


def login_permite_cambio(f):
    @wraps(f)
    def envoltura(*a, **k):
        _exigir_usuario(permitir_cambio=True)
        return f(*a, **k)

    return envoltura


def admin_requerido(f):
    @wraps(f)
    def envoltura(*a, **k):
        u = _exigir_usuario()
        if not u.es_admin:
            raise ApiError("Solo un administrador puede realizar esta acción.", 403)
        return f(*a, **k)

    return envoltura


def _validar_password_nueva(nueva):
    if len(nueva) < MIN_PASSWORD:
        raise ApiError(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.")
    if len(nueva) > MAX_PASSWORD:
        raise ApiError("La contraseña es demasiado larga.")


# ============================================================
# TURNERO POR USUARIO: ajustes y consultas
# ============================================================
def _inicializar_usuario(uid):
    """Crea (si falta) el turnero del usuario: ajustes y sesión nueva."""
    existe = db.session.scalar(select(Ajustes.id).where(Ajustes.usuario_id == uid))
    if existe is not None:
        return
    ahora = ahora_utc()
    db.session.add(
        Ajustes(usuario_id=uid, sesion_token=_nuevo_token(ahora), sesion_inicio=ahora)
    )
    _commit_tolerante()


def _ajustes_de(uid, lock=False):
    for _ in range(2):
        q = select(Ajustes).where(Ajustes.usuario_id == uid)
        if lock:
            # Bloquea la fila: serializa las operaciones que modifican el turnero de ESTE usuario.
            q = q.with_for_update()
        aj = db.session.execute(q).scalar_one_or_none()
        if aj is not None:
            return aj
        _inicializar_usuario(uid)
    raise ApiError("No se pudo inicializar el turnero del usuario.", 500)


def _get_ajustes(lock=False):
    return _ajustes_de(g.usuario.id, lock)


def _contar(uid, estado):
    return (
        db.session.scalar(
            select(func.count())
            .select_from(Turno)
            .where(Turno.usuario_id == uid, Turno.estado == estado)
        )
        or 0
    )


def _primero_en_espera(uid):
    return db.session.scalars(
        select(Turno)
        .where(Turno.usuario_id == uid, Turno.estado == "espera")
        .order_by(Turno.orden_espera.asc(), Turno.id.asc())
        .limit(1)
    ).first()


def _turno_actual(uid):
    return db.session.scalars(
        select(Turno)
        .where(Turno.usuario_id == uid, Turno.estado == "atencion")
        .order_by(Turno.orden_atencion.desc(), Turno.id.desc())
        .limit(1)
    ).first()


def _turno_propio(turno_id):
    """Turno por id, solo si pertenece al usuario de la sesión."""
    t = db.session.get(Turno, turno_id)
    if t is None or t.usuario_id != g.usuario.id:
        return None
    return t


def _cerrar_tiempo(turno, ahora):
    turno.hora_fin = ahora
    turno.duracion_segundos = int((ahora - _aware(turno.hora_llamado)).total_seconds())


def _turno_json(t):
    return {
        "id": t.id,
        "codigo": t.codigo,
        "empleado": t.empleado,
        "cotizacion": t.cotizacion,
    }


def _construir_estado():
    u = g.usuario
    aj = _get_ajustes()
    espera = db.session.scalars(
        select(Turno)
        .where(Turno.usuario_id == u.id, Turno.estado == "espera")
        .order_by(Turno.orden_espera.asc(), Turno.id.asc())
    ).all()
    atencion = db.session.scalars(
        select(Turno)
        .where(Turno.usuario_id == u.id, Turno.estado == "atencion")
        .order_by(Turno.orden_atencion.asc(), Turno.id.asc())
    ).all()
    atendidos = (
        db.session.scalar(
            select(func.count())
            .select_from(Turno)
            .where(
                Turno.usuario_id == u.id,
                Turno.sesion_token == aj.sesion_token,
                Turno.hora_llamado.is_not(None),
            )
        )
        or 0
    )
    actual = atencion[-1] if atencion else None
    return {
        "espera": [_turno_json(t) for t in espera],
        "atencion": [_turno_json(t) for t in atencion],
        "actual": _turno_json(actual) if actual else None,
        "ultimo_espera": espera[-1].codigo if espera else None,
        "siguiente_empleado_pendiente": espera[0].empleado if espera else None,
        "ajustes": {
            "empleado_obligatorio": aj.empleado_obligatorio,
            "cotizacion_activa": aj.cotizacion_activa,
        },
        "stats": {
            "generados": aj.numero_turno,
            "atendidos": atendidos,
            "limpiados": aj.total_limpiados,
            "espera": len(espera),
            "atencion": len(atencion),
        },
        "sesion_inicio": _local(aj.sesion_inicio, "%d/%m/%Y %H:%M"),
    }


def _ok(**extra):
    return jsonify({"ok": True, **extra, "estado": _construir_estado()})


# ============================================================
# PÁGINAS
# ============================================================
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    # No toca la base de datos a propósito: así Neon puede suspenderse sin uso.
    return "ok"


# ============================================================
# API: sesión de usuario (login / logout / yo / contraseña propia)
# ============================================================
@app.post("/api/login")
def login():
    data = _json()
    ip = request.remote_addr or "?"
    _verificar_bloqueo(ip)

    nombre = _normalizar_usuario(data.get("usuario"))
    password = str(data.get("password") or "")
    u = (
        db.session.scalar(select(Usuario).where(Usuario.usuario == nombre))
        if nombre
        else None
    )
    correcto = check_password_hash(u.password_hash if u else _HASH_FALSO, password)
    if u is None or not correcto:
        _registrar_fallo(ip)
        raise ApiError("Usuario o contraseña incorrectos.", 401)
    if not u.activo:
        raise ApiError("Tu acceso está desactivado. Contacta al administrador.", 403)

    _limpiar_fallos(ip)
    session.clear()
    session["uid"] = u.id
    session["v"] = u.version_sesion
    session.permanent = True
    u.ultimo_acceso = ahora_utc()
    db.session.commit()

    g.usuario = u
    _get_ajustes()  # inicializa el turnero del usuario si aún no existe
    return jsonify({"ok": True, "usuario": _usuario_json(u)})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    u = _cargar_usuario()
    if u is None:
        raise ApiError("No has iniciado sesión.", 401)
    return jsonify({"usuario": _usuario_json(u)})


@app.post("/api/password")
@login_permite_cambio
def cambiar_password():
    """El usuario cambia SU propia contraseña (la de otros la restablece un admin)."""
    data = _json()
    u = g.usuario
    ip = request.remote_addr or "?"
    _verificar_bloqueo(ip)

    actual = str(data.get("actual") or "")
    nueva = str(data.get("nueva") or "")
    confirmar = str(data.get("confirmar") or "")

    if not check_password_hash(u.password_hash, actual):
        _registrar_fallo(ip)
        raise ApiError("La contraseña actual es incorrecta.")
    _validar_password_nueva(nueva)
    if nueva != confirmar:
        raise ApiError("Las contraseñas nuevas no coinciden.")
    if nueva == actual:
        raise ApiError("La nueva contraseña debe ser diferente a la actual.")

    _limpiar_fallos(ip)
    u.password_hash = generate_password_hash(nueva)
    u.debe_cambiar = False
    u.version_sesion += 1  # cierra las demás sesiones abiertas de este usuario
    db.session.commit()
    session["v"] = u.version_sesion
    return jsonify({"ok": True, "usuario": _usuario_json(u)})


# ============================================================
# API: estado y turnos (siempre del usuario de la sesión)
# ============================================================
@app.get("/api/estado")
@login_requerido
def api_estado():
    return jsonify(_construir_estado())


@app.post("/api/turnos")
@login_requerido
def crear_turno():
    aj = _get_ajustes(lock=True)
    aj.numero_turno += 1
    maximo = db.session.scalar(
        select(func.max(Turno.orden_espera)).where(
            Turno.usuario_id == g.usuario.id, Turno.estado == "espera"
        )
    )
    turno = Turno(
        usuario_id=g.usuario.id,
        sesion_token=aj.sesion_token,
        codigo=f"T-{aj.numero_turno:03d}",
        estado="espera",
        orden_espera=(maximo if maximo is not None else 0) + 1,
    )
    db.session.add(turno)
    db.session.commit()
    return _ok(turno=_turno_json(turno))


@app.post("/api/siguiente")
@login_requerido
def siguiente():
    data = _json()
    uid = g.usuario.id
    aj = _get_ajustes(lock=True)

    primero = _primero_en_espera(uid)
    if primero is None:
        raise ApiError("No hay turnos en espera para atender.", 409)

    empleado = None
    if primero.empleado:
        # Turno devuelto a la espera: conserva el empleado que ya lo atendía.
        empleado = primero.empleado
    elif aj.empleado_obligatorio:
        empleado = str(data.get("empleado") or "").strip()
        if not empleado:
            raise ApiError("Debes ingresar el número de empleado.", 400)
        if len(empleado) > 60:
            raise ApiError("El número de empleado es demasiado largo.", 400)

    ahora = ahora_utc()

    # Cierra el tiempo del turno que estaba en atención.
    anterior = _turno_actual(uid)
    if anterior is not None and anterior.hora_fin is None and anterior.hora_llamado:
        _cerrar_tiempo(anterior, ahora)

    maximo = db.session.scalar(
        select(func.max(Turno.orden_atencion)).where(
            Turno.usuario_id == uid, Turno.estado == "atencion"
        )
    )
    primero.estado = "atencion"
    primero.orden_espera = None
    primero.orden_atencion = (maximo if maximo is not None else 0) + 1
    primero.empleado = empleado
    primero.hora_llamado = ahora
    primero.hora_fin = None
    primero.duracion_segundos = None
    db.session.commit()
    return _ok(turno=_turno_json(primero))


@app.post("/api/devolver")
@login_requerido
def devolver():
    uid = g.usuario.id
    _get_ajustes(lock=True)
    actual = _turno_actual(uid)
    if actual is None:
        raise ApiError("No hay turnos en atención para devolver.", 409)

    if actual.hora_llamado:
        _cerrar_tiempo(actual, ahora_utc())

    minimo = db.session.scalar(
        select(func.min(Turno.orden_espera)).where(
            Turno.usuario_id == uid, Turno.estado == "espera"
        )
    )
    actual.estado = "espera"
    actual.orden_atencion = None
    actual.orden_espera = (minimo if minimo is not None else 1) - 1
    db.session.commit()
    return _ok()


@app.delete("/api/turnos/<int:turno_id>")
@login_requerido
def eliminar_turno(turno_id):
    _get_ajustes(lock=True)
    turno = _turno_propio(turno_id)
    if turno is None or turno.estado not in ("espera", "atencion"):
        raise ApiError("El turno ya no existe en la lista.", 404)

    if turno.estado == "atencion" or turno.hora_llamado is None:
        db.session.delete(turno)
    else:  # estaba en espera pero ya tiene historial (fue devuelto)
        turno.estado = "cerrado"
        turno.orden_espera = None
    db.session.commit()
    return _ok()


@app.post("/api/turnos/<int:turno_id>/cotizacion")
@login_requerido
def registrar_cotizacion(turno_id):
    data = _json()
    _get_ajustes(lock=True)
    turno = _turno_propio(turno_id)
    if turno is None or turno.estado != "atencion":
        raise ApiError("El turno ya no está en atención.", 404)

    if turno.cotizacion:
        raise ApiError(
            f"El turno {turno.codigo} ya tiene una cotización registrada: "
            f"{turno.cotizacion}.",
            409,
        )

    valor = str(data.get("cotizacion") or "").strip()
    if not valor:
        raise ApiError("Debes ingresar los últimos 6 dígitos.", 400)
    if not re.fullmatch(r"[0-9]{6}", valor):
        raise ApiError("Deben ser exactamente 6 dígitos numéricos.", 400)

    turno.cotizacion = valor
    turno.cotizacion_fecha = ahora_utc()
    db.session.commit()
    return _ok(cotizacion=valor, turno=_turno_json(turno))


# ============================================================
# API: limpiezas (con regla de paridad) y reinicio
# ============================================================
@app.post("/api/limpiar/espera")
@login_requerido
def limpiar_espera():
    data = _json()
    uid = g.usuario.id
    aj = _get_ajustes(lock=True)

    total = _contar(uid, "espera")
    if total == 0:
        raise ApiError("No hay turnos en espera para limpiar.", 409)

    en_atencion = _contar(uid, "atencion")
    if en_atencion > 0:
        raise ApiError(
            "No puedes limpiar la espera.\n\n"
            f"🟢 Hay {en_atencion} turno(s) en atención.\n"
            "La ventana de espera NO puede quedarse vacía mientras haya turnos "
            "en atención.\n\n"
            "Primero devuelve o atiende los turnos en atención, "
            "o limpia ambas ventanas simultáneamente.",
            409,
        )

    if data.get("validar"):
        return jsonify({"ok": True, "total": total})

    for t in db.session.scalars(
        select(Turno).where(Turno.usuario_id == uid, Turno.estado == "espera")
    ).all():
        if t.hora_llamado is None:
            db.session.delete(t)
        else:
            t.estado = "cerrado"
            t.orden_espera = None
    aj.total_limpiados += total
    db.session.commit()
    return _ok(total=total)


@app.post("/api/limpiar/atencion")
@login_requerido
def limpiar_atencion():
    data = _json()
    uid = g.usuario.id
    aj = _get_ajustes(lock=True)

    total = _contar(uid, "atencion")
    if total == 0:
        raise ApiError("No hay turnos en atención para limpiar.", 409)

    en_espera = _contar(uid, "espera")
    if en_espera > 0:
        raise ApiError(
            "No puedes limpiar la atención.\n\n"
            f"🔵 Hay {en_espera} turno(s) en espera.\n"
            "No puede vaciarse una ventana mientras la otra tiene turnos "
            "pendientes.\n\n"
            "Primero atiende o devuelve los turnos en espera, "
            "o limpia ambas ventanas simultáneamente.",
            409,
        )

    if data.get("validar"):
        return jsonify({"ok": True, "total": total})

    ahora = ahora_utc()
    for t in db.session.scalars(
        select(Turno).where(Turno.usuario_id == uid, Turno.estado == "atencion")
    ).all():
        if t.hora_fin is None and t.hora_llamado:
            _cerrar_tiempo(t, ahora)
        t.limpiado = True
        t.estado = "cerrado"
        t.orden_atencion = None
    aj.total_limpiados += total
    db.session.commit()
    return _ok(total=total)


@app.post("/api/reiniciar")
@login_requerido
def reiniciar():
    data = _json()
    uid = g.usuario.id
    aj = _get_ajustes(lock=True)

    espera = _contar(uid, "espera")
    atencion = _contar(uid, "atencion")

    if espera != atencion:
        raise ApiError(
            "No se puede reiniciar el sistema.\n\n"
            "Las ventanas deben tener la misma cantidad de turnos:\n"
            f"🔵 Turnos en espera: {espera}\n"
            f"🟢 Turnos en atención: {atencion}\n\n"
            "No puede vaciarse una ventana si la otra tiene turnos.",
            409,
        )
    if espera > 0 or atencion > 0:
        raise ApiError(
            "Para reiniciar, ambas listas deben estar vacías.\n\n"
            f"Actualmente hay {espera} turno(s) en espera y "
            f"{atencion} turno(s) en atención.\n\n"
            "Atiende o devuelve los turnos pendientes antes de reiniciar.",
            409,
        )

    if data.get("validar"):
        return jsonify({"ok": True})

    aj.numero_turno = 0
    db.session.commit()
    return _ok()


# ============================================================
# API: ajustes del turnero propio
# ============================================================
@app.post("/api/ajustes/empleado")
@login_requerido
def alternar_empleado():
    uid = g.usuario.id
    aj = _get_ajustes(lock=True)
    aj.empleado_obligatorio = not aj.empleado_obligatorio

    # Olvida los empleados asociados a turnos devueltos a la espera.
    pendientes = db.session.scalars(
        select(Turno).where(
            Turno.usuario_id == uid, Turno.estado == "espera", Turno.empleado.is_not(None)
        )
    ).all()
    for t in pendientes:
        t.empleado = None
    db.session.commit()
    return _ok(pendientes_borrados=len(pendientes))


@app.post("/api/ajustes/cotizacion")
@login_requerido
def alternar_cotizacion():
    aj = _get_ajustes(lock=True)
    aj.cotizacion_activa = not aj.cotizacion_activa
    db.session.commit()
    return _ok()


# ============================================================
# SESIONES (cierre + historial) y ESTADÍSTICAS
# ============================================================
def _stats_tiempo(duraciones):
    if not duraciones:
        return {
            "promedio": 0,
            "minimo": 0,
            "maximo": 0,
            "promedio_f": "—",
            "minimo_f": "—",
            "maximo_f": "—",
        }
    prom = sum(duraciones) / len(duraciones)
    return {
        "promedio": prom,
        "minimo": min(duraciones),
        "maximo": max(duraciones),
        "promedio_f": formatear_duracion(prom),
        "minimo_f": formatear_duracion(min(duraciones)),
        "maximo_f": formatear_duracion(max(duraciones)),
    }


def _cerrar_sesion_de(u, aj):
    """Guarda el resumen de la sesión del usuario `u` en su historial y arranca una
    nueva (el turnero queda inicializado). `aj` ya debe estar bloqueado."""
    uid = u.id
    cierre = ahora_utc()
    token = aj.sesion_token

    sin_fin = db.session.scalars(
        select(Turno).where(
            Turno.usuario_id == uid,
            Turno.sesion_token == token,
            Turno.hora_llamado.is_not(None),
            Turno.duracion_segundos.is_(None),
        )
    ).all()
    for t in sin_fin:
        _cerrar_tiempo(t, cierre)

    registros = db.session.scalars(
        select(Turno)
        .where(
            Turno.usuario_id == uid,
            Turno.sesion_token == token,
            Turno.hora_llamado.is_not(None),
        )
        .order_by(Turno.hora_llamado.asc(), Turno.id.asc())
    ).all()

    en_espera = _contar(uid, "espera")
    en_atencion = _contar(uid, "atencion")
    por_empleado = Counter((t.empleado or "(sin empleado)") for t in registros)
    st = _stats_tiempo(
        [t.duracion_segundos for t in registros if t.duracion_segundos is not None]
    )
    cotizaciones = sum(1 for t in registros if t.cotizacion)
    atendidos = len(registros)

    segundos = int((cierre - _aware(aj.sesion_inicio)).total_seconds())
    id_sesion = _local(cierre, "%Y%m%d_%H%M%S")
    inicio_f = _local(aj.sesion_inicio)
    cierre_f = _local(cierre)
    duracion_f = formatear_duracion(segundos)
    modo_emp = "Obligatorio" if aj.empleado_obligatorio else "Opcional"
    modo_cot = "Activado" if aj.cotizacion_activa else "Desactivado"

    resumen = [
        ["Usuario", f"{u.nombre} ({u.usuario})"],
        ["ID de Sesión", id_sesion],
        ["Fecha y Hora de Inicio", inicio_f],
        ["Fecha y Hora de Cierre", cierre_f],
        ["Duración de la Sesión", duracion_f],
        ["Total de Turnos Generados", aj.numero_turno],
        ["Total de Turnos Atendidos", atendidos],
        ["Total de Turnos Limpiados", aj.total_limpiados],
        ["Turnos en Espera al Cerrar", en_espera],
        ["Turnos en Atención al Cerrar", en_atencion],
        ["Empleados que Atendieron", len(por_empleado)],
        ["Modo Empleado", modo_emp],
        ["Modo Cotización", modo_cot],
        ["Cotizaciones Registradas", cotizaciones],
        ["Tiempo Promedio de Atención", st["promedio_f"]],
        ["Tiempo Mínimo de Atención", st["minimo_f"]],
        ["Tiempo Máximo de Atención", st["maximo_f"]],
    ]

    datos = {
        "id_sesion": id_sesion,
        "usuario": u.usuario,
        "nombre": u.nombre,
        "fecha_inicio": inicio_f,
        "fecha_cierre": cierre_f,
        "duracion": duracion_f,
        "total_generados": aj.numero_turno,
        "total_atendidos": atendidos,
        "total_limpiados": aj.total_limpiados,
        "en_espera_al_cerrar": en_espera,
        "en_atencion_al_cerrar": en_atencion,
        "modo_empleado": modo_emp.lower(),
        "modo_cotizacion": "activo" if aj.cotizacion_activa else "inactivo",
        "cotizaciones_registradas": cotizaciones,
        "turnos_por_empleado": dict(por_empleado),
        "tiempo_promedio_atencion": st["promedio_f"],
        "tiempo_minimo_atencion": st["minimo_f"],
        "tiempo_maximo_atencion": st["maximo_f"],
        "promedio_segundos": st["promedio"],
        "minimo_segundos": st["minimo"],
        "maximo_segundos": st["maximo"],
        "detalle_atenciones": [
            {
                "turno": t.codigo,
                "empleado": t.empleado or "(sin empleado)",
                "fecha": _local(t.hora_llamado, "%Y-%m-%d"),
                "hora_llamado": _local(t.hora_llamado, "%H:%M:%S"),
                "hora_fin_atencion": _local(t.hora_fin, "%H:%M:%S"),
                "duracion_segundos": t.duracion_segundos,
                "duracion_formateada": formatear_duracion(t.duracion_segundos)
                if t.duracion_segundos is not None
                else None,
                "limpiado": t.limpiado,
                "cotizacion": t.cotizacion,
                "cotizacion_fecha": _local(t.cotizacion_fecha),
            }
            for t in registros
        ],
    }

    # La columna id_sesion es única en toda la tabla: se le añade el usuario.
    db.session.add(
        Sesion(usuario_id=uid, id_sesion=f"{id_sesion}-{uid}", datos=datos, cerrada=cierre)
    )

    # Los turnos que seguían en los paneles se dan por cerrados.
    db.session.execute(
        update(Turno)
        .where(Turno.usuario_id == uid, Turno.estado.in_(("espera", "atencion")))
        .values(estado="cerrado", orden_espera=None, orden_atencion=None)
    )

    # Nueva sesión.
    aj.numero_turno = 0
    aj.total_limpiados = 0
    aj.sesion_token = _nuevo_token(cierre)
    aj.sesion_inicio = cierre
    db.session.commit()
    return resumen


@app.post("/api/sesion/cerrar")
@login_requerido
def cerrar_sesion():
    """Cierra la sesión de trabajo del usuario (resumen al historial) y arranca otra."""
    aj = _get_ajustes(lock=True)
    resumen = _cerrar_sesion_de(g.usuario, aj)
    return _ok(resumen=resumen)


@app.get("/api/historial")
@login_requerido
def historial():
    filas = db.session.scalars(
        select(Sesion)
        .where(Sesion.usuario_id == g.usuario.id)
        .order_by(Sesion.id.desc())
        .limit(200)
    ).all()
    return jsonify({"sesiones": [f.datos for f in filas]})


# ---------------- Estadísticas ----------------
def _iso_desde_local(texto):
    """'dd/mm/YYYY ...' -> 'YYYY-mm-dd' (o None)."""
    try:
        d, m, y = str(texto)[:10].split("/")
        return f"{y}-{m}-{d}"
    except Exception:
        return None


def _fecha_visible(iso):
    if not iso:
        return "Sin fecha"
    try:
        y, m, d = iso.split("-")
        return f"{d}/{m}/{y}"
    except Exception:
        return iso


def _reg_de_turno(t, sesion):
    return {
        "sesion": sesion,
        "turno": t.codigo,
        "empleado": t.empleado or "(sin empleado)",
        "fecha": _local(t.hora_llamado, "%Y-%m-%d"),
        "hora_llamado": _local(t.hora_llamado, "%H:%M:%S"),
        "hora_fin": _local(t.hora_fin, "%H:%M:%S"),
        "duracion_segundos": t.duracion_segundos,
        "cotizacion": t.cotizacion,
        "limpiado": bool(t.limpiado),
    }


def _regs_de_sesion(datos):
    sid = datos.get("id_sesion")
    fecha_def = _iso_desde_local(datos.get("fecha_inicio"))
    salida = []
    for d in datos.get("detalle_atenciones") or []:
        salida.append(
            {
                "sesion": sid,
                "turno": d.get("turno"),
                "empleado": d.get("empleado") or "(sin empleado)",
                "fecha": d.get("fecha") or fecha_def,
                "hora_llamado": d.get("hora_llamado"),
                "hora_fin": d.get("hora_fin_atencion"),
                "duracion_segundos": d.get("duracion_segundos"),
                "cotizacion": d.get("cotizacion"),
                "limpiado": bool(d.get("limpiado")),
            }
        )
    return salida


def _tiempos(regs):
    durs = [r["duracion_segundos"] for r in regs if r["duracion_segundos"] is not None]
    st = _stats_tiempo(durs)
    st["medidos"] = len(durs)
    st["total"] = sum(durs)
    st["total_f"] = formatear_duracion(sum(durs)) if durs else "—"
    return st


def _agrupar(regs, clave):
    grupos = {}
    for r in regs:
        grupos.setdefault(clave(r), []).append(r)
    return grupos


def _fila_grupo(lista):
    st = _tiempos(lista)
    return {
        "turnos": len(lista),
        "cotizaciones": sum(1 for r in lista if r["cotizacion"]),
        "promedio_f": st["promedio_f"],
        "minimo_f": st["minimo_f"],
        "maximo_f": st["maximo_f"],
        "total_f": st["total_f"],
    }


def _por_empleado(regs):
    filas = [
        {"empleado": emp, **_fila_grupo(lista)}
        for emp, lista in _agrupar(regs, lambda r: r["empleado"]).items()
    ]
    filas.sort(key=lambda f: (-f["turnos"], f["empleado"]))
    return filas


def _por_dia(regs):
    filas = []
    for fecha, lista in _agrupar(regs, lambda r: r["fecha"] or "").items():
        fila = {"fecha": _fecha_visible(fecha), "_orden": fecha, **_fila_grupo(lista)}
        fila["empleados"] = len({r["empleado"] for r in lista})
        filas.append(fila)
    filas.sort(key=lambda f: f["_orden"], reverse=True)
    for f in filas:
        del f["_orden"]
    return filas


def _por_hora(regs):
    cuenta = Counter()
    for r in regs:
        h = str(r.get("hora_llamado") or "")[:2]
        if h.isdigit():
            cuenta[h] += 1
    return [{"hora": f"{h}:00", "turnos": n} for h, n in sorted(cuenta.items())]


def _resumen_bloque(regs, generados, limpiados, espera=None, atencion=None):
    return {
        "generados": generados,
        "atendidos": len(regs),
        "limpiados": limpiados,
        "cotizaciones": sum(1 for r in regs if r["cotizacion"]),
        "empleados": len({r["empleado"] for r in regs}),
        "espera": espera,
        "atencion": atencion,
        "tiempos": _tiempos(regs),
    }


def _estadisticas(u):
    aj = _ajustes_de(u.id)

    turnos = db.session.scalars(
        select(Turno)
        .where(
            Turno.usuario_id == u.id,
            Turno.sesion_token == aj.sesion_token,
            Turno.hora_llamado.is_not(None),
        )
        .order_by(Turno.hora_llamado.asc(), Turno.id.asc())
    ).all()
    regs_actual = [_reg_de_turno(t, "actual") for t in turnos]

    cerradas = db.session.scalars(
        select(Sesion)
        .where(Sesion.usuario_id == u.id)
        .order_by(Sesion.id.desc())
        .limit(200)
    ).all()
    regs_hist = []
    sesiones = []
    gen_hist = 0
    lim_hist = 0
    for s in cerradas:
        d = s.datos or {}
        regs_hist.extend(_regs_de_sesion(d))
        gen_hist += int(d.get("total_generados") or 0)
        lim_hist += int(d.get("total_limpiados") or 0)
        sesiones.append(
            {
                "id_sesion": d.get("id_sesion"),
                "fecha_inicio": d.get("fecha_inicio"),
                "fecha_cierre": d.get("fecha_cierre"),
                "duracion": d.get("duracion"),
                "generados": d.get("total_generados"),
                "atendidos": d.get("total_atendidos"),
                "limpiados": d.get("total_limpiados"),
                "cotizaciones": d.get("cotizaciones_registradas"),
                "promedio": d.get("tiempo_promedio_atencion"),
                "maximo": d.get("tiempo_maximo_atencion"),
            }
        )

    todos = regs_actual + regs_hist
    return {
        "usuario": _usuario_json(u),
        "generado": _local(ahora_utc()),
        "actual": {
            "inicio": _local(aj.sesion_inicio),
            "modo_empleado": "Obligatorio" if aj.empleado_obligatorio else "Opcional",
            "modo_cotizacion": "Activado" if aj.cotizacion_activa else "Desactivado",
            **_resumen_bloque(
                regs_actual,
                aj.numero_turno,
                aj.total_limpiados,
                _contar(u.id, "espera"),
                _contar(u.id, "atencion"),
            ),
            "por_empleado": _por_empleado(regs_actual),
            "detalle": regs_actual,
        },
        "historico": {
            "sesiones_cerradas": len(cerradas),
            **_resumen_bloque(todos, gen_hist + aj.numero_turno, lim_hist + aj.total_limpiados),
            "por_empleado": _por_empleado(todos),
            "por_dia": _por_dia(todos),
            "por_hora": _por_hora(todos),
        },
        "sesiones": sesiones,
    }


@app.get("/api/estadisticas")
@login_requerido
def estadisticas():
    """Estadísticas propias; un administrador puede pedir las de cualquier usuario."""
    yo = g.usuario
    uid = request.args.get("usuario_id", type=int)
    if uid is None or uid == yo.id:
        u = yo
    else:
        if not yo.es_admin:
            raise ApiError("No tienes permiso para ver las estadísticas de otro usuario.", 403)
        u = db.session.get(Usuario, uid)
        if u is None:
            raise ApiError("El usuario no existe.", 404)
    return jsonify(_estadisticas(u))


# ============================================================
# API: administración de usuarios (solo administradores)
# ============================================================
@app.get("/api/admin/usuarios")
@admin_requerido
def admin_listar():
    usuarios = db.session.scalars(select(Usuario).order_by(Usuario.usuario.asc())).all()
    filas = db.session.execute(
        select(Turno.usuario_id, Turno.estado, func.count())
        .where(Turno.estado.in_(("espera", "atencion")))
        .group_by(Turno.usuario_id, Turno.estado)
    ).all()
    conteos = {}
    for uid, estado, n in filas:
        conteos.setdefault(uid, {})[estado] = n

    salida = []
    for u in usuarios:
        d = _usuario_json(u)
        c = conteos.get(u.id, {})
        d["en_espera"] = c.get("espera", 0)
        d["en_atencion"] = c.get("atencion", 0)
        salida.append(d)
    return jsonify({"usuarios": salida})


@app.post("/api/admin/usuarios")
@admin_requerido
def admin_crear():
    data = _json()
    usuario = _normalizar_usuario(data.get("usuario"))
    if not USUARIO_RE.fullmatch(usuario):
        raise ApiError(
            "El usuario debe tener de 3 a 40 caracteres: letras, números, "
            "punto, guion o guion bajo."
        )
    nombre = str(data.get("nombre") or "").strip() or usuario
    if len(nombre) > 80:
        raise ApiError("El nombre es demasiado largo (máximo 80 caracteres).")
    password = str(data.get("password") or "")
    _validar_password_nueva(password)

    if db.session.scalar(select(Usuario.id).where(Usuario.usuario == usuario)) is not None:
        raise ApiError("Ya existe un usuario con ese nombre.", 409)

    u = Usuario(
        usuario=usuario,
        nombre=nombre,
        password_hash=generate_password_hash(password),
        es_admin=bool(data.get("es_admin")),
        activo=True,
        debe_cambiar=bool(data.get("debe_cambiar", True)),
    )
    db.session.add(u)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ApiError("Ya existe un usuario con ese nombre.", 409)

    _inicializar_usuario(u.id)  # su turnero arranca limpio
    return jsonify({"ok": True, "usuario": _usuario_json(u)}), 201


@app.patch("/api/admin/usuarios/<int:uid>")
@admin_requerido
def admin_editar(uid):
    data = _json()
    yo = g.usuario
    u = db.session.get(Usuario, uid)
    if u is None:
        raise ApiError("El usuario no existe.", 404)

    if "nombre" in data:
        nombre = str(data.get("nombre") or "").strip()
        if not nombre:
            raise ApiError("El nombre no puede quedar vacío.")
        if len(nombre) > 80:
            raise ApiError("El nombre es demasiado largo (máximo 80 caracteres).")
        u.nombre = nombre

    if "es_admin" in data:
        nuevo = bool(data["es_admin"])
        if u.id == yo.id and nuevo != u.es_admin:
            raise ApiError("No puedes cambiar tu propio rol.", 409)
        u.es_admin = nuevo

    if "activo" in data:
        nuevo = bool(data["activo"])
        if u.id == yo.id and not nuevo:
            raise ApiError("No puedes quitarte el acceso a ti mismo.", 409)
        if u.activo and not nuevo:
            u.version_sesion += 1  # cierra de inmediato sus sesiones abiertas
        u.activo = nuevo

    password = str(data.get("password") or "")
    if password:
        if u.id == yo.id:
            raise ApiError("Para cambiar tu propia contraseña usa «Mi clave».", 409)
        _validar_password_nueva(password)
        u.password_hash = generate_password_hash(password)
        u.version_sesion += 1
        u.debe_cambiar = bool(data.get("debe_cambiar", True))
    elif "debe_cambiar" in data:
        u.debe_cambiar = bool(data["debe_cambiar"])

    db.session.commit()
    return jsonify({"ok": True, "usuario": _usuario_json(u)})


@app.post("/api/admin/usuarios/<int:uid>/inicializar")
@admin_requerido
def admin_inicializar(uid):
    """Cierra la sesión de trabajo de un usuario (queda en su historial) y deja su
    turnero en blanco, como recién creado."""
    u = db.session.get(Usuario, uid)
    if u is None:
        raise ApiError("El usuario no existe.", 404)
    aj = _ajustes_de(uid, lock=True)
    resumen = _cerrar_sesion_de(u, aj)
    return jsonify({"ok": True, "resumen": resumen})


# ============================================================
# INICIALIZACIÓN Y MIGRACIÓN DE LA BASE DE DATOS
# ============================================================
def _migrar_esquema():
    """Bases creadas con la versión anterior (sin usuarios): añade `usuario_id`."""
    insp = inspect(db.engine)
    tablas = set(insp.get_table_names())
    for tabla in ("ajustes", "turnos", "sesiones"):
        if tabla in tablas:
            columnas = {c["name"] for c in insp.get_columns(tabla)}
            if "usuario_id" not in columnas:
                db.session.execute(text(f"ALTER TABLE {tabla} ADD COLUMN usuario_id INTEGER"))
    db.session.commit()

    if db.engine.dialect.name == "postgresql":
        # La fila antigua de `ajustes` se insertó con id=1 explícito, sin avanzar la
        # secuencia: la sincronizamos para que los nuevos turneros no choquen con ella.
        db.session.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('ajustes', 'id'), "
                "(SELECT COALESCE(MAX(id), 0) + 1 FROM ajustes), false)"
            )
        )
        db.session.commit()

    for sql in (
        # Un solo turnero por usuario, aunque dos procesos intenten crearlo a la vez.
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ajustes_usuario ON ajustes (usuario_id)",
        "CREATE INDEX IF NOT EXISTS ix_turnos_usuario_estado ON turnos (usuario_id, estado)",
        "CREATE INDEX IF NOT EXISTS ix_sesiones_usuario ON sesiones (usuario_id)",
    ):
        db.session.execute(text(sql))
    db.session.commit()


def _credenciales_antiguas():
    if "credenciales" not in inspect(db.engine).get_table_names():
        return []
    filas = db.session.execute(
        text("SELECT rol, usuario, password_hash FROM credenciales")
    ).all()
    return [tuple(f) for f in filas]


def _agregar_usuario(**campos):
    db.session.add(Usuario(**campos))
    _commit_tolerante()


def _crear_usuarios_iniciales():
    """Primer arranque: crea al administrador (o migra las cuentas de la versión anterior)."""
    if db.session.scalar(select(func.count()).select_from(Usuario)):
        return

    antiguas = {rol: (usr, h) for rol, usr, h in _credenciales_antiguas()}
    if "admin" in antiguas:
        usr, h = antiguas["admin"]
        _agregar_usuario(
            usuario=_normalizar_usuario(usr) or "admin",
            nombre="Administrador",
            password_hash=h,
            es_admin=True,
        )
        if "sub_admin" in antiguas:
            usr, h = antiguas["sub_admin"]
            _agregar_usuario(
                usuario=_normalizar_usuario(usr) or "sub_admin",
                nombre="Sub-administrador",
                password_hash=h,
                es_admin=False,
            )
    if db.session.scalar(select(func.count()).select_from(Usuario)):
        return

    usuario = _normalizar_usuario(os.environ.get("ADMIN_USER", "admin")) or "admin"
    password = os.environ.get("ADMIN_PASSWORD", "")
    por_defecto = not password
    if por_defecto:
        app.logger.warning(
            "ADMIN_PASSWORD no está definida: se creó el administrador '%s' con la "
            "clave temporal 'admin123' y se le pedirá cambiarla al entrar.",
            usuario,
        )
    _agregar_usuario(
        usuario=usuario,
        nombre="Administrador",
        password_hash=generate_password_hash(password or "admin123"),
        es_admin=True,
        debe_cambiar=por_defecto,
    )


def _asignar_datos_antiguos():
    """Turnos/sesiones/ajustes de la versión anterior pasan al primer administrador."""
    admin = db.session.scalar(
        select(Usuario)
        .where(Usuario.es_admin.is_(True))
        .order_by(Usuario.id.asc())
        .limit(1)
    )
    if admin is None:
        return
    for tabla in ("ajustes", "turnos", "sesiones"):
        db.session.execute(
            text(f"UPDATE {tabla} SET usuario_id = :uid WHERE usuario_id IS NULL"),
            {"uid": admin.id},
        )
    db.session.commit()


def inicializar_db():
    db.create_all()
    _migrar_esquema()
    _crear_usuarios_iniciales()
    _asignar_datos_antiguos()
    # Cada usuario existente tiene su turnero inicializado.
    for uid in db.session.scalars(select(Usuario.id)).all():
        _inicializar_usuario(uid)


with app.app_context():
    inicializar_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
