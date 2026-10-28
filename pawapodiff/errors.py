"""Readable, serializable failures shared by the API and the Office worker."""

import errno


class DiagnosticError(Exception):
    def __init__(self, message, *, code="PROCESSING_FAILED", actions=(), **context):
        super().__init__(message)
        self.diagnostic = {
            "code": code,
            "summary": message,
            "actions": list(actions)
            or ["診断ログを保存し、エラーコードと一緒に共有してください。"],
            **context,
        }


class InputError(DiagnosticError, ValueError):
    pass


def describe(error, **context):
    data = dict(getattr(error, "diagnostic", {}))
    if not data:
        code, summary, actions = (
            "UNEXPECTED_ERROR",
            "処理中に予期しないエラーが発生しました。",
            ["診断ログを保存して共有してください。"],
        )
        if isinstance(error, MemoryError):
            code, summary, actions = (
                "MEMORY_EXHAUSTED",
                "解析または描画に必要なメモリが不足しました。",
                ["他のアプリを終了するか、資料を分割してお試しください。"],
            )
        elif isinstance(error, OSError):
            if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112:
                code, summary = (
                    "DISK_FULL",
                    "作業フォルダーの空き容量が不足しています。",
                )
                actions = [
                    "Windowsの一時フォルダーがあるドライブの空き容量を増やしてください。"
                ]
            elif isinstance(error, PermissionError):
                code, summary = (
                    "ACCESS_DENIED",
                    "ファイルまたは作業フォルダーへのアクセスが拒否されました。",
                )
                actions = [
                    "ファイルのアクセス権と、会社のセキュリティ製品による制限を確認してください。"
                ]
            elif isinstance(error, FileNotFoundError):
                code, summary = "FILE_MISSING", "処理に必要なファイルが見つかりません。"
                actions = [
                    "OneDrive等の資料はローカルへ完全に保存してから選択してください。"
                ]
        data = {"code": code, "summary": summary, "actions": actions}
    data.update({key: value for key, value in context.items() if value is not None})
    chain, current, seen = [], error, set()
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        chain.append({"type": type(current).__name__, "message": str(current)[:900]})
        current = current.__cause__ or current.__context__
    data.setdefault("exceptions", chain)
    # Office details are carried across the process boundary; don't replace them.
    data.setdefault(
        "technical",
        " <- ".join(f"{item['type']}: {item['message']}" for item in chain)[:1500],
    )
    return data


def readable(data):
    location = " / ".join(
        str(data[key]) for key in ("document", "stage") if data.get(key)
    )
    if data.get("page"):
        location += f" / ページ{data['page']}"
    return (
        f"{data['summary']}\n処理箇所: {location or '不明'}\nエラーコード: {data['code']}\n"
        + "\n".join(data.get("actions", []))
    )


def office_diagnostic(error, stage):
    if isinstance(error, (OSError, MemoryError)):
        return describe(error, stage=stage)
    hresult = getattr(error, "hresult", 0)
    codes = {hresult & 0xFFFFFFFF} if isinstance(hresult, int) else set()
    info = getattr(error, "excepinfo", None)
    if info and len(info) > 5 and isinstance(info[5], int):
        codes.add(info[5] & 0xFFFFFFFF)
    code, summary = "OFFICE_OPERATION_FAILED", "PowerPointの処理に失敗しました。"
    actions = [
        "PowerPointで対象資料を開き、警告・サインイン・修復要求の有無を確認してください。",
        "解決しない場合は診断ログを保存してください。",
    ]
    if codes & {0x80040154, 0x800401F3}:
        code, summary = (
            "POWERPOINT_NOT_INSTALLED",
            "Windows版PowerPointの自動操作機能を利用できません。",
        )
        actions = [
            "デスクトップ版PowerPointを導入・起動してライセンス認証してください。導入済みの場合はOfficeの修復をお試しください。"
        ]
    elif codes & {0x80080005}:
        code, summary = (
            "POWERPOINT_START_FAILED",
            "PowerPointの自動操作用インスタンスを起動できませんでした。",
        )
        actions = [
            "PowerPointを手動で起動して初回設定・サインイン・ライセンス認証を済ませてください。",
            "解決しない場合は会社の管理者にOfficeの自動操作制限を確認してください。",
        ]
    elif codes & {0x80010001, 0x8001010A}:
        code, summary = (
            "POWERPOINT_BUSY",
            "PowerPointが他の処理やダイアログで操作を受け付けていません。",
        )
        actions = [
            "PowerPointのダイアログを閉じ、起動・保存処理が終わってから再実行してください。"
        ]
    elif codes & {0x800706BA, 0x80010108}:
        code, summary = (
            "POWERPOINT_DISCONNECTED",
            "処理中にPowerPointとの接続が切れました。",
        )
        actions = [
            "開いている資料を保存し、PowerPointを再起動してください。メモリ不足やOfficeの異常終了も確認してください。"
        ]
    elif codes & {0x8007000E, 0x80070008}:
        code, summary = (
            "OFFICE_MEMORY_EXHAUSTED",
            "PowerPointのメモリが不足しています。",
        )
        actions = [
            "他の資料やアプリを閉じるか、資料を分割してください。32bit版Officeの場合は64bit版Officeもご検討ください。"
        ]
    elif codes & {0x80070070}:
        code, summary, actions = (
            "DISK_FULL",
            "PowerPointの作業領域の空き容量が不足しています。",
            ["一時フォルダーのあるドライブの空き容量を増やしてください。"],
        )
    elif codes & {0x80070005}:
        code, summary = (
            "OFFICE_ACCESS_DENIED",
            "PowerPointによるファイルへのアクセスが拒否されました。",
        )
        actions = [
            "会社のセキュリティ制限、保護ビュー、資料のアクセス権・権限保護（IRM）を確認してください。保護は自動解除しません。"
        ]
    elif "開く" in stage:
        code, summary = (
            "POWERPOINT_OPEN_FAILED",
            "資料の構造は読み取れましたが、PowerPointで一時コピーを開けませんでした。",
        )
        actions = [
            "PowerPointで資料を直接開き、パスワード・権限保護・修復要求・保護ビュー・ライセンス認証の表示を確認してください。",
            "開ける場合はローカルの別名ファイルに保存してお試しください。拡張子を変えるだけでは解決しません。",
        ]
    return {"code": code, "summary": summary, "actions": actions, "stage": stage}
