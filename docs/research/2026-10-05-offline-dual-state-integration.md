# 両DBのGenius状態を実験bundle生成へ統合

実Macで確認した「Library.musicdbとLibrary Preferences.musicdbの両方を揃える」準備を、独立コピーの生成経路へ組み込んだ。実Macのアクセスは使わず、原本の変更、Appleの有効化、外部送信は行っていない。

## 経路と既定動作

- `build_real_library_experiment.py`：全曲のPID・メタデータを保ったbundle生成。
- `prepare_macos_real_rebase.py`：新規native importのPIDへの再対応、および全native libraryへの再対応。
- 両CLIで `--local-genius-opt-in` を明示した場合だけ、両DBの状態を1にする。既定では保存済みの状態をそのまま保つ。
- `local_genius_state` に入力・出力それぞれの両状態、整合性、鍵ヘッダーの一致、許可した差分を記録する。状態が不一致のままの既定出力も判別できる。
- Libraryのlegacy codec拒否条件は変更していない。bundle生成/rebaseにはlegacyを回避するオプションも追加していない。
- `audit_macos_real_library.py` はnative保存の前後で両状態を比較し、通常終了が確認された後の両状態1を報告する。これは長期保持やiPod対応の確認ではない。

共通の `prepare_state_blobs` は両ファイルをメモリ上で準備し、暗号化往復、状態1バイト以外の全展開データ不変、各鍵ヘッダー不変、両ヘッダー一致を検証する。移行状態や未対応・曖昧なplmaは上書きしない。`inspect_state_pair` は識別子・鍵値を含まない監査結果を返す。

## Webと同じアーティスト設定

bundle生成に `--artist-preference neutral|slight|moderate` を追加した。CLI/APIの既定は従来の `neutral`。Webの現在の設定に合わせる場合は `--artist-preference moderate` を指定する。実際に保存するconfigに適用し、レポートにも記録する。比較対象baselineは従来どおりneutral。

観測入力はファイル集合だけでなく順序も保存する。複数snapshotの関係を統合する際に入力順が候補順へ影響する場合があるため、既存reportの`observation_inputs`順序を再現する。

```sh
.venv/bin/python scripts/build_real_library_experiment.py \
  --bundle /path/to/source.musiclibrary \
  --native-template /path/to/verified-native-probe/after \
  --identity-map /path/to/identity-map.json \
  --observations-directory /path/to/saved-observations \
  --genius-reference /path/to/decrypted-reference.itdb \
  --executable /path/to/Music \
  --output-directory /path/to/new-output \
  --artist-preference moderate --local-genius-opt-in
```

この出力を生きたLibraryへ直接インストールする処理は追加していない。Musicと補助プロセスの停止、完全バックアップ、入力照合は実機での適用時に別途必要。

## ローカル検証

新規4テストと関連する既存18テストが成功。Preferencesだけ1という不整合を修復し、Genius.itdbとそれ以外の展開データを保持すること、既定rebaseが状態を変えないこと、移行状態・鍵不一致を出力作成前に拒否すること、legacy拒否が続くことを確認した。

さらにHDD上の2974曲のLibraryと、保存済みMusic 1.6のnative snapshotを読み取り、実CLIで別bundleを2件生成した。四つの観測directoryを指定した試行は273関連曲・44起点・843有向関係。Swedenの観測1件でmoderateを指定した試行は14関連曲・1起点・13有向関係。以前の286曲・885関係の実Mac試行とは入力集合が異なる。

続いて以前の47観測入力・manifest・cluster設定を、ハッシュだけでなく順序も一致させて再生成した。`data/local-opt-in-v04-web-moderate-20261005/` の完全bundleは45起点・286関連曲・885有向関係を保持する。独立監査で、2974曲のPIDとGenius ID以外の全解析メタデータ、両DBの状態1と鍵ヘッダー一致、実保存configのartist距離`[1,1,4,6]`、実保存SQLの885関係を確認した。45起点すべての選曲PID順序が現在のWebのmoderate結果と一致した。

その監査は同ディレクトリの `independent-audit.json` に保存する。入力集合が同じでもextra3件の順序を変えた先行試行では、1起点のPID順序が異なったため、本番一致の根拠に含めていない。

個人データの出力と監査はGit管理外の`data/`に保持する。今回生成したbundleをMusic.appやiPodで読み込んだとは主張しない。

## 次の更新経路

現行bundle生成は「全曲のGenius IDが0、Genius DBが空」の初期投入専用である。実Macで保持できた286曲の既存IDを維持して推薦対象を増やす更新エンジンは未実装。今回その拒否条件を緩めていない。追加更新では次の不変条件が必要になる。

- 既存PID→Genius ID対応を固定し、新しい対応だけを未使用のuint32 IDへ割り当てる。参照元・参照先とLibrary内の割り当てを照合し、既存曲を再採番しない。
- artist/album groupingの対応表を引き継ぐ。表記の追加・変更で既存group IDを再採番せず、意図する分類変更は別の移行として確認する。
- 出力先の鍵ヘッダー、両DBの状態、全曲メタデータ、native support filesを引き継ぐ。暗号化往復・SQL整合性・許可した差分・source snapshot hashを検証してから独立コピーを出す。
- OpenGeniusが生成した行・関係の由来と入力順をmanifestで管理する。既存Apple由来の行やfingerprint等を生成データと区別し、未知の行を無条件に削除・置換しない。混在した元DBに対する方針を決めるまでは拒否を維持する。
