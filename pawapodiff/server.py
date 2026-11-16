from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from copy import deepcopy
from io import BytesIO
import logging
import json
import platform
import struct
import time
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from .web import BrowserFiles, frontend_directory
from PIL import Image, ImageChops, ImageEnhance
from pydantic import BaseModel, Field
from .engine import inspect, compare, pair_text_shapes
from .consistency import check_consistency
from .exports import notes_csv, consistency_csv
from .llm import (
    Connection,
    ReviewRequest,
    PROMPTS,
    list_models,
    model_info,
    fetch_reference,
    run_review,
    endpoint,
)
from .render import render, RenderError
from .uploads import receive_presentations
from .report import report
from .errors import describe, readable
from . import __version__


@dataclass
class Job:
    status: str = "queued"
    message: str = "待機中"
    result: dict | None = None
    documents: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    diagnostic: dict | None = None
    facts: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    mode: str = "diff"
    threshold: float = 1
    mappings: dict = field(default_factory=dict)
    consistency: dict | None = None
    reviews: dict = field(default_factory=dict)
    render_status: str = "idle"
    render_error: str | None = None


class ThresholdRequest(BaseModel):
    threshold: float = Field(ge=0, le=100)


class MappingRequest(BaseModel):
    pairs: dict[str, str | None] = Field(default_factory=dict)


class ModelRequest(Connection):
    model: str = Field(min_length=1, max_length=200)


class ReferenceRequest(BaseModel):
    url: str = Field(max_length=2048)


