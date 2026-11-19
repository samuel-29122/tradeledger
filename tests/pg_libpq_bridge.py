"""TEST-ONLY PostgreSQL driver: a thin ctypes bridge to libpq.

Why this file exists
--------------------
TradeLedger talks to PostgreSQL through psycopg 3 (see requirements.txt).
The environment Phase 8 was built in has a real PostgreSQL 16 server and
libpq (PostgreSQL's official C client library, the one psql uses), but no
way to install psycopg. Rather than leave the PostgreSQL code untested, the
test suite can register this bridge instead (TRADELEDGER_PG_DRIVER=libpq-bridge),
so every test runs SQL against a REAL PostgreSQL server.

What it is NOT
--------------
* It is not shipped or used by the application: tradeledger/ never imports it.
* It is not psycopg. It covers only the calls TradeLedger's adapter makes
  (connect, cursor().execute, fetchone/fetchall, description, rowcount,
  close) and converts results by type: integers -> int, NUMERIC ->
  Decimal, booleans -> bool, everything else -> str. Parameters are sent as
  text with explicit types for int, Decimal and bool, and "unknown" for
  strings (which is also psycopg's behaviour for str).
* Results obtained through it show the SQL is correct on PostgreSQL. They
  do not prove psycopg-specific behaviour or performance; the CI job with
  real psycopg is the authoritative PostgreSQL run.
"""

import ctypes
import ctypes.util
from decimal import Decimal

# Read by tradeledger.db.register_postgres_driver(): only modules that say
# they are test-only may replace psycopg, and production refuses them all.
TEST_ONLY = True

_lib = ctypes.CDLL(ctypes.util.find_library("pq") or "libpq.so.5")

_lib.PQconnectdb.restype = ctypes.c_void_p
_lib.PQconnectdb.argtypes = [ctypes.c_char_p]
_lib.PQstatus.argtypes = [ctypes.c_void_p]
_lib.PQerrorMessage.restype = ctypes.c_char_p
_lib.PQerrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQfinish.argtypes = [ctypes.c_void_p]
_lib.PQexec.restype = ctypes.c_void_p
_lib.PQexec.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_lib.PQexecParams.restype = ctypes.c_void_p
_lib.PQexecParams.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_char_p),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
]
_lib.PQresultStatus.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorMessage.restype = ctypes.c_char_p
_lib.PQresultErrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorField.restype = ctypes.c_char_p
_lib.PQresultErrorField.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQntuples.argtypes = [ctypes.c_void_p]
_lib.PQnfields.argtypes = [ctypes.c_void_p]
_lib.PQfname.restype = ctypes.c_char_p
_lib.PQfname.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQftype.restype = ctypes.c_uint
_lib.PQftype.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQgetvalue.restype = ctypes.c_char_p
_lib.PQgetvalue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQgetisnull.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQcmdTuples.restype = ctypes.c_char_p
_lib.PQcmdTuples.argtypes = [ctypes.c_void_p]
_lib.PQclear.argtypes = [ctypes.c_void_p]

_CONNECTION_OK = 0
_PGRES_COMMAND_OK, _PGRES_TUPLES_OK = 1, 2
_DIAG_SQLSTATE, _DIAG_CONSTRAINT = ord("C"), ord("n")

# PostgreSQL type OIDs
_BOOL, _INT8, _INT2, _INT4, _OID, _FLOAT4, _FLOAT8, _NUMERIC = 16, 20, 21, 23, 26, 700, 701, 1700


class Error(Exception):
    pass


class DatabaseError(Error):
    def __init__(self, message, sqlstate=None, constraint=None):
        super().__init__(message)
        self.sqlstate = sqlstate
        self.diag = type("Diag", (), {"constraint_name": constraint})()


class IntegrityError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


class DataError(DatabaseError):
    pass


def _convert(text, oid):
    if oid in (_INT2, _INT4, _INT8, _OID):
        return int(text)
    if oid == _NUMERIC:
        return Decimal(text)
    if oid == _BOOL:
        return text == "t"
    if oid in (_FLOAT4, _FLOAT8):
        return float(text)
    return text


