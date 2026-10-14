# 📬 Multi-User Gmail Automation Agent

An autonomous, multi-user Gmail automation system with an interactive Streamlit dashboard built for hackathons and live judging. It connects to real user mailboxes via Google OAuth, ingests and classifies customer correspondence with multi-model LLM architectures, drafts policy-guarded replies directly in Gmail threads, detects security threats and escalations, and maintains resilient state across sessions.

---

## 🔒 Hard Security & Safety Guarantees
1. **NEVER Sends Email**: Drafts only. No code path invokes message sending. Guarded and enforced by continuous AST/regex automated tests (`tests/test_never_send.py`).
2. **Ephemeral OAuth Tokens**: Credentials live strictly in memory (`st.session_state`). Tokens and client secrets are **never** written to disk or logs.
3. **Stateless HMAC-SHA256 OAuth State**: State tokens are generated as `nonce:timestamp:hmac_signature` with a strict 10-minute TTL, immune to server restarts and multi-tab race conditions.
4. **Delimited Prompt Injection Defense**: Untrusted incoming customer emails are sealed inside strict XML delimiters (`<CUSTOMER_EMAIL>`), accompanied by deterministic regex scanners that automatically intercept and flag prompt injection attempts as critical escalations.
5. **Forbidden Financial Commitments Guard**: Post-generation regex scanners prevent unauthorized promises of money amounts (`$`), guaranteed refunds, or compensation unless explicitly permitted in domain policy.
6. **Isolated Domain Logic**: The topic and company policies are decoupled into a single configuration file (`config/domain.py`).

---

## 🏗️ Architecture & Three LLM Roles

The application leverages three distinct LLM models via Groq to prevent single-model bias, maximize rate limit allowances, and enforce a multi-layer verification system:

| Role | Environment Variable | Default Model | Responsibility |
|---|---|---|---|
| **Classifier** | `CLASSIFIER_MODEL` | `llama-3.3-70b-versatile` | Structured classification, sentiment, urgency, verbatim quote extraction, escalation detection. |
| **Verifier** | `VERIFIER_MODEL` | `mixtral-8x7b-32768` | Independent, blind classification without seeing the classifier's output. |
| **Drafter** | `DRAFTER_MODEL` | `llama-3.1-8b-instant` | Policy-constrained draft generation and holding replies. |

For ordinary requests, the drafter receives the email and its matching written policy rule. If model generation fails or is rate-limited, the app marks the policy fallback for human review instead of presenting it as a completed model draft.

> **Note on Groq Rate Limits**: Groq applies TPM (tokens per minute) limits per model. Using three distinct models allows three independent rate-limiting buckets.

---

## 📦 Folder Structure

```
├── app.py                      # Streamlit multipage navigation entrypoint
├── ui_kit.py                   # Shared UI context, auth gates, live engine & panels
├── views/                      # Multipage dashboard views
│   ├── dashboard.py            # Overview telemetry, KPIs & charts
│   ├── mailbox.py              # Filterable mailbox & conversation inspection
│   ├── draft_studio.py         # Dedicated prompt steering & draft revision studio
│   ├── escalations.py          # Security, prompt injection & legal incident triage
│   ├── evaluation.py           # Benchmark runner on golden adversarial test cases
│   └── activity.py             # Sync telemetry, diagnostics & audit trails
├── auth.py                     # Google Web OAuth + HMAC-SHA256 state verifier
├── config/
│   ├── domain.py               # ONLY file to edit when topic/policies change
│   └── settings.py             # Secret retrieval & pipeline tunables
├── llm.py                      # LLM factory (ChatGroq role routing)
├── limiter.py                  # Token-aware rate limiter (rolling 60s window)
├── gmail_client.py             # Gmail list, fetch, parse multipart, drafts, labels
├── rules.py                    # Pre & Post LLM deterministic guards
├── classifier.py               # Structured output classification
├── verifier.py                 # Independent blind verification
├── drafter.py                  # Policy-based drafting & version tracking
├── prompts.py                  # Delimited prompt templates
├── pipeline.py                 # State machine & background sweep engine
├── store.py                    # Dual persistence (Supabase + atomic JSON cache + delta sync)
├── fixtures/                   # Sample emails & precomputed results for offline demo
├── eval/
│   ├── golden_set.json         # 20 adversarial test cases (injections, legal, etc.)
│   └── run_eval.py             # Evaluation benchmark runner
└── tests/                      # Full pytest test suite (34 passing tests)
```

---

## 🚀 Setup & Local Execution

### 1. Prerequisites
- Python 3.11+
- Google Cloud Console Web Application OAuth 2.0 Credentials
- Groq API Key
- Supabase Project (Tables: `account_state`, `account_emails`)

### 2. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your credentials:
```ini
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
REDIRECT_URI=http://localhost:8501
STATE_SECRET=your-random-32-char-hmac-secret-key

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-supabase-service-role-key

GROQ_API_KEY_CLASSIFY=gsk_your_groq_api_key
GROQ_API_KEY_DRAFT=gsk_your_groq_api_key

CLASSIFIER_MODEL=llama-3.3-70b-versatile
VERIFIER_MODEL=mixtral-8x7b-32768
DRAFTER_MODEL=llama-3.1-8b-instant

CLASSIFIER_TPM=8000
VERIFIER_TPM=8000
DRAFTER_TPM=8000
```

### 3. Google OAuth Setup
1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **Gmail API**.
3. Configure the OAuth Consent Screen (External, add test user emails).
4. Create an **OAuth 2.0 Client ID**:
   - Application Type: **Web application**
   - Authorized redirect URIs:
     - Local: `http://localhost:8501`
     - Streamlit Cloud: `https://<your-app-subdomain>.streamlit.app`

### 4. Supabase Database Schema
The app expects two existing tables:
```sql
create table if not exists account_state (
    email_hash text primary key,
    settings jsonb,
    audit jsonb,
    updated_at timestamptz default now()
);

create table if not exists account_emails (
    email_hash text,
    message_id text,
    status text,
    record jsonb,
    updated_at timestamptz default now(),
    primary key (email_hash, message_id),
    foreign key (email_hash) references account_state(email_hash) on delete cascade
);
```

> ⚠️ **Notice**: Free-tier Supabase projects automatically pause after 7 days of inactivity. If paused, restore the project in the Supabase console prior to live demos.

### 5. Running the Application
```bash
# Activate your virtual environment
sentec_env\Scripts\activate

# Launch Streamlit
streamlit run app.py
```

---

## ☁️ Streamlit Community Cloud Deployment

1. Push this repository to GitHub.
2. Visit [share.streamlit.io](https://share.streamlit.io) and create a new app pointing to your repository (`app.py`).
3. Set the Python version to `3.11`.
4. In the app settings under **Secrets**, copy and paste the contents of `.streamlit/secrets.toml.example` with your production values (make sure `REDIRECT_URI` matches your Streamlit app domain).
5. Ensure your Streamlit Cloud domain is added to the Google Cloud OAuth client's **Authorized redirect URIs**.

---

## 🧪 Running Automated Tests

Run the complete test suite with `pytest`:
```bash
python -m pytest -v
```
All 34 tests cover:
- Prohibiting `messages().send` / `drafts().send`
- HMAC state generation, signature verification, tampering, expiration, and malformed rejection
- Pre-LLM guards, prompt injection interception, and verbatim evidence quotes
- Rate limiter rolling window and TPM backoff
- Supabase batch upserts, dirty ID tracking, and delta sync
- Fake Gmail backlog sweeps exceeding `MAX_PER_TICK`
- Benchmark evaluation against `eval/golden_set.json`
