# amoCRM Integration — Design

**Date:** 2026-07-12
**Status:** Draft (pending user approval)

## Goal

Connect the dashboard to amoCRM so that:

1. **Client creation** — when a new client is created, look up amoCRM leads by the
   client's phone number. If a lead exists, mark the client locally as "exists on
   amoCRM". Never write anything to amoCRM in this flow.
2. **Transaction creation** — when a transaction is saved, find the client's lead in
   amoCRM and move it to the standard "Closed – Won" stage (`status_id = 142`)
   immediately (not waiting for admin confirmation).
3. **Source auto-set** — `Transaction.source` is set automatically from the lookup
   result instead of always defaulting to `not_in_amocrm`.

Failures never block saving: if amoCRM is unconfigured, unreachable, or has no
matching lead, the local save proceeds and the user sees a warning message.

## Background / current state

- The repo already contains a contact-sync service (`main/services/amocrm.py`) built
  on the `amocrm-api` library with OAuth. It was never connected (no credentials, no
  tokens) and the OAuth flow (redirect URL + 20-minute auth code) is the main
  obstacle. Decision: **replace it** with a thin HTTP client using a long-lived token.
- `Client` already has `amocrm_id` (contact ID) and `synced_at`; the admin shows an
  "amoCRM / Qo'lda" badge based on `amocrm_id`.
- `Transaction.source` exists with choices `amocrm_website` / `amocrm_other` /
  `not_in_amocrm` but is **not on the form** — today every transaction gets the
  default `not_in_amocrm`.
- Clients are created two ways: directly in `ClientAdmin`, and implicitly via
  `TransactionAdmin.save_model` (`get_or_create` by phone from the `client_name` /
  `client_phone` form fields).
- The local virtualenv (`env/`) is broken (project was moved from
  `/Users/macbookpro/...`); it must be recreated.

## Configuration

Two environment variables replace the five old `AMOCRM_*` OAuth variables:

| Variable | Meaning |
|---|---|
| `AMOCRM_SUBDOMAIN` | e.g. `mycompany` for `mycompany.amocrm.ru` |
| `AMOCRM_TOKEN` | Long-lived token created in amoCRM → Settings → Integrations |

`settings.AMOCRM = {"SUBDOMAIN": ..., "TOKEN": ...}`. If either is empty the service
raises `AmoCRMNotConfigured`, which callers convert to a warning message.

## Components

### 1. `main/services/amocrm.py` (rewritten)

A small `requests`-based client for amoCRM API v4. All requests use
`Authorization: Bearer <token>` and a 5-second timeout.

Public interface:

- `AmoCRMNotConfigured(Exception)` — kept, same meaning as today.
- `find_lead_by_phone(phone) -> LeadMatch | None`
  - Normalizes the phone (strip everything except digits; compare by the last 9
    digits so `+998 90 123-45-67`, `901234567`, and `998901234567` all match).
  - Calls `GET /api/v4/contacts?query=<digits>&with=leads`, then verifies the
    returned contacts actually contain the normalized phone in their phone custom
    field (amoCRM's `query` is fuzzy).
  - Collects the matched contact's leads (`GET /api/v4/leads?filter[id][]=...` for
    details when needed). Picks the best lead: prefer active leads (not in
    `status_id` 142/143), newest first; if only closed leads exist, return the
    newest closed one with `is_active=False`.
  - Returns `LeadMatch(contact_id, lead_id, is_active)` or `None`.
- `close_lead(lead_id) -> None`
  - `PATCH /api/v4/leads/{lead_id}` with `{"status_id": 142}` ("Closed – Won",
    present in every pipeline; the lead stays in its current pipeline).
- `sync_contacts(logger=None) -> {"created": n, "updated": n}`
  - Kept for the existing bulk-import admin action, reimplemented on the HTTP
    client: pages through `GET /api/v4/contacts` and `update_or_create`s `Client`
    rows by `amocrm_id`, same as today.

Network/HTTP errors raise a single `AmoCRMError` exception; callers catch it and
warn. No retries (a manual re-save retries naturally).

