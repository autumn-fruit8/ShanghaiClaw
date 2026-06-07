from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from argparse import Namespace

from infra.runtime_pipeline import _resolve_runtime_root
from skills.analyze.scripts.species import resolve_analysis_selection
from skills.analyze.scripts.analyze import build_parser, resolve_analyze_manifest


def test_runtime_root_is_workspace_owned(tmp_path: Path):
    manifest = tmp_path / 'cn_watchlist.json'
    manifest.write_text('{}', encoding='utf-8')

    runtime_root = _resolve_runtime_root(
        workspace_root=ROOT,
        run_date='2026-04-15',
        region='cn',
        manifest_path=str(manifest),
        manifest_mode='replace',
    )

    assert runtime_root is not None
    assert runtime_root == ROOT / 'adhoc' / '2026-04-15_cn_watchlist'


def test_runtime_root_deduplicates_region_prefix_from_generated_manifest(tmp_path: Path):
    manifest = tmp_path / 'cn_cn_core_satellite.json'
    manifest.write_text('{}', encoding='utf-8')

    runtime_root = _resolve_runtime_root(
        workspace_root=ROOT,
        run_date='2026-04-15',
        region='cn',
        manifest_path=str(manifest),
        manifest_mode='replace',
    )

    assert runtime_root == ROOT / 'adhoc' / '2026-04-15_cn_core_satellite'


def test_default_run_does_not_auto_enable_temp_manifest():
    manifest_path, manifest_mode = resolve_analyze_manifest(
            Namespace(
                region='cn',
                cron=True,
                symbol=None,
                symbols=None,
                manifest_path=None,
                manifest_mode=None,
                use_default_watchlist=False,
                use_active_state=False,
                watchlist=False,
                void=False,
                active=False,
                push=None,
                no_push=False,
            )
    )

    assert manifest_path is None
    assert manifest_mode == 'disabled'


def test_resolve_analysis_selection_uses_catalog_name():
    payload = resolve_analysis_selection(
        workspace_root=ROOT,
        region='us',
        symbol='XLU',
    )

    assert payload['region'] == 'US'
    assert len(payload['assets']) == 1
    assert payload['assets'][0]['symbol'] == 'XLU'
    assert payload['assets'][0]['name'] == 'Utilities Select Sector SPDR'


def test_resolve_analysis_selection_assigns_equal_weights():
    payload = resolve_analysis_selection(
        workspace_root=ROOT,
        region='cn',
        symbols='159611,159930,512400',
    )

    assets = payload['assets']
    assert len(assets) == 3
    weights = [round(a['weight'], 6) for a in assets]
    assert weights == [round(1 / 3, 6)] * 3


def test_resolve_analysis_selection_uses_watchlist_assets_from_state_db():
    payload = resolve_analysis_selection(
        workspace_root=ROOT,
        region='us',
        use_default_watchlist=True,
    )

    symbols = [a['symbol'] for a in payload['assets']]
    assert payload['selection']['mode'] == 'default-watchlist'
    assert 'XLU' in symbols
    assert 'XLB' in symbols
    xlu = next(a for a in payload['assets'] if a['symbol'] == 'XLU')
    assert xlu['notes']  # XLU has a notes string from asset-master


def test_old_default_temp_flag_is_rejected():
    parser = build_parser()

    try:
        parser.parse_args(['--use-default-temp'])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError('Expected --use-default-temp to be rejected')



