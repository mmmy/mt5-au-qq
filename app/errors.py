from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, *, code: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class ValidationError(AppError):
    def __init__(self, message: str, *, code: str = "INVALID_INPUT") -> None:
        super().__init__(message, code=code, status_code=400)


class TemplateError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_ALERT_TEMPLATE", status_code=500)


class AlertNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__("警报不存在，或不是本项目创建的警报", code="ALERT_NOT_FOUND", status_code=404)


class TradingViewError(AppError):
    def __init__(self, message: str, *, code: str = "TRADINGVIEW_ERROR", status_code: int = 502) -> None:
        super().__init__(message, code=code, status_code=status_code)


class TradingDisabledError(AppError):
    def __init__(self, message: str = "交易执行当前未启用") -> None:
        super().__init__(message, code="TRADING_DISABLED", status_code=409)


class Mt5NotReadyError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="MT5_NOT_READY", status_code=409)
