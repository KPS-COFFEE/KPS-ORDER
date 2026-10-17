"""Conservative, row-scoped cleanup rehearsal on isolated restored databases only.

No source cleanup or commit operation is exposed. Evidence from synthetic tests
is explicitly distinct from an actual-source restore rehearsal.
"""
import hashlib
import json

from psycopg import sql
from .prelaunch_inventory import REVIEW, PRESERVE, fiscal_retention_tables


def _digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str,
                                     separators=(',', ':')).encode()).hexdigest()


def rehearse(conn, manifest, *, source_database):
    """Try exact reviewed rows and always roll back, including on success.

    manifest = [{table, id_column, id, sha256}]. Every row needs its original
    whole-row digest. Fiscal-connected tables are retained wholesale until a
    separately reviewed row-level financial archive strategy exists.
    """
    database = conn.execute('SELECT current_database() AS name').fetchone()['name']
    if not database.startswith('test_') or database == source_database:
        raise ValueError('Only a separate test_ restored database may be rehearsed')
    version = int(conn.execute('SHOW server_version_num').fetchone()['server_version_num'])
    if not 180000 <= version < 190000:
        raise ValueError('PostgreSQL 18 restore required')
    if not manifest:
        raise ValueError('Explicit reviewed row manifest required')
    conn.execute('SAVEPOINT cleanup_rehearsal')
    try:
        tables = [r['tablename'] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename")]
        # Quiesce the entire isolated copy. This is never acquired on production.
        conn.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE NOWAIT').format(
            sql.SQL(',').join(sql.Identifier(t) for t in tables)))
        edges = conn.execute("SELECT child.relname AS table_name,parent.relname AS referenced_table "
            "FROM pg_constraint c JOIN pg_class child ON child.oid=c.conrelid "
            "JOIN pg_class parent ON parent.oid=c.confrelid JOIN pg_namespace n ON n.oid=child.relnamespace "
            "WHERE c.contype='f' AND n.nspname=current_schema()").fetchall()
        cross_schema = conn.execute(
            "SELECT count(*) AS n FROM pg_constraint c "
            "JOIN pg_class child ON child.oid=c.conrelid "
            "JOIN pg_class parent ON parent.oid=c.confrelid "
            "JOIN pg_namespace cn ON cn.oid=child.relnamespace "
            "JOIN pg_namespace pn ON pn.oid=parent.relnamespace "
            "WHERE c.contype='f' AND cn.nspname<>pn.nspname "
            "AND (cn.nspname=current_schema() OR pn.nspname=current_schema())").fetchone()['n']
        if cross_schema:
            raise ValueError('Cross-schema relationships require separate retention review')
        retained = set(fiscal_retention_tables(edges)) | PRESERVE
        before = _snapshot(conn, tables)
        expected = {t: list(rows) for t, rows in before.items()}
        seen = set()
        for item in manifest:
            table, column = item['table'], item['id_column']
            key = (table, column, str(item['id']))
            if key in seen:
                raise ValueError('Duplicate manifest row')
            seen.add(key)
            if table not in REVIEW or table in retained or table not in tables:
                raise ValueError('Unreviewed or fiscal-connected table is preserved')
            triggers = conn.execute("SELECT count(*) AS n FROM pg_trigger "
                "WHERE tgrelid=to_regclass(%s) AND NOT tgisinternal AND tgenabled<>'D'", (table,)).fetchone()['n']
            if triggers:
                raise ValueError('User triggers require separate side-effect review')
            # Require a single-column primary key, not an arbitrary predicate.
            pk = conn.execute("SELECT a.attname FROM pg_index i JOIN pg_attribute a "
                "ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
                "WHERE i.indrelid=to_regclass(%s) AND i.indisprimary", (table,)).fetchall()
            if [r['attname'] for r in pk] != [column]:
                raise ValueError('Single-column primary key required')
            query = sql.SQL('SELECT to_jsonb(t) AS row FROM {} t WHERE {}=%s FOR UPDATE').format(
                sql.Identifier(table), sql.Identifier(column))
            rows = conn.execute(query, (item['id'],)).fetchall()
            if len(rows) != 1 or _digest(rows[0]['row']) != item['sha256']:
                raise ValueError('Manifest row missing or changed')
            expected[table].remove(_digest(rows[0]['row']))
            conn.execute(sql.SQL('DELETE FROM {} WHERE {}=%s').format(
                sql.Identifier(table), sql.Identifier(column)), (item['id'],))
        # Includes immutable fiscal JSON, amounts, relationships, settings and IDs.
        if _snapshot(conn, tables) != expected:
            raise ValueError('Unexpected cascade or change outside reviewed rows')
        return {'mode': 'ROLLBACK_REHEARSAL_ONLY', 'database': database,
                'reviewed_rows': len(manifest), 'unexpected_changes': 0,
                'source_cleaned': False, 'backup_verified': False,
                'actual_source_restore_verified': False,
                'production_cleanup_authorized': False}
    finally:
        conn.execute('ROLLBACK TO SAVEPOINT cleanup_rehearsal')
        conn.execute('RELEASE SAVEPOINT cleanup_rehearsal')


def _snapshot(conn, tables):
    return {t: sorted(_digest(r['row']) for r in conn.execute(
        sql.SQL('SELECT to_jsonb(t) AS row FROM {} t').format(sql.Identifier(t)))) for t in tables}
