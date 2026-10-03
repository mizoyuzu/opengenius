"""Append a validated synthetic Genius dataset while preserving template rows.

Auxiliary template tables remain intact; their contents are not rebased to new IDs.
"""
from genius_format import parse_config, parse_metadata, parse_similarities, unsigned_id


def _schema(connection):
    return connection.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name').fetchall()


def _snapshot(connection):
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    if not all(name.startswith('genius_') and name.replace('_', '').isalnum() for name in tables):
        raise ValueError('Unsupported template table')
    return {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY 1').fetchall() for name in tables}


def _columns(connection, table):
    rows = connection.execute('PRAGMA table_xinfo("' + table + '")').fetchall()
    if any(row[6] for row in rows):
        raise ValueError('Generated or hidden template columns are unsupported')
    return [row[1] for row in rows]


def append_dataset(connection, metadata, similarities, config_blob):
    """Validate before insertion; roll back only this operation on any failure."""
    if not isinstance(metadata, dict) or not isinstance(similarities, dict) or not metadata or metadata.keys() != similarities.keys():
        raise ValueError('Need nonempty matching metadata and similarity ID sets')
    identifiers = set(metadata)
    if any(type(identifier) is not int or not 0 < identifier <= 0xFFFFFFFF for identifier in identifiers):
        raise ValueError('New Genius IDs must be nonzero uint32 integers')
    for blob in metadata.values():
        if not isinstance(blob, bytes):
            raise ValueError('Metadata must be bytes')
        words = parse_metadata(blob)
        if words[0] != 0:
            raise ValueError('Synthetic genre must be the zero placeholder')
    relation_counts = {}
    for identifier, blob in similarities.items():
        if not isinstance(blob, bytes):
            raise ValueError('Similarities must be bytes')
        _, targets = parse_similarities(blob)
        if len(targets) != len(set(targets)) or not set(targets) <= identifiers:
            raise ValueError('Targets must be unique and confined to the new dataset')
        relation_counts[identifier] = len(targets)
    if not isinstance(config_blob, bytes):
        raise ValueError('Configuration must be bytes')
    config = parse_config(config_blob)
    if config['version'] != 2 or any(item['type'] == 2 for item in config['filters']):
        raise ValueError('Synthetic genre placeholder requires excluding compatible_genre')

    schema = _schema(connection)
    before = _snapshot(connection)
    required = {'genius_metadata', 'genius_similarities', 'genius_config'}
    if not required <= before.keys():
        raise ValueError('Template lacks required Genius tables')
    for kind, name, table, _, _ in schema:
        if kind not in ('table', 'index', 'trigger') or table not in before:
            raise ValueError('Unsupported template schema object')
    for table in ('genius_metadata', 'genius_similarities'):
        if _columns(connection, table) != ['genius_id', 'version', 'data']:
            raise ValueError('Unsupported Genius row schema')
    columns = _columns(connection, 'genius_config')
    if not {'id', 'version', 'data'} <= set(columns):
        raise ValueError('Unsupported Genius configuration schema')
    config_rows = [row for row in before['genius_config'] if row[columns.index('id')] == 1]
    if len(config_rows) != 1 or config_rows[0][columns.index('version')] != 2:
        raise ValueError('Require exactly one version-2 configuration row with id 1')
    occupied = {unsigned_id(row[0]) for table in ('genius_metadata', 'genius_similarities') for row in before[table]}
    if identifiers & occupied:
        raise ValueError('New IDs collide with template metadata or similarities')
    expected = {table: list(rows) for table, rows in before.items()}
    for table, blobs in (('genius_metadata', metadata), ('genius_similarities', similarities)):
        expected[table] = sorted(expected[table] + [(identifier, 1, blobs[identifier]) for identifier in sorted(identifiers)], key=lambda row: row[0])
    replaced = list(config_rows[0])
    replaced[columns.index('data')] = config_blob
    expected['genius_config'] = [tuple(replaced) if row[columns.index('id')] == 1 else row for row in before['genius_config']]

    # A savepoint also preserves an existing caller transaction on failure.
    connection.execute('SAVEPOINT genius_dataset_append')
    try:
        for table, blobs in (('genius_metadata', metadata), ('genius_similarities', similarities)):
            connection.executemany('INSERT INTO ' + table + '(genius_id,version,data) VALUES(?,1,?)',
                                   [(identifier, blobs[identifier]) for identifier in sorted(identifiers)])
        connection.execute('UPDATE genius_config SET data=? WHERE id=1', (config_blob,))
        if _schema(connection) != schema or _snapshot(connection) != expected:
            raise ValueError('Unexpected schema or table delta')
        integrity = connection.execute('PRAGMA integrity_check').fetchall()
        if integrity != [('ok',)]:
            raise ValueError('Experimental database failed integrity check')
        connection.execute('RELEASE SAVEPOINT genius_dataset_append')
    except BaseException:
        connection.execute('ROLLBACK TO SAVEPOINT genius_dataset_append')
        connection.execute('RELEASE SAVEPOINT genius_dataset_append')
        raise
    return {'appended_metadata_rows': len(identifiers), 'appended_similarity_rows': len(identifiers),
            'new_genius_ids': [f'{identifier:016X}' for identifier in sorted(identifiers)],
            'new_relation_count': sum(relation_counts.values()), 'schema_preserved': True,
            'existing_metadata_rows_preserved': len(before['genius_metadata']),
            'existing_similarity_rows_preserved': len(before['genius_similarities']),
            'config_update': 'Only genius_config id=1 data replaced; other columns and rows preserved',
            'preserved_auxiliary_tables': sorted(set(before) - required),
            'auxiliary_preservation_limits': 'Template fingerprint/additional/other auxiliary rows preserved unchanged; not rebased to new IDs',
            'sqlite_integrity': 'ok', 'music_app_acceptance_verified': False, 'ipod_acceptance_verified': False}
