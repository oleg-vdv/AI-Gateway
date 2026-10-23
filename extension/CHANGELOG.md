# AI-Gate DLP browser extension — changelog

## 0.3.1
- ChatGPT's temporary conversation address changed from `/c/WEB:<uuid>` to
  `/c/local-chatgpt:<uuid>`; any `/c/` id with a colon is now treated as
  temporary, so the session follows the conversation to its real address and
  replies are restored again (was: "нет сессии для этого диалога").

## 0.3.0
- Settings moved from `storage.sync` to `storage.local`: the channel key is no longer
  synced to the user's Google account. Existing settings are migrated on first use.
- Administrators can set the gateway address, channel key and user label by browser
  policy (`storage.managed`, schema in `managed_schema.json`); such fields are read-only
  in the options page.
- New placeholder types are restored in replies: PHONE, EMAIL, ID_DOC, BIRTHDATE,
  ADDRESS, PLATE, CONFIDENTIAL.
- Icons; store packaging via `tools/pack_extension.py`.

## 0.2.3
- Session follows ChatGPT's temporary `/c/WEB:<uuid>` URL to the real conversation id.

## 0.2.2
- Session is carried over by "URL changed shortly after a send", not by a fixed list
  of new-chat paths (projects, GPTs, temporary chats now work).
- Diagnostic messages in the page console, prefixed `[AI-Gate]` (no personal data).

## 0.2.1
- Tokens split across several DOM nodes (`[` + `PERSON_1` + `]`) are restored.
- Only the message composer is excluded from restoring, not every editable block
  (ChatGPT draft cards are restored now).

## 0.2.0
- One gateway session per conversation: the same person keeps the same token
  across messages, and old messages are never restored with another person's data.
- Resubmit by clicking the send button (synthetic Enter is ignored by React apps).
- Restore payload no longer breaks on quotes or backslashes.
- Real values are never written back into the message composer.
- Restoring keeps working for the whole page lifetime (React re-renders).
- Narrowed permissions: localhost only, other gateway hosts requested on demand.
- Messages accepted only from the extension's own content scripts.
- Session ids kept in `storage.session` (memory only).
- Badge shows gateway status; "Check connection" in options; warning on file uploads.
- Firefox background script support.
