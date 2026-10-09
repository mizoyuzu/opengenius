# 新しいライブラリのカタログ補助インデックス

添付された新しいLibraryは3,004曲。別コピーのGenius DBは2,377曲を索引化した（79.13%）。この割合はradio推薦のカバレッジではない。基準グラフには515個の起点があり、そのうち159起点に非空の出辺がある。基準グラフで触れる曲は515曲、うち356曲は出辺がなく受信辺だけを持つ曲だった。孤立した起点はなかった。基準の3,341本は保存済みYTMusic推薦グラフとして受け入れた関係で、radioとrelatedのendpoint由来を辺ごとに区別できない。旧515曲のPID/GIDとmetadataを保持したまま、1,862曲を追加した。

79.13%は索引化率であり、推薦範囲の割合ではない。保存グラフの出辺起点は基準のYTMusic推薦グラフ159、実際のYTMusicアルバムカタログ96、ローカルアルバム収録情報2,265で、それぞれの重複を別集計した。基準グラフ3,341本を保持し、YTMusicアルバムカタログの候補3,006本から重複を除いた2,987本、ローカルアルバム候補60,938本から重複を除いた57,551本を追加した。最終グラフは有向関係63,879本。YTMusicの実アルバムカタログとローカルLibrary由来の関係を基準YTMusic推薦の観測として数えていない。由来を混ぜず、各辺の根拠を別ファイルに保存した。

追加後の出辺起点は基準グラフとアルバムカタログを合わせて2,308曲。根拠別の起点集合では、基準グラフのみ42、YTMusicアルバムカタログのみ1、ローカルアルバムのみ2,059、基準グラフとローカルの両方111、YTMusicアルバムカタログとローカル89、3種類すべて6だった。これは異なる根拠の関係数であり、音楽的距離の順位やアーティスト類似度スコアではない。

YTMusicは最低2秒間隔で取得した。アルバム検索はHTTP上限21回に達して19件の保存済み検索結果を残した。完全なアルバム名・クレジット一致で3件を採用し、続く5回のHTTPリクエストで171曲の実カタログ観測、69件の未登録曲候補を取得した。表記の異なるアルバムを推測で同一視していない。検索候補やメタデータ一致は音源の同一性の証明として扱わない。

ローカルの補助関係は、非空の同じ正規化アルバム名、完全に同じcanonical artist credit集合、同じ版区分を持つ複数曲のグループから作った。これは同じ作品枠内でクレジットが一致することを示すだけで、曲同士の音楽的な近さを示さない。4,586本のローカル候補辺は汎用クレジット`VA`を含むため、アーティスト近接の根拠には使えない。Off Vocal、karaoke、instrumental、live等の明示された版を分離し、未知の版修飾語と年・版マーカーも完全一致させる。BGM・vocalの明示表記も分離する。明示されていない歌唱/BGMの違い、編集盤の作品境界、類似度までは判定できない。1曲につき補助ターゲットは各ソースで最大100曲。ローカルの並びは決定的なタイトル順で、音楽的な近さを測った順位ではない。

ソース別カバレッジ監査はsealed baseline/output state、Library/track snapshotハッシュ、型付き辺台帳、重複排除後の最終辺数を照合する。実行方法: `python3 scripts/report_recommendation_sources.py --graph-dir data/half-coverage-catalog-graphs-20261009-v03 --baseline-graphs data/new-library-rebased-20261009-v03/accepted-baseline-graphs.json --baseline-state data/new-library-rebased-20261009-v03/state.json --track-snapshot data/new-library-rebased-20261009-v03/zero-id-source/track-snapshot.json`。監査ではローカル辺のtrack record hash不一致0、明示版・unknown qualifier facet不一致0、YTMusic response hash欠落0だった。汎用VAの件数はローカル候補内に限定して集計し、アーティスト近接とは解釈しない。

その後、アルバム補助グラフを含めず、保存済みのYTMusic `radio`と`related`観測だけを17起点分追加した。収集上限90リクエストに達した時点で17/20シードを受け入れ、3シードは収集されていない。独立監査は17スナップショットのhashと再マッチ結果、162件の基準観測receipt、sealed stateを照合した。新規17起点はすべて出辺を持ち、基準515起点・3,341辺に252辺を追加し、合計579曲を索引化した（libraryの19.27%）。最終グラフは532起点行、うち出辺あり176、3,593辺。今回の追加スナップショットは2,494件のradioと220件のrelated観測を含み、推薦グラフ上で触れたPIDは105件、新規索引PIDは64件だった。カタログ由来・ローカルアルバム由来の辺は追加していない。Music再生可能な同一音源と確認した数ではなく、選択・メタデータ対応から作った推薦候補グラフである。追加監査コマンドは前述コマンドに`--genuine-radio-growth data/genuine-radio-graphs-20261009-v01`を付ける。

各ローカル辺には元Libraryとtrack snapshotのSHA-256、起点・対象の元レコードのSHA-256を保存する。YTMusicカタログ辺には保存済み検索・get_album応答のhashと曲目位置を保存する。新たなradio観測を捏造しない。

非公開の成果物（Git追跡対象外）:

- 新しいソースと再検証した状態: `data/new-library-rebased-20261009-v03/`
- 全関係と型付き根拠: `data/half-coverage-catalog-graphs-20261009-v03/`
- 書き出したLibraryコピー: `data/half-coverage-final-bundle-20261009-v03/Music Library.musiclibrary`
- 保存後の独立監査: `data/half-coverage-final-bundle-20261009-v03-independent-audit.json`

この添付Libraryは旧形式1.5.6.11のため、別コピーの書き出しに明示的な`--allow-unverified-profile`を使った。Music.appによる今回のコピーの受け入れは未検証で、Musicが再保存時にGenius ID等をリセットする可能性がある。実機で動くことの証明とは区別する。原本と認証情報を公開・変更していない。

独立レビューで空クレジットと未知の版修飾語の境界ケースを検出し、v03で除外した。v01・v02は採用版ではない。Various Artists等の一般的なクレジットは実際の同一作曲者の証明ではなく、アルバム内の補助関係としてのみ扱う。

128件の関連テストが通過した。保存後の独立監査で暗号化DBを復号し、SQL全行・sealed state・3,004曲のLibrary ID枠・SQLite integrity・設定と補助ファイルの保持を照合した。Sweden、CLANNAD、Charlotteの3起点をMusic選曲コアのエミュレータへ渡し、保存関係から到達可能な出力であることを確認した。これはMusic.appのGUIまたはiPod実機による検証ではない。
