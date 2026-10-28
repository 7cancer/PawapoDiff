import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
type Shape = { name: string; box: number[] };
type Change = {
  kind: string;
  fields: string[];
  left: Shape | null;
  right: Shape | null;
  uncertain: boolean;
  highlight?: "format" | "layout" | "mixed" | "other";
};
type Entry = {
  page: number;
  target: string;
  shape?: string;
  uncertain?: boolean;
};
type Issue = {
  kind: string;
  source: string;
  target?: string;
  page?: number;
  entries?: Entry[];
};
type Occurrence = {
  page: number;
  shape: string;
  source_line: number;
  target: string;
  uncertain: boolean;
};
type Translation = {
  pages: number[];
  entries: Occurrence[];
  variants: { target: string; entries: Occurrence[] }[];
  page: number;
  shape: string;
  source: string;
  target: string;
  inconsistent: boolean;
  notes: string[];
};
type Result = {
  notices?: {
    code?: string;
    side: string;
    page: number | null;
    shape_id: string;
    message: string;
  }[];
  warnings?: {
    side: string;
    page: number;
    shape_id: string;
    message: string;
  }[];
  translations: Translation[];
  width: number;
  height: number;
  pages: { page: number; changes: Change[] }[];
  issues: Issue[];
};
type Status = {
  diagnostic?: {
    code: string;
    summary: string;
    actions: string[];
    document?: string;
    stage?: string;
    page?: number;
    part?: string;
    shape_id?: string;
    technical?: string;
    cleanup?: string;
  } | null;
  elapsed_seconds?: number;
  status: string;
  message: string;
  result: Result | null;
  pages: number;
  sides: string[];
};
const token =
  new URLSearchParams(location.hash.slice(1)).get("token") ||
  sessionStorage.getItem("pawapo-token") ||
  "";
