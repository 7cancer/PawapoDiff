import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
from zipfile import ZipFile
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageChops, ImageEnhance
from pydantic import BaseModel, Field
from .engine import inspect, compare, InputError
from .render import render, export_images, RenderError
from .report import report


@dataclass
class Job:
    status: str = "queued"
    message: str = "待機中"
    result: dict | None = None
    documents: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class ExportRequest(BaseModel):
    side: str = "left"
    preset: str = "FHD"
    crop: list[float] | None = None
    width: int = Field(default=1920, ge=64, le=7680)


class ThresholdRequest(BaseModel):
    threshold: float = Field(ge=0, le=100)


def create_app(token=None, root=None, renderer=render):
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
        try:
            job.status, job.message = "running", "資料の構造を解析中"
            for side in job.documents:
                job.documents[side] = inspect(work / key / f"{side}.pptx")
            if "right" in job.documents:
                job.result = compare(
                    job.documents["left"], job.documents["right"], threshold
                )
            job.message = "PowerPointでスライドを描画中"
            for side in job.documents:
                job.images[side] = renderer(
                    work / key / f"{side}.pptx", work / key / side
                )
                if len(job.images[side]) != len(job.documents[side]["slides"]):
                    raise RenderError("描画されたページ数が一致しません。")
            if "right" in job.images:
                diffdir = work / key / "diff"
                diffdir.mkdir()
                for i, (a, b) in enumerate(
                    zip(job.images["left"], job.images["right"]), 1
                ):
                    with Image.open(a) as ia, Image.open(b) as ib:
                        diff = ImageChops.difference(
                            ia.convert("RGB"), ib.convert("RGB")
                        )
                        ImageEnhance.Contrast(diff).enhance(3).save(
                            diffdir / f"{i:04}.png"
                        )
            job.status, job.message = "done", "完了"
        except (InputError, RenderError) as e:
            job.status, job.message = "error", str(e)
        except Exception:
            job.status, job.message = (
                "error",
                "処理に失敗しました。ファイル形式と空き容量を確認してください。",
            )

    async def save_upload(upload, dest):
        if not upload.filename or not upload.filename.lower().endswith(".pptx"):
            raise HTTPException(422, "pptxファイルを選択してください。")
        size = 0
        with dest.open("wb") as f:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > 800 * 1024**2:
                    raise HTTPException(413, "1ファイルの上限は800MBです。")
                f.write(chunk)
        await upload.close()

    @app.post("/api/jobs")
    async def new_job(
        left: UploadFile = File(...),
        right: UploadFile | None = File(None),
        threshold: float = Form(1),
    ):
        if not 0 <= threshold <= 100:
            raise HTTPException(422, "閾値は0〜100ptです。")
        key = secrets.token_hex(12)
        directory = work / key
        directory.mkdir()
        try:
            await save_upload(left, directory / "left.pptx")
            documents = {"left": {}}
            if right is not None:
                await save_upload(right, directory / "right.pptx")
                documents["right"] = {}
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        jobs[key] = Job(documents=documents)
        pool.submit(process, key, threshold)
        return {"id": key}

    @app.get("/api/jobs/{key}")
    def status(key: str):
        job = get_job(key, False)
        return {
            "status": job.status,
            "message": job.message,
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

    @app.post("/api/jobs/{key}/export")
    async def export(key: str, body: ExportRequest):
        job = get_job(key)
        if body.side not in job.images or body.preset not in (
            "HD",
            "FHD",
            "4K",
            "custom",
        ):
            raise HTTPException(422, "出力設定が不正です。")

        def run():
            exportdir = work / key / secrets.token_hex(8)
            try:
                files = export_images(
                    job.images[body.side], exportdir, body.preset, body.crop, body.width
                )
                archive = exportdir.with_suffix(".zip")
                with ZipFile(archive, "w") as z:
                    for file in files:
                        z.write(file, file.name)
                return archive
            except InputError as e:
                raise HTTPException(422, str(e)) from e

        path = await asyncio.get_running_loop().run_in_executor(pool, run)
        return FileResponse(path, filename="PawapoDiff-images.zip")

    import sys

    static = (
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        / "frontend"
        / "dist"
    )
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app
