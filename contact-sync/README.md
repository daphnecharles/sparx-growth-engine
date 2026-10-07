# Sparx Labs: Marketing Contact Sync

A self-hosted [n8n](https://n8n.io) instance on Oracle Cloud that keeps **Attio as the single source of truth for contacts**:

- **Kit (ConvertKit)** runs outbound sales sequences and notifications.
- **Substack** runs the monthly newsletter, *Sparx Signal*.
- n8n moves contacts between them and Attio.

```
             new subscriber (webhook)                 "Send Outbound" (poll, 15 min)
   Kit  ───────────────────────────────►  Attio  ─────────────────────────────────►  Kit sequence
   Kit  ── unsubscribe / bounce / complaint ──►  Attio (email_status)
 Substack ──► notification email ──► Gmail label ──► n8n parses it ──►  Attio
```

This folder is self-contained. You can move it into its own repository without changing anything.

| File | What it is |
|---|---|
| `docker-compose.yml` | Runs n8n (pinned `2.42.4`) and Caddy (pinned `2.11.7`) |
| `Caddyfile` | HTTPS with automatic Let's Encrypt certificates, plus a password prompt on the editor |
| `.env.example` | Every setting, with comments. Copy it to `.env` and fill it in |
| `workflows/*.json` | Four workflows, ready to import into n8n |
| `scripts/bootstrap-vm.sh` | One-time setup on the VM: installs Docker and opens the firewall |
| `scripts/register-kit-webhooks.sh` | Tells Kit where to send its webhooks |
| `scripts/backup-workflows.sh` | Exports every workflow to JSON |

---

## What each workflow does (plain language)

### 1. Kit → Attio (new subscribers) · `workflow-convertkit-to-attio.json`
When someone subscribes or confirms in Kit, Kit notifies n8n right away. n8n then finds that person in Attio by email address, or creates them. It tags them **source = ConvertKit** and sets **email_status = Subscribed**. It never overwrites a name the team has already entered in Attio. It only fills in a name when Attio doesn't have one.

### 2. Attio → Kit (Send Outbound) · `workflow-attio-to-convertkit.json`
Every 15 minutes, n8n asks Attio for people whose **outbound_status** is "Send Outbound" and who haven't been sent to Kit yet. For each person it:
1. adds them to Kit,
2. enrolls them in the outbound sequence,
3. ticks **synced_to_convertkit** in Attio so they aren't sent again.

If one person fails (for example, Kit rejects the address), the others still go through. The run shows as failed in n8n with that person's email and the reason, and they are retried on the next run.

To send someone outbound, set their outbound_status to "Send Outbound" in Attio and wait up to 15 minutes.

### 3. Substack → Attio · `workflow-substack-to-attio.json`
Substack has no API, so this works around it. Substack emails a "new subscriber" notification, and a Gmail filter puts it under the label `substack-subscribers`. Every 5 minutes n8n checks that label, pulls the subscriber's email address out of the notification, and adds or updates them in Attio with **source = Sparx Signal**.

### 4. Kit unsubscribes/bounces → Attio · `workflow-convertkit-unsubscribe-to-attio.json`
When someone unsubscribes, bounces or marks a Kit email as spam, Kit notifies n8n. n8n sets that person's **email_status** in Attio to Unsubscribed, Bounced or Complained, so the team can see it.

> **Why a separate fourth workflow instead of adding this to Workflow 1?** A webhook node in n8n has a single URL, so combining them would have meant two triggers and branching logic in one flow. As a separate workflow, each one is simple. A bug in one can't stop the other, and either can be switched off on its own. The cost is one extra workflow to import.

---

## Setup

### Step 0. Attio fields (manual, about 5 minutes)
These custom attributes don't exist in the Attio workspace yet. Create them under **Settings → Objects → People → Attributes**. The **API slug must match exactly**.

| Title | API slug | Type | Options |
|---|---|---|---|
| Source | `source` | Select, **allow multiple values** | `ConvertKit`, `Sparx Signal` |
| Outbound status | `outbound_status` | Select | `Send Outbound` (add any others you like) |
| Synced to ConvertKit | `synced_to_convertkit` | Checkbox | n/a |
| Email status | `email_status` | Select | `Subscribed`, `Unsubscribed`, `Bounced`, `Complained` |

Option titles must also match exactly. If one doesn't, Attio rejects the write and the workflow run turns red.

### Step 1. VM and network (manual console steps are marked 🖐)
1. 🖐 **Oracle Cloud console:** open the VM's subnet **security list** and add ingress rules for TCP 80, TCP 443 and UDP 443 from `0.0.0.0/0`.
2. 🖐 **DNS:** create an A record, e.g. `automate.sparxlabs.io`, pointing to the VM's public IP. Wait until `dig +short automate.sparxlabs.io` returns that IP.
3. On the VM:
   ```bash
   git clone <this repo> && cd <repo>/contact-sync
   bash scripts/bootstrap-vm.sh        # installs Docker and opens ports 80/443 in the VM's firewall
   # log out and back in
   cp .env.example .env && chmod 600 .env
   nano .env                            # fill in every YOUR_* value (the comments explain how to generate each one)
   docker compose up -d
   docker compose logs -f caddy         # wait for "certificate obtained successfully"
   ```

**✅ Manual acceptance check:** open `https://YOUR_DOMAIN`. The browser should show a valid padlock and ask for the basic-auth username and password. After that, the n8n setup screen appears. The first time, it asks you to create the **owner account**. Use a team-owned email and store the password in the password manager.

> **Login protection: what changed from the original plan.** The plan called for `N8N_BASIC_AUTH_ACTIVE=true`. n8n removed that setting in v1.0, and current versions ignore it. To keep the intent, Caddy now asks for a username and password on everything except the `/webhook/...` paths. Webhooks are exempt because Kit can't send a password. Behind that, n8n's own owner login still applies. So there are two layers, and the credentials still come from `.env`.

### Step 2. Credentials in n8n
In n8n, go to **Overview → Credentials → Create credential**. Use these **exact names**, because the imported workflows look them up by name:

| Credential name | n8n credential type | Fields | Used by |
|---|---|---|---|
| `Attio API` | **Header Auth** | Name: `Authorization`. Value: `Bearer YOUR_ATTIO_API_KEY` | Workflows 1–4 |
| `Kit API` | **Header Auth** | Name: `X-Kit-Api-Key`. Value: `YOUR_KIT_V4_API_KEY` | Workflow 2 |
| `Gmail (Substack notifications)` | **Gmail OAuth2 API** | Client ID and Client Secret (see below), then click **Sign in with Google** | Workflow 3 |

- **Attio key:** Attio → Settings → Developers → New access token. It needs read/write on *Records* and read on *Object configuration*.
- **Kit key:** Kit → Settings → Developer → create a **v4** API key.
- 🖐 **Gmail OAuth (manual):**
  1. In Google Cloud Console, create a project and enable the **Gmail API**.
  2. Set up the OAuth consent screen.
  3. Create an OAuth Client ID of type *Web application* with this redirect URI: `https://YOUR_DOMAIN/rest/oauth2-credential/callback`.
  4. Paste the client ID and secret into n8n and click **Sign in with Google** using the Gmail account that receives the Substack emails.
  5. **Important:** if the consent screen is *External* and still in *Testing*, Google expires the token **every 7 days**. Either use a Google Workspace account with an *Internal* consent screen, or set the app's publishing status to *In production*. An unverified production app works for your own account, after a one-time warning screen.

API keys live only in n8n's credential store, which is encrypted with `N8N_ENCRYPTION_KEY`. They never go in `.env` or in the workflow files.

### Step 3. Import and activate the workflows
1. In n8n, click **Create workflow → ⋯ menu → Import from File**, then pick a file from `workflows/`. Repeat for all four.
2. Open each workflow and click any node with a ⚠️ warning. Select the matching credential from the dropdown. This is usually only needed if n8n didn't link the credential automatically by name.
3. **Workflow 3:** if your Gmail label isn't named `substack-subscribers`, open the Gmail trigger node and edit the Search field (`label:your-label-name`). Gmail writes spaces in label names as `-`.
4. Turn on the **Active** toggle for each workflow.

### Step 4. Point Kit at n8n
Run this on the VM once Workflows 1 and 4 are active:
```bash
KIT_API_KEY=your_v4_key ./scripts/register-kit-webhooks.sh
```
It registers four webhooks: activate, unsubscribe, bounce and complain. Each URL contains the secret `?token=`.

### Step 5. 🖐 Live tests (manual)
| Test | How | Expected |
|---|---|---|
| Kit → Attio | Subscribe a test address through a Kit form | Within seconds, the person appears in Attio with source ConvertKit and email_status Subscribed |
| Attio → Kit | Set a test person's outbound_status to "Send Outbound" | Within 15 minutes, they're in the Kit sequence and synced_to_convertkit is ticked |
| Unsubscribe | Unsubscribe that test address from a Kit email | email_status becomes Unsubscribed |
| **Substack → Attio** | Subscribe a test address to Sparx Signal | Within about 5–10 minutes, the person appears in Attio with source Sparx Signal. Open the execution in n8n and check the *Parse subscriber email* node output: `all_candidates` should contain only the test address |

---

## Webhook security
Kit can't sign its webhooks or send a password. Instead, each webhook URL carries a long random `?token=` that matches `WEBHOOK_SHARED_SECRET`. Any request without the right token is rejected and shows up as a red "Rejected Kit webhook" execution. If you change the secret, re-run `register-kit-webhooks.sh`, and delete the old webhooks in Kit with `GET`/`DELETE https://api.kit.com/v4/webhooks`.

## How the Attio upsert works
Attio's "assert" endpoint creates or updates a person in one call: `PUT /v2/objects/people/records?matching_attribute=email_addresses`. When the matching attribute has multiple values (like email addresses), Attio *adds* the address and keeps the person's other emails. However, any **other** multi-value field sent in that same call gets *replaced*. That would wipe tags such as an existing "Sparx Signal" source. So the workflows do two steps:
1. Assert with **only** the email address.
2. `PATCH` the record, which **appends** to multi-value fields, to add the source tag and status. The name is only set if Attio has no name.

---

## Known limitations
- **Substack has no API or webhooks.** Workflow 3 reads Substack's notification *emails*. That's a workaround, not an official integration. If Substack changes the email wording, parsing can break (see troubleshooting below).
- **No Attio → Substack sync is possible.** Substack has no way to add subscribers from outside.
- **Attio → Kit lags up to 15 minutes.** Attio's webhooks can't trigger on "field equals X and another field is unchecked", so the workflow checks Attio every 15 minutes instead. This is a deliberate tradeoff, not a bug. Each run handles up to 100 people, and the rest go on the next run.
- **Substack notifications give us only an email address**, not a name.
- **source means "every system they came through"**, not "first touch". A person enrolled from Attio into Kit triggers Kit's "subscriber activated" webhook, so they also get the ConvertKit tag.
- **Kit subscribers created by Workflow 2 are added as active**, with no double opt-in. Only mark people "Send Outbound" if you have a legitimate basis to email them, and keep the unsubscribe link in every sequence email.
- Execution history is kept for **30 days**. That's our only log by design. There's no separate monitoring dashboard.

## Backups
**What to back up:**
1. **Workflows.** Run `./scripts/backup-workflows.sh` on the VM. It writes `backups/<date>/*.json`.
2. **The `.env` file, especially `N8N_ENCRYPTION_KEY`.** Store it in the team password manager. Without that key, a restored n8n can't decrypt its credentials.
3. Credentials are deliberately **not** exported because they contain live API keys. After a restore, re-enter them using the Step 2 table.

**Where to store them:** copy the `backups/<date>` folder into the shared Google Drive folder **"Sparx Labs / Automation Backups"**, or commit it to the private company GitHub repo under `contact-sync/backups/`. To do that, remove `backups/` from `contact-sync/.gitignore` first. Do this after every workflow change, and at least monthly.

You can also export one workflow by hand: open it, then **⋯ → Download**.

**Full disaster recovery:** start a new VM, run Step 1 with the same `.env`, recreate the credentials, import the backup JSON files, activate them and re-run `register-kit-webhooks.sh` if the domain changed.

## If something breaks
Start here: n8n → **Overview → Executions**, filter by **Failed**, and open the red execution. The node with the red border shows the error, and every failure message includes the original payload.

| Symptom | Likely cause | Fix |
|---|---|---|
| Substack sign-ups stop appearing in Attio, or *Parse subscriber email* errors with "Could not find a subscriber email" | Substack changed its notification email | Open the failed execution. `raw_body_excerpt` shows the email text. Edit the Code node **Parse subscriber email (Substack template)**. That's the only place parsing happens. Add the missing person to Attio by hand |
| Substack flow picked up the **wrong** address (e.g. yours) | A new non-subscriber address appears in the email | Add it to `SUBSTACK_PUBLISHER_EMAILS` in `.env`, then run `docker compose up -d` |
| Substack flow silent, no executions at all | Gmail token expired (the 7-day "Testing" consent screen problem), or the Gmail filter/label changed | Re-open the Gmail credential and click **Sign in with Google** again. Check that the label still receives the emails |
| `401` / `403` from Attio or Kit | API key revoked, expired or missing a scope | Create a new key and paste it into the `Attio API` / `Kit API` credential |
| Attio `400` mentioning an option or attribute | An Attio field or option was renamed or deleted | Compare with the Step 0 table |
| Kit events not reaching n8n | Webhooks not registered, or the token changed | List them with `curl https://api.kit.com/v4/webhooks -H "X-Kit-Api-Key: …"` and re-run the register script |
| Red "Rejected Kit webhook" executions | A request arrived without the right token. This is either a stale webhook in Kit or a random scanner | Clean up old webhooks in Kit. Scanner requests can be ignored |
| Site won't load or shows a certificate error | DNS doesn't point at the VM yet, or ports 80/443 are blocked | Run `docker compose logs caddy`, check the Oracle security list and the VM firewall (`sudo iptables -L INPUT -n`) |
| Workflow 2 fails every run for the same person | Kit permanently rejects that address | Fix the email in Attio, or clear their "Send Outbound" status |

Upgrading n8n: change the image tag in `docker-compose.yml`, back up first, then run `docker compose pull && docker compose up -d`.

---

## 🖐 Still needed from Sparx Labs (human-only steps)
- [ ] Oracle Cloud: VM provisioned (Ampere A1.Flex, Ubuntu 24.04), SSH working, **security list opened for 80/443**
- [ ] DNS A record for the chosen subdomain → VM public IP (replaces `YOUR_DOMAIN`)
- [ ] Attio: create the four People attributes in Step 0, and create an API token (`YOUR_ATTIO_API_KEY`)
- [ ] Kit: v4 API key (`YOUR_KIT_V4_API_KEY`) and the outbound **sequence ID** (`YOUR_KIT_SEQUENCE_ID`)
- [ ] Gmail: confirm the inbox and label (default `substack-subscribers`) that catch Substack's "new subscriber" emails, and list the publisher/inbox addresses for `SUBSTACK_PUBLISHER_EMAILS`
- [ ] Google Cloud: OAuth client and consent screen, then **Sign in with Google** in n8n
- [ ] Generate `N8N_ENCRYPTION_KEY`, `WEBHOOK_SHARED_SECRET` and the basic-auth hash, and store them in the password manager
- [ ] Create the n8n owner account on first login
- [ ] Run the live tests in Step 5, especially a **real Substack subscription** to confirm the email parser works on Substack's current template
- [ ] Pick the backup location (shared Drive folder or private GitHub) and set a monthly reminder
