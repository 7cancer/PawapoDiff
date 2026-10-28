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
from .engine import inspect, compare
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


class ThresholdRequest(BaseModel):
    threshold: float = Field(ge=0, le=100)


def create_app(token=None, root=None, renderer=render, frontend=None):
    from .diagnostics import configure_diagnostics

    configure_diagnostics()
    token = token or secrets.token_urlsafe(32)
    work = Path(root or tempfile.mkdtemp(prefix="PawapoDiff-"))
    work.mkdir(parents=True, exist_ok=True)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PowerPoint")
    jobs = {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        pool.shutdown(wait=True, cancel_futures=True)
        if root is None:
            shutil.rmtree(work, ignore_errors=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token

    @app.get("/api/health")
    def health():
        return {"status": "ready", "version": __version__}

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
                    job.documents["left"], job.documents["right"], threshold
                )
            for side in job.documents:
                document = "日本語原本" if side == "left" else "英訳版"
                update(
                    f"PowerPointで描画中（全{len(job.documents[side]['slides'])}ページ）"
                )
                options = {"progress": update} if renderer is render else {}
                job.images[side] = renderer(
                    work / key / f"{side}.pptx", work / key / side, **options
                )
                if len(job.images[side]) != len(job.documents[side]["slides"]):
                    raise RenderError("描画されたページ数が一致しません。")
            if "right" in job.images:
                diffdir = work / key / "diff"
                diffdir.mkdir()
                for i, (a, b) in enumerate(
                    zip(job.images["left"], job.images["right"]), 1
                ):
                    document = None
                    update(f"画像差分を作成中 {i}/{len(job.images['left'])}ページ")
                    with Image.open(a) as ia, Image.open(b) as ib:
                        with ia.convert("RGB") as ra, ib.convert("RGB") as rb:
                            with ImageChops.difference(ra, rb) as diff:
                                with ImageEnhance.Contrast(diff).enhance(3) as enhanced:
                                    enhanced.save(diffdir / f"{i:04}.png")
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
        }

    @app.post("/api/jobs/{key}/threshold")
    def recalculate(key: str, body: ThresholdRequest):
        job = get_job(key)
        if "right" not in job.documents:
            raise HTTPException(422, "比較する資料がありません。")
        with job.lock:
            job.result = compare(
                job.documents["left"], job.documents["right"], body.threshold
            )
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

    @app.get("/api/jobs/{key}/report")
    def html_report(key: str):
        job = get_job(key)
        if job.result is None:
            raise HTTPException(422, "比較結果がありません。")
        return Response(
            report(job.result, job.images),
            media_type="text/html",
            headers={
                "Content-Disposition": 'attachment; filename="PawapoDiff-report.html"'
            },
        )

    static = Path(frontend) if frontend is not None else frontend_directory()
    app.state.frontend = static
    if static.is_dir():
        app.mount("/", BrowserFiles(directory=static, html=True), name="frontend")
    return app
