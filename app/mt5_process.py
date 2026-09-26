"""One spawned process owns one MetaTrader5 module connection on Windows."""
from __future__ import annotations

import multiprocessing as mp
from dataclasses import asdict
from threading import Lock

from app.config import Mt5ClientConfig
from app.mt5_gateway import Mt5Gateway, Mt5ExecutionError


def _serve(connection, config: Mt5ClientConfig, gateway_factory=Mt5Gateway) -> None:
    options = asdict(config)
    options.pop("client_id")
    gateway = gateway_factory(**options)
    try:
        while True:
            operation, argument = connection.recv()
            if operation == "stop":
                break
            try:
                result = gateway.status() if operation == "status" else gateway.execute(argument)
                connection.send((True, result))
            except Exception as exc:
                connection.send((False, str(exc)))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        gateway.shutdown()
        connection.close()


class ProcessMt5Gateway:
    def __init__(self, config: Mt5ClientConfig, *, gateway_factory=Mt5Gateway, timeout: float = 75):
        self.config = config
        self.gateway_factory = gateway_factory
        self.timeout = timeout
        self._process = None
        self._connection = None
        self._lock = Lock()

    def _discard(self):
        if self._process:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=5)
            self._process.close()
        if self._connection:
            self._connection.close()
        self._process = self._connection = None

    def _call(self, operation, argument=None):
        with self._lock:
            if self._process is None:
                context = mp.get_context("spawn")
                self._connection, child = context.Pipe()
                self._process = context.Process(target=_serve,
                    args=(child, self.config, self.gateway_factory), name=f"mt5-{self.config.client_id}", daemon=True)
                self._process.start()
                child.close()
            try:
                self._connection.send((operation, argument))
                if not self._connection.poll(min(self.timeout, 6) if operation == "status" else self.timeout):
                    raise Mt5ExecutionError("MT5 响应超时，交易结果可能不明确；请核对终端，不会自动重试")
                success, result = self._connection.recv()
            except (EOFError, BrokenPipeError, OSError, Mt5ExecutionError) as exc:
                self._discard()
                raise Mt5ExecutionError(str(exc) or "MT5 执行进程退出，需核对交易结果") from exc
            if not success:
                raise Mt5ExecutionError(result)
            return result

    def status(self):
        return self._call("status")

    def execute(self, action):
        return self._call("execute", action)

    def shutdown(self):
        with self._lock:
            if self._process and self._process.is_alive():
                try:
                    self._connection.send(("stop", None))
                    self._process.join(timeout=3)
                except (OSError, EOFError):
                    pass
            self._discard()
