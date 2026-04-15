# Notification Node — User Guide

The Notification node sends formatted messages to external channels as part of a workflow. Use it for alerting, status updates, escalation messages, or any workflow step that needs to push information to humans via chat, email, or incident platforms.

For the config schema reference, see [Node Types — Notification](node-types.md#notification-notification).

---

## Quick start

1. Drag a **Notification** node from the palette (Notifications category, bell icon) onto the canvas
2. Connect it downstream from the node whose output you want to notify about
3. Select the node and configure:
   - **Channel** — pick where to send (Slack, Teams, Discord, Telegram, WhatsApp, PagerDuty, email, or generic webhook)
   - **Destination** — paste the webhook URL, or reference a vault secret with `{{ env.SLACK_WEBHOOK }}`
   - **Message Template** — write the message body using Jinja2 syntax: `{{ trigger.field }}` or `{{ node_2.response }}`
4. Run the workflow — the notification is sent when the node executes

---

## Config field value sources

Every string config field on the Notification node supports three ways to provide a value. You can mix these freely.

### Static values

Type the value directly in the field. No `{{ }}` needed.

```
https://hooks.slack.com/services/T00000/B00000/xxxxxxxxxx
```

### Vault secrets (`{{ env.* }}`)

Reference a secret stored in the tenant vault (configured in the Secrets UI accessible from the toolbar). The value is decrypted at runtime.

```
{{ env.SLACK_OPS_WEBHOOK }}
```

This is the recommended approach for API keys, bot tokens, and webhook URLs that shouldn't be visible in the workflow graph.

### Runtime expressions (`{{ trigger.* }}`, `{{ node_N.* }}`)

Pull values from the incoming trigger payload or from upstream node outputs. Resolved at execution time.

```
{{ trigger.recipient_email }}
{{ node_3.extracted_phone }}
```

### Mixed expressions

Combine static text with vault secrets or runtime values:

```
https://api.telegram.org/bot{{ env.TELEGRAM_TOKEN }}/sendMessage
Alert for {{ trigger.service }}: {{ node_4.response }}
```

---

## Channel setup guides

### Slack (`slack_webhook`)

**Prerequisites:** Create an Incoming Webhook in your Slack workspace ([Slack docs](https://api.slack.com/messaging/webhooks)).

| Field | Value |
|-------|-------|
| **Channel** | `slack_webhook` |
| **Destination** | Webhook URL (e.g. `https://hooks.slack.com/services/T00/B00/xxx`) or `{{ env.SLACK_WEBHOOK }}` |
| **Message Template** | Markdown text — Slack renders `*bold*`, `_italic_`, `` `code` ``, and `>` blockquotes |
| **Username** | Display name for the bot (default: `Orchestrator`) |
| **Icon Emoji** | Slack emoji code (default: `:robot_face:`) |

**Example message template:**

```
*Workflow completed* :white_check_mark:
Service: {{ trigger.service }}
Result: {{ node_3.response }}
```

---

### Microsoft Teams (`teams_webhook`)

**Prerequisites:** Create an Incoming Webhook connector in a Teams channel ([Teams docs](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook)).

| Field | Value |
|-------|-------|
| **Channel** | `teams_webhook` |
| **Destination** | Connector URL or `{{ env.TEAMS_WEBHOOK }}` |
| **Message Template** | Plain text or basic HTML — rendered inside a MessageCard |
| **Title** | Optional card title |
| **Theme Color** | Hex color for the card accent strip (default: `0076D7`) |

---

### Discord (`discord_webhook`)

**Prerequisites:** Create a webhook in a Discord channel (Channel Settings → Integrations → Webhooks).

| Field | Value |
|-------|-------|
| **Channel** | `discord_webhook` |
| **Destination** | Discord webhook URL or `{{ env.DISCORD_WEBHOOK }}` |
| **Message Template** | Markdown text — Discord renders standard Markdown |
| **Username** | Override bot display name |
| **Avatar Url** | Override bot avatar image |

**Note:** Discord has a 2000-character limit per message. If your template might exceed this, truncate in the template or use a Code node upstream to shorten the text.

---

### Telegram (`telegram`)

**Prerequisites:** Create a bot via [@BotFather](https://t.me/BotFather) and get the bot token. Find the chat ID by adding the bot to a group and calling `getUpdates`.

| Field | Value |
|-------|-------|
| **Channel** | `telegram` |
| **Destination** | Bot token — use `{{ env.TELEGRAM_BOT_TOKEN }}` (recommended) |
| **Message Template** | Text formatted per the selected parse mode |
| **Chat Id** | Numeric chat or group ID. Static, `{{ env.TELEGRAM_CHAT_ID }}`, or `{{ trigger.chat_id }}` |
| **Parse Mode** | `HTML` (default), `Markdown`, or `MarkdownV2` |

**Example with HTML parse mode:**

```
<b>Alert:</b> {{ trigger.alert_name }}
Severity: {{ trigger.severity }}
Details: {{ node_2.response }}
```

**Tip:** The bot token goes in `destination`, not the API URL. The handler constructs the full `api.telegram.org/bot{token}/sendMessage` URL automatically.

---

### WhatsApp (`whatsapp`)

**Prerequisites:** Set up a Meta WhatsApp Business account and get an access token and phone number ID from the [Meta for Developers dashboard](https://developers.facebook.com/).

| Field | Value |
|-------|-------|
| **Channel** | `whatsapp` |
| **Destination** | Permanent access token — use `{{ env.WHATSAPP_TOKEN }}` |
| **Phone Number** | Recipient in E.164 format (e.g. `+14155551234`). Can use `{{ trigger.phone }}` |
| **Phone Number Id** | Your WhatsApp Business phone number ID from Meta dashboard |
| **Template Name** | Approved template name for business-initiated messages; leave empty for session replies |
| **Message Template** | Only used for session replies (user-initiated within 24 h). Template messages use the `templateName` field instead |

**Two message modes:**

- **Business-initiated (outside 24 h window):** Set `templateName` to an approved Meta message template. The `messageTemplate` field is ignored.
- **Session reply (within 24 h of user message):** Leave `templateName` empty. The rendered `messageTemplate` is sent as a free-form text message.

---

### PagerDuty (`pagerduty`)

**Prerequisites:** Create an Events API v2 integration in a PagerDuty service and get the routing key.

| Field | Value |
|-------|-------|
| **Channel** | `pagerduty` |
| **Destination** | Integration/routing key — use `{{ env.PAGERDUTY_ROUTING_KEY }}` |
| **Message Template** | Alert summary (truncated to 1024 chars by PagerDuty) |
| **Severity** | `critical`, `error`, `warning` (default), or `info` |
| **Event Action** | `trigger` (default), `acknowledge`, or `resolve` |
| **Pd Source** | Source identifier (default: `orchestrator`) |

**Tip:** Use a Condition node upstream to set severity dynamically based on workflow data, then reference `{{ node_X.severity_level }}` in the severity field.

---

### Email (`email`)

Supports three providers: **SendGrid**, **Mailgun**, and **SMTP**.

#### SendGrid

| Field | Value |
|-------|-------|
| **Channel** | `email` |
| **Email Provider** | `sendgrid` |
| **Destination** | SendGrid API key — use `{{ env.SENDGRID_API_KEY }}` |
| **To** | Recipient email(s), comma-separated. Can use `{{ trigger.email }}` |
| **Subject** | Jinja2 template (e.g. `Alert: {{ trigger.service }} — {{ node_3.status }}`) |
| **From** | Verified sender email address |
| **Message Template** | Plain text email body |

#### Mailgun

| Field | Value |
|-------|-------|
| **Channel** | `email` |
| **Email Provider** | `mailgun` |
| **Destination** | Mailgun API key — use `{{ env.MAILGUN_API_KEY }}` |
| **To** | Recipient email(s) |
| **Subject** | Jinja2 template |
| **From** | Sender email (domain is auto-extracted for the API URL) |
| **Message Template** | Plain text email body |

#### SMTP

| Field | Value |
|-------|-------|
| **Channel** | `email` |
| **Email Provider** | `smtp` |
| **Destination** | SMTP server hostname (e.g. `smtp.gmail.com`) or `{{ env.SMTP_HOST }}` |
| **Smtp Port** | Server port (default: `587` for STARTTLS) |
| **Smtp User** | Login username — use `{{ env.SMTP_USER }}` |
| **Smtp Pass** | Login password — use `{{ env.SMTP_PASS }}` |
| **To** | Recipient email(s) |
| **Subject** | Jinja2 template |
| **From** | Sender email address |
| **Message Template** | Plain text email body |

**Note:** SMTP uses STARTTLS on the configured port. SSL-only servers (port 465) are not currently supported.

---

### Generic Webhook (`generic_webhook`)

Send a JSON payload to any URL. Use this for services not covered by the other channels, or for custom integrations.

| Field | Value |
|-------|-------|
| **Channel** | `generic_webhook` |
| **Destination** | Full URL (e.g. `https://my-api.example.com/hooks/alert`) |
| **Message Template** | Text that becomes the `message` field in the JSON body |
| **Http Method** | `POST` (default), `PUT`, or `PATCH` |
| **Http Headers** | JSON object of custom headers (e.g. `{"Authorization": "Bearer xxx"}`) |

**Payload format:**

```json
{
  "message": "<rendered messageTemplate>"
}
```

Custom headers are merged with the default `Content-Type: application/json`.

---

## Conditional UI fields (`visibleWhen`)

The Notification node uses a generic `visibleWhen` feature in the config form. Channel-specific fields are only shown when the matching channel is selected. For example, `chatId` and `parseMode` only appear when `channel` is set to `telegram`.

This keeps the sidebar clean — you only see fields relevant to the selected channel.

---

## Downstream usage

The Notification node outputs a structured result that downstream nodes can reference:

| Output field | Type | Description |
|-------------|------|-------------|
| `success` | boolean | `true` if the HTTP response was 2xx |
| `channel` | string | The channel that was used |
| `status_code` | number | HTTP status code from the target service |
| `message_preview` | string | First 200 characters of the rendered message |
| `response_body` | string | First 2000 characters of the response (useful for debugging) |

**Example:** Use a Condition node after the Notification to handle delivery failure:

```
node_5.success == False
```

True branch → retry logic or fallback channel. False branch → continue workflow.

---

## Save-time validation

The following checks run when you save a workflow containing a Notification node:

- `channel` must be a valid enum value
- `destination` is required and non-empty
- `messageTemplate` is required and non-empty
- Jinja2 syntax in `messageTemplate` is validated (catches typos like `{{ trigger.field }` — missing closing braces)
- Webhook channels (`slack_webhook`, `teams_webhook`, `discord_webhook`, `generic_webhook`) require `destination` to be an `https://` URL or a `{{ env.* }}` expression
- Channel-specific required fields: `chatId` for Telegram, `phoneNumber` + `phoneNumberId` for WhatsApp, `to` + `subject` for email

---

## Common patterns

### Alert on workflow failure

Place a Notification node on the `false` branch of a Condition that checks a previous node's success:

```
Trigger → LLM Agent → Condition (node_2.success) → [No] → Notification (Slack)
                                                  → [Yes] → ...
```

### Multi-channel notification

Use multiple Notification nodes in parallel (connected to the same upstream node) to send to different channels simultaneously:

```
Trigger → LLM Agent → Notification (Slack)
                    → Notification (PagerDuty)
                    → Notification (Email)
```

### Dynamic recipient from trigger payload

Set the `to` (email) or `phoneNumber` (WhatsApp) field to pull from the incoming request:

```
{{ trigger.recipient_email }}
{{ trigger.phone_number }}
```

This enables a single workflow to notify different people based on the trigger payload.

### Summarize then notify

Chain an LLM Agent to summarize content, then send the summary as a notification:

```
Trigger → LLM Agent (summarize) → Notification
```

Message template: `{{ node_2.response }}`

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `destination is empty` error | Vault secret not found or expression didn't resolve | Check the secret name in the Secrets UI; verify the trigger payload has the expected field |
| HTTP 403 from Slack | Webhook URL expired or workspace permissions changed | Regenerate the webhook in Slack settings |
| HTTP 401 from Telegram | Invalid bot token | Verify the token with `https://api.telegram.org/bot<TOKEN>/getMe` |
| HTTP 400 from WhatsApp | Template not approved, or phone not in E.164 format | Check template status in Meta dashboard; use `+` country code prefix |
| Mailgun returns 401 | API key incorrect or not base64-encoded properly | The handler encodes automatically — verify the raw key in your vault secret |
| SMTP timeout | Firewall blocking outbound port 587, or wrong hostname | Test SMTP connectivity from the server; check `smtpPort` matches your provider |
| Message body empty | `messageTemplate` contains only unresolvable expressions | Check that referenced nodes exist upstream and their output fields match |
