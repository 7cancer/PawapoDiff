import React, { useEffect, useState } from "react";
const DEFAULT_OLLAMA_MODEL = "qwen3-vl:8b";
export type Api = (path: string, options?: RequestInit) => Promise<Response>;
type Download = (response: Response, name: string) => Promise<void>;
const json = (data: unknown, method = "POST"): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

export function MappingEditor({
  api,
  job,
  page,
  onApply,
}: {
  api: Api;
  job: string;
  page: number;
  onApply: (data: any) => void;
}) {
  const [data, setData] = useState<any>(null),
    [pairs, setPairs] = useState<Record<string, string | null>>({}),
    [error, setError] = useState(""),
    [saving, setSaving] = useState(false);
  useEffect(() => {
    let live = true;
    setData(null);
    setError("");
    api(`jobs/${job}/mapping/${page}`)
      .then((r) => r.json())
      .then((d) => {
        if (live) {
          setData(d);
          setPairs(d.overrides);
        }
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [job, page]);
  async function apply(values: Record<string, string | null>) {
    setSaving(true);
    setError("");
    try {
      const result = await (
        await api(`jobs/${job}/mapping/${page}`, json({ pairs: values }, "PUT"))
      ).json();
      onApply(result);
      const updated = await (await api(`jobs/${job}/mapping/${page}`)).json();
      setData(updated);
      setPairs(updated.overrides);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }
  return (
    <details className="panel mapping">
      <summary>このページの対応付けを手動設定</summary>
      <p>
        日本語の図形ごとに英語側の対応先を選びます。「対応なし」は追加・削除として扱います。同じ英語図形を複数の日本語図形には指定できません。
      </p>
      {error && <p role="alert">{error}</p>}
      {data && (
        <>
          <div className="scroll-table">
            <table>
              <thead>
                <tr>
                  <th>日本語側の図形・文章</th>
                  <th>英語側の対応先</th>
                </tr>
              </thead>
              <tbody>
                {data.left.map((shape: any) => {
                  const automatic = data.pairs.find(
                    (p: any) => p.left === shape.id,
                  )?.right;
                  return (
                    <tr key={shape.id}>
                      <td>
                        <strong>
                          {shape.name} [{shape.id}]
                        </strong>
                        <p>{shape.text || "（文章なし）"}</p>
                      </td>
                      <td>
                        <select
                          aria-label={`対応先 ${shape.id}`}
                          value={
                            Object.hasOwn(pairs, shape.id)
                              ? (pairs[shape.id] ?? "__none")
                              : "__auto"
                          }
                          onChange={(e) => {
                            const next = { ...pairs };
                            if (e.target.value === "__auto")
                              delete next[shape.id];
                            else
                              next[shape.id] =
                                e.target.value === "__none"
                                  ? null
                                  : e.target.value;
                            setPairs(next);
                          }}
                        >
                          <option value="__auto">
                            自動判定（現在: {automatic || "対応なし"}）
                          </option>
                          <option value="__none">
                            対応なし（削除・未対応）
                          </option>
                          {data.right.map((target: any) => (
                            <option key={target.id} value={target.id}>
                              {target.name} [{target.id}]{" "}
                              {target.text.slice(0, 80)}
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p>
            対応がない英語側の図形は追加として表示します。元のPPTXは変更しません。
          </p>
          <button disabled={saving} onClick={() => apply(pairs)}>
            対応付けを適用
          </button>{" "}
          <button disabled={saving} onClick={() => apply({})}>
            このページを自動判定に戻す
          </button>
        </>
      )}
    </details>
  );
}

export type PageContext = {
  page: number;
  count: number;
  onPageChange: (page: number) => void;
  images: Record<string, string>;
  notes: { page: number; source: string; target: string; status?: string }[];
  renderStatus?: string;
  onRender: () => Promise<void>;
};
function PageWorkspace({
  context: c,
  counts = {},
  children,
  noteContent,
  mapping,
}: {
  context: PageContext;
  counts?: Record<number, number>;
  children: React.ReactNode;
  noteContent?: React.ReactNode;
  mapping?: React.ReactNode;
}) {
  const note = c.notes.find((n) => n.page === c.page);
  return (
    <div className="workspace page-workspace">
      <nav aria-label="スライド別確認">
        <h3>SLIDES</h3>
        {Array.from({ length: c.count }, (_, i) => i + 1).map((p) => (
          <button
            key={p}
            aria-current={p === c.page ? "page" : undefined}
            className={p === c.page ? "active" : ""}
            onClick={() => c.onPageChange(p)}
          >
            {p}ページ {counts[p] ? `（${counts[p]}件）` : ""}
          </button>
        ))}
      </nav>
      <div className="page-viewer">
        <div className="toolbar">
          <button
            disabled={c.page <= 1}
            onClick={() => c.onPageChange(c.page - 1)}
          >
            前のページ
          </button>
          <h3>
            {c.page} / {c.count}ページ
          </h3>
          <button
            disabled={c.page >= c.count}
            onClick={() => c.onPageChange(c.page + 1)}
          >
            次のページ
          </button>
        </div>
        <div className="page-media">
          {[
            ["left", "JP"],
            ["right", "EN"],
          ].map(([side, label]) => (
            <figure key={side}>
              <figcaption>
                {label} · {c.page}ページ
              </figcaption>
              {c.images[side] ? (
                <img
                  src={c.images[side]}
                  alt={`${label}スライド ${c.page}ページ`}
                />
              ) : (
                <p>
                  {c.renderStatus === "done"
                    ? "画像を読み込み中…"
                    : "スライド画像は未生成です。"}
                </p>
              )}
            </figure>
          ))}
        </div>
        {c.renderStatus !== "done" && (
          <div className="toolbar">
            <button
              disabled={c.renderStatus === "running"}
              onClick={c.onRender}
            >
              {c.renderStatus === "running"
                ? "画像を生成中…"
                : "画像を生成してページ確認"}
            </button>
            <span>画像生成にはWindows版PowerPointが必要です。</span>
          </div>
        )}
        <section aria-label="このページのスライド指摘">
          <h3>スライド · {c.page}ページ</h3>
          {children}
        </section>
        <section aria-label="このページの発表者ノート">
          <h3>発表者ノート · {c.page}ページ</h3>
          <div className="notes-pair">
            {[
              ["JP", note?.source],
              ["EN", note?.target],
            ].map(([label, text]) => (
              <div key={label}>
                <h4>{label}</h4>
                <pre>{text || "（発表者ノートなし）"}</pre>
              </div>
            ))}
          </div>
          {noteContent}
        </section>
        {mapping}
      </div>
    </div>
  );
}
export function ConsistencyPanel({
  api,
  download,
  job,
  result,
  context,
  onApply,
}: {
  api: Api;
  download: Download;
  job: string;
  result: any;
  context: PageContext;
  onApply: (data: any) => void;
}) {
  const [only, setOnly] = useState(true);
  const rows = (result?.rows || []).filter(
    (r: any) => !only || r.inconsistent || r.missing || r.uncertain,
  );
  const counts: Record<number, number> = {};
  rows.forEach((r: any) =>
    r.entries.forEach((e: any) => (counts[e.page] = (counts[e.page] || 0) + 1)),
  );
  function candidates(scope: string) {
    const current = rows.filter(
      (r: any) =>
        r.scope === scope &&
        r.entries.some((e: any) => e.page === context.page),
    );
    return current.length ? (
      current.map((r: any, i: number) => (
        <article className="review-result" key={i}>
          <strong>
            {[
              r.inconsistent && "表記差分",
              r.missing && "英語なし",
              r.uncertain && "対応要確認",
            ]
              .filter(Boolean)
              .join(" / ") || "一致"}
          </strong>
          <table>
            <thead>
              <tr>
                <th>JP表記</th>
                <th>このページのEN表記</th>
                <th>場所・補足</th>
              </tr>
            </thead>
            <tbody>
              {r.entries
                .filter((e: any) => e.page === context.page)
                .map((e: any, j: number) => (
                  <tr key={j}>
                    <td>{r.source}</td>
                    <td>{e.target || "（英語なし）"}</td>
                    <td>
                      {e.shape}
                      <p>{e.note}</p>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          <details>
            <summary>他ページの英語表記・移動</summary>
            {r.entries
              .filter((e: any) => e.page !== context.page)
              .map((e: any, j: number) => (
                <p key={j}>
                  <button onClick={() => context.onPageChange(e.page)}>
                    {e.page}ページへ
                  </button>{" "}
                  {e.target || "（英語なし）"}
                </p>
              ))}
          </details>
        </article>
      ))
    ) : (
      <p>このページに表示対象の候補はありません。</p>
    );
  }
  return (
    <section className="panel">
      <div className="toolbar">
        <h2>表記ゆれ検知</h2>
        <button
          onClick={() =>
            api(`jobs/${job}/consistency.csv`).then((r) =>
              download(r, "consistency.csv"),
            )
          }
        >
          全ページCSVをダウンロード
        </button>
        <button
          onClick={() =>
            api(`jobs/${job}/notes.csv`).then((r) =>
              download(r, "speaker-notes.csv"),
            )
          }
        >
          発表者ノート比較をダウンロード
        </button>
      </div>
      <label>
        <input
          type="checkbox"
          checked={only}
          onChange={(e) => setOnly(e.target.checked)}
        />
        差分・英語なし・対応要確認のみ
      </label>
      <p>
        ページを選んで画像・表記・ノートを確認します。他ページの訳語は各候補から参照できます。一括確認は全ページCSVをご利用ください。
      </p>
      <PageWorkspace
        context={context}
        counts={counts}
        noteContent={candidates("notes")}
        mapping={
          <MappingEditor
            api={api}
            job={job}
            page={context.page}
            onApply={onApply}
          />
        }
      >
        {candidates("slide")}
      </PageWorkspace>
    </section>
  );
}

type Review = {
  id: string;
  status: string;
  message: string;
  results: any[];
  model: string;
};
export function LLMPanel({
  api,
  download,
  job,
  renderStatus,
  onRender,
  context,
}: {
  api: Api;
  download: Download;
  job: string;
  renderStatus?: string;
  onRender: () => Promise<void>;
  context: PageContext;
}) {
  const [scope, setScope] = useState("page");
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [provider, setProvider] = useState("ollama"),
    [base, setBase] = useState("http://127.0.0.1:11434"),
    [key, setKey] = useState(""),
    [model, setModel] = useState(DEFAULT_OLLAMA_MODEL),
    [models, setModels] = useState<any[]>([]),
    [vision, setVision] = useState(false),
    [info, setInfo] = useState("");
  const [tasks, setTasks] = useState(["translation", "notes", "overview"]),
    [prompts, setPrompts] = useState<Record<string, string>>({}),
    [defaults, setDefaults] = useState<Record<string, string>>({}),
    [glossary, setGlossary] = useState(""),
    [referenceURL, setReferenceURL] = useState(""),
    [referenceText, setReferenceText] = useState(""),
    [references, setReferences] = useState<{ url: string; text: string }[]>([]),
    [pages, setPages] = useState(""),
    [timeout, setTimeoutValue] = useState(3600),
    [maxTokens, setMaxTokens] = useState(4096);
  const [error, setError] = useState(""),
    [working, setWorking] = useState(false),
    [review, setReview] = useState<Review | null>(null),
    [reviewId, setReviewId] = useState("");
  const connection = () => ({
    provider,
    base_url: base,
    api_key: key,
    timeout,
  });
  useEffect(() => {
    api("llm/defaults")
      .then((r) => r.json())
      .then((d) => {
        setDefaults(d.prompts);
        setPrompts(d.prompts);
      })
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    setReview(null);
    setReviewId("");
  }, [job]);
  useEffect(() => {
    if (!job || !reviewId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const d = await (await api(`jobs/${job}/reviews/${reviewId}`)).json();
        if (!live) return;
        setReview(d);
        if (["queued", "running"].includes(d.status))
          timer = setTimeout(poll, 800);
      } catch (e) {
        if (live) setError(String(e));
      }
    }
    poll();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [job, reviewId]);
  async function action(fn: () => Promise<void>) {
    setError("");
    setWorking(true);
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setWorking(false);
    }
  }
  async function loadModels() {
    await action(async () => {
      const d = await (await api("llm/models", json(connection()))).json();
      setModels(d.models);
      setVision(false);
      if (d.models.length) {
        const selected =
          d.models.find((m: any) => m.id === model) ||
          (provider === "ollama" &&
            d.models.find((m: any) => m.id === DEFAULT_OLLAMA_MODEL)) ||
          d.models[0];
        setModel(selected.id);
      }
      setInfo(`${d.models.length}個のモデルが見つかりました`);
    });
  }
  async function checkModel() {
    await action(async () => {
      const d = await (
        await api("llm/model-info", json({ ...connection(), model }))
      ).json();
      if (d.vision !== null) setVision(d.vision);
      setInfo(
        d.vision === true
          ? "画像入力対応"
          : d.vision === false
            ? "文章のみ（画像比較は使えません）"
            : "画像対応はモデルの仕様を確認し、下のチェックを指定してください。",
      );
    });
  }
  async function start() {
    await action(async () => {
      const selected = pages.trim()
        ? pages.split(",").flatMap((part) => {
            const m = part.trim().match(/^(\d+)(?:-(\d+))?$/);
            if (!m) throw Error("ページ範囲は 1-5,8 の形式です。");
            const a = +m[1],
              b = +(m[2] || m[1]);
            if (a < 1 || b < a || b - a > 999)
              throw Error("ページ範囲が不正です。");
            return Array.from({ length: b - a + 1 }, (_, i) => a + i);
          })
        : [];
      const d = await (
        await api(
          `jobs/${job}/reviews`,
          json({
            ...connection(),
            model,
            vision,
            tasks,
            prompts,
            glossary,
            references,
            pages: selected,
            max_tokens: maxTokens,
          }),
        )
      ).json();
      context.onPageChange(selected.length ? Math.min(...selected) : 1);
      setScope("page");
      setSettingsOpen(false);
      setReviewId(d.id);
      setReview({
        id: d.id,
        status: "queued",
        message: "待機中",
        results: [],
        model,
      });
    });
  }
  const running = review && ["queued", "running"].includes(review.status);
  const labels: Record<string, string> = {
    layout: "ページ別JP/EN画像・レイアウト",
    translation: "翻訳・用語・表記の一貫性",
    notes: "発表者ノート比較",
    overview: "全体を通した確認・まとめ",
  };
  function renderResults(results: any[]) {
    return (
      <>
        {results.map((r, i) => (
          <article className="review-result" key={i}>
            <h3>
              {labels[r.task]} {r.page ? `${r.page}ページ` : ""}
            </h3>
            {r.warning && <p className="error">{r.warning}</p>}
            <p className="preserve">{r.summary}</p>
            <div className="scroll-table">
              <table>
                <thead>
                  <tr>
                    <th>ページ・分類</th>
                    <th>確認候補・根拠</th>
                    <th>修正案・参照元</th>
                  </tr>
                </thead>
                <tbody>
                  {r.issues.map((issue: any, j: number) => (
                    <tr key={j}>
                      <td>
                        {String(issue.page ?? "全体")} /{" "}
                        {String(issue.category ?? "")} /{" "}
                        {String(issue.severity ?? "")}
                      </td>
                      <td>
                        {String(issue.description ?? "")}
                        <p>{String(issue.evidence ?? "")}</p>
                      </td>
                      <td>
                        {String(issue.suggestion ?? "")}
                        <p>{String(issue.reference ?? "未確認")}</p>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details>
              <summary>モデルの応答全文</summary>
              <pre>{r.raw}</pre>
            </details>
          </article>
        ))}
      </>
    );
  }
  const pageResults =
    review?.results.filter((r) => r.page === context.page) || [];
  const globalResults = review?.results.filter((r) => r.page == null) || [];
  const counts: Record<number, number> = {};
  review?.results.forEach((r) => {
    if (r.page) counts[r.page] = (counts[r.page] || 0) + 1;
  });
  return (
    <section className="panel llm-panel">
      <h2>ローカルLLM・画像／翻訳レビュー</h2>
      <div className="toolbar">
        <button
          onClick={() =>
            action(async () =>
              download(await api("llm/guide"), "Ollama-guide.md"),
            )
          }
        >
          Ollamaの動作手順を保存
        </button>
        <button
          onClick={() =>
            action(async () =>
              download(await api("diagnostics"), "PawapoDiff-diagnostics.zip"),
            )
          }
        >
          診断ログを保存
        </button>
      </div>
      <p>
        初回の導入手順とエラーコードごとの対処は手順書を参照してください。実行中の詳細は起動コンソールと診断ログにも表示・保存します。
      </p>
      <p>
        OllamaまたはOpenAI互換サーバー（LM
        Studioなど）で起動したモデルを使用します。
        <a href="https://ollama.com/library" target="_blank" rel="noreferrer">
          Llama・Qwenなどのモデル一覧
        </a>{" "}
        /{" "}
        <a href="https://lmstudio.ai/models" target="_blank" rel="noreferrer">
          LM Studioのモデル
        </a>
      </p>
      <details
        open={settingsOpen}
        onToggle={(e) => setSettingsOpen(e.currentTarget.open)}
      >
        <summary>接続・モデル・検査設定</summary>
        <section
          aria-label="推奨モデル・バージョン"
          className="model-recommendations"
        >
          <h3>まず試すモデル・バージョンの目安</h3>
          <p>
            公式仕様を元にした導入候補です。本アプリでのモデル間の判定精度比較は未実施です。以下はOllamaのモデル名・タグで、LM
            Studioでは同系列の対応モデルをロードし、モデル一覧に表示されるIDを選びます。
          </p>
          <div className="scroll-table">
            <table>
              <thead>
                <tr>
                  <th>用途</th>
                  <th>モデル名・タグ</th>
                  <th>画像入力</th>
                  <th>モデル容量の目安</th>
                  <th>導入コマンド・公式情報</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>既定：画像・翻訳・発表者ノート</td>
                  <td>
                    <code>qwen3-vl:8b</code>
                  </td>
                  <td>対応</td>
                  <td>量子化・配布形式による（公式ページ参照）</td>
                  <td>
                    <code>ollama pull qwen3-vl:8b</code>
                    <br />
                    <a
                      href="https://ollama.com/library/qwen3-vl:8b"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Qwen3-VL 8B公式
                    </a>
                  </td>
                </tr>
                <tr>
                  <td>翻訳・用語・発表者ノート</td>
                  <td>
                    <code>qwen2.5:7b</code>
                  </td>
                  <td>文章のみ</td>
                  <td>約4.7GB</td>
                  <td>
                    <code>ollama pull qwen2.5:7b</code>
                    <br />
                    <a
                      href="https://ollama.com/library/qwen2.5:7b"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Qwen2.5 7B公式
                    </a>
                  </td>
                </tr>
                <tr>
                  <td>軽量な文章・ノート検査</td>
                  <td>
                    <code>qwen2.5:3b</code>
                  </td>
                  <td>文章のみ</td>
                  <td>約1.9GB</td>
                  <td>
                    <code>ollama pull qwen2.5:3b</code>
                    <br />
                    <a
                      href="https://ollama.com/library/qwen2.5:3b"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Qwen2.5 3B公式
                    </a>
                  </td>
                </tr>
                <tr>
                  <td>軽量な画像検査の候補</td>
                  <td>
                    <code>qwen2.5vl:3b</code>
                  </td>
                  <td>対応</td>
                  <td>約3.2GB</td>
                  <td>
                    <code>ollama pull qwen2.5vl:3b</code>
                    <br />
                    <a
                      href="https://ollama.com/library/qwen2.5vl:3b"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Qwen2.5-VL 3B公式
                    </a>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p>
            Ollamaは最新安定版を推奨します。
            <a
              href="https://ollama.com/library/qwen2.5vl"
              target="_blank"
              rel="noreferrer"
            >
              Qwen2.5-VLの公式要件はOllama 0.7.0以上
            </a>
            です。LM Studioも最新安定版を利用し、
            <a
              href="https://lmstudio.ai/docs/developer/openai-compat"
              target="_blank"
              rel="noreferrer"
            >
              OpenAI互換のローカルサーバー
            </a>
            を起動してください。
          </p>
          <p>
            容量はモデルファイルの目安で、実行時のRAM・VRAMとは別です。必要メモリは画像数・文脈長・量子化・実行環境によって増減します。上記の導入コマンドはOllama側で実行し、このアプリではモデルの自動ダウンロードは行いません。配布情報の確認日:
            2026-11-17。
          </p>
        </section>
        <p>
          資料の文章と、画像検査を選んだ場合はスライド画像を、指定した接続先へ送ります。ローカルで完結させる場合はローカルサーバーを指定してください。APIキーはこの画面のメモリ内だけで保持します。
        </p>
        <div className="settings-grid">
          <label>
            接続方式
            <select
              value={provider}
              onChange={(e) => {
                setProvider(e.target.value);
                setBase(
                  e.target.value === "ollama"
                    ? "http://127.0.0.1:11434"
                    : "http://127.0.0.1:1234/v1",
                );
                setModels([]);
                setModel(
                  e.target.value === "ollama" ? DEFAULT_OLLAMA_MODEL : "",
                );
                setVision(false);
                setInfo("");
              }}
            >
              <option value="ollama">Ollama</option>
              <option value="openai">OpenAI互換（LM Studio等）</option>
            </select>
          </label>
          <label>
            接続先URL
            <input
              value={base}
              onChange={(e) => {
                setBase(e.target.value);
                setVision(false);
              }}
            />
          </label>
          <label>
            APIキー（必要な接続先のみ）
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label>
            モデル名
            <input
              list="llm-models"
              value={model}
              onChange={(e) => {
                setModel(e.target.value);
                setVision(false);
              }}
            />
            <datalist id="llm-models">
              {models.map((m) => (
                <option key={m.id} value={m.id} />
              ))}
            </datalist>
          </label>
          <label>
            応答待ち上限（秒）
            <input
              type="number"
              min="5"
              max="86400"
              value={timeout}
              onChange={(e) => setTimeoutValue(+e.target.value)}
            />
          </label>
          <label>
            出力トークン上限
            <input
              type="number"
              min="256"
              max="8192000"
              value={maxTokens}
              onChange={(e) => setMaxTokens(+e.target.value)}
            />
          </label>
        </div>
        <p>
          出力トークンの設定可能上限は8,192,000です。実際の生成量はモデル・サーバーの出力上限と文脈長に依存します。応答待ちは1リクエストにつき最大86,400秒（24時間）、初期値は3,600秒（1時間）です。
        </p>
        <p>
          ページごとに画像はJP/ENの2枚をペアで送信します。画像・翻訳・ノートは1ページの応答を受け取って表示してから次へ進みます。全体の確認は最後にページ別結果を使い、画像は再送しません。
        </p>
        <div className="toolbar">
          <button disabled={working || !!running} onClick={loadModels}>
            モデル一覧・接続確認
          </button>
          <button
            disabled={!model || working || !!running}
            onClick={checkModel}
          >
            モデルの画像対応を確認
          </button>
          <span>{info}</span>
        </div>
        <label>
          <input
            type="checkbox"
            checked={vision}
            onChange={(e) => setVision(e.target.checked)}
          />
          選択したモデルは画像入力に対応している
        </label>
        <fieldset>
          <legend>検査内容</legend>
          {Object.entries(labels).map(([task, label]) => (
            <label key={task}>
              <input
                type="checkbox"
                checked={tasks.includes(task)}
                onChange={(e) =>
                  setTasks(
                    e.target.checked
                      ? [...tasks, task]
                      : tasks.filter((t) => t !== task),
                  )
                }
              />
              {label}
            </label>
          ))}
        </fieldset>
        <label>
          ページ範囲（空欄なら全ページ）
          <input
            placeholder="例: 1-5,8"
            value={pages}
            onChange={(e) => setPages(e.target.value)}
          />
        </label>
        <p>
          画像比較には画像対応モデルとスライド画像が必要です。画像が未生成なら「画像を生成」を押してください（Windows版PowerPointが必要です）。文章・ノート検査のみの場合は不要です。
        </p>
        <button
          disabled={!job || renderStatus === "running" || working || !!running}
          onClick={() => action(onRender)}
        >
          画像を生成
        </button>{" "}
        <span>画像: {renderStatus || "未生成"}</span>
        <details>
          <summary>用語集・公式表記の参照資料</summary>
          <label>
            用語集（例: 日本語 → 公式英語表記、プロダクト名、根拠URL）
            <textarea
              rows={5}
              value={glossary}
              onChange={(e) => setGlossary(e.target.value)}
              maxLength={50000}
            />
          </label>
          <label>
            公式資料のURL
            <input
              value={referenceURL}
              onChange={(e) => setReferenceURL(e.target.value)}
              placeholder="https://公式サイト/..."
            />
          </label>
          <button
            disabled={working || references.length >= 10}
            onClick={() =>
              action(async () => {
                const d = await (
                  await api("llm/reference", json({ url: referenceURL }))
                ).json();
                setReferenceText(d.text);
                if (d.truncated)
                  setInfo(
                    "資料の先頭2万文字を取得しました。必要な引用部分を確認してください。",
                  );
              })
            }
          >
            公式ページの文章を取得
          </button>
          <label>
            根拠の引用文（手入力も可）
            <textarea
              rows={5}
              value={referenceText}
              onChange={(e) => setReferenceText(e.target.value)}
              maxLength={20000}
            />
          </label>
          <button
            disabled={!referenceText.trim() || references.length >= 10}
            onClick={() => {
              setReferences([
                ...references,
                { url: referenceURL, text: referenceText },
              ]);
              setReferenceText("");
              setReferenceURL("");
            }}
          >
            参照資料に追加
          </button>
          <ul>
            {references.map((r, i) => (
              <li key={i}>
                {r.url || "手入力の引用文"}（{r.text.length}文字）{" "}
                <button
                  onClick={() =>
                    setReferences(references.filter((_, j) => j !== i))
                  }
                >
                  除外
                </button>
              </li>
            ))}
          </ul>
          <p>
            モデル自身の知識だけで公式表記を確定せず、根拠がない項目は「未確認」とします。
          </p>
        </details>
        <details>
          <summary>既定／カスタムプロンプト</summary>
          {Object.entries(labels).map(([task, label]) => (
            <label key={task}>
              {label}
              <textarea
                rows={5}
                value={prompts[task] || ""}
                onChange={(e) =>
                  setPrompts({ ...prompts, [task]: e.target.value })
                }
                maxLength={50000}
              />
              <button
                onClick={() =>
                  setPrompts({ ...prompts, [task]: defaults[task] })
                }
              >
                このプロンプトを既定に戻す
              </button>
            </label>
          ))}
        </details>
      </details>
      <div className="toolbar">
        <button
          disabled={
            !job ||
            !model ||
            !tasks.length ||
            working ||
            !!running ||
            (tasks.includes("layout") && (!vision || renderStatus !== "done"))
          }
          onClick={start}
        >
          LLM検査を開始
        </button>
        {running && (
          <button
            onClick={() =>
              action(async () => {
                const d = await (
                  await api(`jobs/${job}/reviews/${reviewId}`, {
                    method: "DELETE",
                  })
                ).json();
                setInfo(d.message);
              })
            }
          >
            検査を中止
          </button>
        )}
        {review && (
          <button
            onClick={() =>
              action(async () =>
                download(
                  await api(`jobs/${job}/reviews/${reviewId}/download`),
                  "llm-review.json",
                ),
              )
            }
          >
            結果・使用プロンプトを保存
          </button>
        )}
      </div>
      {!job && <p>先に日本語・英語のPPTXを読み込んでください。</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {review && (
        <>
          <p aria-live="polite">
            {review.status}: {review.message} ／ モデル: {review.model}
          </p>
          <div className="toolbar">
            <button
              aria-pressed={scope === "page"}
              onClick={() => setScope("page")}
            >
              ページ別レビュー
            </button>
            <button
              aria-pressed={scope === "global"}
              onClick={() => setScope("global")}
            >
              全体・一貫性レビュー（{globalResults.length}件）
            </button>
          </div>
          {scope === "global" ? (
            <section aria-label="全体レビュー">
              {globalResults.length ? (
                renderResults(globalResults)
              ) : (
                <p>全体レビューの結果はまだありません。</p>
              )}
            </section>
          ) : (
            <PageWorkspace
              context={context}
              counts={counts}
              noteContent={renderResults(
                pageResults.filter((r) => r.task === "notes"),
              )}
            >
              {renderResults(pageResults.filter((r) => r.task !== "notes"))}
              {!pageResults.length && (
                <p>
                  このページのレビュー結果はまだありません。処理状況または指定ページ範囲を確認してください。
                </p>
              )}
            </PageWorkspace>
          )}
        </>
      )}
    </section>
  );
}
