"""Channel-aware notification handler.

Sends formatted messages to external channels: Slack, Teams, Discord,
Telegram, WhatsApp (Meta Cloud API), PagerDuty, email (SendGrid/Mailgun/SMTP),
and generic webhooks.

Config values support three resolution sources:
  1. Static text typed directly in the sidebar
  2. {{ env.SECRET_NAME }} resolved from the tenant vault (handled by
     dispatch_node's resolve_config_env_vars before this handler runs)
  3. {{ trigger.field }} or {{ node_N.field }} resolved from the execution
     context at runtime (handled by _resolve_config_expressions below)
"""

from __future__ import annotations

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CHANNEL_BUILDERS = {}


def _channel(name: str):
    """Decorator to register a channel payload builder."""
    def wrapper(fn):
        _CHANNEL_BUILDERS[name] = fn
        return fn
    return wrapper


# ---------------------------------------------------------------------------
# Config expression resolution (pass 2 — after env var resolution)
# ---------------------------------------------------------------------------

def _resolve_config_expressions(config: dict, context: dict[str, Any]) -> dict:
    """Resolve Jinja2 expressions in config string values against execution context.

    Runs AFTER resolve_config_env_vars (which handles {{ env.* }} from the
    tenant vault).  This second pass resolves {{ trigger.* }}, {{ node_*.* }},
    and any other Jinja2 expressions in fields like destination, chatId,
    phoneNumber, to, subject, etc.
    """
    from app.engine.prompt_template import render_prompt

    resolved = {}
    for key, value in config.items():
        if isinstance(value, str) and ("{{" in value or "{%" in value):
            resolved[key] = render_prompt(value, context)
        else:
            resolved[key] = value
    return resolved


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def _handle_notification(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Execute a Notification node.

    1. Resolve remaining Jinja2 expressions in config fields
       (messageTemplate is resolved here too — no separate render pass needed)
    2. Build a channel-specific payload
    3. Send via HTTP
    4. Return structured output for downstream nodes
    """
    config = node_data.get("config", {})
    config = _resolve_config_expressions(config, context)

    channel = config.get("channel", "generic_webhook")
    destination = config.get("destination", "")
    message = config.get("messageTemplate", "")

    if not destination:
        logger.error("Notification node: destination is empty after resolution")
        return {"success": False, "error": "destination is empty", "channel": channel}

    builder = _CHANNEL_BUILDERS.get(channel)
    if not builder:
        logger.error("Notification node: unknown channel '%s'", channel)
        return {"success": False, "error": f"unknown channel: {channel}", "channel": channel}

    try:
        url, payload, headers = builder(message, config)
    except Exception as exc:
        logger.error("Notification node: payload build failed for %s: %s", channel, exc)
        return {"success": False, "error": f"payload build error: {exc}", "channel": channel}

    logger.info(
        "Notification [%s]: url=%s message_len=%d",
        channel, url[:80] if url else "(none)", len(message),
    )

    result = _send_notification(url, payload, headers, channel, config)
    result["channel"] = channel
    result["message_preview"] = message[:200]
    return result


# ---------------------------------------------------------------------------
# HTTP send
# ---------------------------------------------------------------------------

def _send_notification(
    url: str,
    payload: dict | str | None,
    headers: dict,
    channel: str,
    config: dict,
) -> dict[str, Any]:
    """POST (or other method) the payload and return a structured result."""
    method = "POST"
    if channel == "generic_webhook":
        method = config.get("httpMethod", "POST")
    if channel == "email" and config.get("emailProvider") == "smtp":
        return _send_smtp(payload, config)

    try:
        body: str | bytes | None
        if isinstance(payload, dict):
            body = json.dumps(payload)
        else:
            body = payload

        resp = httpx.request(
            method=method,
            url=url,
            content=body,
            headers=headers,
            timeout=30.0,
        )

        success = 200 <= resp.status_code < 300
        if not success:
            logger.warning(
                "Notification [%s]: HTTP %d — %s",
                channel, resp.status_code, resp.text[:500],
            )

        return {
            "success": success,
            "status_code": resp.status_code,
            "response_body": resp.text[:2000],
        }
    except httpx.HTTPError as exc:
        logger.error("Notification [%s]: HTTP error — %s", channel, exc)
        return {"success": False, "error": str(exc)}


def _send_smtp(payload: dict | None, config: dict) -> dict[str, Any]:
    """Send email via SMTP (payload contains pre-built MIMEMultipart parts)."""
    try:
        smtp_host = config.get("destination", "localhost")
        smtp_port = int(config.get("smtpPort", 587))
        smtp_user = config.get("smtpUser", "")
        smtp_pass = config.get("smtpPass", "")

        msg = MIMEMultipart("alternative")
        msg["From"] = payload.get("from", "")
        msg["To"] = payload.get("to", "")
        msg["Subject"] = payload.get("subject", "")
        msg.attach(MIMEText(payload.get("body", ""), "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.sendmail(msg["From"], msg["To"].split(","), msg.as_string())

        return {"success": True, "status_code": 250}
    except Exception as exc:
        logger.error("Notification [email/smtp]: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Channel payload builders
# ---------------------------------------------------------------------------

@_channel("slack_webhook")
def _build_slack(message: str, config: dict) -> tuple[str, dict, dict]:
    payload: dict[str, Any] = {
        "text": message,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": message}}],
    }
    if config.get("username"):
        payload["username"] = config["username"]
    if config.get("iconEmoji"):
        payload["icon_emoji"] = config["iconEmoji"]
    return config["destination"], payload, {"Content-Type": "application/json"}


@_channel("teams_webhook")
def _build_teams(message: str, config: dict) -> tuple[str, dict, dict]:
    payload: dict[str, Any] = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": message[:50],
        "text": message,
    }
    if config.get("title"):
        payload["title"] = config["title"]
    if config.get("themeColor"):
        payload["themeColor"] = config["themeColor"]
    return config["destination"], payload, {"Content-Type": "application/json"}


@_channel("discord_webhook")
def _build_discord(message: str, config: dict) -> tuple[str, dict, dict]:
    payload: dict[str, Any] = {"content": message}
    if config.get("username"):
        payload["username"] = config["username"]
    if config.get("avatarUrl"):
        payload["avatar_url"] = config["avatarUrl"]
    return config["destination"], payload, {"Content-Type": "application/json"}


@_channel("telegram")
def _build_telegram(message: str, config: dict) -> tuple[str, dict, dict]:
    token = config.get("destination", "")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": config.get("chatId", ""),
        "text": message,
        "parse_mode": config.get("parseMode", "HTML"),
    }
    return url, payload, {"Content-Type": "application/json"}


@_channel("whatsapp")
def _build_whatsapp(message: str, config: dict) -> tuple[str, dict, dict]:
    access_token = config.get("destination", "")
    phone_number_id = config.get("phoneNumberId", "")
    url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"

    template_name = config.get("templateName", "")
    if template_name:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": config.get("phoneNumber", ""),
            "type": "template",
            "template": {"name": template_name, "language": {"code": "en"}},
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": config.get("phoneNumber", ""),
            "type": "text",
            "text": {"body": message},
        }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    return url, payload, headers


@_channel("pagerduty")
def _build_pagerduty(message: str, config: dict) -> tuple[str, dict, dict]:
    url = "https://events.pagerduty.com/v2/enqueue"
    routing_key = config.get("destination", "")
    payload = {
        "routing_key": routing_key,
        "event_action": config.get("eventAction", "trigger"),
        "payload": {
            "summary": message[:1024],
            "severity": config.get("severity", "warning"),
            "source": config.get("pdSource", "orchestrator"),
        },
    }
    return url, payload, {"Content-Type": "application/json"}


@_channel("email")
def _build_email(message: str, config: dict) -> tuple[str, dict | str, dict]:
    from_addr = config.get("from", "")
    to_addrs = config.get("to", "")
    subject = config.get("subject", "")
    provider = config.get("emailProvider", "sendgrid")

    if provider == "smtp":
        return "", {"from": from_addr, "to": to_addrs, "subject": subject, "body": message}, {}

    if provider == "sendgrid":
        api_key = config.get("destination", "")
        url = "https://api.sendgrid.com/v3/mail/send"
        payload = {
            "personalizations": [{"to": [{"email": e.strip()} for e in to_addrs.split(",") if e.strip()]}],
            "from": {"email": from_addr},
            "subject": subject,
            "content": [{"type": "text/plain", "value": message}],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        return url, payload, headers

    if provider == "mailgun":
        import base64
        api_key = config.get("destination", "")
        domain = from_addr.split("@")[-1] if "@" in from_addr else "example.com"
        url = f"https://api.mailgun.net/v3/{domain}/messages"
        payload_str = (
            f"from={from_addr}&to={to_addrs}"
            f"&subject={subject}&text={message}"
        )
        encoded = base64.b64encode(f"api:{api_key}".encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        return url, payload_str, headers

    return "", {"error": f"unknown email provider: {provider}"}, {}


@_channel("generic_webhook")
def _build_generic(message: str, config: dict) -> tuple[str, dict, dict]:
    url = config.get("destination", "")
    payload = {"message": message}
    headers = {"Content-Type": "application/json"}
    custom_headers = config.get("httpHeaders", {})
    if isinstance(custom_headers, dict):
        headers.update(custom_headers)
    return url, payload, headers