### 2. Model change

Add to `Client`:

- `amocrm_lead_id = BigIntegerField(null=True, blank=True)` — the matched lead.
  A non-null value means "exists on amoCRM" for the lead-based flows.

One migration. `amocrm_id` (contact) and `synced_at` keep their current meaning.
The admin badge logic becomes: badge shows "amoCRM" if `amocrm_id` **or**
`amocrm_lead_id` is set.

### 3. Client-creation hook

A helper `link_client_to_amocrm(client) -> LeadMatch | None` in the service module:
runs `find_lead_by_phone(client.phone_number)`; on a match, saves
`amocrm_id`, `amocrm_lead_id`, `synced_at` on the client.

Called from:

- `ClientAdmin.save_model` — only on creation (`change=False`). Messages:
  match → success "Mijoz amoCRM'da topildi"; no match → info; API error /
  unconfigured → warning. Save always succeeds.
- `TransactionAdmin.save_model` — when it `get_or_create`s a **new** client (see
  next section, one lookup shared by both concerns).

### 4. Transaction-creation hook

In `TransactionAdmin.save_model`, for **new** transactions (`change=False`), after
the client is resolved:

1. Determine the lead: use `client.amocrm_lead_id` if set, otherwise run
   `find_lead_by_phone` once (and store the result on the client per §3).
2. Set `obj.source` before saving:
   - lead found → `amocrm_other`
   - no lead / error → `not_in_amocrm`
   - (Distinguishing `amocrm_website` automatically is out of scope: the API result
     doesn't carry a reliable "came from website" marker. Future enhancement once
     the user tells us how website leads are tagged in their account.)
3. After the transaction saves, call `close_lead(lead_id)` if a lead was found.
   Messages: moved → success; no lead → warning ("amoCRM'da lead topilmadi");
   API error → warning with the error. The transaction save itself never fails or
   rolls back because of amoCRM.

Edits to existing transactions (`change=True`) do not touch amoCRM.

### 5. Housekeeping

- Recreate the virtualenv (`python3 -m venv env && env/bin/pip install -r requirements.txt`).
- `requirements.txt`: remove `amocrm-api`, keep `requests` (already present).
- Update `core/settings.py` AMOCRM block to the two new variables.
- The `sync_amocrm_clients` management command keeps working unchanged (it calls
  `sync_contacts`).

## Error handling summary

| Situation | Behavior |
|---|---|
| `AMOCRM_*` env vars missing | Local save proceeds; warning "amoCRM sozlanmagan" |
| amoCRM unreachable / 5xx / timeout | Local save proceeds; warning with error text |
| No contact/lead matches phone | Client saves without link; transaction saves with `source=not_in_amocrm`; warning |
| Multiple leads match | Newest active lead wins; closed leads only if no active ones |
| Lead already closed | `close_lead` still PATCHes 142 (idempotent); message notes it was already closed if detectable |

## Testing

Unit tests (Django `TestCase`, `unittest.mock` on the HTTP layer — no real network):

- Phone normalization: various formats map to the same search key.
- `find_lead_by_phone`: match, no match, fuzzy-query false positive filtered out,
  multiple leads → newest active chosen, only-closed-leads case.
- Client admin save: fields stored on match; save succeeds on API error.
- Transaction admin save: `source` set correctly for match/no-match/error;
  `close_lead` called with the right ID; save succeeds when amoCRM fails.
- `AmoCRMNotConfigured` raised when env vars are missing.

Manual verification once the user provides `AMOCRM_SUBDOMAIN` + `AMOCRM_TOKEN`:
create a test client with a phone known to exist in amoCRM, then a transaction, and
confirm the lead moves to "Closed – Won" in the amoCRM UI.

## Out of scope (YAGNI)

- Automatic `amocrm_website` detection (needs account-specific lead tagging info).
- Webhooks / real-time sync from amoCRM.
- Writing contacts, notes, or tasks to amoCRM.
- Background job queue — calls are synchronous with a 5s timeout.
