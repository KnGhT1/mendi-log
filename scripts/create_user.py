"""Crea o actualiza un usuario en la BD.

Uso:
    python scripts/create_user.py --email <email> [--name "Nombre"] [--role admin|user|viewer] [--update]

Solicita la contraseña por stdin (no se acepta como argumento para no
dejarla en el history del shell).

Bootstrap del primer admin: si la BD está vacía cuando se crea el primer
usuario, se fuerza `role="admin"` con independencia del flag, ya que sin
admin no se puede gestionar nada desde la UI.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Permitir ejecución desde la raíz del proyecto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password, revoke_user_sessions  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import User  # noqa: E402

ALLOWED_ROLES = ("admin", "user", "viewer")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea o actualiza un usuario.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None, help="Nombre para mostrar.")
    parser.add_argument(
        "--role", default="user", choices=ALLOWED_ROLES,
        help="Rol del usuario (default: user). El primer usuario se promueve a admin automáticamente.",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Si el email existe, actualiza la contraseña/datos en vez de fallar.",
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    if "@" not in email or len(email) < 5:
        print("Email inválido.", file=sys.stderr)
        return 2

    pw = getpass.getpass("Contraseña (mín 12 chars): ")
    if len(pw) < 12:
        print("La contraseña debe tener al menos 12 caracteres.", file=sys.stderr)
        return 2
    pw2 = getpass.getpass("Repite contraseña: ")
    if pw != pw2:
        print("Las contraseñas no coinciden.", file=sys.stderr)
        return 2

    init_db()
    with SessionLocal() as db:
        existing = db.query(User).filter(User.email == email).first()
        if existing and not args.update:
            print(
                f"El usuario {email} ya existe. Usa --update para cambiar la contraseña.",
                file=sys.stderr,
            )
            return 2
        if existing:
            existing.password_hash = hash_password(pw)
            if args.name:
                existing.display_name = args.name
            # En --update también permitimos cambiar el rol explícitamente
            # cuando el usuario pasa --role. Si no lo pasa, mantenemos el
            # rol actual (default del parser es "user", pero solo aplicamos
            # si difiere del actual y fue explícito).
            if args.role != existing.role:
                existing.role = args.role
            db.commit()
            # H10: al cambiar la password, revoca sus sesiones abiertas.
            revoked = revoke_user_sessions(db, existing.id)
            print(f"Usuario {email} actualizado (rol={existing.role}).")
            if revoked:
                print(f"Sesiones revocadas: {revoked}.")
        else:
            # ¿BD vacía? El primero siempre admin, da igual qué pidieran.
            total_users = db.query(User).count()
            role = "admin" if total_users == 0 else args.role
            if total_users == 0 and args.role != "admin":
                print(f"Nota: primer usuario en la BD — promovido a admin (ignorando --role={args.role}).")
            user = User(
                email=email,
                password_hash=hash_password(pw),
                display_name=args.name,
                is_active=1,
                role=role,
            )
            db.add(user)
            db.commit()
            print(f"Usuario {email} creado con id={user.id} (rol={role}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
