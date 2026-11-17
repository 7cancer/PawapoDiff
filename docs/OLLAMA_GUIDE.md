# OllamaでPawapoDiff / PawapoConsistencyを動かす手順（Windows）

まず文章1ページの検査を成功させ、その後でノート・画像・全体検査へ広げます。アプリのためにPythonやNode.jsをインストールする必要はありません。

## 1. 必要なもの

- Windows PC、EdgeまたはChromeなどのブラウザ。
- [OllamaのWindows版](https://ollama.com/download/windows)。インストール後、WindowsのスタートメニューからOllamaを起動します。
- 本アプリの `PawapoConsistency.exe`、または `PawapoDiff-Setup.exe`。
- 同じページ数・スライド寸法の日本語版／英語版PPTX。
- **スライド画像の検査をする場合だけ**デスクトップ版Microsoft PowerPointが必要です。文章・ノートだけの検査には不要です。

OllamaのWindows要件は[公式Windows手順](https://docs.ollama.com/windows)で確認できます。モデルのダウンロード時にはインターネット接続と保存容量が必要です。

## 2. Ollamaの起動を確認する

インストール後に新しくPowerShellを開き、次を1行ずつ実行します。

```powershell
ollama --version
ollama list
Invoke-RestMethod -Uri http://127.0.0.1:11434/api/tags
```

- 最初のコマンドでバージョンが出ればCLIを利用できます。
- `ollama list` にはダウンロード済みモデルが表示されます。まだ空でも問題ありません。
- 最後のコマンドで `models` を含む応答が返れば、アプリから接続するローカルAPIが動いています。

接続できなければ、まずスタートメニューからOllamaを起動してください。アプリを使わずに手動でサーバーを起動する場合は `ollama serve` を実行し、そのPowerShellを開いたままにします。**`address already in use` /「アドレスは使用中」なら既にサーバーが動いている可能性があります。**もう1つ起動せず、上のAPI確認を試してください。

`ollama` が見つからない場合はPowerShellを開き直します。それでも見つからなければOllamaのインストールを確認してください。

## 3. モデルをダウンロードして単体で試す

文章・ノート用の例です。

```powershell
ollama pull qwen2.5:7b
ollama list
ollama run qwen2.5:7b "日本語で、接続確認OKとだけ返してください。"
```

モデルのダウンロード完了と、最後のコマンドで何らかの回答が出ることを確認します。回答の語句が完全一致する必要はありません。最初の読み込みは時間がかかる場合があります。

PCが重い・メモリ不足になる場合は `qwen2.5:3b` に置き換えて試します。モデル名は、アプリでも同じタグまで含めて指定します。

画像も検査する場合は別途、次の画像対応モデルを準備します。

```powershell
ollama pull qwen2.5vl:7b
ollama list
```

軽量な画像モデルの候補は `qwen2.5vl:3b` です。**`qwen2.5` と `qwen2.5vl` は別モデルです。**通常の `qwen2.5:7b` では画像検査を行えません。[Qwen2.5-VLの公式ページ](https://ollama.com/library/qwen2.5vl)にOllamaの最低要件が記載されています。Ollamaは最新安定版を使用してください。

## 4. 本アプリへPPTXを読み込む

1. `PawapoConsistency.exe` を通常のダブルクリックで起動します。管理者として実行する必要はありません。
2. 自動的に開いたブラウザで「表記ゆれ検知」タブを選びます。
3. 日本語原本と英訳版のPPTXを選び、「比較を開始」を押します。
4. 「完了」が出るまで待ちます。

最初にこの方法を使うと、PowerPointによる画像生成をせず、文章・ノートの読み込みを確認できます。`PawapoDiff-Setup.exe` から始める場合は起動後「表記ゆれ検知」タブへ切り替えてからファイルを読み込んでください。

**起動時に開くコンソールは閉じないでください。**閉じるとアプリのサーバーも止まります。終了時はコンソールでCtrl+Cを押します。

## 5. 文章1ページでLLM接続を確認する

「LLMレビュー」タブで次のように設定します。

| 項目 | 最初に試す設定 |
|---|---|
| 接続方式 | Ollama |
| 接続先URL | `http://127.0.0.1:11434` |
| APIキー | 空欄（同じPCで使う通常のローカルOllama） |
| モデル名 | `qwen2.5:7b`（ダウンロードした名前） |
| 応答待ち上限 | 600秒 |
| 出力トークン上限 | 2048 |
| ページ範囲 | `1` |
| 検査内容 | 「翻訳・用語・表記の一貫性」だけON。他の3つはOFF |

接続先URLの末尾に `/api` や `/api/chat` を付けないでください。アプリ側がAPIパスを付加します。

1. 「モデル一覧・接続確認」を押し、モデルが見つかることを確認します。
2. モデル名に使うモデルを選びます。モデル名を変更したら「モデルの画像対応を確認」を押せます。文章のみのモデルなら、画像対応のチェックはOFFのままにします。
3. 上の検査内容・ページ範囲を指定し、「LLM検査を開始」を押します。
4. 画面の進捗とコンソールを確認します。`request_start` の後はモデルの応答待ちです。
5. `done: 完了` と確認候補が表示されれば成功です。「結果・使用プロンプトを保存」からJSONを保存できます。

公式表記と照合したい場合は「用語集・公式表記の参照資料」に用語集や公式ページの引用文を追加してください。根拠がない公式表記は未確認として扱います。既定プロンプトのままでも開始でき、必要に応じて編集できます。

## 6. ノート・画像・全ページへ広げる

文章1ページが成功してから、必要な検査を追加します。

- **発表者ノート**：「発表者ノート比較」をONにします。まずページ範囲は `1` のまま試します。
- **画像・レイアウト**：モデルを `qwen2.5vl:7b` 等へ変更し、「モデルの画像対応を確認」を押します。「選択したモデルは画像入力に対応している」がONになったことを確認します。「画像を生成」を押し、画像の状態が `done` になってから「ページ別JP/EN画像・レイアウト」をONにして検査します。画像生成にはPowerPointが必要です。
- **全ページ**：ページ範囲を空欄にします。`1-5,8` のような範囲指定もできます。「全体を通した確認・まとめ」もONにすると各検査の結果をまとめます。

画像検査は選択ページを順に処理します。モデル・PC・ページ数によって実行時間は変わります。「検査を中止」は現在のモデル応答後に停止します。

## 7. コンソールログの読み方（v0.7.1以降）

以下の詳細ログはv0.7.1以降で利用できます。v0.7.0を使用中の場合はEXEを更新してください。

次は表示の例です。IDや秒数は実行ごとに変わります。

```text
[LLM] review_start job="..." review="..." model="qwen2.5:7b" page_count="1"
[LLM] task_start job="..." review="..." task="translation" page="all"
[LLM] request_start request="a1b2c3d4" origin="http://127.0.0.1:11434" endpoint="/api/chat" timeout_s="600"
[LLM] request_done request="a1b2c3d4" http_status="200" elapsed_s="12.5"
[LLM] task_done task="translation" issue_count="2"
[LLM] review_finished status="done" completed_tasks="1"
```

- `job`：読み込んだ資料の処理ID。
- `review`：今回のLLM検査のID。
- `request`：1回のAPI通信のID。同じIDの開始・成功・失敗を探します。
- `task`：`layout`（画像）、`translation`（翻訳）、`notes`（ノート）、`overview`（全体）。
- `elapsed_s`：通信や検査にかかった秒数。
- `failure` / `review_failure`：原因コードと対処の案内。予期しない失敗では例外の種類とコード位置も出します。
- `output_warning`：出力上限やJSON形式の問題。モデルの応答全文はWeb UIで確認します。

ログにはAPIキー、認証トークン、スライド本文、ノート、プロンプト、画像データ、モデルの応答本文を出しません。HTTPエラーの本文は既知の原因の分類にだけ使用し、そのまま表示しません。

## 8. エラー別の対処

| 表示・コード | 対処 |
|---|---|
| `LLM_CONNECTION_REFUSED` | Ollamaをスタートメニューから起動し、上の `Invoke-RestMethod` で確認。URL・ポートを確認。 |
| `LLM_TIMEOUT` | 応答待ち上限を600秒にする。モデルを `ollama run` で先に試す。1ページ、小さいモデルで再実行。 |
| `LLM_NOT_FOUND` / HTTP 404 | `ollama list` とモデル名・タグを一致させる。未取得なら `ollama pull モデル名`。URL末尾の `/api` を除く。 |
| `LLM_AUTH_ERROR` / HTTP 401・403 | 同じPCの通常のOllamaはAPIキー空欄。別サーバーは接続先の認証設定を確認。 |
| `LLM_OUT_OF_MEMORY` | 3B等へ変更。他の大きなモデル・アプリを終了し、入力ページや画像量を減らす。 |
| `LLM_VISION_UNSUPPORTED` | `qwen2.5vl` 等の画像対応モデルを使う。文章モデルでは画像検査をOFF。 |
| `LLM_CONTEXT_LIMIT` / `LLM_INPUT_LIMIT` | ページ範囲、用語集、公式資料の引用量を減らす。 |
| `LLM_DNS_ERROR` / `LLM_TLS_ERROR` | 接続先のホスト名・証明書を確認。同じPCならまず `http://127.0.0.1:11434` で試す。 |
| `LLM_INVALID_URL` / `LLM_INVALID_RESPONSE` / `LLM_REQUEST_INVALID` | 接続方式とURLを確認。Ollamaには `http://127.0.0.1:11434`。Webサイトや管理画面のURLを指定しない。URL・APIキーに余分な改行がないかも確認。 |
| `LLM_HTTP_ERROR` | 表示されたHTTP番号を控え、Ollamaの `server.log` を確認。 |
| `LLM_OUTPUT_TRUNCATED` | 出力トークン上限を増やすか、対象ページを減らす。JSONが途中で切れていないかWeb UIの応答全文を確認。 |
| `LLM_OUTPUT_FORMAT` / `LLM_EMPTY_RESPONSE` | 応答全文を確認。既定プロンプトに戻し、出力上限を増やすか、別のモデルで1ページを試す。 |
| 「LLM検査を開始」が押せない | PPTX読み込み完了、モデル名、検査内容の選択を確認。画像検査がONなら画像対応チェックと画像状態 `done` も必要。 |
| 画像生成で `[RENDER] failure` | PowerPointがインストール・ライセンス認証され、資料が開けることを確認。アプリとPowerPointを通常権限で起動。LLM接続とは別の問題。 |

## 9. ログを保存して調べる

アプリの起動コンソールにログの保存先を表示します。通常は次のフォルダーです。

```text
%LOCALAPPDATA%\PawapoDiff\logs
```

書き込めない場合はWindowsの一時フォルダーを使います。LLMタブの「診断ログを保存」からZIPを取得できます。コンソールのメッセージは同じローテーションログにも残します。

Ollama側のログは、PowerShellで次を実行して開けます。

```powershell
notepad "$env:LOCALAPPDATA\Ollama\server.log"
```

問い合わせ時は、エラーコード・request/review ID・前後の数行・Ollamaのバージョン・モデル名・対象ページ範囲を伝えてください。アプリのログとOllama側のログは別です。Ollama側のログ内容も確認してから共有してください。

## 公式情報

- [Windows版Ollamaの導入・ログ保存先](https://docs.ollama.com/windows)
- [OllamaのローカルAPIと認証](https://docs.ollama.com/api/introduction)
- [Qwen2.5](https://ollama.com/library/qwen2.5)
- [画像対応Qwen2.5-VL](https://ollama.com/library/qwen2.5vl)
