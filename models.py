"""Modelos de base de datos.

En producción se usa PostgreSQL (Neon). Sin DATABASE_URL se usa SQLite local.

Cada usuario tiene su propio turnero: sus ajustes, turnos y sesiones llevan
`usuario_id`, así varios usuarios trabajan a la vez sin mezclarse.
"""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def ahora_utc():
    return datetime.now(timezone.utc)


class Usuario(db.Model):
    """Cuenta de acceso. Los administradores gestionan a los demás usuarios."""

    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(40), nullable=False, unique=True, index=True)
    nombre = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    es_admin = db.Column(db.Boolean, nullable=False, default=False)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    # Obliga a cambiar la contraseña en el siguiente acceso.
    debe_cambiar = db.Column(db.Boolean, nullable=False, default=False)
    # Se incrementa al cambiar/restablecer la contraseña: invalida sesiones abiertas.
    version_sesion = db.Column(db.Integer, nullable=False, default=1)
    creado = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora_utc)
    ultimo_acceso = db.Column(db.DateTime(timezone=True))


class Ajustes(db.Model):
    """Estado del turnero de un usuario (una fila por usuario)."""

    __tablename__ = "ajustes"

    id = db.Column(db.Integer, primary_key=True)
    # Nullable solo por compatibilidad con bases creadas antes de existir los
    # usuarios; el código siempre lo rellena.
    usuario_id = db.Column(db.Integer, index=True)
    empleado_obligatorio = db.Column(db.Boolean, nullable=False, default=True)
    cotizacion_activa = db.Column(db.Boolean, nullable=False, default=False)
    numero_turno = db.Column(db.Integer, nullable=False, default=0)
    total_limpiados = db.Column(db.Integer, nullable=False, default=0)
    sesion_token = db.Column(db.String(32), nullable=False)
    sesion_inicio = db.Column(
        db.DateTime(timezone=True), nullable=False, default=ahora_utc
    )


class Turno(db.Model):
    """Un turno y su historial de atención.

    estado: 'espera' | 'atencion' | 'cerrado' (ya no aparece en los paneles,
    pero se conserva para las estadísticas de la sesión).
    """

    __tablename__ = "turnos"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, index=True)
    sesion_token = db.Column(db.String(32), nullable=False, index=True)
    codigo = db.Column(db.String(20), nullable=False)
    estado = db.Column(db.String(10), nullable=False, index=True)
    orden_espera = db.Column(db.Integer)
    orden_atencion = db.Column(db.Integer)
    empleado = db.Column(db.String(60))
    hora_llamado = db.Column(db.DateTime(timezone=True))
    hora_fin = db.Column(db.DateTime(timezone=True))
    duracion_segundos = db.Column(db.Integer)
    cotizacion = db.Column(db.String(6))
    cotizacion_fecha = db.Column(db.DateTime(timezone=True))
    limpiado = db.Column(db.Boolean, nullable=False, default=False)
    creado = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora_utc)


class Sesion(db.Model):
    """Resumen de una sesión cerrada de un usuario (alimenta historial y estadísticas)."""

    __tablename__ = "sesiones"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, index=True)
    id_sesion = db.Column(db.String(40), unique=True, nullable=False)
    datos = db.Column(db.JSON, nullable=False)
    cerrada = db.Column(db.DateTime(timezone=True), nullable=False, default=ahora_utc)
