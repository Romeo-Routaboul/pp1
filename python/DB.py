import json
from configparser import ConfigParser
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import mysql.connector
from mysql.connector import MySQLConnection
from mysql.connector.cursor import MySQLCursorDict


CONFIG_PATH = Path(__file__).resolve().parent.parent / "DB.ini"


def _database_config() -> dict[str, Any]:
    parser = ConfigParser(interpolation=None)
    loaded_files = parser.read(CONFIG_PATH, encoding="utf-8")
    if not loaded_files or not parser.has_section("mysql"):
        raise RuntimeError(f"No se encontró la configuración [mysql] en {CONFIG_PATH}.")

    section = parser["mysql"]
    required = ("host", "user", "database")
    missing = [key for key in required if not section.get(key, "").strip()]
    for key, example_value in (
        ("user", "your_mysql_user"),
        ("password", "your_mysql_password"),
    ):
        if section.get(key, "").strip() == example_value:
            missing.append(key)
    if missing:
        raise RuntimeError(
            "Completá los siguientes valores en DB.ini: " + ", ".join(missing)
        )

    return {
        "host": section["host"].strip(),
        "port": section.getint("port", fallback=3306),
        "user": section["user"].strip(),
        "password": section["password"],
        "database": section["database"].strip(),
        "connection_timeout": section.getint("connection_timeout", fallback=10),
        "charset": "utf8mb4",
        "collation": "utf8mb4_unicode_ci",
    }


@contextmanager
def database_transaction() -> Iterator[tuple[MySQLConnection, MySQLCursorDict]]:
    connection = mysql.connector.connect(**_database_config())
    cursor: MySQLCursorDict | None = None
    try:
        cursor = connection.cursor(dictionary=True)
        yield connection, cursor
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def fetch_one(sql: str, parameters: tuple[object, ...] = ()) -> dict[str, Any] | None:
    with database_transaction() as (_, cursor):
        cursor.execute(sql, parameters)
        return cursor.fetchone()


def fetch_all(sql: str, parameters: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    with database_transaction() as (_, cursor):
        cursor.execute(sql, parameters)
        return cursor.fetchall()


def execute(sql: str, parameters: tuple[object, ...] = ()) -> tuple[int, int]:
    with database_transaction() as (_, cursor):
        cursor.execute(sql, parameters)
        return cursor.lastrowid, cursor.rowcount


def decode_json(value: object, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    raise ValueError("La base de datos devolvió un valor JSON inválido.")
