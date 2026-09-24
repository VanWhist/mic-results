"""Paths, series labels and shared helpers for the mic-results ETL.

Source PDFs live outside the repo (read-only) in ``その他大会のリザルト`` and are described by
registry files in ``etl/registry/*.json`` (one file per source adapter). The ETL never guesses
an event from a file name: every round comes from a registry entry.
"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PDF_ROOT = os.environ.get('MIC_PDF_ROOT', os.path.join(os.path.dirname(REPO), 'その他大会のリザルト'))
MOGULS_RESULTS_REPO = os.environ.get('MOGULS_RESULTS_REPO', os.path.join(os.path.dirname(REPO), 'moguls-results'))
DATA_DIR = os.path.join(REPO, 'data')
DOCS_DIR = os.path.join(REPO, 'docs')
GOLDEN_DIR = os.path.join(REPO, 'golden')
RULES_DIR = os.path.join(HERE, 'rules')
EVENT_RULES_DIR = os.path.join(RULES_DIR, 'events')
REGISTRY_DIR = os.path.join(HERE, 'registry')

EXPECTED_ROUNDS = os.path.join(HERE, 'expected_rounds.json')
PUBLISHED_HASHES = os.path.join(HERE, 'published_hashes.json')
ATHLETE_MASTER = os.path.join(HERE, 'athlete_master.json')
ATHLETE_ALIASES = os.path.join(HERE, 'athlete_aliases.json')
MIC_ROSTER = os.path.join(HERE, 'mic_roster.json')
KNOWN_GAPS = os.path.join(HERE, 'known_gaps.json')
LAYER5_CACHE = os.path.join(HERE, 'layer5_status.json')

# series code -> Japanese label (画面と検証レポートで使う)
SERIES_LABELS = {
    'WC': 'ワールドカップ', 'WSC': '世界選手権', 'OWG': 'オリンピック',
    'EC': 'ヨーロッパカップ', 'NAC': 'ノルアムカップ', 'ANC': 'ANC（豪・NZ）', 'AC': 'アジアカップ',
    'WJC': 'ジュニア世界選手権', 'YOG': 'ユースオリンピック',
    'FIS': 'FISレース', 'NC': 'FIS NC', 'OPN': 'FISオープン',
    'SAJ_AJ': '全日本選手権', 'SAJ_AJJR': '全日本ジュニア', 'SAJ_KOKUSPO': '国スポ', 'SAJ_JOC': 'JOCジュニアオリンピックカップ',
    'SAJ_A': 'A級公認大会', 'SAJ_B': 'B級公認大会', 'SAJ_SEL': '選考会',
}
SERIES_GROUP = {  # 画面の絞り込み用の大分類。それ以外は「国内」
    'WC': 'W杯系', 'WSC': 'W杯系', 'OWG': 'W杯系',
    'EC': 'FIS系', 'NAC': 'FIS系', 'ANC': 'FIS系', 'AC': 'FIS系', 'WJC': 'FIS系', 'YOG': 'FIS系', 'FIS': 'FIS系', 'NC': 'FIS系', 'OPN': 'FIS系',
}
TIER_LABELS = {'detail': 'ジャッジ点まで照合済み', 'score': '得点まで照合済み', 'rank': '順位のみ'}

# internal round codes in display order. SAJ 大会の「予選→決勝→スーパーファイナル」は Q→F1→F2 に写像し、
# 印字の見出し語（決勝・準決勝・スーパーファイナル）は round_text に保持する。
ROUND_ORDER = ['Q', 'Q1', 'Q2', 'F1', 'F2', 'F3']
ROUND_TEXT_DEFAULT = {'Q': 'Qualification', 'Q1': 'Qualification 1', 'Q2': 'Qualification 2',
                      'F1': 'Final 1', 'F2': 'Final 2', 'F3': 'Final 3'}


def slug(s):
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or '').strip('-').lower()
    return s or 'x'


def season_of(date_iso):
    """'2026-03-22' -> '2025-26' (season starts in July)."""
    y, m = int(date_iso[:4]), int(date_iso[5:7])
    start = y if m >= 7 else y - 1
    return f"{start}-{str(start + 1)[2:]}"
