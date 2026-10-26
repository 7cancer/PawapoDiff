"""Stream multipart files directly into the job directory, without a second copy."""

import math
from pathlib import Path
from fastapi import HTTPException, Request
from starlette.formparsers import MultiPartParser, MultiPartException
from .limits import MAX_UPLOAD_BYTES


class UploadLimitExceeded(MultiPartException):
    pass


class DirectUploadParser(MultiPartParser):
    def __init__(self, headers, stream, directory, limit=MAX_UPLOAD_BYTES):
        async def bounded_stream():
            total = 0
            async for chunk in stream:
                total += len(chunk)
                if total > 2 * limit + 1024**2:
                    raise UploadLimitExceeded(
                        "アップロードの合計サイズが上限を超えています。"
                    )
                yield chunk

        super().__init__(
            headers, bounded_stream(), max_files=2, max_fields=1, max_part_size=65536
        )
        self.directory = Path(directory)
        self.limit = limit
        self.paths = {}
        self.received = {}
        self.header_bytes = 0
        self.completed = False

    def on_part_begin(self):
        super().on_part_begin()
        self.header_bytes = 0

    def _header_size(self, size):
        self.header_bytes += size
        if self.header_bytes > 65536:
            raise MultiPartException("ファイル情報が長すぎます。")

    def on_header_field(self, data, start, end):
        self._header_size(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data, start, end):
        self._header_size(end - start)
        super().on_header_value(data, start, end)

    def on_headers_finished(self):
        super().on_headers_finished()
        part = self._current_part
        if part.file is None:
            if part.field_name != "threshold":
                raise MultiPartException("不明な入力項目です。")
            return
        if part.field_name not in ("left", "right") or part.field_name in self.paths:
            raise MultiPartException("原本と英訳版を1つずつ指定してください。")
        if not part.file.filename or not part.file.filename.lower().endswith(".pptx"):
            raise MultiPartException("pptxファイルを選択してください。")
        part.file.file.close()
        path = self.directory / f"{part.field_name}.pptx"
        disk_file = path.open("x+b")
        self._files_to_close_on_error.append(disk_file)
        part.file.file = disk_file
        self.paths[part.field_name] = path
        self.received[part.field_name] = 0

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            name = self._current_part.field_name
            self.received[name] += end - start
            if self.received[name] > self.limit:
                raise UploadLimitExceeded("1ファイルの上限は15GBです。")
        super().on_part_data(data, start, end)

    def on_end(self):
        self.completed = True
        super().on_end()

    def close(self):
        errors = []
        for file in self._files_to_close_on_error:
            try:
                file.close()
            except OSError as error:
                errors.append(error)
        return errors


async def receive_presentations(request: Request, directory, *, limit=MAX_UPLOAD_BYTES):
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > 2 * limit + 1024**2:
                raise HTTPException(
                    413, "アップロードの合計サイズが上限を超えています。"
                )
        except ValueError:
            raise HTTPException(422, "Content-Lengthが不正です。")
    parser = DirectUploadParser(request.headers, request.stream(), directory, limit)
    completed = False
    try:
        form = await parser.parse()
        if not parser.completed or set(parser.paths) != {"left", "right"}:
            raise MultiPartException(
                "原本と英訳版の2つのpptxファイルを指定してください。"
            )
        try:
            threshold = float(form.get("threshold", "1"))
        except (ValueError, TypeError):
            raise MultiPartException("閾値は0〜100ptです。")
        if not math.isfinite(threshold) or not 0 <= threshold <= 100:
            raise MultiPartException("閾値は0〜100ptです。")
        completed = True
        return threshold
    except UploadLimitExceeded as error:
        raise HTTPException(413, str(error)) from error
    except (MultiPartException, KeyError) as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        raise HTTPException(
            507,
            "アップロードを保存できません。空き容量と保存先の権限を確認してください。",
        ) from error
    finally:
        errors = parser.close()
        if errors and completed:
            raise HTTPException(
                507,
                "アップロードを保存できません。空き容量と保存先の権限を確認してください。",
            )
