from dataclasses import dataclass, field

from psycopg import conninfo

_MODELED_PARAMETERS = frozenset((
    "host",
    "port",
    "dbname",
    "user",
    "password",
    "sslmode",
    "connect_timeout",
))


@dataclass(frozen=True)
class ConnectionOptions:
    """Complete set of PostgreSQL connection parameters used by this extension."""

    host: str | None = None
    port: int = 5432
    dbname: str | None = None
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    sslmode: str | None = None
    connect_timeout: int = 10

    @classmethod
    def from_dsn(cls, dsn: str) -> "ConnectionOptions":
        """Parse a libpq URL or keyword DSN into explicit connection options.

        Unrecognized DSN parameters raise instead of being silently dropped, since the
        connect calls pass only the modeled fields.
        """
        parsed = {
            name: str(setting)
            for name, setting in conninfo.conninfo_to_dict(dsn).items()
            if setting is not None
        }
        unknown = ", ".join(sorted(set(parsed) - _MODELED_PARAMETERS))
        if unknown:
            raise RuntimeError(f"Unsupported PostgreSQL connection parameter(s): {unknown}")
        defaults = cls()
        return cls(
            host=parsed.get("host", defaults.host),
            port=int(parsed.get("port", defaults.port)),
            dbname=parsed.get("dbname", defaults.dbname),
            user=parsed.get("user", defaults.user),
            password=parsed.get("password", defaults.password),
            sslmode=parsed.get("sslmode", defaults.sslmode),
            connect_timeout=int(parsed.get("connect_timeout", defaults.connect_timeout)),
        )
