# 2026-10-02 Music.appの静的解析とGenius DB復号

## 結果

提供済みのMusic.appとライブラリだけでGenius.itdbを復号できた。
Linux上で静的解析し、鍵導出関数に限ってARM64をエミュレーションした。
Music.appをmacOS上で起動したり、Appleへの通信を行ったりしていない。
SQLite `PRAGMA integrity_check` は `ok`。
復号したコピーはGit対象外の `data/genius-decrypted.itdb` に保存。
鍵、元バイナリ、復号DB、ユーザーの詳細な調査ログはコミットしない。

## 対象ビルド

- 実行ファイル: `/home/mizoyuzu/Music.app/Contents/MacOS/Music`
- ARM64e、thin little-endian Mach-O、36,379,728 bytes
- SHA-256: `a8ddce118ebe19132a26d8b4d7efab7020fa38386aadedeb5f9ce629e6981f6a`
- LC_UUID: `529a96d2-b9de-3af9-b1cc-a53b5c4265d4`
- 提供環境のMusicバージョン: 1.7.0.146
- アドレスはunslid virtual address。以下の関数位置はこのビルド限定。

## 鍵の保存場所と使用経路

`0x1007da210(1,0)` はライブラリ設定のsingletonを返す。
そのオブジェクトの `+0x50` がCFDataを保持する。
初期化は `0x1007daee8` の `0x1007daf48` と `0x1007dafcc`。

保存側 `0x1000c9a50` の `0x1000ca100..0x1000ca130` は同フィールドを
`CFDataGetLength` / `CFDataGetBytePtr` のwrapperで読み、kind `0x1f6` (502)
としてBOMA writer `0x1000c9790` に渡す。
提供Library Preferences.musicdbのBOMA 502はheader 20、total 40、payload 20 bytes。
このpayloadを使った復号成功により、提供DBとの対応も確認できた。
以前の「ライブラリ内に鍵関連データが見つからない」は更新する必要がある。

使用側 `0x100110218` はGenius.itdbを登録し、設定 `+0x50` のデータから鍵を作る。
同関数のログ参照 `0x100111f7c` は
`libraryPrefs->geniusKeyHeader.IsValid()` を指す。
保存側・使用側・ログを合わせると、BOMA 502はgeniusKeyHeaderに対応する。
kind 502のロード分岐から `+0x50` への格納命令自体はまだ追跡していない。

`0x100111918..0x10011192c` の鍵導出呼び出し:

```text
x0 = CFDataのバイト列
x1 = CFData長 (提供サンプルは20)
x2 = 出力バッファ
x3 = 出力容量を保持するuint32へのポインタ (入力16)
call 0x1011b39ac
```

成功後、出力バッファと長さをDB descriptor `+0x18/+0x20` に設定する。
一般DB open関数 `0x100c16664` 内の `0x100c16fa0` / `0x100c171d8` は
このdescriptorから鍵ポインタと長さを読み、`sqlite3_key`へ渡す。

## ヘッダーと鍵導出の検証

20-byte headerの先頭2バイトはbig-endian version=1。
次の2バイトはbig-endian selectorで、64未満を受け付ける。
残り16バイトをそのままSQLite鍵と見なしていない。
生成側 `0x100110928..0x100110978` はversion=1、0..63のselector、
16-byteのデータを作ってCFDataに保存する。

鍵導出関数は定数表と間接分岐による難読化を含む。
LC_DYLD_CHAINED_FIXUPS (format 12 / ARM64E_USERLAND24) を解決し、
pointer authentication命令を省略した研究用エミュレーションで計5,608命令を処理。
戻り値0、出力長16。外部ライブラリの実行は許可しない。
これはmacOS全体のエミュレーションでもMusic.app全体の実行でもない。
結果の正しさはDB全体の復号とSQLite整合性チェックで検証した。
異なるビルドや異なる鍵ヘッダーバージョンへの一般化は未検証。

## ページ暗号化

提供DBは87ページ、page size=4096、reserved bytes=12。
AES-128-OFBで、各ページのIVは以下。

```text
IV[0:4]  = uint32 little-endianのページ番号 (1始まり)
IV[4:16] = 暗号化ページ末尾の12-byte nonce
```

ページ先頭4084バイトをOFBで処理し、末尾12バイトは保持する。
第1ページのoffset 16..23は平文のままなので、復号後に元の8バイトを復元する。
OFBストリームの位置はこの8バイト分も進む。
最初の16バイトは復号でSQLite magicに戻る。magicを単に上書きしていない。

方式の確認では、既知のSQLite magicと第1ブロックから逆算したOFB IVが
`LE32(1) + 第1ページ末尾12バイト` に一致し、さらに全ページの整合性検証に成功した。
SEE公式仕様の平文header windowとページnonceにも整合する。

## DB内容の集計

| Table | Rows | その他 |
|---|---:|---|
| genius_metadata | 6 | version 1、data各32 bytes |
| genius_similarities | 6 | version 1、data 52..60 bytes |
| genius_config | 1 | version 2、data 151212 bytes |
| genius_fingerprint | 823 | 内容の意味は未解析 |
| genius_additional_match_ids | 40 | IDの対応先は未解析 |

genius_configのdefault_num_results/min_num_resultsはいずれも0。
この値だけで推薦件数やGeniusの可否は判断しない。
類似関係6件は、ほとんどの曲でGeniusが動いていなかったという観測と整合し得るが、
曲対応やBLOBの構造は未解析。iPodへの互換書き込み成功を意味しない。

## 検証と制約

- 保存スクリプトから独立して同じ結果を再現、integrity_check=ok。
- 1bit違う鍵はSQLite magicの検証で拒否。
- 不完全なページ、不正な鍵ヘッダーversionは拒否。
- 復号後の第2ページを破壊するとSQLite整合性検証で拒否。
- Genius.itdbの元SHA-256は以前の値と一致:
  `d44c9982b43486c4591362677b92c71805941c976254444448b8b3d62ddc834b`。
- Library.musicdbの元SHA-256も以前の値と一致:
  `8845d3352a4b311b507fb8ea7caff7c4dfdec647caee2be9bad5b544df8660a6`。
- 実行ファイルのハッシュ一致を必須にし、元ファイルを書き換えず、出力上書きを拒否する。
- AES-OFB自体には改ざん検出機能がない。SQLite整合性チェックは構造を検証するもので、
  全データの真正性を保証するものではない。

次はBLOB構造とローカル曲IDの対応を解析する。
Macでのデバッガ接続やGitHub Actionsへのライブラリ転送は、この復号には不要だった。

## 一次資料

- [Apple Mach-O loader definitions](https://github.com/apple-oss-distributions/cctools/blob/main/include/mach-o/loader.h)
- [Apple dyld chained fixups](https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/fixup-chains.h)
- [SQLite SEE documentation](https://sqlite.org/see/doc/trunk/www/readme.wiki)
- [Unicorn CPU emulator](https://www.unicorn-engine.org/)
