# AI-Gate DLP browser extension — changelog

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
