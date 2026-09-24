# MIC モーグル リザルト（mic-results）

MIC の選手・コーチ・保護者向けのモーグル リザルトデータベース。
W杯・世界選手権・五輪以外の大会（国内 SAJ 公認大会、国内 FIS レース、ANC、ジュニア世界選手権、日本人が出た EC・NAC など）を
公式リザルト PDF から読み取り、多層照合を通ったラウンドだけを静的サイトとして公開する。
画面は ①リザルト（大会一覧） ②大会 ③選手 ④データについて。

- 設計: `docs/実装指示書_Phase1.md`
- 棚卸し（FIS DB と SAJ 競技データバンクの大会一覧）: `inventory/棚卸し_大会一覧.xlsx`
- 元データ: `D:\Claude\ジャッジ分析\その他大会のリザルト\`（読むだけ。書き換えない）
- 土台: `moguls-results`（ナショナルチーム用）の ETL と画面。ETL を「取り込み元アダプタ」経由に一般化した

## 1. 大会が増えたときにやること

### まず Claude Code にこう頼んでください

`D:\Claude\ジャッジ分析\mic-results` で Claude Code を起動して、そのまま貼り付けてください。

> `README.md` の「1. 大会が増えたときにやること」を読んで、
> `その他大会のリザルト\` に追加した ○○大会（YYYY-MM-DD）を取り込んでください。
> registry に登録して ETL を回し、検証レポートの結果を見せてください。内容を確認するまで push はしないでください。

**Van さんがやるのは「PDF を置く」「結果を見て公開の可否を決める」の2つだけです。**

### ステップ1: PDF を置く

`その他大会のリザルト\<系列>\<年またはシーズン>\` に、公式リザルト PDF をブラウザで保存して置きます
（FIS サイトの規約により自動取得はしない。SAJ 競技データバンクも同じ運用）。
系列フォルダ: `SAJ_AJ`（全日本）、`SAJ_AJJR`（全日本ジュニア）、`SAJ_A`、`SAJ_B`、`SAJ_KOKUSPO`、`SAJ_JOC`、`SAJ_SEL`、`FIS`、`ANC`、`WJC`、`EC`、`NAC` …

### ステップ2: registry に登録する

`etl/registry/<アダプタ名>.json` の `events` に大会を1件足します（`etl/registry/saj_aj.json` が見本）。
必須: `event_id`（`<シーズン>-<系列小文字>-<識別子>`）、`season`、`series`、`grade`、`discipline`、`name_ja`、`tier`、`adapter`、
`pdfs[]`（`path`・`sha256`・`url`・`page_url`・`saved_at`）、`rules`（`etl/rules/events/` のファイル名）、`format.advance`（性別ごとの進出人数）。
SAJ 系の規則ファイルは全日本の `規則_YYYY.json` の形式で、印字からの全件再計算で確定した値を書きます。

### ステップ3: ETL を回す

```bash
cd D:\Claude\ジャッジ分析\mic-results
python -m etl.build
```

最後にこの行が出ます。

```
833 records / 11 warnings / 0 errors / 公開対象 38/38 ラウンド
data/ を書き出しました: events 10 / rounds 38 / runs 833 / athletes 193 / dataVersion 2026-09-25-0120
```

新しいラウンドは「新しいラウンド（n 名）」の警告になります。人数が PDF と合っていれば `python -m etl.build --accept-rounds` で基準に登録します。

### ステップ4: この3つだけ見てください

| | 意味 | どうするか |
|---|---|---|
| `errors: 0` | 問題なし | 次へ |
| `errors: 1以上` | **その大会は公開されない**（大会単位で全か無か） | `docs/検証レポート.md` のエラー節を Claude Code に見せて直してもらう |

`warnings` は 0 でなくて構いません。人が見て判断するものの置き場です（規則ファイルの例外、ペースタイムの注記、氏名の表記ゆれなど）。

### ステップ5: 画面で確認して push

```bash
python -m http.server 8790 --directory D:\Claude\ジャッジ分析\mic-results
```

http://localhost:8790/ を開いて確認したら、`git add -A && git commit && git push`。

## 2. 記録の3段階（tier）

| tier | 内容 | 検証 |
|---|---|---|
| `detail` | ジャッジ点まで。再計算で印字と一致 | 第0〜5層＋正解データ |
| `score` | 順位・得点・タイムだけ | 第0・3・4・5層＋正解データ |
| `rank` | 順位だけ（デュアルモーグル） | 第0・4・5層＋正解データ |

## 3. 構成

```
etl/build.py               registry → アダプタ → 多層照合 → data/*.json
etl/adapters/saj_aj/       SAJ 様式（全日本・A級・B級・ジュニア共通）。parse_sajmo（行）＋ verify_nc（座標）の2方式
etl/adapters/fis_pdf/      FIS 様式（moguls-results の parser_a / parser_b の複製。取り込みは Step 2 で接続）
etl/adapters/moguls_results/ W杯・世界選手権・五輪を moguls-results の公開データから得点段階で流用。
                           registry・正解データ・第5層の結果は sync_registry.py が生成する（下記）
etl/registry/*.json        大会の登録簿（出典 URL・SHA-256・規則・進出人数）
etl/rules/events/          大会ごとの規則（印字からの全件再計算で確定）
etl/expected_rounds.json   ラウンドごとの人数の基準（--accept-rounds で登録）
etl/published_hashes.json  公開済みラウンドの PDF ハッシュ（改訂検知）
etl/mic_roster.json        MIC 名簿のスナップショット（在籍期間つき。Van さんが保守）
etl/athlete_master.json    同一人物の統合（自動では統合しない）
golden/                    目視で作った正解データ
inventory/                 棚卸し（FIS カレンダー 1990-91〜、SAJ データバンク 2011-12〜）
data/                      公開データ（内容ハッシュ付き JSON ＋ manifest.json）
docs/検証レポート.md       ETL が毎回生成
```

### FIS 様式の PDF（国内 FIS レース・ANC・WJC・EC・NAC・アジアカップ）

`etl/registry/<系列>.json` に `adapter: "fis_pdf"` で登録する。`pdfs[]` は 1 件＝1 ラウンドで、`round`（Q/Q1/Q2/F1/F2）・`gender`（M/W）・`codex` を持つ。
`rules` は `etl/rules/rulesets.json` の版名（例 `"2025-26"`）。審判ごとの点が無い古い PDF は `tier: "score"` にする。
読み取りは moguls-results と同じ 2 方式（`parser_a`＝座標帯、`parser_b`＝行）で、第 1 層で全項目を突き合わせる。
動作確認: `python -m etl.tests.test_fis_adapter`（W杯 2025-26 Nanto-Toyama 男子 3 ラウンドで全層緑）。

### SAJ 競技データバンクの PDF（A級・B級・全日本ジュニア・国内 FIS レースの SAJ 様式）

`inventory/saj_pdf_plan.json`（棚卸しから作った「元 URL → 保存先」の計画）どおりに `その他大会のリザルト\<系列>\<シーズン>\` へ保存したら、

```bash
python -m etl.adapters.saj_aj.gen_registry
```

で `etl/registry/saj_db.json`（MO、adapter saj_aj）と `etl/registry/saj_db_dm.json`（DM、adapter saj_dm）が生成される。
規則は季節ごとの汎用ファイル `etl/rules/events/規則_SAJ_<シーズン>.json`。大会固有の例外（ペースタイムの根拠、印字を正とする行）は
registry の該当大会に `pace_by_sheet` / `recompute_exceptions` を書く（sheet 名は `<event_id>_<Q|F|SF>-<m|w>`）。再生成しても手で書いた項目は保持される。

### W杯・世界選手権・五輪（moguls-results から流用）

ナショナルチーム用 `moguls-results` が審判点まで照合して公開しているデータを、得点までの段階（`score`）で写す。
moguls-results 側でビルドし直したら、こちらで次を実行してから `python -m etl.build` を回す。

```bash
cd D:\Claude\ジャッジ分析\mic-results
python -m etl.adapters.moguls_results.sync_registry
```

`etl/registry/moguls_results.json`・`golden/golden_moguls_results.json`・`etl/layer5_status.json`（FIS 公式 Web との照合結果）・
`etl/athlete_aliases.json`（確認済みの読み・漢字）が更新される。event_id と round_id は moguls-results と同じにしてあり、
大会ページから moguls-results の同じラウンド（審判点あり）へリンクする。元 PDF は `全試合のリザルト\` を読み、SHA-256 だけ照合する。

## 4. 選手の ID

`athlete_id` は FIS コード → `saj-<SAJ 番号>` → `x-<氏名>-<所属>` の順で決める。同姓同名は自動で統合しない。
統合は `etl/athlete_master.json` に根拠つきで書く。MIC 選手は `etl/mic_roster.json` に `athlete_id`（または氏名）と在籍期間を書くと、画面に MIC バッジが付く。
