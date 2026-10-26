from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageChops, ImageEnhance
from pydantic import BaseModel, Field
from .engine import inspect, compare, InputError
from .render import render, RenderError
from .uploads import receive_presentations
from .report import report


@dataclass
class Job:
    status: str = "queued"
    message: str = "待機中"
    result: dict | None = None
    documents: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


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
            logging.getLogger(__name__).exception("Unexpected job processing failure")
            job.status, job.message = (
                "error",
                "処理に失敗しました。ファイル形式と空き容量を確認してください。",
            )

    @app.get("/api/diagnostics")
    def diagnostics():
        from .diagnostics import diagnostic_files

        files = diagnostic_files()
        if not files:
            raise HTTPException(503, "診断ログの保存先を作成できません。")
        archive = work / f"diagnostics-{secrets.token_hex(8)}.zip"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
            for path in files:
                try:
                    bundle.write(path, path.name)
                except OSError:
                    continue
        return FileResponse(
            archive, filename="PawapoDiff-diagnostics.zip", media_type="application/zip"
        )

    @app.post("/api/jobs")
    async def new_job(request: Request):
        key = secrets.token_hex(12)
        directory = work / key
        directory.mkdir()
        try:
            threshold = await receive_presentations(request, directory)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        jobs[key] = Job(documents={"left": {}, "right": {}})
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

    import sys

    static = (
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        / "frontend"
        / "dist"
    )
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app
