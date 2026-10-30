# ASK Company – backend

Django REST API for ASK Company. The Next.js frontend forwards `/api/...` to it.

## Run locally

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Without a `.env` file it uses SQLite (`db.sqlite3`) and saves uploads in `media/`. See `.env.example` for every setting.

## Host on Render

The database is Render Postgres, uploaded photos and the logo go to Supabase Storage, and the API runs as a Render web service. `render.yaml` describes the web service.

### 1. Supabase Storage (photos and logo)

Render wipes the server's disk on every deploy, so uploads must be stored elsewhere.

1. In your Supabase project, open **Storage → New bucket**, name it `media` and turn **Public bucket** on.
2. Open the Storage **S3 Connection** settings:
   - Note the **Endpoint** and **Region**.
   - Under **S3 Access Keys**, choose **New access key** and copy the **Access key ID** and **Secret access key**. Supabase shows the secret only once.

### 2. Render web service

1. In Render choose **New → Blueprint**, pick this repository, and fill in:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | The database's **Internal Database URL** (database page → **Connect**) |
   | `SUPABASE_URL` | The Supabase S3 **Endpoint**, or the project URL `https://<ref>.supabase.co` (either works) |
   | `SUPABASE_S3_REGION` | The **Region** shown next to the endpoint |
   | `SUPABASE_S3_ACCESS_KEY_ID` | The access key ID |
   | `SUPABASE_S3_SECRET_ACCESS_KEY` | The secret access key |

   `DJANGO_SECRET_KEY` is generated automatically. The service runs in Virginia, the same region as the database, which is what lets the internal URL work.

2. Each deploy runs `build.sh`, which installs packages, collects static files and applies database migrations. The start command is `gunicorn asmah.wsgi`.

3. Once the frontend is online, add `FRONTEND_URL=https://<your-frontend>` to the service. Password-reset emails link to this address.

### 3. Create the admin login

The Render free plan has no shell, so create the admin from your own computer. Use the database's **External Database URL** for this step.

In PowerShell:

```powershell
cd backend
$env:DATABASE_URL = "<External Database URL>"
python manage.py createsuperuser
Remove-Item Env:DATABASE_URL
```

Remove the variable afterwards so `runserver` goes back to your local SQLite database.

### 4. Point the frontend at the API

Where the frontend is hosted, set `BACKEND_URL=https://<your-service>.onrender.com` before `npm run build`. The rewrites in `next.config.mjs` read it at build time.

### Good to know

- **Free web service:** it sleeps after 15 minutes without visits. The first request afterwards takes about a minute.
- **Free Postgres:** a free Render Postgres database expires 30 days after it was created. Use a paid database plan for real loan data.
- **Logs:** errors and tracebacks appear in the service's **Logs** tab.
- **Email:** password-reset emails are printed to the logs until the `EMAIL_*` settings are filled in.
- **Backups:** the in-app backup download (Settings) and `manage.py backup_db` save the database. Photos stay in Supabase.
- **Secrets:** never commit a database URL or key. Set them in Render's **Environment** tab, and in your local `.env` only for local work.
