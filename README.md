# FormPilot

FormPilot is a web UI plus Python/Playwright backend for inspecting Google Forms, loading or generating structured test-response datasets, and running authorized form automation.

## Project layout

```
.
├── index.html              # Web interface
├── formpilot.py            # Flask API + Playwright automation backend
├── open_form.py            # Utility for inspecting a form locally
├── requirements.txt        # Python dependencies
├── run_formpilot.bat       # Windows launcher
├── .env.example
├── .gitignore
└── LICENSE
```

The deployable source files are intentionally at the repository root so hosts that expect the app entry point at the root can detect `index.html` and project files correctly.

## Run locally

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install the Playwright browser

```python -m playwright install chromium
```

### 3. Start FormPilot

Windows:

```
run_formpilot.bat
```

Or directly:

```bash
python formpilot.py
```

Then open the local address printed by Flask.

## Important deployment note

This project is **not a static website**. The frontend is `index.html`, but the automation features call the Flask endpoints in `formpilot.py` and use Playwright/Chromium.

Therefore:

- GitHub Pages can host the HTML but cannot run the Python/Playwright backend.
- A static-only deployment will load the UI but the `/api/*` automation endpoints will not work.
- For the complete application, deploy the Python backend on a service that supports a persistent Python process and browser automation, and point the frontend API calls at that backend.
- If you specifically want Vercel, the backend needs to be adapted to Vercel's Python/serverless runtime and Playwright's browser-runtime constraints; simply moving the files to the repository root does not solve that.

## Responsible use

Use FormPilot only with Google Forms and data you are authorized to test or automate. Do not use it to spam forms, bypass access controls, submit deceptive data, or interfere with services.

## Security

- Never commit API keys, passwords, cookies, or other secrets.
- Keep secrets in environment variables.
- Test automation against forms you own or are authorized to test.
- Do not commit generated `__pycache__` or browser artifacts.

## License

MIT License. See [LICENSE](LICENSE).