def _param(value):
    """(type oid, text) for one parameter; None means SQL NULL."""
    if value is None:
        return 0, None
    if isinstance(value, bool):
        return _BOOL, "t" if value else "f"
    if isinstance(value, int):
        return _INT8, str(value)
    if isinstance(value, Decimal):
        return _NUMERIC, str(value)
    if isinstance(value, float):
        return _FLOAT8, repr(value)
    if isinstance(value, str):
        return 0, value
    raise TypeError(f"the libpq test bridge cannot send {type(value).__name__}")


def _to_numbered(sql):
    """psycopg-style "%s" -> "$1", "$2", ...; "%%" -> "%"."""
    out, n, i = [], 0, 0
    while i < len(sql):
        char = sql[i]
        if char == "%" and i + 1 < len(sql):
            nxt = sql[i + 1]
            if nxt == "s":
                n += 1
                out.append(f"${n}")
                i += 2
                continue
            if nxt == "%":
                out.append("%")
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)


class Cursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self.rowcount = -1
        self._rows = []

    def execute(self, sql, params=None):
        handle = self._conn._handle
        if params is None:
            result = _lib.PQexec(handle, sql.encode())
        else:
            types, values = zip(*(_param(v) for v in params), strict=True) if params else ((), ())
            count = len(values)
            type_array = (ctypes.c_uint * count)(*types)
            value_array = (ctypes.c_char_p * count)(*(None if v is None else v.encode() for v in values))
            result = _lib.PQexecParams(
                handle, _to_numbered(sql).encode(), count, type_array, value_array, None, None, 0
            )
        try:
            status = _lib.PQresultStatus(result)
            if status not in (_PGRES_COMMAND_OK, _PGRES_TUPLES_OK):
                message = (_lib.PQresultErrorMessage(result) or b"").decode(errors="replace").strip()
                sqlstate = (_lib.PQresultErrorField(result, _DIAG_SQLSTATE) or b"").decode() or None
                constraint = (_lib.PQresultErrorField(result, _DIAG_CONSTRAINT) or b"").decode() or None
                state = sqlstate or ""
                cls = (
                    IntegrityError
                    if state.startswith("23")
                    else DataError
                    if state.startswith("22")
                    else DatabaseError
                )
                raise cls(message or "PostgreSQL error", sqlstate, constraint)
            if status == _PGRES_TUPLES_OK:
                fields = _lib.PQnfields(result)
                oids = [_lib.PQftype(result, c) for c in range(fields)]
                self.description = [(_lib.PQfname(result, c).decode(),) for c in range(fields)]
                self._rows = [
                    tuple(
                        None
                        if _lib.PQgetisnull(result, r, c)
                        else _convert(_lib.PQgetvalue(result, r, c).decode(), oids[c])
                        for c in range(fields)
                    )
                    for r in range(_lib.PQntuples(result))
                ]
                self.rowcount = len(self._rows)
            else:
                self.description = None
                self._rows = []
                affected = (_lib.PQcmdTuples(result) or b"").decode()
                self.rowcount = int(affected) if affected else -1
        finally:
            _lib.PQclear(result)
        return self

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class Connection:
    def __init__(self, conninfo):
        self._handle = _lib.PQconnectdb(conninfo.encode())
        if _lib.PQstatus(self._handle) != _CONNECTION_OK:
            message = (_lib.PQerrorMessage(self._handle) or b"").decode(errors="replace").strip()
            _lib.PQfinish(self._handle)
            self._handle = None
            raise OperationalError(message)

    def cursor(self):
        return Cursor(self)

    def close(self):
        if self._handle:
            _lib.PQfinish(self._handle)
            self._handle = None


def connect(conninfo, autocommit=True):
    """libpq runs each statement in its own transaction unless BEGIN is sent,
    which is exactly psycopg's autocommit=True behaviour."""
    if not autocommit:
        raise ValueError("the libpq test bridge only supports autocommit=True")
    return Connection(conninfo)
