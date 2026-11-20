"""The database address supports a TCP host and a Unix socket directory (Cloud Run mounts Cloud SQL at /cloudsql)."""

from psycopg.conninfo import conninfo_to_dict

from veloxa.config import DbSettings


def test_unix_socket_directory_is_passed_as_host_parameter():
    d = conninfo_to_dict(DbSettings("/cloudsql/p:asia-south1:i", 5432, "veloxa_company").dsn("veloxa_app", "p@ss/w"))
    assert (d["host"], d["dbname"], d["user"], d["password"]) == ("/cloudsql/p:asia-south1:i", "veloxa_company",
                                                                  "veloxa_app", "p@ss/w")


def test_tcp_host_is_unchanged():
    d = conninfo_to_dict(DbSettings("127.0.0.1", 5432, "veloxa").dsn("u", "p"))
    assert (d["host"], d["port"], d["dbname"]) == ("127.0.0.1", "5432", "veloxa")
