# AI-Gate DLP browser extension — changelog

## 0.3.4 — security fix
- **Messages on claude.ai were sent unmasked.** The composer was found only by
  fixed selectors; Claude's new markup matched none of them, so the Enter
  handler returned early and the site sent the original text. Found on
  23.10.2026 with test data.
- The chat input is now recognised where the user types: any editable field
  (textarea / contenteditable, outermost editor root) that has a chat hint
  (aria-label, placeholder) or a send button in a block without other editors.
  An ordinary document editor on the same page keeps Enter as a new line.
- Send buttons are recognised by aria-label / data-testid / title (send,
  submit, отправ) and by type=submit in a form with an editor.
- Listeners moved from document to window (capture), the earliest point.
- Fail-closed: a send button with no recognisable input stops the click;
  if no chat input is visible on a supported site, a red warning is shown and
  the badge turns "!".

## 0.3.3
- "Copy the last reply with real values" finds ChatGPT's "Текст" card, which
  has no data-message-author-role: falls back to the last conversation turn
  without a user message, then to the last block with a placeholder. Only the
  reply text is copied, without the card's header.

## 0.3.2 — security fix
- **Real values are no longer written into the site's page.** Restoring
  placeholders in the reply's DOM leaked them: ChatGPT keeps the conversation
  locally and syncs it, and its editable reply blocks treat a DOM change as a
  user edit, so the restored name, IIN and phone were saved to OpenAI after the
  reply. Found on 23.10.2026 with test data: the conversation opened on
  another device showed real values.
- Instead, a panel (closed shadow root outside `<body>`) lists each visible
  placeholder with its value and copies the last reply with real values.
- Verification: open the conversation on another device; reloading the page in
  the same browser is not proof.

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
