# 曲ごとの作品コンテキスト

推薦を調整する前に、Library内の各曲がどの作品・ブランドに属し、どの役割を持つかを記録する。ユーザーの希望は「J-POPからアニソンへの推薦は許容、逆方向は抑える」。この方向性は個人の好みであり、曲の出典とは別に保存する。今回の変更では推薦順位は変更しない。

## 保存と表示

`music_track_context.py` はLibraryのSHA-256とpersistent IDに結び付くclaimsを検証する。作品名、ブランド、媒体、OP/ED/挿入歌/キャラクター曲/BGMなどの役割、曲種、根拠、確認状態を保持する。同一曲に複数の出典を記録できる。

全曲のレポートには元の曲名・歌手・アルバム・長さとバージョン修飾も残す。公式情報とローカル表記の対応が不明確なものは暫定、未調査の曲は空claimsとする。歌手の名前だけでジャンルや作品を決めない。作品帰属の確認は、録音やマスターの同一性の確認を意味しない。

Webビューは `--track-context` でclaimsを読み込む。曲名をクリックすると作品・役割・公式出典を表示する。推薦曲の詳細にも同じ情報を表示する。作品名とブランド名で検索できる。表示のために分類設定を自動変更しない。

```sh
.venv/bin/python scripts/music_track_context.py \
  --track-snapshot data/final-library-tracks.json \
  --claims data/track-context-claims-20261007.json \
  --output data/track-context-report-20261007.json

.venv/bin/python scripts/serve_music_review.py \
  --track-snapshot data/final-library-tracks.json \
  --track-context data/track-context-claims-20261007.json \
  --port 0
```

調査データは個人のLibraryに結び付くため `data/` に保存し、Gitの追跡対象にはしない。元のLibraryや認証データを調査サイトに送信しない。公式ページを検索し、曲名・アルバム表記との対応はローカルで行う。

## 判定上の注意

- ベスト盤のアルバム名だけでは、それぞれの曲の作品出典が分からない。
- MinecraftのAlphaとBetaは同じ作品系統に属するが、全曲のゲーム内使用箇所を断言しない。
- アイマスのユニット歌唱曲、ゲームBGM、アニメOPを分ける。
- 原曲がOPでも、Off Vocal音源自体のOP使用は推定しない。
- 英語表記やローマ字表記を公式日本語曲名に結び付ける際、未確認の対応は暫定とする。

## 一次情報の例

[鈴木このみ「銀閃の風」](https://www.konomi-suzuki.net/posts/discography/ezeyje) は「魔弾の王と戦姫」のOPとの対応を確認する資料。
[「カオスシンドローム」](https://www.konomi-suzuki.net/posts/discography/bcujre) は「CHAOS;CHILD」のEDとの対応を確認する資料。

[C418 Volume Alpha](https://c418.bandcamp.com/album/minecraft-volume-alpha)、[Volume Beta](https://c418.bandcamp.com/album/minecraft-volume-beta) はMinecraftサウンドトラックの公式曲目。

[Lantis CANVAS 01](https://shinycolors.lantis.jp/releaseinfo/lacm-24361/) はシャイニーカラーズのユニットと収録曲を確認する資料。[ゲームBGMの公式配信](https://gamemusic.bn-ent.net/release/473/) と [ツバサグラビティ](https://shinycolors.lantis.jp/releaseinfo/lacm-24508/) はゲームBGMとアニメOPを区別する資料。

## 今回の実データと検証

2,974曲の全曲レポートを作成し、828曲に837件のcontext claimsを付けた。668曲には確認済みclaimがあり、160曲は暫定情報のみ。claimsの内訳は確認済み671件、暫定166件。作品への帰属が分かっても具体的な曲役割まで分かるとは限らず、766件のroleはunknownのまま。残る2,146曲にはclaimsを付けていない。

Minecraft、シャイニーカラーズ、9-nine、喫茶ステラ、レールガン、鈴木このみの一部タイアップ、Key作品、東方5作品、カービィスターアライズ、Superflyを調査した。`Charottle Original Soundtrack` は公式Charlotte OST曲目と照合して作品を記録し、Libraryの誤記は原文のまま保持した。

private artifacts:

- `data/track-context-inventory-20261007.json`: 677組の歌手・アルバム群、表記の曖昧さ。
- `data/track-context-claims-20261007.json`: Webビューで読み込める統合claims。
- `data/track-context-report-20261007.json`: 全2,974曲のメタデータ、版、claims。
- `data/track-context-provenance-20261007.json`: 各調査入力のhash、重複統合の記録。
- `data/personal-context-direction-20261007.json`: ユーザーの方向性。ランキングには未適用。

全333テスト通過。ブラウザで作品名「魔弾」による検索、曲詳細のOP表示・公式URLを確認し、JavaScriptエラーは0件。
