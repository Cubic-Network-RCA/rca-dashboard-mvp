# RCA Closed-Loop Dashboard (MVP)

A lightweight open-source demo you can show today:
- **Streamlit** web GUI
- **SQLite** local DB (file-based)
- **Evidence + Verification** workflow (closed loop)
- **"AI" flavour** via open-source TF‑IDF similarity matching to detect likely recurrence (e.g., Nissan)

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt

# run the dashboard
streamlit run app.py
```

## What to demo (2 minutes)
1. In sidebar click **Seed demo data**
2. Tick **Pre-Live last 6 months (audit view)**
3. Show **Missing evidence** and **Overdue actions** KPIs
4. Open **New Incident (AI match)** and click **Find similar RCAs**
5. Open **RCA Detail** to show action evidence + verification separation

## Next steps (when you productize)
- Replace SQLite with Postgres
- Add SSO (Azure AD / Okta)
- Integrate Jira/ServiceNow for incidents/defects
- Add proper embeddings + vector DB (optional) for stronger similarity search
- Enforce gates: cannot close RCA until all actions are **Verified**
