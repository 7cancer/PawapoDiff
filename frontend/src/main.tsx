import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
type Shape = { name: string; box: number[] };
type Change = {
  kind: string;
  fields: string[];
  left: Shape | null;
  right: Shape | null;
  uncertain: boolean;
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
type Result = {
  width: number;
  height: number;
  pages: { page: number; changes: Change[] }[];
  issues: Issue[];
};
type Status = {
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
  const [page, setPage] = useState(1),
    [threshold, setThreshold] = useState(1),
    [mode, setMode] = useState("pair"),
    [alpha, setAlpha] = useState(0.5),
    [marks, setMarks] = useState(true);
  const [images, setImages] = useState<Record<string, string>>({}),
    [side, setSide] = useState("left"),
    [preset, setPreset] = useState("FHD"),
    [width, setWidth] = useState(1920),
    [crop, setCrop] = useState<number[] | null>(null);
  const cropRef = useRef<HTMLDivElement>(null),
    start = useRef<number[] | null>(null);
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
    if (!left) return;
    setError("");
    setBusy(true);
    setStatus(null);
    setImages({});
    setCrop(null);
    setPage(1);
    setSide("left");
    const data = new FormData();
    data.append("left", left);
    if (right) data.append("right", right);
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
  function slide(s: string) {
    return (
      <div className="slide">
        {images[s] && (
          <img
            src={images[s]}
            alt={`${s === "left" ? "原本" : "英訳版"} ${page}ページ`}
          />
        )}
        {marks &&
          result &&
          changes
            .filter((c) => c.kind === "structure")
            .map((c, i) => {
              const b = (s === "left" ? c.left : c.right)?.box;
              return b ? (
                <span
                  key={i}
                  className="mark"
                  title={c.fields.join(" / ")}
                  style={{
                    left: (b[0] / result.width) * 100 + "%",
                    top: (b[1] / result.height) * 100 + "%",
                    width: (b[2] / result.width) * 100 + "%",
                    height: (b[3] / result.height) * 100 + "%",
                  }}
                />
              ) : null;
            })}
      </div>
    );
  }
  function point(e: React.PointerEvent) {
    const r = cropRef.current!.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    ];
  }
  function move(e: React.PointerEvent) {
    if (!start.current) return;
    const p = point(e),
      a = start.current;
    setCrop([
      Math.min(a[0], p[0]),
      Math.min(a[1], p[1]),
      Math.abs(a[0] - p[0]),
      Math.abs(a[1] - p[1]),
    ]);
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
              01 原本（画像出力のみの場合もこちら）
              <input
                required
                type="file"
                accept=".pptx"
                onChange={(e) => setLeft(e.target.files?.[0] || null)}
              />
            </label>
            <label>
              02 英訳版（比較する場合）
              <input
                type="file"
                accept=".pptx"
                onChange={(e) => setRight(e.target.files?.[0] || null)}
              />
            </label>
            <button disabled={busy || !left}>
              {" "}
              {busy ? "処理中…" : right ? "比較を開始 →" : "画像出力の準備 →"}
            </button>
            <small>同ページ数・同寸法の .pptx ／ 各800MBまで</small>
          </form>
        </section>
        {error && (
          <div role="alert" className="error">
            {error}
          </div>
        )}
        {status && <p aria-live="polite">{status.message}</p>}
        {status?.status === "done" && (
          <>
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
                      <label>
                        <input
                          type="checkbox"
                          checked={marks}
                          onChange={(e) => setMarks(e.target.checked)}
                        />
                        ハイライト
                      </label>
                    </>
                  )}
                </div>
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
                    <img className="diff" src={images.diff} alt="画像差分" />
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
                    <div className="changes">
                      {changes.length === 0 ? (
                        <p>検出された変更はありません。</p>
                      ) : (
                        changes.map((c, i) => (
                          <article key={i}>
                            <span className={"badge " + c.kind}>
                              {c.kind === "text" ? "文字内容" : "書式・配置"}
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
              </section>
            )}
            <section className="panel">
              <h2>スライドを画像として保存</h2>
              <div className="toolbar">
                <label>
                  対象{" "}
                  <select
                    value={side}
                    onChange={(e) => setSide(e.target.value)}
                  >
                    {status.sides.map((s) => (
                      <option key={s} value={s}>
                        {s === "left" ? "原本" : "英訳版"}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  解像度{" "}
                  <select
                    value={preset}
                    onChange={(e) => setPreset(e.target.value)}
                  >
                    <option value="HD">HD（1280×720枠）</option>
                    <option value="FHD">フルHD（1920×1080枠）</option>
                    <option value="4K">4K（3840×2160枠）</option>
                    <option value="custom">カスタム切り抜き</option>
                  </select>
                </label>
                {preset === "custom" && (
                  <label>
                    出力幅{" "}
                    <input
                      type="number"
                      min="64"
                      max="7680"
                      value={width}
                      onChange={(e) => setWidth(+e.target.value)}
                    />{" "}
                    px
                  </label>
                )}
                <button
                  disabled={
                    busy ||
                    (preset === "custom" &&
                      (!crop || crop[2] < 0.001 || crop[3] < 0.001))
                  }
                  onClick={() =>
                    action(async () =>
                      download(
                        await api(`jobs/${job}/export`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ side, preset, width, crop }),
                        }),
                        "PawapoDiff-images.zip",
                      ),
                    )
                  }
                >
                  全ページをPNGで保存
                </button>
              </div>
              {preset === "custom" && (
                <>
                  <p>
                    上のページ一覧で代表ページを選び、画像上をドラッグしてください。同じ範囲を全ページに適用します。
                  </p>
                  <button onClick={() => setCrop([0, 0, 1, 1])}>
                    全体を選択
                  </button>
                  <div
                    className="crop"
                    ref={cropRef}
                    onPointerDown={(e) => {
                      start.current = point(e);
                      e.currentTarget.setPointerCapture(e.pointerId);
                      setCrop(null);
                    }}
                    onPointerMove={move}
                    onPointerUp={(e) => {
                      move(e);
                      start.current = null;
                    }}
                    onPointerCancel={() => {
                      start.current = null;
                    }}
                  >
                    <img
                      src={images[side]}
                      alt="切り抜き範囲の選択"
                      draggable={false}
                    />
                    {crop && (
                      <span
                        style={{
                          left: crop[0] * 100 + "%",
                          top: crop[1] * 100 + "%",
                          width: crop[2] * 100 + "%",
                          height: crop[3] * 100 + "%",
                        }}
                      />
                    )}
                  </div>
                  {crop && (
                    <p>
                      選択範囲：
                      {crop.map((n) => (n * 100).toFixed(1) + "%").join(" / ")}
                    </p>
                  )}
                </>
              )}
            </section>
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