if (token) sessionStorage.setItem("pawapo-token", token);
history.replaceState(null, "", location.pathname);
async function api(path: string, options: RequestInit = {}) {
  const r = await fetch("/api/" + path, {
    ...options,
    headers: { ...options.headers, "X-Pawapo-Token": token },
  });
  if (!r.ok) {
    let message = "処理に失敗しました";
    try {
      const j = await r.json();
      message =
        typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(message);
  }
  return r;
}
async function download(response: Response, name: string) {
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function App() {
  const [left, setLeft] = useState<File | null>(null),
    [right, setRight] = useState<File | null>(null);
  const [job, setJob] = useState(""),
    [status, setStatus] = useState<Status | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [logError, setLogError] = useState("");
  const [page, setPage] = useState(1),
    [threshold, setThreshold] = useState(1),
    [mode, setMode] = useState("pair"),
    [alpha, setAlpha] = useState(0.5),
    [marks, setMarks] = useState(true);
  const [activeChange, setActiveChange] = useState<number | null>(null);
  useEffect(() => {
    setActiveChange(null);
  }, [page, job, marks, mode, status?.result]);
  const [images, setImages] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!job) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const s: Status = await (await api(`jobs/${job}`)).json();
        if (cancelled) return;
        setStatus(s);
        if (s.status === "error") {
          setError(s.message);
          setBusy(false);
        } else if (s.status === "done") setBusy(false);
        else timer = setTimeout(poll, 700);
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setBusy(false);
        }
      }
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job]);
  useEffect(() => {
    if (status?.status !== "done") return;
    let cancelled = false;
    const urls: string[] = [];
    setImages({});
    async function load() {
      try {
        const sides = [...status!.sides, ...(status!.result ? ["diff"] : [])];
        const entries = await Promise.all(
          sides.map(async (s) => {
            const blob = await (
              await api(`jobs/${job}/images/${s}/${page}`)
            ).blob();
            const url = URL.createObjectURL(blob);
            urls.push(url);
            return [s, url] as const;
          }),
        );
        if (!cancelled) setImages(Object.fromEntries(entries));
        else urls.forEach(URL.revokeObjectURL);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    }
    load();
    return () => {
      cancelled = true;
      urls.forEach(URL.revokeObjectURL);
    };
  }, [job, status?.status, page]);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!left || !right) return;
    if (left.size > 15 * 1024 ** 3 || right.size > 15 * 1024 ** 3) {
      setError("1ファイルの上限は15GBです。");
      return;
    }
    setError("");
    setLogError("");
    setJob("");
    setBusy(true);
    setStatus(null);
    setImages({});
    setPage(1);
    const data = new FormData();
    data.append("left", left);
    data.append("right", right);
    data.append("threshold", String(threshold));
    try {
      const j = await (
        await api("jobs", { method: "POST", body: data })
      ).json();
      setJob(j.id);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }
  async function action(fn: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const result = status?.result,
    changes = result?.pages[page - 1]?.changes || [];
  function activateChange(index: number, revealList = false) {
    if (!marks) return;
    setActiveChange(index);
    if (revealList) {
      const entry = document.getElementById(`change-${page}-${index}`);
      const list = entry?.parentElement;
      if (entry && list) {
        const offset =
          entry.getBoundingClientRect().top - list.getBoundingClientRect().top;
        if (offset < 0 || offset + entry.offsetHeight > list.clientHeight) {
          list.scrollTop += offset;
        }
      }
    }
  }
  function goToPage(next: number) {
    setPage(next);
    document
      .querySelector(".workspace")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function slide(s: string) {
    return (
      <div className="slide">
        {images[s] && (
          <img
            src={images[s]}
            alt={`${s === "left" ? "原本" : s === "right" ? "英訳版" : "画像差分"} ${page}ページ`}
          />
        )}
        {marks &&
          result &&
          changes.map((c, index) => {
            if (c.kind !== "structure" && activeChange !== index) return null;
            return (s === "diff" ? ["left", "right"] : [s]).map((side) => {
              const item = side === "left" ? c.left : c.right;
              if (!item) return null;
              const b = item.box;
              return (
                <button
                  type="button"
                  key={`${index}-${side}`}
                  className={`mark ${c.highlight || "other"}${activeChange === index ? " active" : ""}`}
                  data-change={index}
                  aria-label={`変更 ${index + 1}: ${item.name}`}
                  title={`${index + 1}. ${c.fields.join(" / ")}`}
                  onMouseEnter={() => activateChange(index, true)}
                  onMouseLeave={() => setActiveChange(null)}
                  onFocus={() => activateChange(index, true)}
                  onBlur={() => setActiveChange(null)}
                  style={{
                    left: (b[0] / result.width) * 100 + "%",
                    top: (b[1] / result.height) * 100 + "%",
                    width: (b[2] / result.width) * 100 + "%",
                    height: (b[3] / result.height) * 100 + "%",
                  }}
                >
                  <span className="mark-number">{index + 1}</span>
                </button>
              );
            });
          })}
      </div>
    );
  }
  return (
    <>
      <header>
        <strong>▣ PawapoDiff</strong>
        <span>スライドの変化を、見逃さない。</span>
        <small>LOCAL · オフライン処理</small>
      </header>
      <main>
        <section className="intro">
          <div>
            <p className="eyebrow">POWERPOINT COMPARISON</p>
            <h1>
              翻訳の前後を、
              <br />
              ひと目で比較。
            </h1>
            <p>配置・書式の変更と、翻訳の表記揺れを確認します。</p>
          </div>
          <form onSubmit={submit}>
            <label>
              01 日本語原本
              <input
                required
                type="file"
                accept=".pptx"
                onChange={(e) => setLeft(e.target.files?.[0] || null)}
              />
            </label>
            <label>
              02 英訳版
              <input
                required
                type="file"
                accept=".pptx"
                onChange={(e) => setRight(e.target.files?.[0] || null)}
              />
            </label>
            <button disabled={busy || !left || !right}>
              {" "}
              {busy ? "処理中…" : "比較を開始 →"}
            </button>
            <small>同ページ数・同寸法の .pptx ／ 各15GBまで</small>
          </form>
        </section>
        {error && (
          <div role="alert" className="error">
            {status?.status === "error" && status.diagnostic ? (
              <>
                <h2>{status.diagnostic.summary}</h2>
                <p>
                  対象: {status.diagnostic.document || "比較処理"} ／ 処理:{" "}
                  {status.diagnostic.stage}
                  {status.diagnostic.page
                    ? ` ／ ${status.diagnostic.page}ページ`
                    : ""}
                </p>
                <p>
                  エラーコード: <code>{status.diagnostic.code}</code>
                </p>
                <ul>
                  {status.diagnostic.actions.map((action, index) => (
                    <li key={index}>{action}</li>
                  ))}
                </ul>
                <details>
                  <summary>技術情報（問い合わせ用）</summary>
                  <pre>{JSON.stringify(status.diagnostic, null, 2)}</pre>
                </details>
              </>
            ) : (
              <p style={{ whiteSpace: "pre-wrap" }}>{error}</p>
            )}
            <button
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setLogError("");
                try {
                  await download(
                    await api("diagnostics"),
                    "PawapoDiff-diagnostics.zip",
                  );
                } catch (e) {
                  setLogError("診断ログの保存に失敗しました: " + String(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              診断ログを保存
            </button>
            {logError && <p>{logError}</p>}
          </div>
        )}
        {busy && !status && (
          <p aria-live="polite">
            資料を作業フォルダーへ転送中。大容量の場合は時間がかかります。
          </p>
        )}
        {status && status.status !== "error" && (
          <p aria-live="polite">
            {status.message}{" "}
            {status.status === "running"
              ? `（経過 ${Math.floor(status.elapsed_seconds || 0)}秒）`
              : ""}
          </p>
        )}
        {status?.status === "done" && (
          <>
            {!!result?.notices?.length && (
              <section className="media-notice">
                <h2>
                  {result.notices.some((notice) =>
                    notice.code?.startsWith("IMAGE_"),
                  )
                    ? "画像・メディアの確認事項"
                    : "動画・音声は静止画として比較します"}
                </h2>
                <p>
                  {result.notices.some((notice) =>
                    notice.code?.startsWith("IMAGE_"),
                  )
                    ? "取得できる画像・代替枠で比較を続けます。表示された箇所は目視で確認してください。外部ファイルの取得・PowerPointの参照回復は行わず、元のファイルも変更しません。"
                    : "動画本体の差分は判定しません。再生・外部参照・参照回復は行わず、元のファイルも変更しません。"}
                </p>
                <details>
                  <summary>
                    比較時の確認箇所（{result.notices.length}項目）
                  </summary>
                  <ul>
                    {result.notices.map((notice, index) => (
                      <li key={index}>
                        {notice.side === "left" ? "日本語原本" : "英訳版"}{" "}
                        {notice.page ? `${notice.page}ページ` : "共通パーツ"} ／
                        図形ID {notice.shape_id}: {notice.message}
                      </li>
                    ))}
                  </ul>
                </details>
              </section>
            )}
            {!!result?.warnings?.length && (
              <section className="analysis-warning" role="status">
                <h2>
                  一部の要素は解析できませんでした（{result.warnings.length}
                  項目）
                </h2>
                <p>
                  比較・翻訳チェックには不完全な箇所があります。該当ページを画像で確認してください。
                </p>
                <details>
                  <summary>対象ページと要素を表示</summary>
                  <ul>
                    {result.warnings.map((warning, index) => (
                      <li key={index}>
                        {warning.side === "left" ? "日本語原本" : "英訳版"}{" "}
                        {warning.page}ページ ／ 図形ID {warning.shape_id}:{" "}
                        {warning.message}
                      </li>
                    ))}
                  </ul>
                </details>
              </section>
            )}
            <section className="workspace">
              <nav>
                <h3>
                  SLIDES <small>{status.pages}</small>
                </h3>
                {Array.from({ length: status.pages }, (_, i) => (
                  <button
                    className={page === i + 1 ? "selected" : ""}
                    onClick={() => setPage(i + 1)}
                    key={i}
                  >
                    ページ {String(i + 1).padStart(2, "0")}{" "}
                    <small>
                      {result?.pages[i].changes.filter(
                        (c) => c.kind === "structure",
                      ).length ?? "—"}{" "}
                      件
                    </small>
                  </button>
                ))}
              </nav>
              <div className="viewer">
                <div className="toolbar">
                  <h2>ページ {page}</h2>
                  {result && (
                    <>
                      <select
                        aria-label="表示方法"
                        value={mode}
                        onChange={(e) => setMode(e.target.value)}
                      >
                        <option value="pair">左右比較</option>
                        <option value="overlay">重ね合わせ</option>
                        <option value="diff">画像差分（補助）</option>
                      </select>
                      <label className="highlight-toggle">
                        <input
                          aria-label="ハイライト表示"
                          type="checkbox"
                          checked={marks}
                          onChange={(e) => setMarks(e.target.checked)}
                        />
                        ハイライト表示（{marks ? "ON" : "OFF"}）
                      </label>
                    </>
                  )}
                </div>
                {result && (
                  <div className="legend" aria-label="ハイライトの凡例">
                    <span className="format">書式のみ</span>
                    <span className="layout">配置・サイズ・回転</span>
                    <span className="mixed">書式と配置</span>
                    <span className="other">画像・追加削除・要確認</span>
                  </div>
                )}
                {result && mode === "overlay" ? (
                  <>
                    <label>
                      英訳版の濃さ{" "}
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step=".01"
                        value={alpha}
                        onChange={(e) => setAlpha(+e.target.value)}
                      />
                    </label>
                    <div className="overlay">
                      {slide("left")}
                      <div style={{ opacity: alpha }}>{slide("right")}</div>
                    </div>
                  </>
                ) : result && mode === "diff" ? (
                  <>
                    <p>
                      文字の翻訳も差分に含まれます。明るい箇所が画像上の変更です。
                    </p>
                    {slide("diff")}
                  </>
                ) : (
                  <div className="pair">
                    <div>
                      <h4>原本</h4>
                      {slide("left")}
                    </div>
                    {result && (
                      <div>
                        <h4>英訳版</h4>
                        {slide("right")}
                      </div>
                    )}
                  </div>
                )}
                {result && (
                  <>
                    <div className="toolbar">
                      <h3>変更一覧</h3>
                      <label>
                        位置・サイズ閾値{" "}
                        <input
                          className="number"
                          type="number"
                          min="0"
                          max="100"
                          step=".1"
                          value={threshold}
                          onChange={(e) => setThreshold(+e.target.value)}
                        />{" "}
                        pt
                      </label>
                      <button
                        disabled={busy}
                        onClick={() =>
                          action(async () => {
                            const r = await (
                              await api(`jobs/${job}/threshold`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ threshold }),
                              })
                            ).json();
                            setStatus({ ...status, result: r });
                          })
                        }
                      >
                        再判定
                      </button>
                    </div>
                    <p className="interaction-help">
                      図の番号にカーソルを合わせると、対応する変更が一覧で強調されます。一覧からも図を確認できます。
                    </p>
                    <div className="changes">
                      {changes.length === 0 ? (
                        <p>検出された変更はありません。</p>
                      ) : (
                        changes.map((c, i) => (
                          <article
                            key={i}
                            id={`change-${page}-${i}`}
                            data-change={i}
                            className={activeChange === i ? "active" : ""}
                            tabIndex={0}
                            onMouseEnter={() => activateChange(i)}
                            onMouseLeave={() => setActiveChange(null)}
                            onFocus={() => activateChange(i)}
                            onBlur={() => setActiveChange(null)}
                          >
                            <span className="change-number">{i + 1}</span>
                            <span
                              className={
                                "badge " +
                                (c.kind === "text"
                                  ? "text"
                                  : c.highlight || "other")
                              }
                            >
                              {c.kind === "text"
                                ? "文字内容"
                                : c.highlight === "format"
                                  ? "書式のみ"
                                  : c.highlight === "layout"
                                    ? "配置"
                                    : c.highlight === "mixed"
                                      ? "書式と配置"
                                      : "その他・要確認"}
                            </span>
                            <strong>{(c.left || c.right)?.name}</strong>
                            <p>
                              {c.fields.join(" / ")}{" "}
                              {c.uncertain && "【対応要確認】"}
                            </p>
                          </article>
                        ))
                      )}
                    </div>
                  </>
                )}
              </div>
            </section>
            {result && (
              <section className="panel">
                <div className="toolbar">
                  <h2>
                    翻訳チェック <small>{result.issues.length} 件の候補</small>
                  </h2>
                  <button
                    disabled={busy}
                    onClick={() =>
                      action(async () =>
                        download(
                          await api(`jobs/${job}/report`),
                          "PawapoDiff-report.html",
                        ),
                      )
                    }
                  >
                    HTMLレポートを保存
                  </button>
                </div>
                <p>
                  意味の正確さは判定しません。固有名詞や文脈による訳し分けも、人が確認してください。
                </p>
                {result.issues.map((issue, i) => (
                  <article key={i}>
                    <span className="badge">{issue.kind}</span>
                    <strong>{issue.source}</strong>
                    {(
                      issue.entries || [
                        { page: issue.page!, target: issue.target! },
                      ]
                    ).map((e, j) => (
                      <p key={j}>
                        p.{e.page} → {e.target || "（空）"}{" "}
                        {e.uncertain && "【対応要確認】"}
                      </p>
                    ))}
                  </article>
                ))}
                {!result.issues.length && <p>候補はありません。</p>}
                <h3>
                  日英比較表 <small>{result.translations.length} 件</small>
                </h3>
                <p>
                  改行ごとに分割し、原文側が2文字以下の行は省略します。同じ原文を全ページから1行にまとめ、英訳ごとに出現ページを表示します。改行数が異なる場合は対応を要確認とします。
                </p>
                <div className="translation-table-wrap">
                  <table className="translation-table">
                    <thead>
                      <tr>
                        <th>ページ・要素</th>
                        <th>日本語原文</th>
                        <th>英訳</th>
                        <th>注釈</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.translations.map((row, i) => (
                        <tr
                          key={i}
                          className={row.inconsistent ? "inconsistent" : ""}
                        >
                          <td>
                            <div className="page-links">
                              {row.pages.map((number) => (
                                <button
                                  key={number}
                                  onClick={() => goToPage(number)}
                                >
                                  p.{number}
                                </button>
                              ))}
                            </div>
                            <small>{row.entries.length} 箇所</small>
                            <details>
                              <summary>要素と行を表示</summary>
                              {row.entries.map((entry, j) => (
                                <small key={j}>
                                  p.{entry.page} ／ {entry.shape} ／{" "}
                                  {entry.source_line}行目
                                  {entry.uncertain ? "（要確認）" : ""}
                                </small>
                              ))}
                            </details>
                          </td>
                          <td>{row.source}</td>
                          <td>
                            {row.variants.map((variant, j) => (
                              <div className="translation-variant" key={j}>
                                <div>{variant.target || "（訳文なし）"}</div>
                                <div className="page-links">
                                  {Array.from(
                                    new Set(
                                      variant.entries.map(
                                        (entry) => entry.page,
                                      ),
                                    ),
                                  ).map((number) => (
                                    <button
                                      key={number}
                                      onClick={() => goToPage(number)}
                                    >
                                      p.{number}
                                    </button>
                                  ))}
                                </div>
                              </div>
                            ))}
                          </td>
                          <td>
                            {row.notes.length
                              ? row.notes.map((note) => (
                                  <span className="translation-note" key={note}>
                                    {note}
                                  </span>
                                ))
                              : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
        <footer>
          画像内の文字・SmartArt・グラフ内部は目視で確認してください。原本ファイルは変更しません。
          <br />
          終了するには起動したコンソールで Ctrl+C を押してください。
        </footer>
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
