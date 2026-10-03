# 本番Library全体から作品を分散した20起点を評価

本番Libraryの保存済み2,974曲から20起点を明示選択した。
7枠のゲームBGM、3枠のアニメBGM、2枠のピアノ編曲、6枠の歌もの、Minecraft、Tychoを含む。
20種類のartist表記から選び、シャニマスは比較用1枠。区分は試験の層を説明するためで、推薦の重みには使わない。
元Libraryや音源の読み取りは必要なく、HDDは未接続のまま。

## 入力と取得時の問題

`--seed-plan`でschema version、元Library SHA、20件とPIDの一意性を検証する。
計画にはPIDと比較区分だけを許可し、曲名等は現在の曲一覧から読み、上書きを拒否する。
計画のハッシュを取得manifestへ記録し、異なる計画での再開を拒否する。

日本語設定では20件のsongs検索が全て空だった。
SDKのローカル実装は棚見出しに英語のsongを要求し、『曲』『楽曲』の模擬応答で
候補が捨てられることを再現した。同じ認証で英語設定の実検索は候補を返した。
このため未配信の証拠とは扱わず、新しいdirectoryとlanguage=enで再取得した。
現行アダプターでは、検索を伴う日本語設定は通信前に中止する。
既知video IDの保存済み検索を再利用する取得とplan-onlyは日本語設定を許可する。

喫茶ステラではradioとwatch取得後の任意の関連欄でSDKのKeyErrorが発生した。
取得済みradioを保持し、related_status=parser_errorと型のみ記録するようにした。
HTTP・認証・rate limit・timeoutは引き続き停止し、例外本文を保存しない。
今後の診断用にエラー発生file basename/function/lineだけを記録する。

Nightlyの更新Cookieは独立subagentが取得し、Git管理外のmode0600ファイルだけに保存した。
親の会話に認証値・account名を出しておらず、元ブラウザープロファイルも変更していない。
各runは5秒間隔・最大100リクエストで制限し、再開時には取得済み検索と観測を再利用した。

## 20起点の結果

初回の厳密照合では7取得・13保留。
Tycho A Walkの2候補はtitle/artist/長さが一致し、LibraryのDive (Deluxe Version)へ
album名が完全一致するのは1候補だけだったため、それをmetadata上で優先した。
album一致の動画も複数ある場合は保留する。録音同一性の確認ではない。
最終結果は8取得・12保留。保留を長さ閾値の拡大や安易なartist別名で埋めていない。

取得できた起点は喫茶ステラ、のんのんびより、響け！ユーフォニアム、fripSide、Superfly、C418、Tycho、シャニマス。
529観測、非起点のunique video IDは477個。共通metadata93曲、有向関係90本。
検索結果は同定だけに使い、推薦の辺には使わない。

| 起点 | 直接候補 | 通常条件の曲数 | 重複回避のみの曲数 |
|---|---:|---:|---:|
| Welcome to Cafe Stella! | 12 | 4 | 13 |
| 陽だまり道 | 15 | 3 | 16 |
| 追想 - Tsuisou | 6 | 3 | 7 |
| Only My Railgun | 9 | 8 | 10 |
| Beautiful | 3 | 4 | 4 |
| Sweden | 13 | 1 | 14 |
| A Walk | 3 | 1 | 4 |
| 星の声 | 29 | 25 | 25 |

曲数に起点を含む。通常条件はcompatible_genreだけを除外する。
比較条件ではdistance、skip、jitter等をまとめて除くため、どの個別フィルターが原因かは未確定。
BGMや同一artistに集中する候補では、通常条件のままでは過度に短いプレイリストになることを確認した。

12保留は、指定曲が検索結果にない3件、長さ・収録版の差5件、クレジット役割・収録盤差2件、
表記確認と曖昧性2件。CLANNADの一般artist別名に使える条件一致の証拠は1曲だけで、一般化して有効化しなかった。
東方の候補は長さが20.573秒違い、同一録音として採用しなかった。
カービィは原曲のartistに対応する候補ではなく、別artistの編曲が返った。

## 成果物と次の課題

個人データはGit管理外。

- `data/final-library-diverse-seed-plan-v01.json`: 20起点の計画。
- `data/ytmusic/coverage-diverse-en-v01/`: 取得結果とサニタイズした検索・API応答。
- `data/ytmusic/diverse-evaluation-v02.json`: 通常条件の8起点評価。
- `data/ytmusic/diverse-evaluation-relations-only-v02.json`: 比較条件。
- `data/ytmusic/diverse-library-results-v01.md`: 20起点の状態と、8起点の実際の曲一覧。
- `data/ytmusic/diverse-search-diagnostics-v03.json`: album優先を適用する前の7一致・13保留の診断。

次に分けて検証する課題は、BGMに対する距離条件、表記・publisher/composer役割の扱い、
別収録を関係性取得の代理として許せるか。作品外の曲を強制挿入したり、検索候補を推薦辺に流用したりはしていない。
元Library変更・Music.app全体・iPod実機の受け入れは未確認。

全71テスト成功。計画の検証、album候補の曖昧性保持、任意relatedの解析失敗とHTTP失敗の区別、
日本語検索の通信前中止、実際のMusicコアの共有ID空間試験を含む。
