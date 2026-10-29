"""Read-only readiness check for a future final Genius package copy."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

SOURCE_AUDITOR = Path(__file__).resolve().with_name('audit_final_ytmusic_source_v17.py')
SOURCE_AUDITOR_SHA = 'acf9becd36488ab55dc387b2591fb95a8cfd7a762d2cbd6c6621b1f4e2eb35e0'
_spec = importlib.util.spec_from_file_location('_source_audit_v16', SOURCE_AUDITOR)
if _spec is None or _spec.loader is None:
    raise RuntimeError('cannot load fixed source auditor')
source_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(source_audit)
if Path(source_audit.__file__).resolve() != SOURCE_AUDITOR:
    raise RuntimeError('source auditor loaded from an unexpected path')
if hashlib.sha256(SOURCE_AUDITOR.read_bytes()).hexdigest() != SOURCE_AUDITOR_SHA:
    raise RuntimeError('fixed source auditor bytes changed')


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / 'data/new-library-rebased-20261009-v03/Music Library.musiclibrary'
NATIVE = Path('/tmp/opengenius-final-campaign-native-v13-child-v04')
MUSIC = Path('/mnt/data/mizoyuzu/Music.app/Contents/MacOS/Music')
EXPECTED_LIBRARY_FILES = {
    'Library.musicdb': '3869b162bf7be71fe0502dcdb175ca9899c123643170e6c47be702b737c3140c',
    'Genius.itdb': '1fd5019e6eee66d5d2b0dba1f74d9563ce2cfd50d64b417c11d4e1d34275a190',
    'Library Preferences.musicdb': 'ecb3b4619800d6b5b0db08d6ae0470e9281afeb3c9f7c55b90399e9458b6c804',
}
EXPECTED_NATIVE_FILES = {
    'accepted-baseline-graphs.json': '8d14cf54cc75acb17b7fc15757691b2c7b4528d539dfdf86960475d6068207e3',
    'baseline-rebase-report.json': 'bdfc0d0789119e58543ff1d2691920e96e7a7ff98b6c00fcfbc06997254578dc',
    'native-baseline-alias-child-receipt.json': '1bd5c2193d7a9a672704171be87e9b93012abab638ac3f520734f8160aee1809',
    'baseline-state.json': '0ff818736f942e4e849487dd486fdf7a4b39c993c32369a889c60f6e3e383b95',
}
EXPECTED_MUSIC_SHA = 'a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_baseline():
    for filename, expected in EXPECTED_LIBRARY_FILES.items():
        path = ORIGINAL / filename
        require(path.is_file() and sha(path) == expected,
                'original Library bundle changed or is missing: ' + filename)
    require(MUSIC.is_file() and sha(MUSIC) == EXPECTED_MUSIC_SHA,
            'installed Music executable changed')
    for filename, expected in EXPECTED_NATIVE_FILES.items():
        path = NATIVE / filename
        require(path.is_file() and sha(path) == expected,
                'physical Native baseline input changed: ' + filename)
    graph = json.loads((NATIVE / 'accepted-baseline-graphs.json').read_text())
    rows = graph.get('accepted_roots')
    require(isinstance(rows, list) and len(rows) == 515
            and sum(bool(row.get('ordered_target_pids')) for row in rows) == 159
            and sum(len(row.get('ordered_target_pids', [])) for row in rows) == 3341,
            'physical Native baseline no longer has 515 assignments / 159 roots / 3341 edges')
    state = json.loads((NATIVE / 'baseline-state.json').read_text())
    require(state.get('state_sha256') == '188e671a4d3d6743fd33d025a1684c831d1ddd9f87d72bae08250340eee6bd89',
            'physical Native baseline state seal changed')
    parent = source_audit.verify_accepted_782_parent()
    require(parent['native_only_roots'] == 159 and parent['ytmusic_source_roots'] == 623
            and parent['native_ytmusic_overlap'] == 0 and parent['unknown_roots'] == 0,
            'accepted 782 parent source-origin counts do not form the expected disjoint union')
    return {
        'status': 'parent_lineage_ready_final_source_audit_and_package_pending',
        'source_parent': parent,
        'original_library_track_count': 3004,
        'physical_native_assignments': 515,
        'physical_native_nonempty_roots': 159,
        'physical_native_edges': 3341,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-only', action='store_true')
    args = parser.parse_args()
    if not args.baseline_only:
        parser.error('final copy checks require the ≥1000-root independent source audit first')
    print(json.dumps(check_baseline(), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