def create_app(token=None, root=None, renderer=render, frontend=None, *, mode="diff"):
    from .diagnostics import configure_diagnostics

    configure_diagnostics()
    token = token or secrets.token_urlsafe(32)
    work = Path(root or tempfile.mkdtemp(prefix="PawapoDiff-"))
    work.mkdir(parents=True, exist_ok=True)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PowerPoint")
    jobs = {}
    review_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="LLM")

    @asynccontextmanager
    async def lifespan(app):
        yield
        for job in jobs.values():
            for review in job.reviews.values():
                review["cancel"].set()
        review_pool.shutdown(wait=True, cancel_futures=True)
        pool.shutdown(wait=True, cancel_futures=True)
        if root is None:
            shutil.rmtree(work, ignore_errors=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token

    @app.get("/api/health")
    def health():
        return {"status": "ready", "version": __version__, "mode": mode}

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        hostname = host.split(":")[0]
        origin = request.headers.get("origin")
        if hostname not in ("127.0.0.1", "localhost") or (
            origin and origin not in (f"http://{host}",)
        ):
            return JSONResponse({"detail": "ローカルからのみ利用できます。"}, 403)
        if request.url.path.startswith("/api/"):
            if not secrets.compare_digest(
                request.headers.get("x-pawapo-token", ""), token
            ):
                return JSONResponse(
                    {
                        "detail": "認証に失敗しました。起動時の画面を開き直してください。"
                    },
                    403,
                )
        return await call_next(request)

    def get_job(key, ready=True):
        job = jobs.get(key)
        if not job:
            raise HTTPException(404, "ジョブが見つかりません。")
        if ready and job.status != "done":
            raise HTTPException(409, "処理が完了していません。")
        return job

    def process(key, threshold):
        job = jobs[key]
        document, stage = None, "資料の構造を解析中"

        def update(label):
            nonlocal stage
            stage = label
            job.message = f"{document or '比較'}: {label}"
            event = {
                "elapsed_seconds": round(time.monotonic() - job.started, 1),
                "document": document,
                "stage": label,
            }
            with job.lock:
                job.events.append(event)
                job.events[:] = job.events[-1500:]
            logging.getLogger(__name__).info(
                "job=%s document=%s stage=%s elapsed=%s",
                key,
                document,
                label,
                event["elapsed_seconds"],
            )

        try:
            job.status = "running"
            for side in job.documents:
                document = "日本語原本" if side == "left" else "英訳版"
                update("PPTXの内部構造を検査中")
                job.documents[side] = inspect(
                    work / key / f"{side}.pptx",
                    progress=lambda page, total: update(
                        f"構造を解析中 {page}/{total}ページ"
                    ),
                )
                with job.lock:
                    job.facts[side]["pages"] = len(job.documents[side]["slides"])
                    job.facts[side]["media_notices"] = job.documents[side].get(
                        "notices", []
                    )
                    job.facts[side]["analysis_warnings"] = job.documents[side].get(
                        "warnings", []
                    )
            if "right" in job.documents:
                document = None
                update("ページ数・寸法の確認と図形の比較")
                job.result = compare(
                    job.documents["left"],
                    job.documents["right"],
                    threshold,
                    mappings=job.mappings,
                )
            job.consistency = check_consistency(
                job.documents["left"], job.documents["right"], mappings=job.mappings
            )
            if job.mode == "diff":
                render_images(key)
            update("完了")
            job.status, job.message = "done", "完了"
        except Exception as error:
            logging.getLogger(__name__).exception(
                "Job failed: job=%s document=%s stage=%s", key, document, stage
            )
            job.diagnostic = describe(error, document=document, job_id=key)
            job.diagnostic.setdefault("stage", stage)
            job.message = readable(job.diagnostic)
            job.status = "error"

    def render_images(key):
        job = jobs[key]
        job.render_status = "running"
        job.render_error = None
        try:
            rendered = {}
            for side in job.documents:
                job.message = f"{side}: PowerPointで画像を生成中"
                options = (
                    {"progress": lambda message: setattr(job, "message", message)}
                    if renderer is render
                    else {}
                )
                directory = work / key / side
                if directory.exists():
                    shutil.rmtree(directory)
                rendered[side] = renderer(
                    work / key / f"{side}.pptx", work / key / side, **options
                )
                if len(rendered[side]) != len(job.documents[side]["slides"]):
                    raise RenderError("描画されたページ数が一致しません。")
            diffdir = work / key / "diff"
            diffdir.mkdir(exist_ok=True)
            for i, (a, b) in enumerate(zip(rendered["left"], rendered["right"]), 1):
                with Image.open(a) as ia, Image.open(b) as ib:
                    # Equal slide sizes can still produce different pixel sizes.
                    ra, rb = ia.convert("RGB"), ib.convert("RGB")
                    if ra.size != rb.size:
                        rb = rb.resize(ra.size)
                    with ImageChops.difference(ra, rb) as diff:
                        ImageEnhance.Contrast(diff).enhance(3).save(
                            diffdir / f"{i:04}.png"
                        )
            job.images = rendered
            job.render_status = "done"
            job.message = "完了"
        except Exception as error:
            job.render_status = "error"
            job.render_error = readable(describe(error))
            if job.status != "done":
                raise

    @app.get("/api/diagnostics")
    def diagnostics():
        from .diagnostics import diagnostic_files

        files = diagnostic_files()
        try:
            free_bytes = shutil.disk_usage(work).free
        except OSError:
            free_bytes = None
        snapshot = {
            "app_version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "process_bits": struct.calcsize("P") * 8,
            "work_disk_free_bytes": free_bytes,
            "jobs": {},
        }
        for key, job in list(jobs.items()):
            with job.lock:
                snapshot["jobs"][key] = {
                    "status": job.status,
                    "message": job.message,
                    "diagnostic": job.diagnostic,
                    "files": deepcopy(job.facts),
                    "elapsed_seconds": round(time.monotonic() - job.started, 1),
                    "events": list(job.events),
                }
        summary = [
            f"PawapoDiff {__version__} 診断サマリー",
            f"OS: {snapshot['platform']} / Python {snapshot['python']} ({snapshot['process_bits']}bit)",
            f"作業ドライブ空き容量: {f'{free_bytes / 1024**3:.1f}GB' if free_bytes is not None else '取得できませんでした'}",
            "スライド本文・認証トークンはこのサマリーに含めません。",
        ]
        for key, entry in snapshot["jobs"].items():
            summary.extend(
                [
                    "",
                    f"ジョブ: {key} / 状態: {entry['status']}",
                    f"経過: {entry['elapsed_seconds']}秒",
                    f"資料: {entry['files']}",
                    entry["message"],
                ]
            )
            if entry["diagnostic"]:
                summary.append(
                    json.dumps(entry["diagnostic"], ensure_ascii=False, indent=2)
                )
            summary.extend(
                f"  +{event['elapsed_seconds']}秒 {event['document'] or '比較'}: {event['stage']}"
                for event in entry["events"]
            )
        if not snapshot["jobs"]:
            summary.append(
                "この起動では、まだ比較ジョブがありません。エラーが起きた画面で診断ログを保存してください。"
            )
        # Diagnostics must still be downloadable when the work disk is full.
        archive = BytesIO()
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
            bundle.writestr(
                "diagnostic-summary.txt", "\n".join(summary).encode("utf-8-sig")
            )
            bundle.writestr(
                "diagnostic-summary.json",
                json.dumps(snapshot, ensure_ascii=False, indent=2),
            )
            for path in files:
                try:
                    bundle.write(path, path.name)
                except OSError:
                    continue
        return Response(
            archive.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="PawapoDiff-diagnostics.zip"'
            },
        )

    @app.post("/api/jobs")
    async def new_job(request: Request):
        job_mode = request.query_params.get("mode", mode)
        if job_mode not in ("diff", "consistency"):
            raise HTTPException(422, "比較モードが不正です。")
        key = secrets.token_hex(12)
        directory = work / key
        try:
            directory.mkdir()
            threshold = await receive_presentations(request, directory)
            facts = {
                side: {"bytes": (directory / f"{side}.pptx").stat().st_size}
                for side in ("left", "right")
            }
        except BaseException as error:
            logging.getLogger(__name__).exception("Upload failed: job=%s", key)
            if isinstance(error, Exception):
                diagnostic = describe(
                    error, stage="アップロードの受信・保存", job_id=key
                )
                if isinstance(error, HTTPException):
                    diagnostic.update(
                        code=f"UPLOAD_HTTP_{error.status_code}",
                        summary=str(error.detail),
                    )
                jobs[key] = Job(
                    status="error", message=readable(diagnostic), diagnostic=diagnostic
                )
            shutil.rmtree(directory, ignore_errors=True)
            if isinstance(error, Exception) and not isinstance(error, HTTPException):
                raise HTTPException(
                    507 if isinstance(error, OSError) else 500, readable(diagnostic)
                ) from error
            raise
        jobs[key] = Job(
            documents={"left": {}, "right": {}},
            facts=facts,
            mode=job_mode,
            threshold=threshold,
        )
        logging.getLogger(__name__).info(
            "Upload accepted: job=%s bytes=%s", key, jobs[key].facts
        )
        pool.submit(process, key, threshold)
        return {"id": key}

    @app.get("/api/jobs/{key}")
    def status(key: str):
        job = get_job(key, False)
        return {
            "status": job.status,
            "message": job.message,
            "diagnostic": job.diagnostic,
            "elapsed_seconds": round(time.monotonic() - job.started, 1),
            "result": job.result if job.status == "done" else None,
            "pages": (
                len(job.documents.get("left", {}).get("slides", []))
                if job.status == "done"
                else 0
            ),
            "sides": list(job.images) if job.status == "done" else [],
            "consistency": job.consistency if job.status == "done" else None,
            "render_status": job.render_status,
            "render_error": job.render_error,
            "mode": job.mode,
        }

    @app.post("/api/jobs/{key}/threshold")
    def recalculate(key: str, body: ThresholdRequest):
        job = get_job(key)
        if "right" not in job.documents:
            raise HTTPException(422, "比較する資料がありません。")
        with job.lock:
            job.result = compare(
                job.documents["left"],
                job.documents["right"],
                body.threshold,
                mappings=job.mappings,
            )
        job.threshold = body.threshold
        return job.result

    @app.get("/api/jobs/{key}/images/{side}/{page}")
    def image(key: str, side: str, page: int):
        job = get_job(key)
        if (
            side not in (*job.images, "diff")
            or side == "diff"
            and "right" not in job.images
            or page < 1
            or page > len(job.images["left"])
        ):
            raise HTTPException(404)
        return FileResponse(work / key / side / f"{page:04}.png")

    def filtered_result(result, categories):
        if categories is None:
            return result
        selected = set(categories.split(",")) - {""}
        if selected - {"format", "layout", "mixed", "other"}:
            raise HTTPException(422, "表示種別が不正です。")
        result = deepcopy(result)
        for page in result["pages"]:
            page["changes"] = [
                c for c in page["changes"] if c.get("highlight", "other") in selected
            ]
        return result

    @app.get("/api/jobs/{key}/report")
    def html_report(key: str, categories: str | None = None):
        job = get_job(key)
        if not job.images:
            raise HTTPException(409, "先に画像を生成してください。")
        if job.result is None:
            raise HTTPException(422, "比較結果がありません。")
        return Response(
            report(filtered_result(job.result, categories), job.images),
            media_type="text/html",
            headers={
                "Content-Disposition": 'attachment; filename="PawapoDiff-report.html"'
            },
        )

    @app.get("/api/jobs/{key}/notes.csv")
    def download_notes(key: str):
        job = get_job(key)
        return Response(
            notes_csv(job.result.get("speaker_notes", [])),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="speaker-notes.csv"'},
        )

    @app.get("/api/jobs/{key}/consistency.csv")
    def download_consistency(key: str):
        return Response(
            consistency_csv(get_job(key).consistency),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="consistency.csv"'},
        )

    @app.get("/api/jobs/{key}/mapping/{page}")
    def get_mapping(key: str, page: int):
        job = get_job(key)
        if not 1 <= page <= len(job.documents["left"]["slides"]):
            raise HTTPException(404, "ページがありません。")
        left, right = (
            job.documents[side]["slides"][page - 1] for side in ("left", "right")
        )

        def shapes(items):
            return [
                {k: item.get(k) for k in ("id", "name", "text", "kind", "box")}
                for item in items
            ]

        return {
            "left": shapes(left),
            "right": shapes(right),
            "overrides": job.mappings.get(page, {}),
            "pairs": [
                {
                    "left": a["id"] if a else None,
                    "right": b["id"] if b else None,
                    "uncertain": uncertain,
                }
                for a, b, uncertain in pair_text_shapes(
                    left, right, job.mappings.get(page)
                )
            ],
        }

    @app.put("/api/jobs/{key}/mapping/{page}")
    def set_mapping(key: str, page: int, body: MappingRequest):
        job = get_job(key)
        get_mapping(key, page)
        with job.lock:
            updated = {**job.mappings, page: body.pairs}
            try:
                result = compare(
                    job.documents["left"],
                    job.documents["right"],
                    job.threshold,
                    mappings=updated,
                )
                consistency = check_consistency(
                    job.documents["left"], job.documents["right"], mappings=updated
                )
            except Exception as error:
                raise HTTPException(422, str(error)) from error
            job.mappings, job.result, job.consistency = updated, result, consistency
        return {"result": result, "consistency": consistency}

    @app.post("/api/jobs/{key}/render")
    def generate_images(key: str):
        job = get_job(key)
        with job.lock:
            if job.render_status in ("running", "done"):
                return {"status": job.render_status}
            job.render_status = "running"
        pool.submit(render_images, key)
        return {"status": "running"}

    @app.get("/api/llm/defaults")
    def defaults():
        return {"prompts": PROMPTS}

    @app.post("/api/llm/models")
    def models(body: Connection):
        try:
            return {"models": list_models(body)}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/llm/model-info")
    def info(body: ModelRequest):
        try:
            return model_info(body, body.model)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/llm/reference")
    def reference(body: ReferenceRequest):
        try:
            return fetch_reference(body.url)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/jobs/{key}/reviews")
    def new_review(key: str, body: ReviewRequest):
        job = get_job(key)
        try:
            endpoint(body, "")
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        count = len(job.documents["left"]["slides"])
        if any(page < 1 or page > count for page in body.pages):
            raise HTTPException(422, "ページ範囲が資料の範囲外です。")
        if any(
            task not in PROMPTS or not text.strip() or len(text) > 50000
            for task, text in body.prompts.items()
        ):
            raise HTTPException(422, "プロンプトの種類または長さが不正です。")
        if any(r["status"] in ("queued", "running") for r in job.reviews.values()):
            raise HTTPException(409, "検査が実行中です。")
        if "layout" in body.tasks and (not body.vision or job.render_status != "done"):
            raise HTTPException(
                422, "画像入力対応モデルを指定し、先に画像を生成してください。"
            )
        review_id = secrets.token_hex(12)
        record = {
            "id": review_id,
            "status": "queued",
            "message": "待機中",
            "results": [],
            "cancel": threading.Event(),
            "model": body.model,
            "provider": body.provider,
            "base_url": body.base_url,
            "tasks": body.tasks,
            "pages": body.pages,
            "vision": body.vision,
            "max_tokens": body.max_tokens,
            "glossary": body.glossary,
            "references": body.references,
            "prompts": {
                task: body.prompts.get(task, PROMPTS[task]) for task in body.tasks
            },
        }
        job.reviews[review_id] = record
        docs = deepcopy(job.documents)
        images = deepcopy(job.images)
        mappings = deepcopy(job.mappings)

        def worker():
            record["status"] = "running"

            def progress(message, item=None):
                if item is not None:
                    record["results"].append(item)
                else:
                    record["message"] = message

            try:
                run_review(
                    body,
                    docs,
                    images,
                    progress,
                    record["cancel"].is_set,
                    mappings=mappings,
                )
                record["status"] = "cancelled" if record["cancel"].is_set() else "done"
                record["message"] = (
                    "中止しました" if record["cancel"].is_set() else "完了"
                )
            except InterruptedError:
                record["status"], record["message"] = "cancelled", "中止しました"
            except Exception as error:
                record["status"], record["message"] = "error", (
                    str(error)
                    if isinstance(error, ValueError)
                    else "LLM検査に失敗しました。接続先とモデルを確認してください。"
                )

        review_pool.submit(worker)
        return {"id": review_id}

    def get_review(key, review_id):
        record = get_job(key).reviews.get(review_id)
        if not record:
            raise HTTPException(404, "検査がありません。")
        return record

    @app.get("/api/jobs/{key}/reviews/{review_id}")
    def review_status(key: str, review_id: str):
        return {k: v for k, v in get_review(key, review_id).items() if k != "cancel"}

    @app.delete("/api/jobs/{key}/reviews/{review_id}")
    def cancel_review(key: str, review_id: str):
        record = get_review(key, review_id)
        record["cancel"].set()
        return {"message": "中止を要求しました。現在のモデル応答後に停止します。"}

    @app.get("/api/jobs/{key}/reviews/{review_id}/download")
    def review_download(key: str, review_id: str):
        return Response(
            json.dumps(review_status(key, review_id), ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="llm-review.json"'},
        )

    static = Path(frontend) if frontend is not None else frontend_directory()
    app.state.frontend = static
    if static.is_dir():
        app.mount("/", BrowserFiles(directory=static, html=True), name="frontend")
    return app
