# YTMusic由来の実験用Genius DBとLibrary ID割り当て案

保存済みの本番曲一覧、別名対応表v03、YTMusicのradio/related観測から、
29曲のmetadataとsimilaritiesを復号済みGenius DBのコピーへ追加した。
ツバサグラビティを起点とする28本の有向関係だけを登録し、逆方向の関係は生成していない。
検索結果は関係データに含めない。曲の対応はメタデータ上の優先候補で、録音同一性は未確認。

`data/genius-dataset-v01/`に次の成果物を保存した。個人データなのでGitには含めない。

- `Genius.experimental.sqlite`: 平文SQLite。Music.appにそのまま配置する形式ではない。
- `library-assignments.json`: 元Library SHA-256で束縛した29曲のGenius ID割り当て案。
  `rewrite_music_ids.py`の入力形式。本番Libraryへの書き込みは行っていない。
- `report.json`: 入力ハッシュ、追加行、検証結果と選曲結果。

IDは本番曲一覧と参照DBのmetadata/similaritiesにあるIDを避ける。
artist/album/songの等価グループ値も、参照metadataの同じ位置の値を避ける。
選択曲に既存Genius IDがあれば中止し、上書きしない。
ジャンルは0の仮値で、compatible_genreフィルターを除外する。

DBの全入力を検証してからsavepoint内で追加し、既存行とschemaを保持したことを照合する。
metadataとsimilaritiesは各6→35行、configは1行のdataだけを更新。
fingerprint 823行とadditional_match_ids 40行を保持した。
この補助テーブルは別Library由来で、本番Libraryへの対応付けは済んでいない。
保持できたことと本番環境で有効であることは別の検証事項になる。

SQLite integrity_checkに加え、メモリ内の使い捨てAES鍵でページ暗号化・復号を行い、
全テーブルの論理内容が一致することを確認した。鍵とこの試験用暗号文は保存しない。
保存したDBを読み直し、提供済みARM64 Musicの実際の選曲コアで25曲を生成した。
新規行だけをメモリ内で渡した場合と、参照DBの既存行も含む保存後DBで、結果のID列が一致した。

次の段階はHDD再接続後に、本番Libraryのハッシュと割り当て可能性を再確認すること。
そのLibrary用のGenius鍵で暗号化し、整合するLibraryコピーを作る必要がある。
Music.app全体での読み込みと実機iPodでの受け入れは未確認。
