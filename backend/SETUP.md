# Earth Engine setup

The backend reads Copernicus Sentinel-2 imagery through Google Earth Engine, which
is free for noncommercial use.

## Earth Engine does not use API keys

If you have a Google Cloud key that looks like `AIzaSy...`, it will **not** work
here. Those are Cloud API keys for public-data services (Maps, Places, and so on).
Earth Engine's compute API only accepts **OAuth 2.0** credentials, so it rejects
API keys no matter what permissions they carry. There is no configuration that
changes this.

Use one of the two methods below instead.

---

## Option A — user login (fastest, use this for local work)

### 1. Register a Cloud project for Earth Engine

1. Go to https://code.earthengine.google.com/register
2. Sign in with the Google account you want to use.
3. Create a new Cloud project, or select an existing one.
4. Choose **Unpaid usage** and pick the noncommercial purpose that applies
   (academic research, education, and so on).
5. Submit. This is usually instant, occasionally a few minutes.
6. Note the **project ID**.

### 2. Log in from your machine

From `backend/`, with your virtualenv active:

```bash
earthengine authenticate
```

A browser opens. Sign in with the same account, approve access, and the token is
cached under `~/.config/earthengine/`. You only do this once per machine.

If the browser cannot open (headless machine, remote shell), use:

```bash
earthengine authenticate --quiet
```

and follow the printed URL manually.

### 3. Set the project ID

Copy `.env.example` to `.env` and set just one value:

```
GEE_PROJECT_ID=your-project-id
```

Leave `GEE_SERVICE_ACCOUNT_EMAIL` and `GEE_SERVICE_ACCOUNT_KEY` blank. The backend
falls back to your cached login when they are empty.

Skip to **Verify** below.

---

## Option B — service account (for deployment)

Use this when the backend runs somewhere no human can complete a browser flow.

1. Complete step 1 of Option A so the project is registered.
2. Enable the Earth Engine API:
   https://console.cloud.google.com/apis/library/earthengine.googleapis.com
3. Create a service account at
   https://console.cloud.google.com/iam-admin/serviceaccounts, granting it the
   **Earth Engine Resource Viewer** role. Copy its email address.
4. In the service account's **Keys** tab, choose **Add key > Create new key >
   JSON**. Save the file to `backend/secrets/gee-service-account.json`.
   That directory is gitignored — never commit the key.
5. Register the service account for Earth Engine at
   https://code.earthengine.google.com/register, or add its email as a user of the
   project in the Code Editor's project settings.
6. Fill in all three values in `.env`:

```
GEE_PROJECT_ID=your-project-id
GEE_SERVICE_ACCOUNT_EMAIL=your-service-account@your-project.iam.gserviceaccount.com
GEE_SERVICE_ACCOUNT_KEY=./secrets/gee-service-account.json
```

---

## Verify

From `backend/`:

```bash
pip install -r requirements.txt
python scripts/verify_gee_auth.py
```

Expected final lines:

```
SUCCESS: found N Sentinel-2 scenes over Bhopal in January 2024.
Earth Engine is correctly configured.
```

Do not move on until this passes — nothing else in the backend can work without it.

Then run the paper's Bhopal case study end to end:

```bash
python scripts/run_bhopal.py
```

## Troubleshooting

| Message | Cause |
| --- | --- |
| `not signed up for Earth Engine` / `project is not registered` | Step 1 was skipped or has not propagated yet. Wait a few minutes and retry. |
| `Please authorize access to your Earth Engine account` | Run `earthengine authenticate`. |
| `Caller does not have permission` (service account) | The service account was never registered with Earth Engine — Option B step 5. |
| `401` with an `AIza...` key configured | Expected. Earth Engine does not accept API keys; use Option A or B. |
