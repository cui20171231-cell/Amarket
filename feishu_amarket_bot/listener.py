"""Amarket Bot: Feishu WebSocket message listener."""

from __future__ import annotations

import argparse
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import lark_oapi as lark
from dotenv import load_dotenv
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
LOG_DIR = BASE_DIR / "logs"
COMMAND_PREFIX = "/A"


def configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("amarket_feishu_bot")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        file_handler = RotatingFileHandler(
            LOG_DIR / "listener.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(console)
        logger.addHandler(file_handler)

    return logger


def load_credentials() -> tuple[str, str]:
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()

    if not app_id or not app_secret:
        raise RuntimeError(
            "缺少本机密钥配置，请先运行 configure_credentials.ps1。"
        )
    if not app_id.startswith("cli_"):
        raise RuntimeError("APP_ID 格式不正确，通常应以 cli_ 开头。")
    return app_id, app_secret


def message_text(raw_content: str | None) -> str:
    if not raw_content:
        return ""
    try:
        parsed = json.loads(raw_content)
        if isinstance(parsed, dict):
            value = parsed.get("text")
            if isinstance(value, str):
                raw_content = value
    except (json.JSONDecodeError, TypeError):
        pass
    return " ".join(str(raw_content).split())[:1000]


def command_parameter(text: str) -> str | None:
    if text == COMMAND_PREFIX:
        return ""
    prefix_with_space = COMMAND_PREFIX + " "
    if text.startswith(prefix_with_space):
        return text[len(prefix_with_space) :].strip()
    return None


def remove_mentions(text: str, mentions: object) -> str:
    cleaned = text
    for mention in mentions or []:
        key = getattr(mention, "key", "")
        if key:
            cleaned = cleaned.replace(key, " ")
    return " ".join(cleaned.split())


def build_event_handler(logger: logging.Logger) -> lark.EventDispatcherHandler:
    def on_message(data: P2ImMessageReceiveV1) -> None:
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        sender_id = getattr(getattr(sender, "sender_id", None), "open_id", "")

        text = message_text(getattr(message, "content", ""))
        logger.info(
            "收到飞书消息 | chat_type=%s | message_type=%s | chat_id=%s | sender=%s | text=%s",
            getattr(message, "chat_type", ""),
            getattr(message, "message_type", ""),
            getattr(message, "chat_id", ""),
            sender_id,
            text,
        )

        command_text = remove_mentions(
            text,
            getattr(message, "mentions", None),
        )
        parameter = command_parameter(command_text)
        if parameter is not None:
            logger.info(
                "识别到 Amarket 触发指令 | prefix=%s | parameter=%s",
                COMMAND_PREFIX,
                parameter,
            )

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Amarket Bot 飞书长连接监听器")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查本机配置，不连接飞书",
    )
    args = parser.parse_args()

    logger = configure_logging()
    try:
        app_id, app_secret = load_credentials()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 2

    if args.check:
        logger.info("配置检查通过（APP_ID 和 APP_SECRET 均未显示）。")
        return 0

    logger.info("Amarket Bot 监听器启动，正在建立飞书长连接……")
    handler = build_event_handler(logger)
    client = lark.ws.Client(
        app_id,
        app_secret,
        log_level=lark.LogLevel.WARNING,
        event_handler=handler,
        auto_reconnect=True,
    )

    try:
        client.start()
    except KeyboardInterrupt:
        logger.info("监听器已由用户停止。")
        return 0
    except Exception:
        logger.exception("监听器异常退出。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
