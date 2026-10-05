"""Empirical distance profiles for the observed Music Genius config."""
from emulate_ytmusic_candidates import controlled_configs
from genius_format import pack_config, parse_config


def artist_preference_config(config, preference='neutral'):
    """Relax initial artist spacing by one or two; preserve its floor and other filters."""
    if preference not in ('neutral', 'slight', 'moderate'):
        raise ValueError('Unsupported artist preference')
    if preference == 'neutral':
        return config
    parsed = parse_config(config)
    for item in parsed['filters']:
        if item['type'] == 3 and item['parameters'][0] == 1:
            parameters = item['parameters']
            parameters[2] = max(parameters[1], parameters[2] - {'slight': 1, 'moderate': 2}[preference])
    # Relations-only has no artist filter, so this modifier has no effect there.
    return pack_config(parsed)


def distance_profiles(config):
    baseline = parse_config(controlled_configs(config)['without-compatible-genre'])
    observed = {item['parameters'][0] for item in baseline['filters'] if item['type'] == 3}
    if not {1, 2, 3} <= observed:
        raise ValueError('Expected observed artist/album/song distance indices')
    variants = {}
    for name, lower, remove in [
        ('baseline', set(), set()),
        ('artist-minimum-one', {1}, set()),
        ('album-minimum-one', {2}, set()),
        ('artist-album-minimum-one', {1, 2}, set()),
        ('without-artist-distance', set(), {1}),
        ('without-album-distance', set(), {2}),
        ('without-artist-album-distance', set(), {1, 2}),
    ]:
        parsed = parse_config(pack_config(baseline))
        filters = []
        for item in parsed['filters']:
            if item['type'] == 3:
                index = item['parameters'][0]
                if index in remove:
                    continue
                if index in lower:
                    item['parameters'][1] = 1
            filters.append(item)
        parsed['filters'] = filters
        variants[name] = pack_config(parsed)
    variants['relations-only'] = controlled_configs(config)['relations-only']
    return variants
